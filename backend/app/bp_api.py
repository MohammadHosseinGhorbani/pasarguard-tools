"""Async client for a BackPack web panel (Iran server).

Covers what the SSH/menu automation cannot do robustly: structured tunnel
inventory, cross-server actions, one-call pair creation of both ends, and
structured connection-test progress. Auth is a write-scope API token
(Settings -> Security -> API tokens on the BackPack panel); the panel TLS
cert is self-signed by default, hence the verify toggle.

Endpoint shapes follow BackPack v1.8.5+/v1.9 (form-encoded actions,
JSON creates). Anything the panel answers unexpectedly surfaces with the
response body attached.
"""
from __future__ import annotations

from typing import Any

import httpx


def _clean_body(text: str) -> str:
    text = (text or "").strip()
    return text[:3000]


class BackpackAPIError(RuntimeError):
    pass


class BackpackAPI:
    def __init__(self, base_url: str, token: str, verify: bool = False, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.verify = verify
        self.timeout = timeout
        if not self.base_url or not self.token:
            raise ValueError("BackPack panel URL and API token are both required.")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            verify=self.verify,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    @staticmethod
    def _check(action: str, r: httpx.Response) -> Any:
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = _clean_body(r.text)
            except Exception:
                pass
            detail = f"BackPack {action} failed: {exc.response.status_code}"
            if body and body not in str(exc):
                detail += f"\nPanel response: {body}"
            if r.status_code in (401, 403):
                detail += "\n(Check the API token: write scope, not expired.)"
            raise BackpackAPIError(detail) from exc
        ctype = r.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
        return {"raw": r.text}

    async def _get(self, action: str, path: str, params: dict | None = None) -> Any:
        try:
            async with self._client() as c:
                return self._check(action, await c.get(path, params=params))
        except BackpackAPIError:
            raise
        except Exception as exc:
            raise BackpackAPIError(f"BackPack {action} failed: {exc}") from exc

    async def _post_json(self, action: str, path: str, payload: dict, timeout: float | None = None) -> Any:
        try:
            if timeout is None:
                timeout = self.timeout
            async with httpx.AsyncClient(
                base_url=self.base_url, verify=self.verify, timeout=timeout,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            ) as c:
                return self._check(action, await c.post(path, json=payload))
        except BackpackAPIError:
            raise
        except Exception as exc:
            raise BackpackAPIError(f"BackPack {action} failed: {exc}") from exc

    async def _post_form(self, action: str, path: str, fields: dict, timeout: float | None = None) -> Any:
        try:
            if timeout is None:
                timeout = self.timeout
            async with httpx.AsyncClient(
                base_url=self.base_url, verify=self.verify, timeout=timeout,
                headers={"Authorization": f"Bearer {self.token}"},
            ) as c:
                return self._check(action, await c.post(path, data={k: v for k, v in fields.items() if v is not None}))
        except BackpackAPIError:
            raise
        except Exception as exc:
            raise BackpackAPIError(f"BackPack {action} failed: {exc}") from exc

    # -- inventory & actions ------------------------------------------------
    async def tunnels(self) -> Any:
        return await self._get("list tunnels", "/api/tunnels")

    async def tunnel_action(self, name: str, action: str, also_far_end: bool = False) -> Any:
        fields: dict = {"action": action, "name": name}
        if action == "delete" and also_far_end:
            fields["alsoFarEnd"] = "1"
        return await self._post_form("tunnel action", "/api/tunnel/action", fields, timeout=120.0)

    # -- create ---------------------------------------------------------------
    async def create_reverse(self, form: dict) -> Any:
        return await self._post_json("create tunnel", "/api/tunnel/create", form, timeout=300.0)

    async def create_direct(self, form: dict) -> Any:
        return await self._post_json("create direct tunnel", "/api/direct/create", form, timeout=300.0)

    async def direct_options(self) -> Any:
        return await self._get("direct options", "/api/direct/options")

    async def pair(self, payload: dict, timeout: float = 900.0) -> Any:
        """Create BOTH ends at once (Iran panel pushes the peer to its node)."""
        return await self._post_json("pair tunnel", "/api/node/pair", payload, timeout=timeout)

    # -- connection test --------------------------------------------------------
    async def conntest_start(self, host: str = "", preset: str = "turbo") -> Any:
        return await self._post_form(
            "start connection test", "/api/conntest",
            {"action": "start", "host": host, "preset": preset or "turbo"},
            timeout=60.0,
        )

    async def conntest_stop(self) -> Any:
        return await self._post_form("stop connection test", "/api/conntest", {"action": "stop"})

    async def conntest_poll(self) -> Any:
        return await self._get("poll connection test", "/api/conntest")

    # -- fleet --------------------------------------------------------------------
    async def nodes(self) -> Any:
        return await self._get("list managed servers", "/api/nodes")

    async def node_add(
        self, name: str, host: str, port: int = 22, user: str = "root",
        password: str = "", install: bool = True, timeout: float = 900.0,
    ) -> Any:
        return await self._post_form(
            "add managed server", "/api/nodes",
            {
                "action": "add", "name": name, "host": host, "port": str(port),
                "user": user, "password": password, "install": "1" if install else "0",
            },
            timeout=timeout,
        )
