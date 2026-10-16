"""Proton SRP-6a auth primitives (client side).

Ported from the ``proton-switcher`` project (``proton_switcher/srp.py``) so
this backend can log in with a Proton username/password without a browser.

Protocol sketch:
  1. POST auth/info {Username} -> {Version, Salt (b64), ServerEphemeral (b64),
     Modulus (PGP signed cleartext armor), SRPSession}
  2. Client picks secret `a`, sends A = g^a mod N (g=2).
  3. Client computes x = EHash(bcrypt(password, salt) || N), u = EHash(A || B),
     S = (B - k*v)^(a + u*x) with v = g^x, k = EHash(g || N).
  4. ClientProof M = EHash(A || B || S); server replies ServerProof which must
     equal EHash(A || M || S).

EHash is Proton's expanded hash: SHA512(msg + b'\\x00') || ... || SHA512(msg + b'\\x03')
(256 bytes). Integers are 256-byte little-endian on the wire.
"""
from __future__ import annotations

import base64
import hashlib
import os

WIDTH = 256  # SRP element size in bytes (2048-bit group)
GENERATOR = 2


def expanded_hash(data: bytes = b"") -> bytes:
    """Proton's 256-byte expanded hash (4x SHA-512 with domain separators)."""
    return b"".join(hashlib.sha512(data + bytes([i])).digest() for i in range(4))


class _Expander:
    """Hash object with update()/digest() interface over expanded_hash."""

    def __init__(self, initial: bytes = b""):
        self._buf = bytearray(initial)

    def update(self, data: bytes) -> None:
        self._buf += data

    def digest(self) -> bytes:
        return expanded_hash(bytes(self._buf))


def _bcrypt_prefix(salt: bytes) -> bytes:
    std = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    bcr = b"./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return base64.b64encode(salt).translate(bytes.maketrans(std, bcr))


def hash_password(password: bytes, salt: bytes, modulus_le: bytes) -> bytes:
    """Proton password hash: bcrypt(password, salt) then expanded hash.

    Returns 256-byte digest. `salt` is the raw server salt; internally
    `(salt + b'proton')[:16]` is used (bcrypt 2y, cost 10).
    """
    import bcrypt as _bcrypt

    s16 = (salt + b"proton")[:16]
    encoded = _bcrypt_prefix(s16)[:22]
    hashed = _bcrypt.hashpw(password, b"$2y$10$" + encoded)
    return expanded_hash(hashed + modulus_le)


def int_from_le(data: bytes) -> int:
    return int.from_bytes(data, "little")


def int_to_le(value: int, length: int = WIDTH) -> bytes:
    return value.to_bytes(length, "little")


def random_int(num_bytes: int = 32) -> int:
    raw = int_from_le(os.urandom(num_bytes))
    return raw | (1 << (num_bytes * 8 - 1))  # force top bit (fixed length)


def hash_ints(*parts: bytes) -> int:
    h = _Expander()
    for part in parts:
        h.update(part)
    return int_from_le(h.digest())


def extract_modulus(signed_armor_or_b64: str) -> bytes:
    """Extract raw modulus bytes from /auth/info `Modulus` field.

    The server sends a PGP SIGNED MESSAGE (cleartext armor). The payload is
    base64 in the cleartext section; the signature is not verified here
    (TLS protects transport — same tradeoff as the `RawSRPModulus` mode in
    Proton's Rust SDK). If the input has no armor header it is treated as
    raw base64.
    """
    text = signed_armor_or_b64.strip()
    if not text.startswith("-----"):
        return base64.b64decode(text)
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if "BEGIN PGP SIGNED MESSAGE" in ln)
    except StopIteration as e:
        raise ValueError("Unrecognized Modulus format (no PGP header)") from e
    # skip armor headers up to first blank line
    i = start + 1
    while i < len(lines) and lines[i].strip() != "":
        i += 1
    i += 1  # first cleartext line
    payload_lines = []
    for ln in lines[i:]:
        if ln.startswith("-----BEGIN PGP SIGNATURE-----"):
            break
        # dash-unescape per OpenPGP cleartext framework
        payload_lines.append(ln[2:] if ln.startswith("- ") else ln)
    payload = "".join(payload_lines).strip()
    if not payload:
        raise ValueError("Empty Modulus payload in PGP message")
    try:
        return base64.b64decode(payload)
    except Exception as e:
        raise ValueError(f"Modulus payload is not valid base64: {e}") from e


class SrpClient:
    """Client-side SRP-6a state for one login attempt."""

    def __init__(self, password: str, modulus: bytes):
        self._password = password.encode()
        self._modulus = int_from_le(modulus)
        self._modulus_le = int_to_le(self._modulus)
        self._secret = random_int(32)
        self._public = pow(GENERATOR, self._secret, self._modulus)
        self._multiplier = hash_ints(int_to_le(GENERATOR), self._modulus_le)
        self._server_public: int | None = None
        self._proof: bytes | None = None
        self._expected_server_proof: bytes | None = None
        self._authenticated = False

    @property
    def public_challenge(self) -> bytes:
        """A as 256-byte LE (b64-encode for ClientEphemeral)."""
        return int_to_le(self._public)

    def answer_challenge(self, salt: bytes, server_ephemeral: bytes) -> bytes:
        """Process server challenge; returns client proof M (send b64 as ClientProof)."""
        server_pub = int_from_le(server_ephemeral)
        if server_pub % self._modulus == 0:
            raise ValueError("Server sent an invalid SRP challenge (B mod N == 0)")
        scrambler = hash_ints(int_to_le(self._public), int_to_le(server_pub))
        if scrambler == 0:
            raise ValueError("Server sent an invalid SRP challenge (u == 0)")
        secret_exp = int_from_le(hash_password(self._password, salt, self._modulus_le))
        verifier = pow(GENERATOR, secret_exp, self._modulus)
        base = (server_pub - self._multiplier * verifier) % self._modulus
        shared = pow(base, self._secret + scrambler * secret_exp, self._modulus)
        session_key = int_to_le(shared)
        proof = _Expander()
        proof.update(int_to_le(self._public))
        proof.update(int_to_le(server_pub))
        proof.update(session_key)
        self._proof = proof.digest()
        expect = _Expander()
        expect.update(int_to_le(self._public))
        expect.update(self._proof)
        expect.update(session_key)
        self._expected_server_proof = expect.digest()
        self._server_public = server_pub
        return self._proof

    def verify(self, server_proof: bytes) -> bool:
        """Check server proof; returns True when mutual auth succeeded."""
        self._authenticated = (
            self._expected_server_proof is not None
            and self._expected_server_proof == server_proof
        )
        return self._authenticated
