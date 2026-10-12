"""pg-node install + credential collection over SSH (from CLI node_setup.py).

No prints; install returns (ok, tail-output) and raises only for
programming errors (bad ports). Callers decide how to surface failures.
"""
from __future__ import annotations

import re

from .ssh_service import SshSession

PG_NODE_INSTALL_CMD = (
    'bash -c "$(curl -sL https://github.com/PasarGuard/scripts/raw/main/pg-node.sh)" @ install'
)
INSTALL_DEFAULT_SERVICE_PORT = 62050
INSTALL_DEFAULT_API_PORT = 62051

ENV_FILE = "/opt/pg-node/.env"
CERT_FILE = "/var/lib/pg-node/certs/ssl_cert.pem"


def check_pg_node_status(ssh: SshSession) -> tuple[bool, str]:
    """Run `pg-node status`. Returns (is_up, combined_output)."""
    for cmd in ("pg-node status", "/usr/local/bin/pg-node status"):
        rc, out, err = ssh.run(cmd, timeout=30.0)
        combined = (out or "") + ("\n" + err if err else "")
        if rc == 0:
            low = combined.lower()
            if "running" in low or "up" in low:
                return True, combined
            return False, combined
        if "not found" not in combined.lower() and "no such" not in combined.lower():
            return False, combined
    return False, "pg-node command not found"


def get_occupied_ports(ssh: SshSession) -> set[int]:
    """TCP ports currently listened on (ss, else netstat). Empty set on failure."""
    rc, out, _ = ssh.run_as_root(
        "(ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -oE ':[0-9]+$' || true"
    )
    if rc != 0:
        return set()
    ports: set[int] = set()
    for line in (out or "").splitlines():
        m = re.search(r":(\d+)\s*$", line)
        if m:
            ports.add(int(m.group(1)))
    return ports


def pick_free_port(
    occupied: set[int], preferred: int, reserved: set[int] = frozenset(), tries: int = 200
) -> int:
    """First free port at/above preferred, skipping occupied + reserved."""
    port = preferred
    for _ in range(tries):
        if port not in occupied and port not in reserved:
            return port
        port += 1
    raise RuntimeError(f"No free port found near {preferred}.")


def install_pg_node(
    ssh: SshSession,
    timeout: float = 600.0,
    service_port: int | None = None,
    api_port: int | None = None,
) -> tuple[bool, str, dict]:
    """Install pg-node via the official script. Returns (ok, tail, ports).

    When ports are not given they are auto-picked (defaults 62050/62051,
    bumping past occupied ones like the CLI did).
    """
    occupied = get_occupied_ports(ssh)
    if service_port is None:
        service_port = pick_free_port(occupied, INSTALL_DEFAULT_SERVICE_PORT)
    if api_port is None:
        api_port = pick_free_port(occupied, INSTALL_DEFAULT_API_PORT, reserved={service_port})
    cmd = (
        f"{PG_NODE_INSTALL_CMD} -y --self-signed --use-grpc "
        f"--service-port {service_port} --api-port {api_port} --install-service"
    )
    rc, out, err = ssh.run_as_root(cmd, timeout=timeout)
    combined = (out or "") + ("\n" + err if err else "")
    tail = "\n".join(combined.strip().splitlines()[-25:])
    info = {"service_port": service_port, "api_port": api_port, "command": f"sudo {cmd}"}
    return (rc == 0), tail, info


def parse_env_content(content: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def get_node_credentials(ssh: SshSession) -> dict[str, str]:
    """Read API_KEY / SERVICE_PORT / API_PORT / SERVICE_PROTOCOL + server CA cert."""
    env_raw = ssh.read_file(ENV_FILE)
    env = parse_env_content(env_raw)
    cert = ssh.read_file(CERT_FILE)

    api_key = env.get("API_KEY", "").strip()
    service_port = env.get("SERVICE_PORT", "62050").strip() or "62050"
    proto = (env.get("SERVICE_PROTOCOL", "grpc") or "grpc").strip().strip('"').strip("'").lower()
    proto = "rest" if "rest" in proto else "grpc"
    if not api_key:
        raise RuntimeError(f"API_KEY not found in {ENV_FILE}")
    if not cert or "BEGIN CERTIFICATE" not in cert:
        raise RuntimeError(f"valid certificate not found in {CERT_FILE}")
    m = re.search(r"\d+", service_port)
    port = int(m.group(0)) if m else 62050
    m2 = re.search(r"\d+", env.get("API_PORT", "") or "")
    api_port = int(m2.group(0)) if m2 else port + 1
    return {
        "api_key": api_key,
        "service_port": str(port),
        "api_port": str(api_port),
        "protocol": proto,
        "server_ca": cert,
    }
