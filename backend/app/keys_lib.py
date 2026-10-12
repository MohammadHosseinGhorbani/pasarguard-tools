"""Key generation for REALITY (x25519) and VLESS encryption (from CLI keys.py).

Tries remote `xray` binary on the target server first, falls back to local
X25519 generation. No prints; returns (value, value, source) tuples.
"""
from __future__ import annotations

import base64
import re
import secrets

from .ssh_service import SshSession

_B64_43 = r"[A-Za-z0-9_-]{43}"


def b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_x25519_pair() -> tuple[str, str]:
    """Return (private_b64, public_b64) using local cryptography lib."""
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    except ImportError as exc:
        raise RuntimeError("cryptography package is required for local key generation") from exc
    priv = X25519PrivateKey.generate()
    pub = priv.public_key()
    from cryptography.hazmat.primitives import serialization

    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return b64url_nopad(priv_raw), b64url_nopad(pub_raw)


def generate_short_id(nbytes: int = 8) -> str:
    return secrets.token_hex(nbytes)


def generate_short_ids(count: int = 4, nbytes: int = 8) -> list[str]:
    """Panel dashboard creates 4 shortIds per Reality inbound — match that."""
    return [secrets.token_hex(nbytes) for _ in range(count)]


def _parse_x25519_output(output: str) -> tuple[str, str] | None:
    priv = pub = None
    m = re.search(r"Private[Kk]ey\s*:\s*(" + _B64_43 + r")", output)
    if m:
        priv = m.group(1)
    for label in ("PublicKey", "Public Key", "Password"):
        m = re.search(rf"{label}\s*:\s*({_B64_43})", output)
        if m:
            pub = m.group(1)
            break
    if priv and pub:
        return priv, pub
    tokens = re.findall(_B64_43, output)
    if len(tokens) >= 2:
        return tokens[0], tokens[1]
    return None


def _parse_vlessenc_output(output: str) -> tuple[str, str] | None:
    dec = re.findall(r'"decryption"\s*:\s*"([^"]+)"', output)
    enc = re.findall(r'"encryption"\s*:\s*"([^"]+)"', output)
    if dec and enc:
        return dec[0], enc[0]
    return None


def remote_x25519(ssh: SshSession, xray_path: str = "xray") -> tuple[str, str] | None:
    for cmd in (f"{xray_path} x25519", "/usr/local/bin/xray x25519"):
        rc, out, _ = ssh.run_as_root(cmd, timeout=30.0)
        if rc == 0 and out:
            parsed = _parse_x25519_output(out)
            if parsed:
                return parsed
    return None


def remote_vlessenc(ssh: SshSession, xray_path: str = "xray") -> tuple[str, str] | None:
    for cmd in (f"{xray_path} vlessenc", "/usr/local/bin/xray vlessenc"):
        rc, out, _ = ssh.run_as_root(cmd, timeout=30.0)
        if rc == 0 and out:
            parsed = _parse_vlessenc_output(out)
            if parsed:
                return parsed
    return None


def build_vless_encryption_local() -> tuple[str, str]:
    """Local fallback: mlkem768x25519plus.native.600s.<priv> / .0rtt.<pub>."""
    priv, pub = generate_x25519_pair()
    return f"mlkem768x25519plus.native.600s.{priv}", f"mlkem768x25519plus.native.0rtt.{pub}"


def get_reality_keys(ssh: SshSession) -> tuple[str, str, str]:
    """Return (privateKey, publicKey, source) trying remote first."""
    remote = remote_x25519(ssh)
    if remote:
        return remote[0], remote[1], "remote:xray x25519"
    priv, pub = generate_x25519_pair()
    return priv, pub, "local:X25519"


def get_vless_encryption(ssh: SshSession) -> tuple[str, str, str]:
    """Return (decryption, encryption, source) trying remote first."""
    remote = remote_vlessenc(ssh)
    if remote:
        return remote[0], remote[1], "remote:xray vlessenc"
    dec, enc = build_vless_encryption_local()
    return dec, enc, "local:X25519"
