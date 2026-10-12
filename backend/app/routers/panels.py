"""Panel CRUD + remote panel ops (install / status / logs / up / down / restart / update)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Panel, Server
from ..schemas import PanelIn, PanelOpsInstallIn, PanelOpsRunIn, PanelOut
from ..ssh_service import SshSession, target_from_server

router = APIRouter(prefix="/api/panels", tags=["panels"])

PANEL_INSTALL_BASE = (
    'bash -c "$(curl -fsSL https://github.com/PasarGuard/scripts/raw/main/pasarguard.sh)" @ install'
)

OPS_COMMANDS = {
    "status": ("pasarguard status", 60.0),
    "logs": ("pasarguard logs --tail 200 || pasarguard logs", 60.0),
    "up": ("pasarguard up", 300.0),
    "down": ("pasarguard down", 120.0),
    "restart": ("pasarguard restart", 300.0),
    "update": ("pasarguard update", 600.0),
}


def _mask(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return "-"
    if len(key) <= 12:
        return "****"
    return f"{key[:8]}...{key[-4:]}"


def _to_out(p: Panel) -> dict:
    return {
        "id": p.id, "name": p.name, "base_url": p.base_url or "",
        "verify_ssl": p.verify_ssl, "is_active": p.is_active,
        "api_key_masked": _mask(p.api_key),
    }


def _get_panel(db: Session, panel_id: int) -> Panel:
    p = db.query(Panel).filter(Panel.id == panel_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return p


def _get_server(db: Session, server_id: int) -> Server:
    s = db.query(Server).filter(Server.id == server_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return s


@router.get("", response_model=list[PanelOut])
def list_panels(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return [_to_out(p) for p in db.query(Panel).order_by(Panel.name).all()]


@router.post("", response_model=PanelOut)
def create_panel(body: PanelIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    if db.query(Panel).filter(Panel.name == body.name.strip()).first():
        raise HTTPException(status_code=400, detail="Panel name already exists")
    p = Panel(
        name=body.name.strip(), base_url=body.base_url.strip(),
        api_key=body.api_key.strip(), verify_ssl=body.verify_ssl,
        is_active=db.query(Panel).count() == 0,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.get("/{panel_id}", response_model=PanelOut)
def get_panel(panel_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _to_out(_get_panel(db, panel_id))


@router.put("/{panel_id}", response_model=PanelOut)
def update_panel(panel_id: int, body: PanelIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _get_panel(db, panel_id)
    if body.name.strip() != p.name and db.query(Panel).filter(Panel.name == body.name.strip()).first():
        raise HTTPException(status_code=400, detail="Panel name already exists")
    p.name = body.name.strip()
    p.base_url = body.base_url.strip()
    if body.api_key.strip():
        p.api_key = body.api_key.strip()
    p.verify_ssl = body.verify_ssl
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.delete("/{panel_id}")
def delete_panel(panel_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _get_panel(db, panel_id)
    was_active = p.is_active
    db.delete(p)
    db.commit()
    if was_active:
        first = db.query(Panel).order_by(Panel.id).first()
        if first is not None:
            first.is_active = True
            db.commit()
    return {"ok": True}


@router.post("/{panel_id}/activate", response_model=PanelOut)
def activate_panel(panel_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _get_panel(db, panel_id)
    db.query(Panel).update({Panel.is_active: False})
    p.is_active = True
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.post("/ops/run")
async def panel_ops_run(body: PanelOpsRunIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    cmd, timeout = OPS_COMMANDS[body.action]
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            rc, out, err = ssh.run_as_root(cmd, timeout=timeout)
            combined = (out or "") + ("\n" + err if err else "")
            return {"action": body.action, "rc": rc, "output": combined.strip()}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


def build_panel_install_command(database: str, ssl_mode: str, ssl_domain: str) -> str:
    cmd = PANEL_INSTALL_BASE
    if database and database != "sqlite":
        cmd += f" --database {database}"
    if ssl_mode == "no-ssl":
        cmd += " --no-ssl"
    elif ssl_mode == "domain":
        domain = (ssl_domain or "").strip().rstrip(".")
        if not domain or "." not in domain:
            raise HTTPException(status_code=400, detail="ssl_domain is required for ssl_mode=domain")
        cmd += f" --ssl-domain {domain}"
    return cmd


@router.post("/ops/install")
async def panel_ops_install(body: PanelOpsInstallIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    cmd = build_panel_install_command(body.database, body.ssl_mode, body.ssl_domain)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            rc, out, err = ssh.run_as_root(cmd, timeout=900.0)
            combined = (out or "") + ("\n" + err if err else "")
            return {"rc": rc, "command": f"sudo {cmd}", "output": combined.strip()}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")
