"""Let's Encrypt certificate handling on the target server via SSH (from CLI certs.py)."""
from __future__ import annotations

from .ssh_service import SshSession

LE_LIVE = "/etc/letsencrypt/live"


def check_certbot(ssh: SshSession) -> str | None:
    """Return certbot version output, or None when not installed."""
    for cmd in ("certbot --version", "/usr/bin/certbot --version"):
        rc, out, err = ssh.run_as_root(cmd, timeout=30.0)
        combined = (out or err or "").strip()
        if rc == 0 and "certbot" in combined.lower():
            return combined.splitlines()[0]
    return None


def port80_busy(ssh: SshSession) -> str | None:
    """Return a hint when something already listens on port 80 (breaks --standalone)."""
    rc, out, _ = ssh.run_as_root(
        "ss -ltn 2>/dev/null | grep -w ':80' || netstat -ltn 2>/dev/null | grep -w ':80' || true",
        timeout=30.0,
    )
    out = (out or "").strip()
    return out or None


def install_certbot(ssh: SshSession, timeout: float = 300.0) -> None:
    """Install certbot via apt (Debian/Ubuntu). Raises on failure."""
    rc, _, _ = ssh.run_as_root("command -v apt-get", timeout=30.0)
    if rc != 0:
        raise RuntimeError(
            "certbot is missing and this is not a Debian/Ubuntu host (no apt-get). "
            "Install certbot manually, then re-run."
        )
    rc, out, err = ssh.run_as_root("apt-get update && apt-get install -y certbot", timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"apt-get install certbot failed: {(err or out).strip()[-1500:]}")


def obtain_cert(ssh: SshSession, domain: str, email: str = "", timeout: float = 300.0) -> str:
    """Run `certbot certonly --standalone -d {domain}` non-interactively."""
    domain = domain.strip().rstrip(".")
    mail_part = f"--email {email.strip()}" if email.strip() else "--register-unsafely-without-email"
    cmd = (
        f"certbot certonly --standalone -d {domain} --non-interactive "
        f"--agree-tos {mail_part}"
    )
    rc, out, err = ssh.run_as_root(cmd, timeout=timeout)
    combined = (out or "") + ("\n" + err if err else "")
    if rc != 0:
        raise RuntimeError(
            f"certbot failed for {domain} (is the A record propagated and port 80 reachable?):\n"
            + "\n".join(combined.strip().splitlines()[-20:])
        )
    return combined


def cert_still_valid(ssh: SshSession, domain: str) -> bool:
    """True when a non-expired cert already exists (reruns skip issuance)."""
    domain = domain.strip().rstrip(".")
    rc, _, _ = ssh.run_as_root(
        f"test -f {LE_LIVE}/{domain}/fullchain.pem "
        f"&& openssl x509 -in {LE_LIVE}/{domain}/fullchain.pem -noout -checkend 0",
        timeout=30.0,
    )
    return rc == 0


def read_pem_lines(ssh: SshSession, path: str) -> list[str]:
    """Read a PEM file and return its lines (dashboard stores cert/key as line arrays)."""
    text = ssh.read_file(path)
    lines = text.replace("\r\n", "\n").strip().split("\n")
    if not any("BEGIN" in line for line in lines):
        raise RuntimeError(f"{path} does not look like a PEM file.")
    return lines


def get_domain_cert_lines(ssh: SshSession, domain: str) -> tuple[list[str], list[str]]:
    """Return (fullchain_lines, privkey_lines) for a certbot-issued domain."""
    domain = domain.strip().rstrip(".")
    cert = read_pem_lines(ssh, f"{LE_LIVE}/{domain}/fullchain.pem")
    key = read_pem_lines(ssh, f"{LE_LIVE}/{domain}/privkey.pem")
    return cert, key
