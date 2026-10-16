"""WireGuard .conf -> xray outbound conversion.

Faithful port of the standalone "WireGuard config converter" HTML tool:
same parse rules (single [Interface] + [Peer] blocks, `# name:` comments,
inline `#` comments stripped) and same xray settings shape
(ForceIP, noKernelTun, default reserved [0,0,0]).

Each replacement box holds exactly ONE single-peer config; anything else
is a validation error, never a silent partial conversion.
"""
from __future__ import annotations

import re


def parse_wg_conf(text: str) -> list[dict]:
    """Parse .conf text into [{name, iface, peers}]. Keys are lowercased."""
    groups: list[dict] = []
    g: dict | None = None
    cur: dict | None = None
    pend: str | None = None

    def _mk() -> None:
        nonlocal g, cur, pend
        g = {"name": pend, "iface": {}, "peers": []}
        pend = None
        groups.append(g)

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(";"):
            m = re.match(r"^[#;]\s*name\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if m:
                pend = m.group(1).strip()
            continue
        m = re.match(r"^\[(.+)\]$", re.sub(r"\s*[#;].*$", "", line))
        if m:
            kind = m.group(1).strip().lower()
            if kind == "interface":
                _mk()
                cur = g["iface"] if g is not None else None
            elif kind == "peer":
                if g is None:
                    _mk()
                assert g is not None
                cur = {}
                g["peers"].append(cur)
            else:
                cur = None
            continue
        i = line.find("=")
        if i < 1 or cur is None:
            continue
        cur[line[:i].strip().lower()] = re.sub(r"\s+#.*$", "", line[i + 1 :]).strip()
    return groups


def _split_endpoint(ep: str) -> tuple[str, str] | None:
    if not ep:
        return None
    m = re.match(r"^\[(.+)\]:(\d+)$", ep)
    if m:
        return ("[" + m.group(1) + "]", m.group(2))
    i = ep.rfind(":")
    if i < 1:
        return None
    return (ep[:i], ep[i + 1 :])


def _str_list(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def wg_conf_to_outbound(tag: str, conf_text: str, reserved: list[int] | None = None) -> dict:
    """Convert one single-peer .conf into an xray wireguard outbound dict.

    Raises ValueError with a human message on any problem.
    """
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("Outbound name is required.")
    groups = parse_wg_conf(conf_text)
    if len(groups) != 1:
        raise ValueError(
            f"Outbound '{tag}': paste exactly one WireGuard config "
            f"(found {len(groups)} [Interface] blocks)."
        )
    g = groups[0]
    if len(g["peers"]) != 1:
        raise ValueError(
            f"Outbound '{tag}': paste a config with exactly one [Peer] "
            f"(found {len(g['peers'])})."
        )
    iface = g["iface"]
    p = g["peers"][0]
    ep = _split_endpoint((p.get("endpoint") or "").strip())
    missing = [
        label
        for val, label in (
            (iface.get("privatekey"), "PrivateKey"),
            (p.get("publickey"), "PublicKey"),
            (ep, "Endpoint"),
        )
        if not val
    ]
    if missing:
        raise ValueError(f"Outbound '{tag}' is missing {', '.join(missing)}.")
    assert ep is not None

    if reserved is None:
        reserved = [0, 0, 0]
    if (
        len(reserved) != 3
        or any(not isinstance(n, int) or n < 0 or n > 255 for n in reserved)
    ):
        raise ValueError(f"Outbound '{tag}': reserved must be three bytes 0-255.")

    st: dict = {"secretKey": iface["privatekey"]}
    addr = _str_list(iface.get("address", ""))
    if addr:
        st["address"] = addr
    try:
        mtu = int((iface.get("mtu") or "").strip())
    except (ValueError, AttributeError):
        mtu = 0
    if mtu:
        st["mtu"] = mtu
    dns = ",".join(_str_list(iface.get("dns", "")))
    if dns:
        st["DNS"] = dns
    st["domainStrategy"] = "ForceIP"
    st["noKernelTun"] = True
    st["reserved"] = reserved

    peer: dict = {"endpoint": ep[0] + ":" + ep[1], "publicKey": p["publickey"]}
    if p.get("presharedkey"):
        peer["preSharedKey"] = p["presharedkey"]
    ips = _str_list(p.get("allowedips", ""))
    peer["allowedIPs"] = ips if ips else ["0.0.0.0/0", "::/0"]
    try:
        keep = int((p.get("persistentkeepalive") or "").strip())
    except (ValueError, AttributeError):
        keep = 0
    if keep:
        peer["keepAlive"] = keep
    st["peers"] = [peer]
    return {"protocol": "wireguard", "tag": tag, "settings": st}
