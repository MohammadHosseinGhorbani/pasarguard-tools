"""Server CRUD + SSH test/exec."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Server
from ..schemas import BpSettingsIn, ExecIn, ServerIn, ServerOut
from ..ssh_service import SshSession, target_from_server

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _to_out(s: Server) -> dict:
    return {
        "id": s.id, "name": s.name, "host": s.host, "username": s.username, "port": s.port,
        "password": "", "key_path": s.key_path or "", "key_content": "",
        "notes": s.notes or "", "role": (s.role or "kharej"),
        "bp_url": s.bp_url or "", "bp_token": "", "bp_verify": bool(s.bp_verify),
        "bp_node": s.bp_node or "",
        "has_password": bool(s.password), "has_key": bool(s.key_path or s.key_content),
        "has_bp_token": bool(s.bp_token),
    }


def _role_or_400(role: str | None) -> str | None:
    if role is None or role == "":
        return None
    if role not in ("iran", "kharej"):
        raise HTTPException(status_code=400, detail="role must be iran or kharej")
    return role


def _normalize_bp_url(raw: str) -> str:
    """Normalize a BackPack panel URL to scheme://host:port[/path-prefix].

    The panel answers ONLY under an unguessable path prefix (see BackPack
    internal/webui/server.go: withBasePath 404s anything not under the
    prefix), so the path must be preserved — copy the full URL from the
    backpack menu's Web Panel screen, prefix included."""
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Panel URL is not a valid URL.")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=400, detail="Panel URL must be like https://host:port[/prefix]")
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    path = (parts.path or "").rstrip("/")
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def _get(db: Session, server_id: int) -> Server:
    s = db.query(Server).filter(Server.id == server_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return s


@router.get("", response_model=list[ServerOut])
def list_servers(
    role: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = db.query(Server)
    r = _role_or_400(role)
    if r is not None:
        q = q.filter(Server.role == r)
    return [_to_out(s) for s in q.order_by(Server.name).all()]


VALID_ROLES = ("iran", "kharej")


@router.post("", response_model=ServerOut)
def create_server(body: ServerIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    if db.query(Server).filter(Server.name == body.name).first():
        raise HTTPException(status_code=400, detail="Server name already exists")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="role must be iran or kharej")
    s = Server(
        name=body.name.strip(), host=body.host.strip(), username=body.username or "root",
        port=body.port or 22, password=body.password or "", key_path=body.key_path or "",
        key_content=body.key_content or "", notes=body.notes or "", role=body.role,
        bp_url=_normalize_bp_url(body.bp_url), bp_token=body.bp_token.strip(),
        bp_verify=body.bp_verify, bp_node=body.bp_node.strip(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_out(s)


@router.get("/{server_id}", response_model=ServerOut)
def get_server(server_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _to_out(_get(db, server_id))


@router.put("/{server_id}", response_model=ServerOut)
def update_server(server_id: int, body: ServerIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get(db, server_id)
    if body.name.strip() != s.name and db.query(Server).filter(Server.name == body.name.strip()).first():
        raise HTTPException(status_code=400, detail="Server name already exists")
    s.name = body.name.strip()
    s.host = body.host.strip()
    s.username = body.username or "root"
    s.port = body.port or 22
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="role must be iran or kharej")
    s.role = body.role
    # Empty password/key_content in an update means "keep existing" only when
    # the frontend sends a sentinel? No — keep it simple: empty clears.
    # Frontend always sends back existing state via has_* flags, so explicit clear is intended.
    s.password = body.password or ""
    s.key_path = body.key_path or ""
    if body.key_content:
        s.key_content = body.key_content
    elif not body.key_path and not s.key_path:
        s.key_content = ""
    s.notes = body.notes or ""
    s.bp_url = body.bp_url.strip()
    if body.bp_token.strip():
        s.bp_token = body.bp_token.strip()
    s.bp_verify = body.bp_verify
    s.bp_node = body.bp_node.strip()
    db.commit()
    db.refresh(s)
    return _to_out(s)


@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get(db, server_id)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.patch("/{server_id}/bp", response_model=ServerOut)
def update_bp_settings(
    server_id: int, body: BpSettingsIn, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """Update only BackPack panel settings (never touches SSH secrets)."""
    s = _get(db, server_id)
    s.bp_url = _normalize_bp_url(body.bp_url)
    if body.bp_token.strip():
        s.bp_token = body.bp_token.strip()
    s.bp_verify = body.bp_verify
    s.bp_node = body.bp_node.strip()
    db.commit()
    db.refresh(s)
    return _to_out(s)


def _ssh_test(target) -> dict:
    started = time.monotonic()
    outputs: dict[str, str] = {}
    with SshSession(target) as ssh:
        for label, cmd in (
            ("basic", "echo ok; whoami; hostname; cat /etc/os-release | head -3"),
            ("pg_node", "pg-node status 2>&1 || /usr/local/bin/pg-node status 2>&1 || echo 'pg-node: not installed'"),
            ("panel", "pasarguard status 2>&1 || echo 'pasarguard: not installed'"),
        ):
            try:
                rc, out, err = ssh.run(cmd, timeout=30.0)
                outputs[label] = ((out or "") + ("\n" + err if err else "")).strip()
            except Exception as exc:
                outputs[label] = f"error: {exc}"
    return {"ok": True, "latency_ms": int((time.monotonic() - started) * 1000), "outputs": outputs}


@router.post("/{server_id}/test")
async def test_server(server_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get(db, server_id)
    target = target_from_server(s)
    try:
        return await asyncio.to_thread(_ssh_test, target)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/{server_id}/exec")
async def exec_command(server_id: int, body: ExecIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get(db, server_id)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            rc, out, err = ssh.run(body.command, timeout=body.timeout, use_sudo=body.use_sudo)
            return {"rc": rc, "out": out, "err": err}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH exec failed: {exc}")
