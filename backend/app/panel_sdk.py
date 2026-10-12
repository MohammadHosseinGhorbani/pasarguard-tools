"""Async helpers around the pasarguard SDK (panel side, from CLI panel_ops.py).

No prints; ``ensure_node_with_core`` returns (node, core_id, notes) where
notes is a list of human-readable progress strings the API returns to the
frontend instead of printing.
"""
from __future__ import annotations

from typing import Any

import httpx
from pasarguard import CoreCreate, CoreType, CreateHost, NodeConnectionType, NodeCreate, PasarguardAPI

from .schemas import model_to_dict


def require_panel_key(api_key: str) -> str:
    if api_key and api_key.strip():
        return api_key.strip()
    raise ValueError("Panel has no API key stored.")


def http_error_detail(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if resp is None:
        return str(exc)
    try:
        body = resp.text or ""
    except Exception:
        body = ""
    base = str(exc)
    if body and body not in base:
        return f"{base}\nPanel response: {body[:3000]}"
    return base


def make_api(base_url: str, verify: bool, timeout: float = 30.0) -> PasarguardAPI:
    return PasarguardAPI(base_url=base_url, verify=verify, timeout=timeout)


async def modify_core_verbose(
    api: PasarguardAPI, token: str, core_id: int, core: CoreCreate, restart_nodes: bool = True
) -> Any:
    try:
        return await api.modify_core_config(
            core_id=core_id, core=core, restart_nodes=restart_nodes, token=token
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(http_error_detail(exc)) from exc


# Dropped connection while the panel restarts nodes after a core update.
# "Server disconnected without sending a response" means the change usually
# DID apply — so verify by re-reading instead of failing outright.
VERIFYABLE_ERRORS = (httpx.RemoteProtocolError, httpx.TimeoutException)


async def modify_core_resilient(
    api: PasarguardAPI,
    token: str,
    core_id: int,
    core: CoreCreate,
    verify,
    restart_nodes: bool = True,
) -> tuple[Any, bool]:
    """PUT a core config, tolerating a dropped connection.

    `verify(config)` decides whether the change is present. Returns
    (result, recovered) where recovered=True means the panel dropped us
    mid-restart but the change was verified present.
    """
    import asyncio

    try:
        res = await modify_core_verbose(api, token, core_id, core, restart_nodes=restart_nodes)
        return res, False
    except RuntimeError:
        raise  # validation errors etc. from modify_core_verbose — real failures
    except VERIFYABLE_ERRORS:
        pass
    await asyncio.sleep(8)  # let the node restart proceed
    try:
        full = await api.get_core_config(core_id=core_id, token=token)
    except Exception as exc2:
        raise RuntimeError(
            f"Panel dropped the connection and the re-check also failed: {exc2}"
        ) from exc2
    if verify(full.config or {}):
        return full, True
    raise RuntimeError(
        "Panel dropped the connection and the change is NOT present on re-check. "
        "The node restart may still be in progress — check the core in the dashboard."
    )


async def find_node_by_address(api: PasarguardAPI, token: str, address: str):
    nodes = await api.get_nodes(token=token)
    items = getattr(nodes, "nodes", nodes) or []
    for n in items:
        if getattr(n, "address", "") == address:
            return n
    return None


async def find_node_by_name(api: PasarguardAPI, token: str, name: str):
    nodes = await api.get_nodes(token=token)
    items = getattr(nodes, "nodes", nodes) or []
    for n in items:
        if getattr(n, "name", "") == name:
            return n
    return None


async def find_core_by_name(api: PasarguardAPI, token: str, name: str):
    cores = await api.get_all_cores(token=token)
    items = getattr(cores, "cores", cores) or []
    for c in items:
        if getattr(c, "name", "") == name:
            return c
    return None


async def ensure_core_for_reality(
    api: PasarguardAPI, token: str, core_name: str, inbound: dict[str, Any],
    update_existing: bool = True,
) -> Any:
    from .inbound_lib import build_core_config, merge_inbound_into_config

    existing = await find_core_by_name(api, token, core_name)
    if existing is None:
        return await api.create_core_config(
            CoreCreate(
                name=core_name, config=build_core_config(inbound), type=CoreType.XRAY,
                exclude_inbound_tags=[], fallbacks_inbound_tags=[],
            ),
            token=token,
        )
    if not update_existing:
        return existing
    full = await api.get_core_config(core_id=existing.id, token=token)
    merged = merge_inbound_into_config(full.config or {}, inbound)
    tag = inbound.get("tag")
    updated, _ = await modify_core_resilient(
        api, token, existing.id,
        CoreCreate(
            name=existing.name,
            config=merged,
            type=getattr(existing, "type", CoreType.XRAY),
            exclude_inbound_tags=getattr(existing, "exclude_inbound_tags", None) or [],
            fallbacks_inbound_tags=getattr(existing, "fallbacks_inbound_tags", None) or [],
        ),
        verify=lambda cfg: any(i.get("tag") == tag for i in (cfg.get("inbounds") or [])),
        restart_nodes=True,
    )
    return updated


async def ensure_node_with_core(
    api: PasarguardAPI, token: str, *, node_address: str, node_name: str,
    core_name: str, inbound: dict[str, Any], creds: dict[str, str],
) -> tuple[Any, int, list[str]]:
    """Returns (node, core_id, notes). Reuses the node's current core when present."""
    from .inbound_lib import merge_inbound_into_config

    notes: list[str] = []
    existing = await find_node_by_address(api, token, node_address)
    if existing is not None:
        notes.append(f"Node already in panel: id={existing.id} name={existing.name} addr={existing.address}")
        core_id = getattr(existing, "core_config_id", None)
        if core_id is None:
            core = await ensure_core_for_reality(api, token, core_name, inbound)
            core_id = core.id
            notes.append(f"Core '{core_name}' ready (id={core_id}). Attach it to node {existing.id} in the panel.")
            return existing, core_id, notes
        full = await api.get_core_config(core_id=core_id, token=token)
        current = (full.config or {}).get("inbounds", []) or []
        for ib in current:
            if ib.get("port") == inbound.get("port"):
                notes.append(
                    f"WARNING: port {inbound.get('port')} is already used by "
                    f"inbound {ib.get('tag')!r} — the panel may reject a duplicate port."
                )
        merged = merge_inbound_into_config(full.config or {}, inbound)
        tag = inbound.get("tag")
        _, recovered = await modify_core_resilient(
            api, token, core_id,
            CoreCreate(
                name=full.name,
                config=merged,
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
        else:
            notes.append(f"Inbound '{inbound.get('tag')}' merged into core id={core_id} (nodes restarting).")
        return existing, core_id, notes
    notes.append("Node not found in panel. Creating core config + node...")
    core = await ensure_core_for_reality(api, token, core_name, inbound)
    notes.append(f"Core created/found: id={core.id} name={core.name}")
    node = await create_panel_node(
        api, token, name=node_name, address=node_address,
        service_port=int(creds["service_port"]),
        api_port=int(creds.get("api_port") or 62051),
        connection_type=creds["protocol"],
        server_ca=creds["server_ca"],
        api_key=creds["api_key"],
        core_config_id=core.id,
    )
    notes.append(f"Node created: id={node.id} name={node.name}")
    return node, core.id, notes


ECH_PRESETS: dict[str, str] = {
    "ip.gs": "ip.gs+udp://8.8.8.8",
    "cloudflare": "cloudflare-ech.com+udp://1.1.1.1",
}


def _panel_auth_headers(credential: str) -> dict[str, str]:
    if credential.startswith("pg_key_"):
        return {"X-Api-Key": credential}
    return {"Authorization": f"Bearer {credential}"}


async def create_host_raw(
    base_url: str, verify: bool, token: str, payload: dict[str, Any], timeout: float = 20.0
) -> dict[str, Any]:
    """POST /api/host/ with an exact dashboard-shaped payload (ECH needs nesting)."""
    return await raw_host_request(base_url, verify, token, "POST", "/api/host/", payload, timeout)


async def raw_host_request(
    base_url: str, verify: bool, token: str, method: str, path: str,
    payload: dict[str, Any], timeout: float = 20.0,
) -> dict[str, Any]:
    """Raw host API call (the SDK flattens ECH fields the panel ignores)."""
    try:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"), verify=verify, timeout=timeout
        ) as client:
            r = await client.request(method, path, json=payload, headers=_panel_auth_headers(token))
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {"result": data}
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(http_error_detail(exc)) from exc


def build_reality_host(
    remark: str, inbound_tag: str, address: str = "", port: int | None = None, sni: str = "",
) -> CreateHost:
    """Subscription host for a direct (Reality) inbound: real server address."""
    return CreateHost(
        remark=remark.strip(),
        address=[address.strip()] if address.strip() else [],
        inbound_tag=inbound_tag.strip(),
        port=port,
        sni=[sni.strip()] if sni.strip() else None,
        priority=1,
    )


def build_cloudflare_host_payload(
    remark: str, inbound_tag: str, camouflage_address: str = "", ech: str | None = None,
) -> dict[str, Any]:
    """Dashboard-shaped host for a Cloudflare inbound (ECH nested for xray)."""
    payload: dict[str, Any] = {
        "remark": remark.strip(),
        "address": [camouflage_address.strip()] if camouflage_address.strip() else [],
        "inbound_tag": inbound_tag.strip(),
        "status": ["active"],
        "security": "inbound_default",
        "allowinsecure": False,
        "random_user_agent": False,
        "use_sni_as_host": False,
        "priority": 20,
        "is_disabled": False,
    }
    if ech and ech != "none":
        config_list = ECH_PRESETS.get(ech, ech)
        payload["ech"] = {"xray": {"config_list": config_list, "query_strategy": "full"}}
    return payload


def ech_preset_of(config_list: str | None) -> str:
    """Map a stored ech config_list back to a preset name (or 'none')."""
    for name, value in ECH_PRESETS.items():
        if (config_list or "").strip() == value:
            return name
    return "none"


async def ensure_cf_host(
    api: PasarguardAPI, panel_base_url: str, panel_verify: bool, token: str, *,
    remark: str, inbound_tag: str, camouflage_address: str, ech_config_list: str | None,
) -> dict[str, Any]:
    hosts = await api.get_hosts(token=token)
    for h in hosts or []:
        if getattr(h, "remark", "") == remark and getattr(h, "inbound_tag", "") == inbound_tag:
            return {"id": getattr(h, "id", None), "remark": remark, "existing": True}
    payload: dict[str, Any] = {
        "remark": remark,
        "address": [camouflage_address],
        "inbound_tag": inbound_tag,
        "status": ["active"],
        "security": "inbound_default",
        "allowinsecure": False,
        "random_user_agent": False,
        "use_sni_as_host": False,
        "priority": 20,
        "is_disabled": False,
    }
    if ech_config_list:
        payload["ech"] = {"xray": {"config_list": ech_config_list, "query_strategy": "full"}}
    return await create_host_raw(panel_base_url, panel_verify, token, payload)


async def create_panel_node(
    api: PasarguardAPI, token: str, *, name: str, address: str,
    service_port: int = 62050, api_port: int = 62051, connection_type: str = "grpc",
    server_ca: str, api_key: str, core_config_id: int, keep_alive: int = 30,
) -> Any:
    ct = NodeConnectionType.GRPC if connection_type.lower() == "grpc" else NodeConnectionType.REST
    return await api.create_node(
        NodeCreate(
            name=name, address=address, port=service_port, api_port=api_port,
            connection_type=ct, server_ca=server_ca, keep_alive=keep_alive,
            core_config_id=core_config_id, api_key=api_key,
        ),
        token=token,
    )


async def ensure_host_for_inbound(
    api: PasarguardAPI, token: str, *, remark: str, inbound_tag: str,
    address: str, port: int, sni: str,
) -> Any:
    """Create a Host so clients get subscription links for the inbound."""
    hosts = await api.get_hosts(token=token)
    for h in hosts or []:
        if getattr(h, "remark", "") == remark and getattr(h, "inbound_tag", "") == inbound_tag:
            return h
    return await api.create_host(
        CreateHost(
            remark=remark, address=[address], inbound_tag=inbound_tag,
            port=port, sni=[sni], priority=1,
        ),
        token=token,
    )


async def get_node_inbounds(
    base_url: str, verify: bool, token: str, address: str,
) -> dict[str, Any]:
    """Resolve node by address, fetch its core config, return node/core/inbounds."""
    async with make_api(base_url, verify) as api:
        node = await find_node_by_address(api, token, address)
        if node is None:
            raise RuntimeError(f"No node with address '{address}' in this panel.")
        core_id = getattr(node, "core_config_id", None)
        if core_id is None:
            return {"node": model_to_dict(node), "core": None, "inbounds": []}
        full = await api.get_core_config(core_id=core_id, token=token)
        cfg = full.config or {}
        return {
            "node": model_to_dict(node),
            "core": {"id": getattr(full, "id", core_id), "name": getattr(full, "name", "")},
            "inbounds": cfg.get("inbounds", []) or [],
        }
