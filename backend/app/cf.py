"""Cloudflare DNS API client (httpx, Bearer token).

Same semantics as the original CLI ``cloudflare.py`` plus generic
list/create/update/delete DNS record helpers for the web UI.
"""
from __future__ import annotations

from typing import Any

import httpx

CF_API = "https://api.cloudflare.com/client/v4"


def pick_zone(zones: list[dict], fqdn: str) -> dict | None:
    """Longest zone name that is a DNS-suffix of fqdn (case-insensitive)."""
    fqdn = fqdn.strip().lower().rstrip(".")
    best: dict | None = None
    for z in zones:
        name = str(z.get("name", "")).lower().rstrip(".")
        if not name:
            continue
        if fqdn == name or fqdn.endswith("." + name):
            if best is None or len(name) > len(str(best.get("name", ""))):
                best = z
    return best


class CloudflareAPI:
    def __init__(self, token: str, timeout: float = 20.0):
        self._token = token.strip()
        if not self._token:
            raise RuntimeError("Cloudflare token is empty.")
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=CF_API,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )

    @staticmethod
    def _check(action: str, data: dict) -> dict:
        if data.get("success"):
            return data
        msgs = "; ".join(
            f"{e.get('code', '?')}: {e.get('message', '?')}" for e in data.get("errors", [])
        )
        raise RuntimeError(f"Cloudflare {action} failed: {msgs or data}")

    async def list_zones(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/zones", params={"per_page": 100})
            r.raise_for_status()
            return self._check("list zones", r.json()).get("result", [])

    async def find_zone(self, fqdn: str) -> dict:
        zones = await self.list_zones()
        zone = pick_zone(zones, fqdn)
        if zone is None:
            names = ", ".join(str(z.get("name")) for z in zones[:20]) or "(no zones visible to this token)"
            raise RuntimeError(
                f"No Cloudflare zone matches '{fqdn}' in this account. "
                f"Visible zones: {names}. Fix the domain or use a token with Zone:DNS access."
            )
        return zone

    async def list_records(self, zone_id: str, rtype: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"per_page": 100}
        if rtype:
            params["type"] = rtype
        async with self._client() as c:
            r = await c.get(f"/zones/{zone_id}/dns_records", params=params)
            r.raise_for_status()
            return self._check("list records", r.json()).get("result", [])

    async def get_record(self, zone_id: str, record_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/zones/{zone_id}/dns_records/{record_id}")
            r.raise_for_status()
            return self._check("get record", r.json())["result"]

    async def create_record(
        self, zone_id: str, name: str, content: str, rtype: str = "A",
        proxied: bool = False, ttl: int = 1,
    ) -> dict:
        body = {"type": rtype, "name": name.strip().rstrip("."), "content": content, "ttl": ttl, "proxied": proxied}
        async with self._client() as c:
            r = await c.post(f"/zones/{zone_id}/dns_records", json=body)
            r.raise_for_status()
            return self._check("create record", r.json())["result"]

    async def update_record(self, zone_id: str, record_id: str, record: dict, fields: dict) -> dict:
        body = {
            "type": fields.get("type") or record.get("type", "A"),
            "name": fields.get("name") or record.get("name"),
            "content": fields.get("content") if fields.get("content") is not None else record.get("content"),
            "ttl": fields.get("ttl") if fields.get("ttl") is not None else record.get("ttl", 1),
            "proxied": fields.get("proxied") if fields.get("proxied") is not None else record.get("proxied", False),
        }
        async with self._client() as c:
            r = await c.put(f"/zones/{zone_id}/dns_records/{record_id}", json=body)
            r.raise_for_status()
            return self._check("update record", r.json())["result"]

    async def delete_record(self, zone_id: str, record_id: str) -> dict:
        async with self._client() as c:
            r = await c.delete(f"/zones/{zone_id}/dns_records/{record_id}")
            r.raise_for_status()
            return self._check("delete record", r.json())["result"]

    async def upsert_a_record(self, zone_id: str, fqdn: str, ip: str, proxied: bool = False) -> dict:
        """Create the A record, or update it when the same name already exists."""
        fqdn = fqdn.strip().rstrip(".")
        async with self._client() as c:
            r = await c.get(
                f"/zones/{zone_id}/dns_records", params={"type": "A", "name": fqdn, "per_page": 10}
            )
            r.raise_for_status()
            existing = [
                x for x in self._check("list records", r.json()).get("result", [])
                if x.get("name", "").rstrip(".").lower() == fqdn.lower()
            ]
            body = {"type": "A", "name": fqdn, "content": ip, "ttl": 1, "proxied": proxied}
            if existing:
                rec_id = existing[0]["id"]
                r = await c.put(f"/zones/{zone_id}/dns_records/{rec_id}", json=body)
                r.raise_for_status()
                rec = self._check("update A record", r.json())["result"]
                return {"record": rec, "created": False}
            r = await c.post(f"/zones/{zone_id}/dns_records", json=body)
            r.raise_for_status()
            rec = self._check("create A record", r.json())["result"]
            return {"record": rec, "created": True}

    async def set_proxied(self, zone_id: str, record_id: str, record: dict, proxied: bool) -> dict:
        """Flip the orange cloud on an existing record (full-object PUT)."""
        body = {
            "type": record.get("type", "A"),
            "name": record.get("name"),
            "content": record.get("content"),
            "ttl": record.get("ttl", 1),
            "proxied": proxied,
        }
        async with self._client() as c:
            r = await c.put(f"/zones/{zone_id}/dns_records/{record_id}", json=body)
            r.raise_for_status()
            return self._check("update proxy state", r.json())["result"]
