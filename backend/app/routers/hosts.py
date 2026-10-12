"""Subscription host CRUD per inbound (panel API, no SSH).

Plain properties: remark, address, ech_config. The ECH block is sent nested
exactly as the panel dashboard does (the SDK flattens it and the panel
ignores it), so all calls go through the raw client.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Panel
from ..panel_sdk import (
    http_error_detail,
    make_api,
    raw_host_request,
    require_panel_key,
)
from ..schemas import HostCreateIn, HostUpdateIn, model_to_dict

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def _get_panel(db: Session, panel_id: int) -> Panel:
    p = db.query(Panel).filter(Panel.id == panel_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    if not p.base_url or not p.api_key.strip():
        raise HTTPException(status_code=400, detail=f"Panel '{p.name}' is incomplete (base URL + API key needed).")
    return p


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.replace(",", " ").split() if x.strip()]


@router.get("")
async def list_hosts(
    panel_id: int = Query(...),
    inbound_tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    p = _get_panel(db, panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            hosts = await api.get_hosts(token=token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {http_error_detail(exc)}")
    out = [model_to_dict(h) for h in hosts or []]
    if inbound_tag:
        out = [h for h in out if h.get("inbound_tag") == inbound_tag]
    return {"hosts": out}


@router.post("")
async def create_host(body: HostCreateIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = _get_panel(db, body.panel_id)
    if not body.remark.strip() or not body.inbound_tag.strip():
        raise HTTPException(status_code=400, detail="remark and inbound_tag are required.")
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload: dict = {
        "remark": body.remark.strip(),
        "address": _split(body.address),
        "inbound_tag": body.inbound_tag.strip(),
        "status": ["active"],
        "security": "inbound_default",
        "allowinsecure": False,
        "random_user_agent": False,
        "use_sni_as_host": False,
        "priority": 1,
        "is_disabled": False,
    }
    if body.ech_config.strip():
        payload["ech"] = {"xray": {"config_list": body.ech_config.strip(), "query_strategy": "full"}}
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            host = await raw_host_request(p.base_url, p.verify_ssl, token, "POST", "/api/host/", payload)
            return {"ok": True, "host": host}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {http_error_detail(exc)}")


@router.put("/{host_id}")
async def update_host(
    host_id: int, body: HostUpdateIn, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    p = _get_panel(db, body.panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            current = await api.get_host(host_id=host_id, token=token)
            payload = model_to_dict(current)
            if body.remark is not None:
                if not body.remark.strip():
                    raise HTTPException(status_code=400, detail="remark cannot be empty.")
                payload["remark"] = body.remark.strip()
            if body.address is not None:
                payload["address"] = _split(body.address)
            if body.ech_config is not None:
                if body.ech_config.strip():
                    payload["ech"] = {
                        "xray": {"config_list": body.ech_config.strip(), "query_strategy": "full"}
                    }
                else:
                    payload.pop("ech", None)
            host = await raw_host_request(
                p.base_url, p.verify_ssl, token, "PUT", f"/api/host/{host_id}", payload
            )
            return {"ok": True, "host": host}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {http_error_detail(exc)}")


@router.delete("/{host_id}")
async def delete_host(
    host_id: int, panel_id: int = Query(...), db: Session = Depends(get_db), _=Depends(get_current_user)
):
    p = _get_panel(db, panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            await api.remove_host(host_id=host_id, token=token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {http_error_detail(exc)}")
    return {"ok": True}
