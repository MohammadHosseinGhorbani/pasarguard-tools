"""BackPack web-panel API (each server stands alone; our app drives both sides).

Where the SSH flows drive menus, these endpoints talk to each panel API:
structured inventory, per-side actions, direct both-sides creation, and
structured connection-test progress. The Kharej test-join stays a single
SSH `link apply` (the panel still needs a joiner). Bootstrap (panel
enablement, API token) is guided in the UI.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import tunnel_lib
from ..bp_api import BackpackAPI, BackpackAPIError
from ..database import get_db
from ..deps import get_current_user
from ..jobs import store
from ..models import Server
from ..schemas import (
    BpActionIn,
    BpConnTestIn,
    BpConnTestStopIn,
    BpCreateIn,
    BpDeleteIn,
    BpTestIn,
    BpTunnelsIn,
    model_to_dict,
)
from ..ssh_service import SshSession, target_from_server

router = APIRouter(prefix="/api/bp", tags=["backpack-api"])


def _get_server(db: Session, server_id: int) -> Server:
    s = db.query(Server).filter(Server.id == server_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return s


def _api_for(server: Server, timeout: float = 30.0) -> BackpackAPI:
    if not (server.bp_url or "").strip() or not (server.bp_token or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"Server '{server.name}' has no BackPack panel URL/token set. "
            "Save them first (Iran > Tunnel > BackPack panel).",
        )
    try:
        return BackpackAPI(server.bp_url, server.bp_token, verify=bool(server.bp_verify), timeout=timeout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _wrap_api(exc: Exception) -> HTTPException:
    msg = str(exc)
    if "401" in msg or "403" in msg:
        return HTTPException(status_code=502, detail=msg)
    return HTTPException(status_code=502, detail=msg)


@router.post("/test")
async def bp_test(body: BpTestIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    api = _api_for(s)
    try:
        data = await api.tunnels()
    except BackpackAPIError as exc:
        raise _wrap_api(exc)
    tunnels = data if isinstance(data, list) else (data.get("tunnels", []) if isinstance(data, dict) else [])
    return {"ok": True, "tunnel_count": len(tunnels) if isinstance(tunnels, list) else 0}


@router.post("/tunnels")
async def bp_tunnels(body: BpTunnelsIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    api = _api_for(s)
    try:
        data = await api.tunnels()
    except BackpackAPIError as exc:
        raise _wrap_api(exc)
    if isinstance(data, list):
        return {"tunnels": data}
    if isinstance(data, dict):
        return {"tunnels": data.get("tunnels", data)}
    return {"tunnels": []}


@router.post("/action")
async def bp_action(body: BpActionIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    api = _api_for(s, timeout=120.0)
    try:
        return await api.tunnel_action(body.name, body.action)
    except BackpackAPIError as exc:
        raise _wrap_api(exc)


@router.post("/create", status_code=202)
def bp_create(body: BpCreateIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    iran = _get_server(db, body.iran_server_id)
    kh = _get_server(db, body.kharej_server_id)
    if iran.id == kh.id:
        raise HTTPException(status_code=400, detail="Iran and Kharej must be different servers.")
    iran_api = _api_for(iran, timeout=900.0)
    kh_api = _api_for(kh, timeout=900.0)
    direction = body.direction
    name = (body.tunnel_name or "").strip()
    token = secrets.token_urlsafe(48)

    async def _build():
        if direction == "reverse":
            if body.transport not in tunnel_lib.TRANSPORT_BY_VALUE:
                raise RuntimeError(f"Unknown transport '{body.transport}'.")
            t = tunnel_lib.TRANSPORT_BY_VALUE[body.transport]
            port = (body.tunnel_port or "").strip()
            iran_res = await iran_api.create_reverse({
                "role": "server",
                "transport": body.transport,
                "name": name,
                "tunnelPort": port,
                "token": token,
                "ports": (body.forwarded_ports or "").strip(),
                "preset": "turbo",
            })
            kh_res = await kh_api.create_reverse({
                "role": "client",
                "transport": body.transport,
                "name": name,
                "tunnelPort": port,
                "serverAddr": iran.host,
                "token": token,
            })
            return {"direction": "reverse", "transport": t["label"], "iran": iran_res, "kharej": kh_res}
        if body.carrier not in tunnel_lib.DIRECT_CARRIER_BY_VALUE:
            raise RuntimeError(f"Unknown direct carrier '{body.carrier}'.")
        c = tunnel_lib.DIRECT_CARRIER_BY_VALUE[body.carrier]
        port = (body.tunnel_port or "").strip() or "9000"
        iran_res = await iran_api.create_direct({
            "side": "iran",
            "carrier": body.carrier,
            "name": name,
            "token": token,
            "peerAddr": kh.host,
            "tunnelPort": port,
            "ports": (body.forwarded_ports or "").strip(),
            "preset": "turbo",
        })
        kh_res = await kh_api.create_direct({
            "side": "kharej",
            "carrier": body.carrier,
            "name": name,
            "token": token,
            "tunnelPort": port,
            "preset": "turbo",
        })
        return {"direction": "direct", "carrier": c["label"], "iran": iran_res, "kharej": kh_res}

    what = body.transport if direction == "reverse" else body.carrier
    job = store.create("bp-create", f"BackPack API create {direction}/{what} (both panels)")

    def _run(j):
        j.log(f"Creating {direction} tunnel via both panel APIs...")
        try:
            out = asyncio.run(_build())
        except BackpackAPIError as exc:
            raise RuntimeError(str(exc))
        j.log("Both sides reported by their panels.")
        return out if isinstance(out, dict) else {"result": out}

    store.run(job, _run)
    return {"job_id": job.id}


@router.post("/delete", status_code=202)
def bp_delete(body: BpDeleteIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    api = _api_for(s, timeout=300.0)
    job = store.create("bp-delete", f"BackPack API delete '{body.name}'")

    def _run(j):
        j.log(f"Deleting '{body.name}'...")
        try:
            out = asyncio.run(api.tunnel_action(body.name, "delete"))
        except BackpackAPIError as exc:
            raise RuntimeError(str(exc))
        j.log("Delete reported by the panel.")
        return out if isinstance(out, dict) else {"result": out}

    store.run(job, _run)
    return {"job_id": job.id}


def _map_conntest_rows(view: dict) -> tuple[list[dict], dict | None]:
    rows = view.get("rows", []) or []
    ranked = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or r.get("tr") or "")
        tr = str(r.get("tr") or "")
        setup = tunnel_lib.map_test_transport(tr) or tunnel_lib.map_test_transport(name)
        rtt = r.get("rtt") or r.get("RTTms")
        mbps = r.get("mbps") or r.get("Mbps")
        ranked.append({
            "transport": name or tr,
            "rtt": f"{rtt}ms" if rtt else "",
            "speed": f"{mbps} Mbps" if mbps else str(r.get("status") or ""),
            "winner": False,
            "setup_value": setup,
            "status": str(r.get("status") or ""),
        })
    best = view.get("best") if isinstance(view.get("best"), dict) else None
    winner = None
    if best:
        bname = str(best.get("transport") or best.get("name") or best.get("tr") or "")
        if bname:
            winner = {"transport": bname, "measured": str(best.get("detail") or best.get("measured") or "")}
            for row in ranked:
                candidates = {str(row.get("transport") or "").lower(), str(row.get("setup_value") or "").lower()}
                candidates.discard("")
                if bname.lower() in candidates:
                    row["winner"] = True
    return ranked, winner


@router.post("/conntest", status_code=202)
def bp_conntest(body: BpConnTestIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    iran = _get_server(db, body.iran_server_id)
    kh = _get_server(db, body.kharej_server_id)
    if iran.id == kh.id:
        raise HTTPException(status_code=400, detail="Iran and Kharej must be different servers.")
    api = _api_for(iran, timeout=120.0)
    kh_target = target_from_server(kh)
    host = (body.host or "").strip()
    preset = (body.preset or "turbo").strip() or "turbo"
    job = store.create("bp-conntest", f"BackPack API connection test {iran.name} <-> {kh.name}")

    def _run(j):
        async def _flow():
            j.log("Starting connection test via panel API...")
            view = await api.conntest_start(host=host, preset=preset)
            if not isinstance(view, dict):
                raise RuntimeError(f"Unexpected conntest response: {view}")
            link = str(view.get("link") or "")
            if not link:
                raise RuntimeError(f"Panel did not return a test link: {view}")
            j.log(f"Test link ready: {link[:26]}...")
            j.log(f"Joining from Kharej {kh_target.label} (link apply)...")
            import shlex

            from ..tunnel_lib import ensure_backpack, exec_streaming, resolve_sudo, root_prefix, sudo_stdin_lines

            def _join():
                with SshSession(kh_target) as ssh:
                    ensure_backpack(ssh, j.log)
                    mode = resolve_sudo(ssh)
                    cmd = root_prefix(mode) + "backpack link apply " + shlex.quote(link)
                    rc, out = exec_streaming(ssh, cmd, 1500.0, sudo_stdin_lines(ssh, mode), on_text=j.log)
                    if rc != 0:
                        raise RuntimeError(f"Kharej join failed (rc={rc}).")

            # The Kharej CLI blocks until it collects the verdict (~4+ min),
            # so joining in the foreground would stall polling until the test
            # is already over. Join in the background and poll immediately.
            import threading

            join_out: dict = {"done": False, "error": ""}
            started_evt = threading.Event()

            def _join_bg():
                started_evt.set()
                try:
                    _join()
                except Exception as exc:  # noqa: BLE001 - surfaced below
                    join_out["error"] = str(exc)
                finally:
                    join_out["done"] = True

            jt = threading.Thread(target=_join_bg, daemon=True)
            jt.start()
            await asyncio.to_thread(started_evt.wait, 10.0)
            j.log("Kharej join launched in background. Polling structured results...")
            deadline = time.time() + 30 * 60
            final: dict = {}
            last_sig: tuple = ("", -1)
            while time.time() < deadline:
                await asyncio.sleep(5)
                if join_out["error"]:
                    try:
                        await api.conntest_stop()
                    except Exception:
                        pass
                    raise RuntimeError(f"Kharej join failed: {join_out['error']}")
                view = await api.conntest_poll()
                if not isinstance(view, dict):
                    continue
                # Panel vocabulary (internal/webui/handlers_conntest.go):
                # idle/starting/waiting/running/done/failed/stopped.
                state = str(view.get("state") or "").lower()
                nrows = len(view.get("rows") or [])
                # Publish the partial rows live: the frontend polls the job and
                # renders result.steady as it fills (panel streams per-transport
                # rows while running; winner is marked only at the end).
                ranked_live, _ = _map_conntest_rows(view)
                j.result = {"steady": ranked_live, "live": True, "state": state, "failed": False}
                # Aggregate echo/measured counters so the log visibly advances
                # through the long soak (state and row count stay flat for minutes).
                rows = view.get("rows") or []
                tot_tried = sum(int(r.get("tried") or 0) for r in rows if isinstance(r, dict))
                tot_ok = sum(int(r.get("ok") or 0) for r in rows if isinstance(r, dict))
                measured = sum(1 for r in rows if isinstance(r, dict) and (r.get("rtt") or r.get("mbps")))
                if (state, nrows, tot_tried, tot_ok, measured) != last_sig:
                    j.log(f"... state={state or '?'} rows={nrows} tried={tot_tried} ok={tot_ok} measured={measured}")
                    last_sig = (state, nrows, tot_tried, tot_ok, measured)
                if state in ("done", "finished", "failed", "stopped"):
                    final = view
                    break
            if not final:
                raise RuntimeError("Connection test did not finish within 30 minutes.")
            ranked, winner = _map_conntest_rows(final)
            failed = str(final.get("state") or "").lower() == "failed"
            if failed:
                j.log(f"Test reported failure: {final.get('error') or ''}")
            # The Kharej CLI exits once it collects the verdict; don't wait
            # forever if it lingers (daemon thread, harmless either way).
            await asyncio.to_thread(jt.join, 180.0)
            if jt.is_alive():
                j.log("Note: Kharej link-apply session still open; test verdict above stands.")
            elif join_out["error"]:
                raise RuntimeError(f"Kharej join failed: {join_out['error']}")
            else:
                j.log("Kharej join session closed.")
            return {"winner": winner, "steady": ranked, "failed": failed, "live": False}

        try:
            return asyncio.run(_flow())
        except BackpackAPIError as exc:
            raise RuntimeError(str(exc))

    store.run(job, _run)
    return {"job_id": job.id}


@router.post("/conntest/stop")
async def bp_conntest_stop(body: BpConnTestStopIn, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = _get_server(db, body.server_id)
    api = _api_for(s)
    try:
        return await api.conntest_stop()
    except BackpackAPIError as exc:
        raise _wrap_api(exc)
