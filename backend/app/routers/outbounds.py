"""WireGuard outbounds: list + whole-config save for a core.

Editing is staged entirely in the UI (rename / replace-config / delete /
add do nothing on their own). Save sends the complete desired WireGuard
outbound list; the backend converts pasted .conf text via wg_lib, keeps
untouched outbounds byte-identical, drops the rest, and PUTs the full
core config once (restart_nodes=true), like the dashboard does.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Panel
from ..panel_sdk import make_api, modify_core_resilient, require_panel_key
from ..schemas import OutboundsSaveIn, RoutingSaveIn, model_to_dict
from ..wg_lib import wg_conf_to_outbound

router = APIRouter(prefix="/api/outbounds", tags=["outbounds"])


def _get_panel(db: Session, panel_id: int) -> Panel:
    p = db.query(Panel).filter(Panel.id == panel_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    if not p.base_url or not p.api_key.strip():
        raise HTTPException(status_code=400, detail=f"Panel '{p.name}' is incomplete (base URL + API key needed).")
    return p


def _wireguard_only(config: dict) -> list[dict]:
    return [o for o in (config.get("outbounds") or []) if isinstance(o, dict) and o.get("protocol") == "wireguard"]


def _all_outbounds(config: dict) -> list[dict]:
    return [o for o in (config.get("outbounds") or []) if isinstance(o, dict)]


def _routing_rules(config: dict) -> list[dict]:
    routing = config.get("routing") or {}
    if not isinstance(routing, dict):
        return []
    return [r for r in (routing.get("rules") or []) if isinstance(r, dict)]


@router.get("")
async def list_outbounds(
    panel_id: int, core_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    p = _get_panel(db, panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            full = await api.get_core_config(core_id=core_id, token=token)
            return {
                "core": {"id": core_id, "name": getattr(full, "name", ""), "type": str(getattr(full, "type", ""))},
                "outbounds": _wireguard_only(full.config or {}),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


@router.put("")
async def save_outbounds(body: OutboundsSaveIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from pasarguard import CoreCreate, CoreType

    p = _get_panel(db, body.panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            full = await api.get_core_config(core_id=body.core_id, token=token)
            config = full.config or {}
            current_wg = _wireguard_only(config)
            by_tag = {str(o.get("tag") or ""): o for o in current_wg}
            kept = [o for o in (config.get("outbounds") or []) if not (isinstance(o, dict) and o.get("protocol") == "wireguard")]

            new_wg: list[dict] = []
            seen: set[str] = set()
            for item in body.outbounds:
                tag = (item.tag or "").strip()
                if not tag:
                    raise HTTPException(status_code=400, detail="Every outbound needs a name.")
                if tag in seen:
                    raise HTTPException(status_code=400, detail=f"Duplicate outbound name '{tag}'.")
                seen.add(tag)
                conf = (item.wireguard_conf or "").strip()
                if not conf:
                    orig = (item.original_tag or "").strip()
                    if not orig or orig not in by_tag:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Outbound '{tag}': paste a WireGuard config (no existing outbound to keep).",
                        )
                    ob = dict(by_tag[orig])
                    ob["tag"] = tag
                    new_wg.append(ob)
                else:
                    try:
                        new_wg.append(wg_conf_to_outbound(tag, conf))
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc))
            for o in kept:
                if str(o.get("tag") or "") in seen:
                    raise HTTPException(
                        status_code=400, detail=f"Outbound name '{o.get('tag')}' clashes with a kept outbound."
                    )

            merged = dict(config)
            merged["outbounds"] = kept + new_wg
            expected = set(seen)
            updated, recovered = await modify_core_resilient(
                api, token, body.core_id,
                CoreCreate(
                    name=full.name, config=merged,
                    type=getattr(full, "type", CoreType.XRAY),
                    exclude_inbound_tags=[],
                    fallbacks_inbound_tags=[],
                ),
                verify=lambda cfg: {
                    str(o.get("tag") or "")
                    for o in (cfg.get("outbounds") or [])
                    if isinstance(o, dict) and o.get("protocol") == "wireguard"
                } == expected,
                restart_nodes=True,
            )
            return {"ok": True, "core": model_to_dict(updated), "outbounds": new_wg, "recovered": recovered}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


@router.get("/routing")
async def get_routing(
    panel_id: int, core_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """All outbounds (tag + protocol) plus routing rules of a core."""
    p = _get_panel(db, panel_id)
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            full = await api.get_core_config(core_id=core_id, token=token)
            return {
                "core": {"id": core_id, "name": getattr(full, "name", "")},
                "outbounds": [
                    {"tag": str(o.get("tag") or ""), "protocol": str(o.get("protocol") or "")}
                    for o in _all_outbounds(full.config or {})
                ],
                "rules": _routing_rules(full.config or {}),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


@router.put("/routing")
async def save_routing(body: RoutingSaveIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Assign an inbound to an outbound via one routing rule.

    Existing rules referencing the inbound are removed first (the tag is
    dropped from multi-inbound rules, single-inbound rules are deleted),
    then a fresh {type: field, inboundTag: [tag], outboundTag} rule is
    added — unless outbound_tag is empty, which just removes the assignment.
    """
    from pasarguard import CoreCreate, CoreType

    p = _get_panel(db, body.panel_id)
    inbound_tag = (body.inbound_tag or "").strip()
    outbound_tag = (body.outbound_tag or "").strip()
    if not inbound_tag:
        raise HTTPException(status_code=400, detail="inbound_tag is required.")
    try:
        token = require_panel_key(p.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with make_api(p.base_url, p.verify_ssl) as api:
            full = await api.get_core_config(core_id=body.core_id, token=token)
            config = full.config or {}
            known = {str(o.get("tag") or "") for o in _all_outbounds(config)}
            if outbound_tag and outbound_tag not in known:
                raise HTTPException(
                    status_code=400, detail=f"Outbound '{outbound_tag}' does not exist on this core."
                )
            rules: list[dict] = []
            for r in _routing_rules(config):
                tags = [str(t) for t in (r.get("inboundTag") or []) if str(t)]
                tags = [t for t in tags if t != inbound_tag]
                if not tags and inbound_tag in [str(t) for t in (r.get("inboundTag") or []) if str(t)]:
                    continue  # rule only covered this inbound — drop it
                if tags != [str(t) for t in (r.get("inboundTag") or []) if str(t)]:
                    r = dict(r)
                    r["inboundTag"] = tags
                rules.append(r)
            if outbound_tag:
                rules.append({"type": "field", "inboundTag": [inbound_tag], "outboundTag": outbound_tag})

            merged = dict(config)
            routing = dict(merged.get("routing") or {})
            routing["rules"] = rules
            merged["routing"] = routing
            updated, recovered = await modify_core_resilient(
                api, token, body.core_id,
                CoreCreate(
                    name=full.name, config=merged,
                    type=getattr(full, "type", CoreType.XRAY),
                    exclude_inbound_tags=[],
                    fallbacks_inbound_tags=[],
                ),
                verify=lambda cfg: _find_assignment(_routing_rules(cfg), inbound_tag) == (outbound_tag or None),
                restart_nodes=True,
            )
            return {
                "ok": True, "core": model_to_dict(updated),
                "inbound_tag": inbound_tag, "outbound_tag": outbound_tag or None,
                "recovered": recovered,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Panel request failed: {exc}")


def _find_assignment(rules: list[dict], inbound_tag: str) -> str | None:
    for r in rules:
        tags = [str(t) for t in (r.get("inboundTag") or []) if str(t)]
        if inbound_tag in tags:
            return str(r.get("outboundTag") or "") or None
    return None
