"""Proton VPN client, wireguard rendering and server picking.

Ported from the ``proton-switcher`` project so the backend can create, delete
and rotate Proton WireGuard configs through the same API the web dashboard
uses:

  GET    /api/vpn/logicals
  GET    /api/vpn/v1/certificate/key/EC
  POST   /api/vpn/v1/certificate
  GET    /api/vpn/v1/certificate/all
  DELETE /api/vpn/v1/certificate

Auth is the Proton SRP handshake (username/password, no browser) with a
refresh token for ~30-day sessions; see ``proton_srp.py``.

This module holds only pure helpers + a thin sync client. Persistence lives
in ``models.ProtonAccount`` / ``models.ProtonConfig`` and the HTTP surface in
``routers/proton.py``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

PROTON_API_BASE = "https://account.protonvpn.com"
# The SRP handshake runs on the same host that serves the web dashboard.
PROTON_AUTH_BASE = "https://account.protonvpn.com"
# Must match the live web dashboard or the API returns 5003 "web page is out
# of date". Bump it whenever Proton ships a new dashboard.
DEFAULT_APP_VERSION = "web-vpn-settings@5.0.422.0"
DEFAULT_LOCALE = "en_US"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
# Proton rate-limits certificate creation; pause between key-gen and register.
KEY_THROTTLE_SECONDS = 5.0
# Refresh when fewer than 7 days remain (Proton sessions live ~30 days).
REFRESH_MARGIN_SECONDS = 7 * 24 * 3600
DEFAULT_EXPIRES_IN = 30 * 24 * 3600
# Old default that triggers API 5003 "web page is out of date".
STALE_APP_VERSIONS = {"web-vpn-settings@5.0.2.0"}


class ProtonError(RuntimeError):
    """Any Proton-side failure with a human-readable message."""


# --------------------------------------------------------------------------
# wireguard helpers
# --------------------------------------------------------------------------


def ed25519_priv_body_to_x25519(priv_body_b64: str) -> str:
    """Convert the API EC private-key body to a WireGuard PrivateKey.

    Proton returns an Ed25519 key as PKCS#8-ish base64; the last 32 bytes are
    the seed. Hash with SHA-512, clamp, first 32 bytes -> X25519 key.
    """
    raw = base64.b64decode(priv_body_b64)
    seed = raw[-32:]
    h = bytearray(hashlib.sha512(seed).digest()[:32])
    h[0] &= 0xF8
    h[31] &= 0x7F
    h[31] |= 0x40
    return base64.b64encode(bytes(h)).decode()


# Full-name / code -> ISO exit-country code. Keys are lowercase.
COUNTRY_ALIASES = {
    "nl": "NL", "netherlands": "NL", "holland": "NL",
    "us": "US", "usa": "US", "united states": "US", "united states of america": "US", "america": "US",
    "jp": "JP", "japan": "JP",
    "ro": "RO", "romania": "RO",
    "pl": "PL", "poland": "PL",
    "de": "DE", "germany": "DE", "deutschland": "DE",
    "ch": "CH", "switzerland": "CH", "schweiz": "CH",
    "ca": "CA", "canada": "CA",
    "mx": "MX", "mexico": "MX",
    "no": "NO", "norway": "NO",
    "sg": "SG", "singapore": "SG",
    "se": "SE", "sweden": "SE",
    "is": "IS", "iceland": "IS",
    "fr": "FR", "france": "FR",
    "uk": "UK", "gb": "GB", "united kingdom": "GB", "britain": "GB", "england": "GB",
    "es": "ES", "spain": "ES",
    "it": "IT", "italy": "IT",
    "be": "BE", "belgium": "BE",
    "dk": "DK", "denmark": "DK",
    "fi": "FI", "finland": "FI",
    "at": "AT", "austria": "AT",
    "cz": "CZ", "czechia": "CZ", "czech republic": "CZ", "czech": "CZ",
    "ie": "IE", "ireland": "IE",
    "pt": "PT", "portugal": "PT",
    "gr": "GR", "greece": "GR",
    "hu": "HU", "hungary": "HU",
    "sk": "SK", "slovakia": "SK",
    "si": "SI", "slovenia": "SI",
    "hr": "HR", "croatia": "HR",
    "rs": "RS", "serbia": "RS",
    "ua": "UA", "ukraine": "UA",
    "bg": "BG", "bulgaria": "BG",
    "ee": "EE", "estonia": "EE",
    "lv": "LV", "latvia": "LV",
    "lt": "LT", "lithuania": "LT",
    "au": "AU", "australia": "AU",
    "nz": "NZ", "new zealand": "NZ",
    "br": "BR", "brazil": "BR",
    "ar": "AR", "argentina": "AR",
    "cl": "CL", "chile": "CL",
    "co": "CO", "colombia": "CO",
    "kr": "KR", "south korea": "KR", "korea": "KR",
    "hk": "HK", "hong kong": "HK",
    "tw": "TW", "taiwan": "TW",
    "in": "IN", "india": "IN",
    "il": "IL", "israel": "IL",
    "za": "ZA", "south africa": "ZA",
}


def resolve_country_code(token: str) -> str:
    """Resolve 'netherlands' / 'NL' / 'nl' -> 'NL'. Raises ValueError if unknown."""
    t = (token or "").strip().lower()
    if t in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[t]
    if len(t) == 2 and t.isalpha():
        return t.upper()
    raise ValueError(
        f"Unknown country {token!r}. Use a 2-letter code (NL, US, DE, ...) "
        "or a full name (netherlands, germany, ...)."
    )


def resolve_country_list(raw: str) -> list[str]:
    """Parse a comma list of names/codes -> list of codes."""
    if not raw or not raw.strip():
        return []
    return [resolve_country_code(x) for x in raw.split(",") if x.strip()]


def infer_country_code(name: str) -> str | None:
    """Try to infer an exit-country code from a config name like 'netherlands'."""
    try:
        return resolve_country_code(name.strip())
    except ValueError:
        return None


def pick_best_server(
    logicals: list[dict],
    *,
    tier: int = 0,
    countries: list[str] | None = None,
) -> dict:
    """Pick the best server: enabled, matching tier/countries, lowest Score then Load."""
    wanted: set[str] = set()
    for c in countries or []:
        try:
            wanted.add(resolve_country_code(c))
        except ValueError:
            wanted.add(str(c).strip().upper())
    cands = []
    for s in logicals:
        if s.get("Status") not in (None, 1):
            continue
        try:
            if int(s.get("Tier", -1)) != int(tier):
                continue
        except (TypeError, ValueError):
            continue
        if wanted and s.get("EntryCountry", "").upper() not in wanted and s.get("ExitCountry", "").upper() not in wanted:
            continue
        if not s.get("Servers"):
            continue
        cands.append(s)
    if not cands:
        raise ProtonError(
            f"No servers match tier={tier} countries={sorted(wanted) or 'any'}. "
            "Try the server list to see what's available."
        )
    cands.sort(key=lambda s: (float(s.get("Score", 1e9)), float(s.get("Load", 100))))
    return cands[0]


def sanitize_config_name(name: str) -> str:
    """Trim to a safe config name (no whitespace/path chars, 64-char budget)."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]+", "", (name or ""))
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    return cleaned[:64]


def render_conf(
    *,
    client_priv_x25519: str,
    server_pubkey: str,
    entry_ip: str,
    server_name: str,
    key_label: str,
    port: int = 51820,
    address: str = "10.2.0.2/32",
    dns: str = "10.2.0.1",
) -> str:
    """Render the standard WireGuard .conf text (the same text the website shows)."""
    return (
        "[Interface]\n"
        f"# Key for {key_label}\n"
        f"PrivateKey = {client_priv_x25519}\n"
        f"Address = {address}\n"
        f"DNS = {dns}\n"
        "\n"
        "[Peer]\n"
        f"# {server_name}\n"
        f"PublicKey = {server_pubkey}\n"
        "AllowedIPs = 0.0.0.0/0\n"
        f"Endpoint = {entry_ip}:{port}\n"
    )


def suggest_outbound_tag(name: str) -> str:
    """Default xray outbound tag for a Proton config (e.g. 'netherlands' -> 'proton-netherlands')."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"proton-{base}" if base else "proton"


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------


@dataclass
class ProtonSession:
    uid: str = ""
    access_token: str = ""  # sent as the AUTH-<uid> cookie
    refresh_token: str = ""
    session_id: str = ""
    app_version: str = DEFAULT_APP_VERSION
    obtained_at: float = 0.0
    expires_in: int = 0
    username: str = ""
    auth_base: str = PROTON_AUTH_BASE

    @property
    def has_refresh(self) -> bool:
        return bool(self.refresh_token)

    @property
    def expires_at(self) -> float:
        if self.expires_in > 0 and self.obtained_at > 0:
            return self.obtained_at + self.expires_in
        return 0.0

    def seconds_left(self, now: float | None = None) -> float:
        exp = self.expires_at
        if exp <= 0:
            return -1.0  # unknown -> caller treats as non-expiring
        return exp - (now if now is not None else time.time())

    def needs_refresh(self, now: float | None = None) -> bool:
        if not self.has_refresh or self.expires_at <= 0:
            return False
        return self.seconds_left(now) < REFRESH_MARGIN_SECONDS

    def apply_refresh(
        self,
        access_token: str,
        refresh_token: str | None = None,
        expires_in: int | None = None,
        session_id: str | None = None,
        now: float | None = None,
    ) -> None:
        self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        if expires_in:
            self.expires_in = int(expires_in)
        if session_id:
            self.session_id = session_id
        self.obtained_at = now if now is not None else time.time()


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------


def _new_client(*, base: str, timeout: float, proxy: str | None, **kwargs: Any) -> httpx.Client:
    kwargs = {
        "base_url": base.rstrip("/"),
        "timeout": timeout,
        "verify": True,
        # requests followed redirects by default; keep that behavior.
        "follow_redirects": True,
        **kwargs,
    }
    if proxy:
        try:
            return httpx.Client(proxy=proxy, **kwargs)
        except TypeError:  # older httpx used `proxies`
            return httpx.Client(proxies=proxy, **kwargs)
    return httpx.Client(**kwargs)


def _post_json(client: httpx.Client, path: str, body: dict, *, headers: dict | None = None) -> dict:
    resp = client.post(path, content=json.dumps(body), headers=headers)
    try:
        data = resp.json()
    except ValueError:
        raise ProtonError(f"Non-JSON response from {path}: HTTP {resp.status_code} {resp.text[:200]}")
    if not isinstance(data, dict):
        raise ProtonError(f"Unexpected response from {path}: {str(data)[:200]}")
    code = data.get("Code", 1000 if resp.status_code == 200 else resp.status_code)
    if code == 8002:
        raise ProtonError("Incorrect username or password (8002).")
    if code == 2028 or data.get("Error") == "APIError":
        raise ProtonError(
            "Proton temporarily limited this account (2028, too many recent login "
            "attempts). Every attempt extends the lockout, so wait several hours "
            "and then try Login exactly once."
        )
    if code == 9001:
        raise ProtonError(
            "Proton demands human verification/CAPTCHA (9001), common from "
            "datacenter/VPN IPs. Sign in once at account.proton.me from a "
            "residential connection, or set a proxy in the Proton VPN settings."
        )
    if resp.status_code == 404:
        raise ProtonError(f"{path} not found (HTTP 404).")
    if resp.status_code != 200 or code not in (1000, 1001):
        raise ProtonError(
            f"Login request {path} failed: HTTP {resp.status_code} code={data.get('Code')}: "
            f"{str(data.get('Error', data))[:300]}"
        )
    return data


def _auth_headers(app_version: str) -> dict:
    return {
        "x-pm-appversion": app_version,
        "x-pm-locale": DEFAULT_LOCALE,
        "Accept": "application/vnd.protonmail.v1+json",
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
        "Origin": "https://account.protonvpn.com",
        "Referer": "https://account.protonvpn.com/login",
    }


_AUTH_PATHS = {
    "info": ("/api/core/v4/auth/info", "/api/auth/info"),
    "auth": ("/api/core/v4/auth", "/api/auth"),
    "2fa": ("/api/core/v4/auth/2fa", "/api/auth/2fa"),
}


def _post_auth(client: httpx.Client, which: str, body: dict, headers: dict) -> dict:
    last: Exception | None = None
    for path in _AUTH_PATHS[which]:
        try:
            return _post_json(client, path, body, headers=headers)
        except ProtonError as e:
            if "HTTP 404" in str(e):
                last = e
                continue
            raise
    raise last or ProtonError(f"Auth endpoint {which} not found on this API host.")


def _uid_from_cookies(client: httpx.Client) -> str:
    try:
        for cookie in client.cookies.jar:
            name = getattr(cookie, "name", "")
            if name.startswith("AUTH-") and len(name) > 5 and bool(getattr(cookie, "value", "")):
                return name[len("AUTH-"):]
    except Exception:
        pass
    return ""


def _bootstrap_session(client: httpx.Client, headers: dict) -> tuple[str, dict]:
    """Create the anonymous session the dashboard holds before login."""
    h = dict(headers)
    h["x-enforce-unauthsession"] = "true"
    data = _post_json(client, "/api/auth/v4/sessions", {}, headers=h)
    uid = str(data.get("UID", "") or "") or _uid_from_cookies(client)
    return uid, data


SUPPORTED_AUTH_VERSIONS = (3, 4)


def srp_login(
    username: str,
    password: str,
    *,
    app_version: str = DEFAULT_APP_VERSION,
    base: str = PROTON_API_BASE,
    auth_base: str = PROTON_AUTH_BASE,
    timeout: float = 30.0,
    totp: str | None = None,
    proxy: str | None = None,
) -> ProtonSession:
    """Full SRP handshake; returns an unsaved :class:`ProtonSession`."""
    from .proton_srp import SrpClient, extract_modulus

    username = (username or "").strip()
    if not username or not password:
        raise ProtonError("Username and password are required to log in.")

    headers = _auth_headers(app_version)
    with _new_client(base=auth_base, timeout=timeout, proxy=proxy) as client:
        anon_uid, anon_data = _bootstrap_session(client, headers)
        if anon_uid:
            headers["x-pm-uid"] = anon_uid
        anon_token = str(anon_data.get("AccessToken", "") or "")
        if anon_token:
            token_type = str(anon_data.get("TokenType", "") or "Bearer")
            headers["Authorization"] = f"{token_type} {anon_token}"

        info = _post_auth(client, "info", {"Username": username, "Intent": "Auto"}, headers)
        rotated = _uid_from_cookies(client)
        if rotated and rotated != headers.get("x-pm-uid"):
            headers["x-pm-uid"] = rotated

        version = int(info.get("Version", 4))
        if version not in SUPPORTED_AUTH_VERSIONS:
            raise ProtonError(
                f"Unsupported SRP auth version {version} (support 3/4). "
                "Legacy 2-password accounts must switch to single-password mode first."
            )
        try:
            modulus = extract_modulus(info["Modulus"])
            salt = base64.b64decode(info["Salt"])
            server_eph = base64.b64decode(info["ServerEphemeral"])
            srp_session = info["SRPSession"]
        except KeyError as e:
            raise ProtonError(f"/auth/info response missing field: {e}") from e
        except Exception as e:
            raise ProtonError(f"Could not parse /auth/info response: {e}") from e

        client_srp = SrpClient(password, modulus)
        try:
            proof = client_srp.answer_challenge(salt, server_eph)
        except ValueError as e:
            raise ProtonError(f"SRP challenge failed: {e}") from e

        auth = _post_auth(
            client,
            "auth",
            {
                "Username": username,
                "ClientEphemeral": base64.b64encode(client_srp.public_challenge).decode(),
                "ClientProof": base64.b64encode(proof).decode(),
                "SRPSession": srp_session,
                "PersistentCookies": 1,
            },
            headers,
        )
        try:
            server_proof = base64.b64decode(auth["ServerProof"])
        except KeyError as e:
            raise ProtonError(f"/auth response missing ServerProof (keys: {sorted(auth)})") from e
        if not client_srp.verify(server_proof):
            raise ProtonError("Server proof mismatch — wrong password or connection tampering.")

        if auth.get("TwoFactor"):
            twofa = auth.get("2FA", {}) or {}
            if not twofa.get("TOTP"):
                raise ProtonError("Account needs FIDO2/WebAuthn 2FA; only TOTP is supported here.")
            if not totp:
                raise ProtonError("Account needs a TOTP 2FA code — provide one and try again.")
            auth_headers = dict(headers)
            auth_headers["x-pm-uid"] = auth.get("UID", "")
            if auth.get("AccessToken"):
                auth_headers["Authorization"] = f"Bearer {auth['AccessToken']}"
            _post_auth(client, "2fa", {"TwoFactorCode": totp}, auth_headers)

    now = time.time()
    return ProtonSession(
        uid=str(auth.get("UID", "")),
        access_token=str(auth.get("AccessToken", "")),
        refresh_token=str(auth.get("RefreshToken", "")),
        session_id=str(auth.get("SessionID", auth.get("SessionId", auth.get("Session-Id", ""))) or ""),
        app_version=app_version,
        obtained_at=now,
        expires_in=int(auth.get("ExpiresIn", DEFAULT_EXPIRES_IN) or DEFAULT_EXPIRES_IN),
        username=username,
        auth_base=auth_base,
    )


def refresh_access_token(
    session: ProtonSession,
    *,
    base: str = PROTON_API_BASE,
    timeout: float = 30.0,
    proxy: str | None = None,
) -> ProtonSession:
    """Exchange RefreshToken for a new AccessToken (+ rotation). Mutates and returns `session`."""
    if not session.refresh_token:
        raise ProtonError("No refresh token stored — log in again.")

    headers = _auth_headers(session.app_version or DEFAULT_APP_VERSION)
    headers["x-pm-uid"] = session.uid
    body = {
        "ResponseType": "token",
        "GrantType": "refresh_token",
        "RefreshToken": session.refresh_token,
        "RedirectURI": "http://protonmail.ch",
    }
    hosts = [session.auth_base or PROTON_AUTH_BASE, base]
    paths = ("/api/auth/refresh", "/api/core/v4/auth/refresh")

    data: dict | None = None
    last: Exception | None = None
    for host in hosts:
        with _new_client(base=host, timeout=timeout, proxy=proxy) as client:
            for path in paths:
                try:
                    resp = client.post(path, content=json.dumps(body), headers=headers)
                    payload = resp.json()
                except ValueError as e:
                    last = ProtonError(f"Non-JSON refresh response from {host}{path}: {e}")
                    continue
                except Exception as e:  # network error -> try next
                    last = e
                    continue
                if resp.status_code == 404:
                    last = ProtonError(f"{host}{path} not found")
                    continue
                code = payload.get("Code", 1000 if resp.status_code == 200 else resp.status_code)
                if resp.status_code == 200 and code in (1000, 1001):
                    data = payload
                    break
                last = ProtonError(
                    f"Refresh failed: HTTP {resp.status_code} code={payload.get('Code')}: "
                    f"{str(payload.get('Error', payload))[:200]}"
                )
            if data:
                break
    if not data:
        raise last if isinstance(last, ProtonError) else ProtonError(
            f"Refresh failed: {last}. Log in again."
        )
    session.apply_refresh(
        access_token=str(data.get("AccessToken", session.access_token)),
        refresh_token=str(data.get("RefreshToken", "") or session.refresh_token),
        expires_in=int(data.get("ExpiresIn", 0) or 0),
        session_id=str(data.get("SessionID", data.get("SessionId", "")) or ""),
    )
    return session


# --------------------------------------------------------------------------
# Proton VPN API client
# --------------------------------------------------------------------------


class ProtonClient:
    """Minimal VPN-API client (logicals + certificate lifecycle)."""

    def __init__(
        self,
        session: ProtonSession,
        base: str = PROTON_API_BASE,
        *,
        timeout: float = 30.0,
        proxy: str | None = None,
    ):
        self.session = session
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._client = _new_client(
            base=self.base, timeout=timeout, proxy=proxy, headers=self._headers()
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "ProtonClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _headers(self, extra: dict | None = None) -> dict:
        s = self.session
        cookie = f"AUTH-{s.uid}={s.access_token}"
        if s.session_id:
            cookie += f"; Session-Id={s.session_id}"
        headers = {
            "x-pm-appversion": s.app_version or DEFAULT_APP_VERSION,
            "x-pm-uid": s.uid,
            "x-pm-locale": DEFAULT_LOCALE,
            "Accept": "application/vnd.protonmail.v1+json",
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": cookie,
        }
        if extra:
            headers.update(extra)
        return headers

    def _check(self, resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:2000]}
        if isinstance(data, dict) and data.get("Code") == 5003:
            raise ProtonError(
                f"HTTP {resp.status_code}: API reports a stale app version (5003). "
                "Update the app-version field in the Proton VPN settings."
            )
        if resp.status_code != 200:
            raise ProtonError(
                f"HTTP {resp.status_code} {resp.reason_phrase}: {json.dumps(data)[:400]} "
                "(tokens likely expired — press Login again)"
            )
        if isinstance(data, dict) and "Code" in data and data["Code"] not in (1000, 1001):
            raise ProtonError(f"Proton API error {data.get('Code')}: {data.get('Error', data)}")
        return data

    # -- servers --
    def get_logicals(self) -> list[dict]:
        resp = self._client.get("/api/vpn/logicals")
        if resp.status_code == 404:
            resp = self._client.get("/api/vpn/v1/logicals")
        data = self._check(resp)
        servers = data.get("LogicalServers", [])
        if not servers and "Servers" in data:
            servers = data["Servers"]
        return servers

    # -- keys / certificates --
    def get_ec_keypair(self) -> tuple[str, str, str]:
        """Return (private_pem_full, public_b64_body, private_b64_body)."""
        resp = self._client.get("/api/vpn/v1/certificate/key/EC")
        data = self._check(resp)
        try:
            priv_pem = data["PrivateKey"]
            pub_body = data["PublicKey"].split("\n")[1]
            priv_body = priv_pem.split("\n")[1]
        except (KeyError, IndexError) as e:
            raise ProtonError(f"Unexpected key response: {str(data)[:200]}") from e
        return priv_pem, pub_body, priv_body

    def register_config(
        self,
        server_name: str,
        entry_ip: str,
        server_pubkey: str,
        client_pubkey: str,
        device_name: str,
        *,
        platform: str = "Windows",
    ) -> dict:
        body = {
            "ClientPublicKey": client_pubkey,
            "Mode": "persistent",
            "DeviceName": device_name,
            "Features": {
                "peerName": server_name,
                "peerIp": entry_ip,
                "peerPublicKey": server_pubkey,
                "platform": platform,
                "SafeMode": False,
                "SplitTCP": True,
                "PortForwarding": False,
                "RandomNAT": False,
                "NetShieldLevel": 0,
            },
        }
        resp = self._client.post(
            "/api/vpn/v1/certificate",
            content=json.dumps(body),
            headers=self._headers({"Content-Type": "application/json"}),
        )
        return self._check(resp)

    def list_configs(self) -> list[dict]:
        resp = self._client.get(
            "/api/vpn/v1/certificate/all",
            params={"Mode": "persistent", "Offset": 0, "Limit": 100},
        )
        if resp.status_code == 404:
            resp = self._client.get("/api/vpn/v1/certificate")
        data = self._check(resp)
        for key in ("Certificates", "Certificate", "Configs", "Devices"):
            if isinstance(data.get(key), list):
                return data[key]
        if isinstance(data, list):
            return data
        return []

    def delete_config_by_serial(self, serial: str) -> int:
        resp = self._client.request(
            "DELETE",
            "/api/vpn/v1/certificate",
            content=json.dumps({"SerialNumber": serial}),
            headers=self._headers({"Content-Type": "application/json"}),
        )
        data = self._check(resp)
        return int(data.get("Count", 0) or 0)
