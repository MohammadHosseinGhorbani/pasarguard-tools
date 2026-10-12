"""pg-node lifecycle: status / install / credentials / register / actions."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..certs_lib import (
    cert_still_valid,
    check_certbot,
    get_domain_cert_lines,
    install_certbot,
    obtain_cert,
    port80_busy,
)
from ..database import get_db
from ..deps import get_current_user
from ..models import Panel, Server
from ..node_lib import check_pg_node_status, get_node_credentials, install_pg_node
from ..panel_sdk import (
    create_panel_node,
    find_core_by_name,
    find_node_by_address,
    http_error_detail,
    make_api,
    require_panel_key,
)
from ..schemas import (
    NodeActionIn,
    NodeCertIssueIn,
    NodeCertsIn,
    NodeCredentialsIn,
    NodeInstallIn,
    NodeRegisterIn,
    NodeStatusIn,
    model_to_dict,
)
from ..ssh_service import SshSession, target_from_server

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

NODE_ACTIONS = {
    "up": ("pg-node up", 120.0),
    "down": ("pg-node down", 120.0),
    "restart": ("pg-node restart", 180.0),
    # Newer pg-node uses -n/--no-follow; older ones --tail. `timeout` guards
    # against follow mode hanging when neither flag exists.
    "logs": ("timeout 50 pg-node logs -n 2>&1 || timeout 50 pg-node logs --tail 200 2>&1", 60.0),
    "core-update": ("pg-node core-update", 600.0),
    "renew-cert": ("pg-node renew-cert", 300.0),
}


def _get_server(db: Session, server_id: int) -> Server:
    s = db.query(Server).filter(Server.id == server_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return s


def _get_panel(db: Session, panel_id: int) -> Panel:
    p = db.query(Panel).filter(Panel.id == panel_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return p


@router.post("/status")
async def node_status(body: NodeStatusIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            up, output = check_pg_node_status(ssh)
            return {"up": up, "output": (output or "").strip()}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


@router.post("/install")
async def node_install(body: NodeInstallIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            up, output = check_pg_node_status(ssh)
            if up:
                return {"already_installed": True, "up": True, "output": output.strip()}
            ok, tail, info = install_pg_node(
                ssh, service_port=body.service_port, api_port=body.api_port
            )
            if not ok:
                raise RuntimeError(f"pg-node install script failed:\n{tail}")
            up2, output2 = check_pg_node_status(ssh)
            return {
                "already_installed": False, "ok": ok, "up": up2,
                "output": tail, "status_output": output2.strip(), "ports": info,
            }

    try:
        return await asyncio.to_thread(_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


@router.post("/credentials")
async def node_credentials(
    body: NodeCredentialsIn, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    s = _get_server(db, body.server_id)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            creds = get_node_credentials(ssh)
            return {
                "service_port": creds["service_port"],
                "api_port": creds["api_port"],
                "protocol": creds["protocol"],
                "api_key_masked": "****",
                "has_server_ca": True,
            }

    try:
        return await asyncio.to_thread(_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


@router.post("/register")
async def node_register(body: NodeRegisterIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    p = _get_panel(db, body.panel_id)
    target = target_from_server(s)
    node_name = (body.node_name or "").strip() or s.host

    def _read_creds():
        with SshSession(target) as ssh:
            return get_node_credentials(ssh)

    try:
        creds = await asyncio.to_thread(_read_creds)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")

    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            existing = await find_node_by_address(api, token, s.host)
            if existing is not None:
                return {
                    "existing": True, "node": model_to_dict(existing),
                    "core_id": getattr(existing, "core_config_id", None),
                }
            # Idempotent core: a previous attempt may have created it already
            # (a duplicate name comes back as a bare 400).
            reuse = await find_core_by_name(api, token, node_name)
            if reuse is not None:
                core = reuse
                reused_core = True
            else:
                reused_core = False
            # Registering a node standalone needs a core; create one named like
            # the node, dashboard-shaped (the panel validates the whole xray
            # config, not just inbounds, and rejects zero inbounds outright —
            # hence the mimic Shadowsocks, exactly what the dashboard seeds).
            # Tag lists go as [] (never absent/null).
            from pasarguard import CoreCreate, CoreType

            from ..inbound_lib import build_dashboard_config, build_mimic_inbound

            if not reused_core:
                try:
                    core = await api.create_core_config(
                        CoreCreate(
                            name=node_name,
                            config=build_dashboard_config([build_mimic_inbound()]),
                            type=CoreType.XRAY,
                            exclude_inbound_tags=[],
                            fallbacks_inbound_tags=[],
                        ),
                        token=token,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Core creation failed: {http_error_detail(exc)}",
                    )
            node = await create_panel_node(
                api, token, name=node_name, address=s.host,
                service_port=int(creds["service_port"]),
                api_port=int(creds.get("api_port") or 62051),
                connection_type=creds["protocol"],
                server_ca=creds["server_ca"], api_key=creds["api_key"],
                core_config_id=core.id,
            )
            return {
                "existing": False, "node": model_to_dict(node), "core": model_to_dict(core),
                "reused_core": reused_core,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


@router.post("/action")
async def node_action(body: NodeActionIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    cmd, timeout = NODE_ACTIONS[body.action]
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            rc, out, err = ssh.run(cmd, timeout=timeout)
            combined = (out or "") + ("\n" + err if err else "")
            return {"action": body.action, "rc": rc, "output": combined.strip()}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


def _list_live_certs(ssh) -> list[dict]:
    """List /etc/letsencrypt/live/* with expiry + subject. Certbot-independent read."""
    import re

    rc, out, _ = ssh.run_as_root("ls -1 /etc/letsencrypt/live 2>/dev/null", timeout=30.0)
    if rc != 0 or not (out or "").strip():
        return []
    certs: list[dict] = []
    for domain in (out or "").split():
        domain = domain.strip().rstrip(".")
        if not domain or domain.startswith("."):
            continue
        cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        rc2, dates, _ = ssh.run_as_root(
            f"openssl x509 -in {cert_path} -noout -subject -dates 2>/dev/null", timeout=30.0
        )
        info: dict = {"domain": domain}
        if rc2 == 0:
            m = re.search(r"notAfter=(.+)", dates or "")
            info["valid_until"] = (m.group(1).strip() if m else "")
            m2 = re.search(r"notBefore=(.+)", dates or "")
            info["valid_from"] = (m2.group(1).strip() if m2 else "")
            m3 = re.search(r"subject=(.+)", dates or "")
            info["subject"] = (m3.group(1).strip() if m3 else "")
            rc3, check, _ = ssh.run_as_root(
                f"openssl x509 -in {cert_path} -noout -checkend 0 2>&1", timeout=30.0
            )
            info["valid"] = rc3 == 0
        else:
            info["valid"] = False
        certs.append(info)
    return certs


@router.post("/certificates")
async def node_certificates(
    body: NodeCertsIn, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    s = _get_server(db, body.server_id)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            cb = check_certbot(ssh)
            return {"certbot": cb or "not installed", "certs": _list_live_certs(ssh)}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


@router.post("/cert-issue")
async def node_cert_issue(
    body: NodeCertIssueIn, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    s = _get_server(db, body.server_id)
    domain = (body.domain or "").strip().rstrip(".")
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="A valid FQDN domain is required.")
    target = target_from_server(s)

    def _run():
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
            busy = port80_busy(ssh)
            if busy:
                notes.append(f"WARNING: something listens on port 80:\n{busy}")
            obtain_cert(ssh, domain, email=body.email)
            notes.append(f"Issued certificate for {domain}.")
            cert_lines, key_lines = get_domain_cert_lines(ssh, domain)
        return {
            "domain": domain, "cert_lines": len(cert_lines),
            "key_lines": len(key_lines), "notes": notes,
        }

    try:
        return await asyncio.to_thread(_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")
