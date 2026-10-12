"""Inbound inspection + full reality / cloudflare flows.

NOTE (v1): long operations (installs, flows) run synchronously with
generous timeouts and return full JSON output. The frontend shows a
spinner while waiting — no background jobs / streaming in v1.
"""
from __future__ import annotations

import asyncio
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import keys_lib
from ..certs_lib import (
    cert_still_valid,
    check_certbot,
    get_domain_cert_lines,
    install_certbot,
    obtain_cert,
    port80_busy,
)
from ..cf import CloudflareAPI
from ..database import get_db
from ..deps import get_current_user
from ..inbound_lib import (
    CF_PORTS,
    build_cloudflare_tls_inbound,
    build_reality_inbound,
    merge_inbound_into_config,
    parse_sni,
    remove_inbound_from_config,
)
from ..keys_lib import generate_short_ids
from ..models import CfAccount, Panel, Server
from ..node_lib import check_pg_node_status, get_node_credentials, install_pg_node
from ..panel_sdk import (
    ensure_node_with_core,
    get_node_inbounds,
    make_api,
    modify_core_resilient,
    require_panel_key,
)
from ..schemas import CloudflareCertAutoIn, CloudflareCertIn, CloudflareInboundIn, CloudflareQuickIn, RealityIn, RealityQuickIn, model_to_dict
from ..ssh_service import SshSession, target_from_server

router = APIRouter(prefix="/api/inbounds", tags=["inbounds"])


def _get_server(db: Session, server_id: int) -> Server:
    s = db.query(Server).filter(Server.id == server_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return s


def _get_panel(db: Session, panel_id: int) -> Panel:
    p = db.query(Panel).filter(Panel.id == panel_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    if not p.base_url or not p.api_key.strip():
        raise HTTPException(status_code=400, detail=f"Panel '{p.name}' is incomplete (base URL + API key needed).")
    return p


def _get_cf(db: Session, account_id: int) -> CfAccount:
    a = db.query(CfAccount).filter(CfAccount.id == account_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Cloudflare account not found")
    if not a.token.strip():
        raise HTTPException(status_code=400, detail="Cloudflare account token is empty")
    return a


@router.get("")
async def list_inbounds(
    panel_id: int = Query(...),
    server_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    p = _get_panel(db, panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        if server_id is not None:
            s = _get_server(db, server_id)
            return await get_node_inbounds(p.base_url, p.verify_ssl, token, s.host)
        # No server filter: return every node with its core inbounds.
        async with make_api(p.base_url, p.verify_ssl) as api:
            nodes = await api.get_nodes(token=token)
            items = getattr(nodes, "nodes", nodes) or []
            out = []
            for n in items:
                core_id = getattr(n, "core_config_id", None)
                inbounds: list = []
                core_name = ""
                if core_id is not None:
                    try:
                        full = await api.get_core_config(core_id=core_id, token=token)
                        inbounds = (full.config or {}).get("inbounds", []) or []
                        core_name = getattr(full, "name", "")
                    except Exception:
                        inbounds = []
                out.append({
                    "node": model_to_dict(n),
                    "core": {"id": core_id, "name": core_name},
                    "inbounds": inbounds,
                })
            return {"nodes": out}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


@router.delete("")
async def delete_inbound(
    panel_id: int = Query(...),
    core_id: int = Query(...),
    tag: str = Query(...),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    from pasarguard import CoreCreate, CoreType

    p = _get_panel(db, panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            full = await api.get_core_config(core_id=core_id, token=token)
            merged = remove_inbound_from_config(full.config or {}, tag)
            updated, recovered = await modify_core_resilient(
                api, token, core_id,
                CoreCreate(
                    name=full.name, config=merged,
                    type=getattr(full, "type", CoreType.XRAY),
                    exclude_inbound_tags=getattr(full, "exclude_inbound_tags", None) or [],
                    fallbacks_inbound_tags=getattr(full, "fallbacks_inbound_tags", None) or [],
                ),
                verify=lambda cfg: all(i.get("tag") != tag for i in (cfg.get("inbounds") or [])),
                restart_nodes=True,
            )
            return {
                "ok": True, "core": model_to_dict(updated),
                "recovered": recovered,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


def _ensure_node_or_install(target, notes: list[str]) -> None:
    """Step 1/3 equivalent: ensure pg-node is up, installing if missing."""
    with SshSession(target) as ssh:
        up, output = check_pg_node_status(ssh)
        notes.append(output.strip() or "(no output)")
        if up:
            notes.append("pg-node is installed and running.")
            return
    notes.append("pg-node missing/not running — installing via official script...")
    with SshSession(target) as ssh:
        ok, tail, _ = install_pg_node(ssh)
        notes.append(tail)
        if not ok:
            raise RuntimeError(f"pg-node installation failed:\n{tail}")
    with SshSession(target) as ssh:
        up2, output2 = check_pg_node_status(ssh)
        notes.append(output2.strip() or "(no output)")
        if not up2:
            raise RuntimeError("pg-node installed but not running. Check `pg-node logs` on the server.")


@router.post("/reality")
async def create_reality(body: RealityIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    p = _get_panel(db, body.panel_id)
    if body.network not in ("xhttp", "tcp"):
        raise HTTPException(status_code=400, detail="network must be 'xhttp' or 'tcp'.")
    node_name = (body.node_name or "").strip() or s.host
    tag = (body.tag or "").strip() or f"Reality {node_name}"
    core_name = (body.core_name or "").strip() or f"{node_name}-reality"
    target = target_from_server(s)
    notes: list[str] = []

    def _ssh_phase():
        with SshSession(target) as ssh:
            creds = get_node_credentials(ssh)
            reality_priv, reality_pub, reality_src = keys_lib.get_reality_keys(ssh)
            decryption, encryption, enc_src = keys_lib.get_vless_encryption(ssh)
            short_ids = generate_short_ids()
        return {
            "creds": creds,
            "reality_priv": reality_priv, "reality_pub": reality_pub, "reality_src": reality_src,
            "decryption": decryption, "encryption": encryption, "enc_src": enc_src,
            "short_ids": short_ids,
        }

    try:
        await asyncio.to_thread(_ensure_node_or_install, target, notes)
        phase = await asyncio.to_thread(_ssh_phase)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")

    sni_target, server_name = parse_sni(body.sni)
    try:
        inbound = build_reality_inbound(
            tag=tag, port=body.port, target=sni_target, server_name=server_name,
            private_key=phase["reality_priv"], public_key=phase["reality_pub"],
            short_ids=phase["short_ids"], decryption=phase["decryption"],
            encryption=phase["encryption"], network=body.network,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            node, core_id, pnotes = await ensure_node_with_core(
                api, token, node_address=s.host, node_name=node_name,
                core_name=core_name, inbound=inbound, creds=phase["creds"],
            )
            notes.extend(pnotes)
            notes.append("No subscription host created (disabled) — add one in the panel dashboard if needed.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")

    return {
        "ok": True, "tag": tag, "port": body.port, "network": body.network,
        "server": f"{s.host}:{body.port}", "sni": sni_target,
        "public_key": phase["reality_pub"], "short_ids": phase["short_ids"],
        "encryption": phase["encryption"],
        "key_sources": {"reality": phase["reality_src"], "vless": phase["enc_src"]},
        "node": model_to_dict(node), "core_id": core_id,
        "notes": notes,
    }


def _looks_like_ip(host: str) -> bool:
    h = host.strip()
    return bool(re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", h) or ":" in h)


@router.post("/cloudflare")
async def create_cloudflare(body: CloudflareInboundIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    p = _get_panel(db, body.panel_id)
    cf_acc = _get_cf(db, body.cf_account_id)
    domain = (body.domain or "").strip().rstrip(".")
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="A valid FQDN domain is required.")
    if int(body.port) not in CF_PORTS:
        raise HTTPException(status_code=400, detail=f"Port {body.port} is not proxied by Cloudflare. Use one of {list(CF_PORTS)}.")
    if body.network not in ("xhttp", "ws"):
        raise HTTPException(status_code=400, detail="network must be 'xhttp' or 'ws'.")
    server_ip = (body.server_ip or "").strip() or s.host
    if not _looks_like_ip(server_ip):
        raise HTTPException(
            status_code=400,
            detail=f"Server IP '{server_ip}' is not an IP; pass server_ip explicitly for the A record.",
        )
    node_name = s.host
    tag = f"Cloudflare {node_name}"
    core_name = f"{node_name}-cloudflare"
    target = target_from_server(s)
    notes: list[str] = []

    # Step 1: A record DNS-only first.
    try:
        cf = CloudflareAPI(cf_acc.token)
        zone = await cf.find_zone(domain)
        zone_id, zone_name = str(zone["id"]), str(zone.get("name"))
        notes.append(f"Zone: {zone_name} (id={zone_id})")
        res = await cf.upsert_a_record(zone_id, domain, server_ip, proxied=False)
        rec = res["record"]
        notes.append(
            f"A record {'created' if res['created'] else 'updated'}: "
            f"{rec.get('name')} -> {rec.get('content')} (proxied={rec.get('proxied')})"
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")

    # Step 2: SSH — node + certbot cert + creds + keys.
    def _ssh_phase():
        out_notes: list[str] = []
        with SshSession(target) as ssh:
            cb = check_certbot(ssh)
            if cb is None:
                install_certbot(ssh)
                cb = check_certbot(ssh)
                if cb is None:
                    raise RuntimeError("certbot still not available after install.")
            out_notes.append(f"certbot: {cb}")
            busy = port80_busy(ssh)
            if busy:
                out_notes.append(f"WARNING: something listens on port 80:\n{busy}")
            if cert_still_valid(ssh, domain):
                out_notes.append("Valid certificate already on server — reusing, no certbot run.")
            else:
                last_err: Exception | None = None
                for attempt in (1, 2, 3):
                    try:
                        obtain_cert(ssh, domain, email=body.email)
                        last_err = None
                        break
                    except RuntimeError as exc:
                        last_err = exc
                        if attempt < 3:
                            time.sleep(20)
                if last_err is not None:
                    raise last_err
            cert_lines, key_lines = get_domain_cert_lines(ssh, domain)
            creds = get_node_credentials(ssh)
            decryption, encryption, enc_src = keys_lib.get_vless_encryption(ssh)
        return {
            "notes": out_notes, "cert_lines": cert_lines, "key_lines": key_lines,
            "creds": creds, "decryption": decryption, "encryption": encryption, "enc_src": enc_src,
        }

    try:
        await asyncio.to_thread(_ensure_node_or_install, target, notes)
        phase = await asyncio.to_thread(_ssh_phase)
        notes.extend(phase["notes"])
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")

    try:
        inbound = build_cloudflare_tls_inbound(
            tag=tag, port=int(body.port), domain=domain,
            cert_lines=phase["cert_lines"], key_lines=phase["key_lines"],
            decryption=phase["decryption"], encryption=phase["encryption"],
            network=body.network,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Step 3: cert in hand — flip proxy ON.
    try:
        updated = await cf.set_proxied(zone_id, str(rec["id"]), rec, True)
        notes.append(f"A record {updated.get('name')} proxied={updated.get('proxied')}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare proxy enable failed: {exc}")

    # Step 4: panel — node/core (no hosts; add them in the dashboard if needed).
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            node, core_id, pnotes = await ensure_node_with_core(
                api, token, node_address=s.host, node_name=node_name,
                core_name=core_name, inbound=inbound, creds=phase["creds"],
            )
            notes.extend(pnotes)
            notes.append("No subscription/ECH hosts created (disabled) — add them in the panel dashboard if needed.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")

    return {
        "ok": True, "tag": tag, "port": int(body.port), "network": body.network,
        "domain": domain, "server": f"{domain}:{body.port}",
        "a_record": {"name": domain, "content": server_ip, "proxied": True},
        "encryption": phase["encryption"],
        "node": model_to_dict(node), "core_id": core_id, "notes": notes,
    }


# ------------------------------------------------------------------
# Panel-only quick paths (no server, no SSH): operate on an EXISTING
# node directly through the panel API. Keys are generated locally,
# TLS certs are pasted by the user. Reusing an existing tag replaces
# that inbound (merge is by tag).
# ------------------------------------------------------------------

async def _find_node_by_id(api, token: str, node_id: int):
    nodes = await api.get_nodes(token=token)
    items = getattr(nodes, "nodes", nodes) or []
    for n in items:
        try:
            if int(getattr(n, "id", -1)) == int(node_id):
                return n
        except (TypeError, ValueError):
            continue
    raise RuntimeError(f"No node with id '{node_id}' in this panel.")


async def _merge_into_node_core(api, token: str, node, inbound: dict):
    """Merge inbound into the node's core. Returns (core_id, notes)."""
    from pasarguard import CoreCreate, CoreType

    core_id = getattr(node, "core_config_id", None)
    if core_id is None:
        raise RuntimeError(f"Node '{getattr(node, 'name', '?')}' has no core config attached.")
    full = await api.get_core_config(core_id=core_id, token=token)
    current = (full.config or {}).get("inbounds", []) or []
    notes: list[str] = []
    for ib in current:
        if ib.get("port") == inbound.get("port") and ib.get("tag") != inbound.get("tag"):
            notes.append(
                f"WARNING: port {inbound.get('port')} is already used by "
                f"inbound {ib.get('tag')!r} — the panel may reject a duplicate port."
            )
    merged = merge_inbound_into_config(full.config or {}, inbound)
    tag = inbound.get("tag")
    _, recovered = await modify_core_resilient(
        api, token, core_id,
        CoreCreate(
            name=full.name, config=merged,
            type=getattr(full, "type", CoreType.XRAY),
            exclude_inbound_tags=getattr(full, "exclude_inbound_tags", None) or [],
            fallbacks_inbound_tags=getattr(full, "fallbacks_inbound_tags", None) or [],
        ),
        verify=lambda cfg: any(i.get("tag") == tag for i in (cfg.get("inbounds") or [])),
        restart_nodes=True,
    )
    if recovered:
        notes.append(
            f"Panel dropped the connection during restart, but inbound '{tag}' "
            f"was verified present in core id={core_id}."
        )
    return core_id, notes


def _pem_to_lines(pem: str, label: str) -> list[str]:
    lines = (pem or "").replace("\r\n", "\n").strip().split("\n")
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not any("BEGIN" in ln for ln in lines):
        raise ValueError(f"{label} does not look like a PEM block.")
    return lines


@router.post("/reality/quick")
async def quick_reality(body: RealityQuickIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _get_panel(db, body.panel_id)
    if body.network not in ("xhttp", "tcp"):
        raise HTTPException(status_code=400, detail="network must be 'xhttp' or 'tcp'.")
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Local-only key generation (no SSH).
    priv, pub = keys_lib.generate_x25519_pair()
    decryption, encryption = keys_lib.build_vless_encryption_local()
    short_ids = generate_short_ids()
    sni_target, server_name = parse_sni(body.sni)
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            node = await _find_node_by_id(api, token, body.node_id)
            tag = (body.tag or "").strip() or f"Reality {getattr(node, 'name', body.node_id)}"
            inbound = build_reality_inbound(
                tag=tag, port=body.port, target=sni_target, server_name=server_name,
                private_key=priv, public_key=pub, short_ids=short_ids,
                decryption=decryption, encryption=encryption, network=body.network,
            )
            core_id, notes = await _merge_into_node_core(api, token, node, inbound)
            notes.append("No subscription host created (disabled).")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")
    return {
        "ok": True, "tag": tag, "port": body.port, "network": body.network,
        "sni": sni_target, "public_key": pub, "short_ids": short_ids,
        "encryption": encryption, "node": model_to_dict(node),
        "core_id": core_id, "notes": notes,
    }


def _read_cert_from_node_server_host(address: str, cert_domain: str) -> tuple[list[str], list[str], str]:
    """Read a live cert from the saved server matching a node address.

    Read-only: issuing belongs to the Certificates tab. Opens its own DB
    session (safe to run in a worker thread).
    """
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        servers = db.query(Server).all()
    finally:
        db.close()
    match = next(
        (s for s in servers if (s.host or "").strip().lower() == (address or "").strip().lower()),
        None,
    )
    if match is None:
        known = ", ".join(f"{s.name} ({s.host})" for s in servers) or "(no saved servers)"
        raise RuntimeError(
            f"No saved server matches node address '{address}'. Saved: {known}. "
            "Save it first (Kharej > Nodes uses the same servers list)."
        )
    target = target_from_server(match)
    with SshSession(target) as ssh:
        try:
            cert_lines, key_lines = get_domain_cert_lines(ssh, cert_domain)
        except RuntimeError:
            raise RuntimeError(
                f"No certificate for '{cert_domain}' on {match.name} ({match.host}). "
                "Issue it first from Cloudflare > Certificates."
            )
    return cert_lines, key_lines, f"{cert_domain} on {match.name}"


@router.post("/cloudflare/quick")
async def quick_cloudflare(body: CloudflareQuickIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _get_panel(db, body.panel_id)
    domain = (body.domain or "").strip().rstrip(".")
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="A valid FQDN domain is required.")
    if int(body.port) not in CF_PORTS:
        raise HTTPException(status_code=400, detail=f"Port {body.port} is not proxied by Cloudflare. Use one of {list(CF_PORTS)}.")
    if body.network not in ("xhttp", "ws"):
        raise HTTPException(status_code=400, detail="network must be 'xhttp' or 'ws'.")
    decryption, encryption = keys_lib.build_vless_encryption_local()
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if body.cert_pem.strip() and body.key_pem.strip():
        try:
            cert_lines = _pem_to_lines(body.cert_pem, "cert_pem")
            key_lines = _pem_to_lines(body.key_pem, "key_pem")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        cert_source = "pasted PEM"
        pending_cert_domain: str | None = None
    elif (body.cert_domain or "").strip():
        cert_lines, key_lines = [], []
        cert_source = ""
        pending_cert_domain = body.cert_domain.strip().rstrip(".")
    else:
        raise HTTPException(
            status_code=400,
            detail="Select a certificate (cert_domain) or paste cert_pem + key_pem.",
        )
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            node = await _find_node_by_id(api, token, body.node_id)
            if pending_cert_domain:
                address = (getattr(node, "address", "") or "").strip()
                try:
                    cert_lines, key_lines, cert_source = await asyncio.to_thread(
                        _read_cert_from_node_server_host, address, pending_cert_domain
                    )
                except RuntimeError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")
            try:
                inbound = build_cloudflare_tls_inbound(
                    tag="", port=int(body.port), domain=domain,
                    cert_lines=cert_lines, key_lines=key_lines,
                    decryption=decryption, encryption=encryption, network=body.network,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            tag = (body.tag or "").strip() or f"Cloudflare {getattr(node, 'name', body.node_id)}"
            inbound["tag"] = tag
            core_id, notes = await _merge_into_node_core(api, token, node, inbound)
            notes.insert(0, f"Certificate: {cert_source}.")
            notes.append("No subscription/ECH hosts created (disabled).")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")
    return {
        "ok": True, "tag": tag, "port": int(body.port), "network": body.network,
        "domain": domain, "encryption": encryption,
        "node": model_to_dict(node), "core_id": core_id, "notes": notes,
    }


@router.post("/cloudflare/cert")
async def cloudflare_cert(body: CloudflareCertIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Read a domain's cert/key from a server over SSH (certbot).

    Reuses a still-valid cert when present; issues a new one via
    `certbot --standalone` only when issue=true (needs port 80 + DNS).
    """
    s = _get_server(db, body.server_id)
    domain = (body.domain or "").strip().rstrip(".")
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="A valid FQDN domain is required.")
    try:
        return await asyncio.to_thread(_fetch_cert_from_server, s, domain, body.issue, body.email)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


def _fetch_cert_from_server(s: Server, domain: str, issue: bool, email: str) -> dict:
    """SSH into a saved server and return its cert/key for domain."""
    target = target_from_server(s)
    notes: list[str] = []
    with SshSession(target) as ssh:
        cb = check_certbot(ssh)
        if cb is None:
            notes.append("certbot not found — installing via apt...")
            install_certbot(ssh)
            cb = check_certbot(ssh)
            if cb is None:
                raise RuntimeError("certbot still not available after install.")
        notes.append(f"certbot: {cb}")
        if cert_still_valid(ssh, domain):
            notes.append(f"Valid certificate already on {s.host} — reusing it.")
        elif issue:
            busy = port80_busy(ssh)
            if busy:
                notes.append(f"WARNING: something listens on port 80:\n{busy}")
            obtain_cert(ssh, domain, email=email)
            notes.append("Issued new certificate via certbot.")
        else:
            raise RuntimeError(
                f"No valid certificate for {domain} on {s.host}. "
                "Enable 'issue' to create one (needs port 80 free and DNS pointing here)."
            )
        cert_lines, key_lines = get_domain_cert_lines(ssh, domain)
    return {
        "cert_pem": "\n".join(cert_lines),
        "key_pem": "\n".join(key_lines),
        "server_id": s.id,
        "server_name": s.name,
        "notes": notes,
    }


@router.post("/cloudflare/cert-auto")
async def cloudflare_cert_auto(body: CloudflareCertAutoIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Same as /cert, but the server is resolved automatically: the cert must
    live on the machine the domain points to, i.e. the node's address. Finds
    the saved server whose host matches the node's address."""
    p = _get_panel(db, body.panel_id)
    domain = (body.domain or "").strip().rstrip(".")
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="A valid FQDN domain is required.")
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            node = await _find_node_by_id(api, token, body.node_id)
            address = (getattr(node, "address", "") or "").strip()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")
    if not address:
        raise HTTPException(status_code=400, detail="Node has no address; pick the server manually.")
    match = None
    for s in db.query(Server).all():
        if (s.host or "").strip().lower() == address.lower():
            match = s
            break
    if match is None:
        known = ", ".join(f"{s.name} ({s.host})" for s in db.query(Server).all()) or "(no saved servers)"
        raise HTTPException(
            status_code=400,
            detail=f"No saved server matches node address '{address}'. Saved: {known}. "
            "Save it under Servers or pick one manually.",
        )
    try:
        return await asyncio.to_thread(_fetch_cert_from_server, match, domain, body.issue, body.email)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")
