"""BackPack tunnel: test / create / manage / delete across Iran + Kharej.

Test/create/delete run as background jobs (they take minutes); the frontend
polls the job for the live transcript. Listing is fast and synchronous.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import tunnel_lib
from ..database import get_db
from ..deps import get_current_user
from ..jobs import store
from ..models import Server
from ..schemas import TunnelCreateIn, TunnelDeleteIn, TunnelListIn, TunnelTestIn
from ..ssh_service import SshSession, target_from_server

router = APIRouter(prefix="/api/tunnel", tags=["tunnel"])


def _get_server(db: Session, server_id: int) -> Server:
    s = db.query(Server).filter(Server.id == server_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return s


@router.get("/transports")
def transports(_=Depends(get_current_user)):
    return {"transports": tunnel_lib.TRANSPORTS, "direct_carriers": tunnel_lib.DIRECT_CARRIERS}


@router.post("/test", status_code=202)
def start_test(body: TunnelTestIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    iran = _get_server(db, body.iran_server_id)
    kharej = _get_server(db, body.kharej_server_id)
    if iran.id == kharej.id:
        raise HTTPException(status_code=400, detail="Iran and Kharej must be different servers.")
    iran_t, kh_t = target_from_server(iran), target_from_server(kharej)
    iran_ip = (body.iran_ip or "").strip()
    job = store.create("tunnel-test", f"BackPack test {iran.name} <-> {kharej.name}")

    def _run(j):
        j.log(f"Test started: Iran {iran_t.label} / Kharej {kh_t.label}")
        return tunnel_lib.run_test_flow(iran_t, kh_t, iran_ip, j.log)

    store.run(job, _run)
    return {"job_id": job.id}


@router.post("/create", status_code=202)
def start_create(body: TunnelCreateIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    iran = _get_server(db, body.iran_server_id)
    kharej = _get_server(db, body.kharej_server_id)
    if iran.id == kharej.id:
        raise HTTPException(status_code=400, detail="Iran and Kharej must be different servers.")
    if body.direction not in ("reverse", "direct"):
        raise HTTPException(status_code=400, detail="direction must be reverse or direct.")
    if body.direction == "reverse":
        if body.transport not in tunnel_lib.TRANSPORT_BY_VALUE:
            raise HTTPException(status_code=400, detail=f"Unknown transport '{body.transport}'.")
        if not (body.forwarded_ports or "").strip():
            raise HTTPException(status_code=400, detail="Forwarded ports are required.")
    else:
        if body.carrier not in tunnel_lib.DIRECT_CARRIER_BY_VALUE:
            raise HTTPException(status_code=400, detail=f"Unknown direct carrier '{body.carrier}'.")
    iran_t, kh_t = target_from_server(iran), target_from_server(kharej)
    spec = {
        "direction": body.direction,
        "transport": body.transport,
        "carrier": body.carrier,
        "tunnel_port": (body.tunnel_port or "").strip(),
        "forwarded_ports": (body.forwarded_ports or "").strip(),
        "tunnel_name": (body.tunnel_name or "").strip(),
        "iran_ip": (body.iran_ip or "").strip(),
        "kharej_addr": (body.kharej_addr or "").strip() or kharej.host,
    }
    if body.direction == "reverse":
        t = tunnel_lib.TRANSPORT_BY_VALUE[body.transport]
        what = f"{t['family']} / {t['label']}"
    else:
        c = tunnel_lib.DIRECT_CARRIER_BY_VALUE[body.carrier]
        what = f"direct / {c['label']}"
    job = store.create("tunnel-create", f"BackPack create {what} {iran.name} <-> {kharej.name}")

    def _run(j):
        j.log(f"Create started: {what} ({body.direction})")
        return tunnel_lib.run_create_flow(iran_t, kh_t, spec, j.log)

    store.run(job, _run)
    return {"job_id": job.id}


@router.post("/delete", status_code=202)
def start_delete(body: TunnelDeleteIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    name = (body.tunnel_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tunnel name is required.")
    target = target_from_server(s)
    job = store.create("tunnel-delete", f"BackPack delete '{name}' on {s.name}")

    def _run(j):
        j.log(f"Connecting to {target.label} ...")
        ssh = SshSession(target)
        try:
            ssh.connect()
            ssh.client.get_transport().set_keepalive(30)
            note = tunnel_lib.delete_tunnel_on_server(ssh, name, j.log, s.name)
        except Exception as exc:
            note = f"{s.name}: delete failed: {exc}"
        finally:
            ssh.close()
        j.log(note)
        return {"notes": [note]}

    store.run(job, _run)
    return {"job_id": job.id}


@router.post("/list")
async def tunnel_list(body: TunnelListIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    target = target_from_server(s)

    def _run():
        with SshSession(target) as ssh:
            return tunnel_lib.list_tunnels(ssh)

    try:
        return await asyncio.to_thread(_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH failed: {exc}")


@router.get("/jobs")
def list_jobs(_=Depends(get_current_user)):
    return {"jobs": store.list()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, since: int = Query(default=0), _=Depends(get_current_user)):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict(since=since)
