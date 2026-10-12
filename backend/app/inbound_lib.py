"""Builders for the vless/xhttp/reality inbound + core config (from CLI inbound.py)."""
from __future__ import annotations


def parse_sni(sni_input: str) -> tuple[str, str]:
    """Return (target, server_name). Default input is like 'icloud.com:443'."""
    s = (sni_input or "").strip() or "icloud.com:443"
    if ":" in s:
        host, _, port = s.rpartition(":")
        host = host.strip() or "icloud.com"
        port = port.strip() or "443"
        return f"{host}:{port}", host
    return f"{s}:443", s


def build_reality_inbound(
    tag: str,
    port: int,
    target: str,
    server_name: str,
    private_key: str,
    public_key: str,
    short_ids: list[str],
    decryption: str,
    encryption: str | None = None,
    flow: str = "xtls-rprx-vision",
    network: str = "xhttp",
) -> dict:
    """vless + reality inbound. network is "xhttp" (default) or "tcp"."""
    if network not in ("xhttp", "tcp"):
        raise ValueError("Reality network must be 'xhttp' or 'tcp'.")
    settings: dict = {"clients": [], "flow": flow, "decryption": decryption}
    if encryption is not None:
        settings["encryption"] = encryption
    stream: dict = {
        "network": network,
        "security": "reality",
        "realitySettings": {
            "target": target,
            "serverNames": [server_name],
            "privateKey": private_key,
            "shortIds": short_ids,
            "xver": 0,
            "fingerprint": "chrome",
            "publicKey": public_key,
        },
    }
    if network == "xhttp":
        stream["xhttpSettings"] = {"path": "/", "mode": "auto"}
    else:
        stream["tcpSettings"] = {}
    return {"tag": tag, "port": port, "protocol": "vless", "settings": settings, "streamSettings": stream}


def finalize_inbound(d: dict) -> dict:
    d = dict(d)
    d["settings"] = dict(d.get("settings", {}))
    return d


def build_mimic_inbound() -> dict:
    """Placeholder inbound exactly like the panel dashboard creates it.

    The panel rejects cores with zero inbounds ("config doesn't have
    inbounds"), so standalone registration seeds the core with this mimic
    Shadowsocks (port 1080, no clients). Delete it from the Inbounds tab
    once real inbounds exist.
    """
    return {
        "tag": "Shadowsocks TCP",
        "port": 1080,
        "protocol": "shadowsocks",
        "settings": {"clients": [], "network": "tcp,udp"},
        "streamSettings": {"network": "tcp", "security": "none", "tcpSettings": {}},
    }


def build_dashboard_config(inbounds: list) -> dict:
    """Full xray core config exactly like the panel dashboard creates it.

    The panel validates more than inbounds/outbounds — policy, observatory,
    log and routing sections must be present or create/modify returns 400.
    """
    return {
        "policy": {"levels": {"0": {"statsUserOnline": True}}},
        "burstObservatory": {
            "subjectSelector": ["DIRECT"],
            "pingConfig": {
                "destination": "https://www.google.com/generate_204",
                "connectivity": "https://www.google.com/generate_204",
                "interval": "1m",
                "sampling": 10,
                "timeout": "5s",
                "httpMethod": "HEAD",
            },
        },
        "log": {"loglevel": "info"},
        "inbounds": [finalize_inbound(i) for i in inbounds],
        "outbounds": [
            {"protocol": "freedom", "tag": "DIRECT"},
            {"protocol": "blackhole", "tag": "BLOCK"},
        ],
        "routing": {
            "rules": [{"ip": ["geoip:private"], "outboundTag": "BLOCK", "type": "field"}]
        },
    }


def build_core_config(inbound: dict, name: str = "") -> dict:
    return build_dashboard_config([inbound])


# Cloudflare only proxies these ports (dashboard rejects the rest for CF inbounds).
CF_PORTS = (443, 2053, 2083, 2087, 2096, 8443)


def build_cloudflare_tls_inbound(
    tag: str,
    port: int,
    domain: str,
    cert_lines: list[str],
    key_lines: list[str],
    decryption: str,
    encryption: str,
    flow: str = "xtls-rprx-vision",
    network: str = "xhttp",
) -> dict:
    """vless + tls inbound exactly like the panel dashboard creates it."""
    if int(port) not in CF_PORTS:
        raise ValueError(f"Port {port} is not proxied by Cloudflare. Use one of {list(CF_PORTS)}.")
    if network not in ("xhttp", "ws"):
        raise ValueError("Cloudflare network must be 'xhttp' or 'ws'.")
    domain = domain.strip().rstrip(".")
    stream: dict = {
        "network": network,
        "security": "tls",
        "tlsSettings": {
            "serverName": domain,
            "alpn": ["h2", "h3"],
            "fingerprint": "chrome",
            "minVersion": "1.2",
            "maxVersion": "1.3",
            "certificates": [
                {"certificate": list(cert_lines), "key": list(key_lines), "serveOnNode": False}
            ],
        },
    }
    if network == "xhttp":
        stream["xhttpSettings"] = {"host": domain, "path": "/", "mode": "auto"}
    else:
        stream["wsSettings"] = {"path": "/", "headers": {"Host": domain}}
    return {
        "tag": tag,
        "port": int(port),
        "protocol": "vless",
        "settings": {"clients": [], "flow": flow, "decryption": decryption, "encryption": encryption},
        "streamSettings": stream,
    }


def merge_inbound_into_config(existing_config: dict, inbound: dict) -> dict:
    """Append/replace inbound by tag in an existing core config dict."""
    cfg = dict(existing_config or {})
    inbounds = list(cfg.get("inbounds") or [])
    tag = inbound.get("tag")
    inbounds = [i for i in inbounds if i.get("tag") != tag]
    inbounds.append(finalize_inbound(inbound))
    cfg["inbounds"] = inbounds
    if not cfg.get("outbounds"):
        cfg["outbounds"] = [{"tag": "direct", "protocol": "freedom"}]
    return cfg


def remove_inbound_from_config(existing_config: dict, tag: str) -> dict:
    cfg = dict(existing_config or {})
    cfg["inbounds"] = [i for i in (cfg.get("inbounds") or []) if i.get("tag") != tag]
    if not cfg.get("outbounds"):
        cfg["outbounds"] = [{"tag": "direct", "protocol": "freedom"}]
    return cfg
