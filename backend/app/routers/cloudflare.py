"""Cloudflare account CRUD + DNS records."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..cf import CloudflareAPI
from ..database import get_db
from ..deps import get_current_user
from ..models import CfAccount
from ..schemas import CfAccountIn, CfAccountOut, CfProxyPatch, CfRecordCreate, CfRecordUpdate

router = APIRouter(prefix="/api/cf-accounts", tags=["cloudflare"])


def _mask(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return "-"
    return f"{t[:6]}...{t[-4:]}" if len(t) > 12 else "****"


def _to_out(a: CfAccount) -> dict:
    return {"id": a.id, "name": a.name, "token_masked": _mask(a.token)}


def _get(db: Session, account_id: int) -> CfAccount:
    a = db.query(CfAccount).filter(CfAccount.id == account_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Cloudflare account not found")
    return a


@router.get("", response_model=list[CfAccountOut])
def list_accounts(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return [_to_out(a) for a in db.query(CfAccount).order_by(CfAccount.name).all()]


@router.post("", response_model=CfAccountOut)
def create_account(body: CfAccountIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    if db.query(CfAccount).filter(CfAccount.name == body.name.strip()).first():
        raise HTTPException(status_code=400, detail="Account name already exists")
    a = CfAccount(name=body.name.strip(), token=body.token.strip())
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.get("/{account_id}", response_model=CfAccountOut)
def get_account(account_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _to_out(_get(db, account_id))


@router.put("/{account_id}", response_model=CfAccountOut)
def update_account(account_id: int, body: CfAccountIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _get(db, account_id)
    if body.name.strip() != a.name and db.query(CfAccount).filter(CfAccount.name == body.name.strip()).first():
        raise HTTPException(status_code=400, detail="Account name already exists")
    a.name = body.name.strip()
    if body.token.strip():
        a.token = body.token.strip()
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = _get(db, account_id)
    db.delete(a)
    db.commit()
    return {"ok": True}


def _api(db: Session, account_id: int) -> CloudflareAPI:
    a = _get(db, account_id)
    if not a.token.strip():
        raise HTTPException(status_code=400, detail="Account token is empty")
    return CloudflareAPI(a.token)


@router.get("/{account_id}/zones")
async def list_zones(account_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    try:
        return {"zones": await _api(db, account_id).list_zones()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")


@router.get("/{account_id}/zones/{zone_id}/records")
async def list_records(
    account_id: int, zone_id: str,
    type: str | None = Query(default=None),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    try:
        return {"records": await _api(db, account_id).list_records(zone_id, type)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")


@router.post("/{account_id}/zones/{zone_id}/records")
async def create_record(
    account_id: int, zone_id: str, body: CfRecordCreate,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    try:
        rec = await _api(db, account_id).create_record(
            zone_id, body.name, body.content, body.type, body.proxied, body.ttl
        )
        return {"record": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")


@router.put("/{account_id}/zones/{zone_id}/records/{record_id}")
async def update_record(
    account_id: int, zone_id: str, record_id: str, body: CfRecordUpdate,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    try:
        api = _api(db, account_id)
        current = await api.get_record(zone_id, record_id)
        rec = await api.update_record(zone_id, record_id, current, body.model_dump())
        return {"record": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")


@router.patch("/{account_id}/zones/{zone_id}/records/{record_id}/proxy")
async def patch_proxy(
    account_id: int, zone_id: str, record_id: str, body: CfProxyPatch,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    try:
        api = _api(db, account_id)
        current = await api.get_record(zone_id, record_id)
        rec = await api.set_proxied(zone_id, record_id, current, body.proxied)
        return {"record": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")


@router.delete("/{account_id}/zones/{zone_id}/records/{record_id}")
async def delete_record(
    account_id: int, zone_id: str, record_id: str,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    try:
        result = await _api(db, account_id).delete_record(zone_id, record_id)
        return {"ok": True, "result": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare request failed: {exc}")
