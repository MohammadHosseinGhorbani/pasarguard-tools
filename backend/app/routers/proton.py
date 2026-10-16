"""Proton VPN: account settings (headless-browser login) + WireGuard config
create/delete/rotate.

Configs are stored locally (``models.ProtonConfig``) because Proton only
returns the client private key once, at creation — that is also what powers
"Pick from Proton VPN" in the WireGuard outbounds editor.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import ProtonAccount, ProtonConfig
from ..proton_browser import selenium_login
from ..proton_lib import (
    DEFAULT_APP_VERSION,
    KEY_THROTTLE_SECONDS,
    PROTON_AUTH_BASE,
    STALE_APP_VERSIONS,
    ProtonClient,
    ProtonError,
    ProtonSession,
    ed25519_priv_body_to_x25519,
    infer_country_code,
    pick_best_server,
    refresh_access_token,
    render_conf,
    resolve_country_list,
    sanitize_config_name,
    suggest_outbound_tag,
)
from ..schemas import ProtonConfigIn, ProtonLoginIn, ProtonSettingsIn

router = APIRouter(prefix="/api/proton", tags=["proton"])


# --------------------------------------------------------------------------
# account / session plumbing
# --------------------------------------------------------------------------


def _account(db: Session) -> ProtonAccount:
    a = db.query(ProtonAccount).order_by(ProtonAccount.id).first()
    if a is None:
        a = ProtonAccount()
        db.add(a)
        db.commit()
        db.refresh(a)
    return a


def _session_from_account(a: ProtonAccount) -> ProtonSession:
    app_version = a.app_version or DEFAULT_APP_VERSION
    if app_version in STALE_APP_VERSIONS:
        app_version = DEFAULT_APP_VERSION
    return ProtonSession(
        uid=a.uid or "",
        access_token=a.access_token or "",
        refresh_token=a.refresh_token or "",
        session_id=a.session_id or "",
        app_version=app_version,
        obtained_at=float(a.obtained_at or 0.0),
        expires_in=int(a.expires_in or 0),
        username=a.username or "",
        auth_base=PROTON_AUTH_BASE,
    )


def _persist_session(a: ProtonAccount, sess: ProtonSession) -> None:
    a.uid = sess.uid
    a.access_token = sess.access_token
    a.refresh_token = sess.refresh_token
    a.session_id = sess.session_id
    a.app_version = sess.app_version
    a.obtained_at = sess.obtained_at
    a.expires_in = sess.expires_in


def _client_for(db: Session, a: ProtonAccount) -> ProtonClient:
    """A ProtonClient with a fresh access token (refresh when <7 days left)."""
    sess = _session_from_account(a)
    if not (sess.access_token or sess.refresh_token):
        raise ProtonError(
            "Not logged in to Proton. Set your credentials in Settings → Proton VPN and press Login."
        )
    if sess.has_refresh and sess.needs_refresh():
        try:
            refresh_access_token(sess, proxy=a.proxy or None)
            _persist_session(a, sess)
            db.commit()
        except ProtonError:
            pass  # fall through; the API call will surface a clear 401 message
    return ProtonClient(sess, proxy=a.proxy or None, timeout=30.0)


def _proton_http_error(exc: ProtonError, default_status: int = 502) -> HTTPException:
    """Map a ProtonError to a 400 for client mistakes, 502 for upstream failures."""
    msg = str(exc)
    if msg.startswith("Could not reach"):
        return HTTPException(status_code=502, detail=msg)
    client_error = msg.startswith("Not logged in") or msg.startswith("No servers match")
    return HTTPException(status_code=400 if client_error else default_status, detail=msg)


def _status(a: ProtonAccount) -> dict:
    sess = _session_from_account(a)
    left = sess.seconds_left() if sess.expires_at > 0 else -1.0
    return {
        "username": a.username or "",
        "has_password": bool(a.password),
        "app_version": sess.app_version,
        "proxy": a.proxy or "",
        "logged_in": bool(sess.access_token or sess.refresh_token),
        "mode": "refresh" if sess.refresh_token else ("cookie" if sess.access_token else "none"),
        "uid": a.uid or "",
        "expires_at": int(sess.expires_at) if sess.expires_at > 0 else 0,
        "seconds_left": int(left),
        "days_left": round(left / 86400, 1) if left >= 0 else None,
    }


def _config_out(c: ProtonConfig) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "country": c.country or "",
        "server_name": c.server_name or "",
        "entry_ip": c.entry_ip or "",
        "port": int(c.port or 51820),
        "address": c.address or "10.2.0.2/32",
        "dns": c.dns or "10.2.0.1",
        "serial": c.serial or "",
        "expires_at": int(c.expires_at or 0),
        "created_at": float(c.created_at or 0.0),
        "outbound_tag": suggest_outbound_tag(c.name),
        "conf": render_conf(
            client_priv_x25519=c.private_key,
            server_pubkey=c.server_pubkey,
            entry_ip=c.entry_ip,
            server_name=c.server_name,
            key_label=c.name,
            port=int(c.port or 51820),
            address=c.address or "10.2.0.2/32",
            dns=c.dns or "10.2.0.1",
        ),
    }


# --------------------------------------------------------------------------
# settings / login
# --------------------------------------------------------------------------


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _status(_account(db))


@router.put("/settings")
def save_settings(body: ProtonSettingsIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    if body.username.strip():
        a.username = body.username.strip()
    if body.password:
        a.password = body.password
    if body.app_version.strip():
        a.app_version = body.app_version.strip()
    a.proxy = body.proxy.strip()
    db.commit()
    db.refresh(a)
    return _status(a)


@router.post("/login")
async def login(body: ProtonLoginIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    username = (body.username or "").strip() or (a.username or "")
    password = body.password or (a.password or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Enter your Proton username and password first.")
    if body.username.strip():
        a.username = username
    if body.password:
        a.password = password
    db.commit()
    try:
        sess = await asyncio.to_thread(
            selenium_login,
            username,
            password,
            app_version=a.app_version or DEFAULT_APP_VERSION,
            proxy=a.proxy or None,
            totp=body.totp or None,
        )
    except ProtonError as exc:
        raise _proton_http_error(exc, default_status=400)
    _persist_session(a, sess)
    db.commit()
    db.refresh(a)
    return _status(a)


@router.post("/logout")
def logout(db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    a.uid = ""
    a.access_token = ""
    a.refresh_token = ""
    a.session_id = ""
    a.obtained_at = 0.0
    a.expires_in = 0
    db.commit()
    db.refresh(a)
    return _status(a)


@router.delete("/account")
def clear_account(db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    db.delete(a)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# servers
# --------------------------------------------------------------------------


@router.get("/servers")
async def list_servers(
    tier: int = 0,
    countries: str = "",
    limit: int = 0,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    a = _account(db)
    try:
        wanted = resolve_country_list(countries)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        with _client_for(db, a) as client:
            logicals = await asyncio.to_thread(client.get_logicals)
    except ProtonError as exc:
        raise _proton_http_error(exc)

    rows: list[dict] = []
    for s in logicals:
        try:
            if int(s.get("Tier", -1)) != int(tier):
                continue
        except (TypeError, ValueError):
            continue
        if wanted and s.get("EntryCountry", "").upper() not in wanted and s.get("ExitCountry", "").upper() not in wanted:
            continue
        peers = s.get("Servers") or []
        rows.append({
            "name": str(s.get("Name") or ""),
            "exit_country": str(s.get("ExitCountry") or ""),
            "entry_country": str(s.get("EntryCountry") or ""),
            "load": s.get("Load"),
            "score": s.get("Score"),
            "status": s.get("Status"),
            "entry_ip": str((peers[0] or {}).get("EntryIP") or "") if peers else "",
        })
    rows.sort(key=lambda s: (float(s.get("score") or 1e9), float(s.get("load") or 100)))
    if limit > 0:
        rows = rows[:limit]
    return {"servers": rows, "tier": tier}


# --------------------------------------------------------------------------
# configs
# --------------------------------------------------------------------------


def _existing(db: Session, name: str) -> ProtonConfig | None:
    return db.query(ProtonConfig).filter(ProtonConfig.name == name).first()


async def _create(db: Session, a: ProtonAccount, *, name: str, country: str, tier: int) -> ProtonConfig:
    """Pick the best server, register a new certificate and store the config."""
    try:
        wanted = resolve_country_list(country)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not wanted:
        inferred = infer_country_code(name)
        if inferred:
            wanted = [inferred]

    try:
        with _client_for(db, a) as client:
            logicals = await asyncio.to_thread(client.get_logicals)
            server = pick_best_server(logicals, tier=tier, countries=wanted)
            peers = server.get("Servers") or [{}]
            peer0 = peers[0] or {}
            entry_ip = str(peer0.get("EntryIP") or "")
            server_pub = str(peer0.get("X25519PublicKey") or "")
            server_name = str(server.get("Name") or "")
            if not entry_ip or not server_pub:
                raise ProtonError(f"Server {server_name or '?'} has no WireGuard endpoint.")
            await asyncio.sleep(KEY_THROTTLE_SECONDS)
            _, pub_body, priv_body = await asyncio.to_thread(client.get_ec_keypair)
            reg = await asyncio.to_thread(
                client.register_config,
                server_name=server_name,
                entry_ip=entry_ip,
                server_pubkey=server_pub,
                client_pubkey=pub_body,
                device_name=name,
            )
    except ProtonError as exc:
        raise _proton_http_error(exc)

    priv_x25519 = ed25519_priv_body_to_x25519(priv_body)
    cfg = ProtonConfig(
        name=name,
        country=(server.get("ExitCountry") or "").upper(),
        server_name=server_name,
        entry_ip=entry_ip,
        port=51820,
        address="10.2.0.2/32",
        dns="10.2.0.1",
        private_key=priv_x25519,
        server_pubkey=server_pub,
        serial=str(reg.get("SerialNumber") or ""),
        expires_at=int(reg.get("ExpirationTime") or 0),
        created_at=time.time(),
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


@router.get("/configs")
def list_configs(db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    cfgs = db.query(ProtonConfig).order_by(ProtonConfig.name).all()
    return {
        "configs": [_config_out(c) for c in cfgs],
        "logged_in": bool(a.access_token or a.refresh_token),
    }


@router.post("/configs")
async def create_config(body: ProtonConfigIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    name = sanitize_config_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="Config name is required.")
    if _existing(db, name):
        raise HTTPException(
            status_code=400, detail=f"Config '{name}' already exists — rotate it or use another name."
        )
    cfg = await _create(db, a, name=name, country=body.country, tier=body.tier)
    out = _config_out(cfg)
    return {"ok": True, "config": out, "conf": out["conf"]}


@router.post("/configs/rotate")
async def rotate_config(body: ProtonConfigIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    name = sanitize_config_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="Config name is required.")

    notes: list[str] = []
    old = _existing(db, name)
    country = body.country
    if old is not None:
        if not country:
            country = old.country or ""
        serial = old.serial or ""
        if serial:
            try:
                with _client_for(db, a) as client:
                    count = await asyncio.to_thread(client.delete_config_by_serial, serial)
                notes.append(f"deleted previous certificate {serial} (count={count})")
            except ProtonError as exc:
                notes.append(f"previous certificate delete failed: {exc}")
        else:
            notes.append("previous config had no serial; removed locally only")
        db.delete(old)
        db.commit()
    else:
        notes.append("no previous config; created a new one")

    cfg = await _create(db, a, name=name, country=country, tier=body.tier)
    out = _config_out(cfg)
    return {"ok": True, "config": out, "conf": out["conf"], "notes": notes}


@router.delete("/configs/{name}")
async def delete_config(name: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _account(db)
    cfg = _existing(db, sanitize_config_name(name))
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found.")
    resolved_name = cfg.name
    notes: list[str] = []
    if cfg.serial:
        try:
            with _client_for(db, a) as client:
                count = await asyncio.to_thread(client.delete_config_by_serial, cfg.serial)
            notes.append(f"deleted remote certificate {cfg.serial} (count={count})")
        except ProtonError as exc:
            notes.append(f"remote certificate delete failed: {exc}")
    else:
        notes.append("no serial stored; removed locally only")
    db.delete(cfg)
    db.commit()
    return {"ok": True, "name": resolved_name, "notes": notes}
