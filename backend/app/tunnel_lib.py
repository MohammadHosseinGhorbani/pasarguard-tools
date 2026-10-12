"""BackPack tunnel primitives + test/create/delete flows (no prints, no prompts).

Ported from the pasarguard-tools `tunnel` branch (tunnel.py + tunnel_flow.py),
adapted for the web backend: progress goes to a `log(str)` callback, failures
raise RuntimeError with transcript tails. Interactive BackPack menus are driven
expect-style over piped (non-PTY) exec channels.
"""
from __future__ import annotations

import json
import re
import shlex
import time

from .ssh_service import SshSession

INSTALL_URL = "https://raw.githubusercontent.com/AminMGMT/BackPack/main/install.sh"
INSTALL_CMD = (
    "curl -fsSL " + INSTALL_URL + " -o /tmp/bp-install.sh "
    "&& BP_HELPERS=naive,xray bash /tmp/bp-install.sh; "
    "rc=$?; rm -f /tmp/bp-install.sh; exit $rc"
)

PROMPT_TIMEOUT = 120.0
LINK_TIMEOUT = 300.0
TEST_TIMEOUT = 1500.0
INSTALL_TIMEOUT = 900.0
APPLY_TIMEOUT = 900.0
CLOSE_TIMEOUT = 120.0

LINK_RE = re.compile(r"backpack://\S+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# (family_no, variant_no, value) — ChooseOpt menus are 1-based, main menu 0-10.
TRANSPORTS: list[dict] = [
    {"family": "TCP", "family_no": 1, "label": "TCP", "value": "tcp", "variant_no": 1, "managed": False},
    {"family": "TCP", "family_no": 1, "label": "TCP Mux", "value": "tcpmux", "variant_no": 2, "managed": False},
    {"family": "TCP", "family_no": 1, "label": "TCP + Stealth", "value": "stealth", "variant_no": 3, "managed": False},
    {"family": "TCP", "family_no": 1, "label": "TCP + PCK", "value": "pck", "variant_no": 4, "managed": False},
    {"family": "UDP", "family_no": 2, "label": "UDP", "value": "udp", "variant_no": 1, "managed": False},
    {"family": "UDP", "family_no": 2, "label": "UDP + KCP + FEC", "value": "kcp", "variant_no": 2, "managed": False},
    {"family": "UDP", "family_no": 2, "label": "UDP + QUIC", "value": "quic", "variant_no": 3, "managed": False},
    {"family": "WebSocket", "family_no": 3, "label": "WS", "value": "ws", "variant_no": 1, "managed": False},
    {"family": "WebSocket", "family_no": 3, "label": "WS Mux", "value": "wsmux", "variant_no": 2, "managed": False},
    {"family": "WebSocket", "family_no": 3, "label": "WSS", "value": "wss", "variant_no": 3, "managed": False},
    {"family": "WebSocket", "family_no": 3, "label": "WSS Mux", "value": "wssmux", "variant_no": 4, "managed": False},
    {"family": "HTTPS", "family_no": 4, "label": "Naive / HTTP2", "value": "naive", "variant_no": 1, "managed": True},
    {"family": "HTTPS", "family_no": 4, "label": "XHTTP / TLS", "value": "xhttp", "variant_no": 2, "managed": True},
    {"family": "HTTPS", "family_no": 4, "label": "REALITY / Vision", "value": "reality", "variant_no": 3, "managed": True},
]

TRANSPORT_BY_VALUE = {t["value"]: t for t in TRANSPORTS}

# Direct (L3) carriers in CLI menu order (1-based). Spoofing carriers need
# route-specific interactive answers (even upstream's panel refuses them),
# so automation supports the first four only.
DIRECT_CARRIERS: list[dict] = [
    {"label": "xDi", "value": "xdi", "no": 1, "needs_root": True},
    {"label": "PCK", "value": "pck", "no": 2, "needs_root": True},
    {"label": "UDP", "value": "udp", "no": 3, "needs_root": False},
    {"label": "Quic", "value": "quic", "no": 4, "needs_root": False},
]

DIRECT_CARRIER_BY_VALUE = {c["value"]: c for c in DIRECT_CARRIERS}


def strip_ansi(text: str) -> str:
    text = ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_link(text: str) -> str | None:
    for line in strip_ansi(text).splitlines():
        line = line.strip()
        if line.startswith("backpack://"):
            return line.split()[0].strip("\"'(),;")
    m = LINK_RE.search(strip_ansi(text))
    if m:
        return m.group(0).strip("\"'(),;")
    return None


def _rx(pat: str) -> "re.Pattern[str]":
    return re.compile(pat, re.IGNORECASE)


def check_backpack(ssh: SshSession) -> str | None:
    stdin, stdout, stderr = ssh.client.exec_command(
        "command -v backpack && backpack -v", timeout=30.0
    )
    rc = stdout.channel.recv_exit_status()
    out = (stdout.read().decode(errors="replace") or "").strip()
    if rc == 0 and out:
        return out.splitlines()[-1].strip()
    return None


def resolve_sudo(ssh: SshSession) -> str:
    """'none' | 'plain' (passwordless sudo) | 'stdin' (password first)."""
    if ssh.target.username == "root":
        return "none"
    stdin, stdout, stderr = ssh.client.exec_command("sudo -n true", timeout=15.0)
    if stdout.channel.recv_exit_status() == 0:
        return "plain"
    if ssh.target.password:
        stdin2, stdout2, stderr2 = ssh.client.exec_command("sudo -S -p '' true", timeout=15.0)
        try:
            stdin2.write(ssh.target.password + "\n")
            stdin2.flush()
        except Exception:
            pass
        if stdout2.channel.recv_exit_status() == 0:
            return "stdin"
        raise RuntimeError("Sudo password was rejected by the server.")
    raise RuntimeError("This step needs root: reconnect as root or store the sudo password.")


def root_prefix(mode: str) -> str:
    if mode == "none":
        return ""
    if mode == "plain":
        return "sudo "
    return "sudo -S -p '' "


def sudo_stdin_lines(ssh: SshSession, mode: str) -> tuple[str, ...]:
    return (ssh.target.password,) if mode == "stdin" and ssh.target.password else ()


def exec_streaming(ssh, command, timeout, stdin_lines=(), on_text=None) -> tuple[int, str]:
    chan = ssh.client.get_transport().open_session()
    try:
        chan.settimeout(30.0)
        chan.exec_command(command)
        for line in stdin_lines:
            chan.send(line + "\n")
        chunks: list[bytes] = []
        start = time.monotonic()
        while True:
            new_data = b""
            if chan.recv_ready():
                new_data += chan.recv(65536)
            if chan.recv_stderr_ready():
                new_data += chan.recv_stderr(65536)
            if new_data:
                chunks.append(new_data)
                if on_text is not None:
                    cleaned = strip_ansi(new_data.decode(errors="replace"))
                    if cleaned:
                        on_text(cleaned)
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
            if time.monotonic() - start > timeout:
                raise TimeoutError(
                    f"command timed out after {int(timeout)}s: {command[:120]}\n"
                    f"--- partial output ---\n{b''.join(chunks).decode(errors='replace')[-3000:]}"
                )
            time.sleep(0.2)
        rc = chan.recv_exit_status()
        return rc, b"".join(chunks).decode(errors="replace")
    finally:
        chan.close()


def ensure_backpack(ssh: SshSession, log) -> None:
    ver = check_backpack(ssh)
    if ver:
        log(f"backpack present: {ver}")
        return
    log("backpack not found — installing (with naive,xray helpers, may take minutes)...")
    mode = resolve_sudo(ssh)
    rc, out = exec_streaming(
        ssh, root_prefix(mode) + INSTALL_CMD, INSTALL_TIMEOUT,
        sudo_stdin_lines(ssh, mode), on_text=log,
    )
    tail = "\n".join(strip_ansi(out).strip().splitlines()[-15:])
    if rc != 0:
        raise RuntimeError(f"BackPack install failed:\n{tail}")
    ver = check_backpack(ssh)
    if not ver:
        raise RuntimeError("Installer finished but `backpack` is still not on PATH.")
    log(f"installed: {ver}")


class InteractiveSession:
    """Piped exec channel driven expect-style (fresh buffer resets per hit)."""

    def __init__(self, ssh, command, stdin_lines=(), on_text=None):
        self.chan = ssh.client.get_transport().open_session()
        self.chan.settimeout(30.0)
        self.chan.exec_command(command)
        self._fresh = ""
        self.chunks: list[bytes] = []
        self._on_text = on_text
        for line in stdin_lines:
            self.send(line)

    def send(self, line: str) -> None:
        data = (line + "\n").encode()
        while data:
            try:
                n = self.chan.send(data)
            except Exception as exc:
                raise RuntimeError(f"SSH channel send failed: {exc}")
            if n <= 0:
                raise RuntimeError("SSH channel closed while sending input.")
            data = data[n:]

    def _pump(self) -> bool:
        new_data = b""
        if self.chan.recv_ready():
            new_data += self.chan.recv(65536)
        if self.chan.recv_stderr_ready():
            new_data += self.chan.recv_stderr(65536)
        if new_data:
            self.chunks.append(new_data)
            cleaned = strip_ansi(new_data.decode(errors="replace"))
            self._fresh += cleaned
            if cleaned and self._on_text is not None:
                self._on_text(cleaned)
        return (
            self.chan.exit_status_ready()
            and not self.chan.recv_ready()
            and not self.chan.recv_stderr_ready()
        )

    def expect(self, patterns: list[tuple[str, "re.Pattern[str]"]], timeout: float) -> str:
        start = time.monotonic()
        while True:
            done = self._pump()
            for name, rx in patterns:
                if rx.search(self._fresh):
                    self._fresh = ""
                    return name
            if done:
                raise RuntimeError(
                    "Remote process exited while waiting for: "
                    + ", ".join(n for n, _ in patterns)
                    + "\n--- recent output ---\n" + self._fresh[-2000:]
                )
            if time.monotonic() - start > timeout:
                raise RuntimeError(
                    "Timed out waiting for: " + ", ".join(n for n, _ in patterns)
                    + f" ({int(timeout)}s)\n--- recent output ---\n" + self._fresh[-2000:]
                )
            time.sleep(0.2)

    def wait_closed(self, timeout: float):
        start = time.monotonic()
        while True:
            if self._pump():
                try:
                    return self.chan.recv_exit_status()
                except Exception:
                    return None
            if time.monotonic() - start > timeout:
                return None
            time.sleep(0.5)

    @property
    def transcript(self) -> str:
        return strip_ansi(b"".join(self.chunks).decode(errors="replace"))

    def set_on_text(self, cb) -> None:
        self._on_text = cb

    def close(self) -> None:
        try:
            self.chan.close()
        except Exception:
            pass


IP_DEFAULT_RE = re.compile(r"\[([^\]\r\n]{0,64})\]")


def prompt_default_from(transcript: str) -> str:
    found = IP_DEFAULT_RE.findall(transcript)
    return found[-1].strip() if found else ""


def _safe_send(sess: InteractiveSession, value: str) -> bool:
    """Send, tolerating an already-closed channel. Returns False if it failed."""
    try:
        sess.send(value)
        return True
    except Exception:
        return False


def exit_backpack_menu(sess: InteractiveSession, log=None) -> None:
    """End a menu session by closing the channel.

    BackPack's menu reader treats EOF as "nobody left" and returns cleanly,
    so this always works — no "10", no extra Enters the menu can misread
    as Invalid option. Drains trailing output first.
    """
    say = log or (lambda text: None)
    try:
        sess.wait_closed(5)
    except Exception:
        pass
    say("closing menu session...")
    sess.close()


def _tail(text: str, n: int = 30) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-n:])


def _before_final_prompt(text: str) -> str:
    idx = text.lower().rfind("press enter to continue")
    return text[:idx] if idx >= 0 else text


FAIL_MARKERS = _rx(r"never checked in|stopped\.|connection refused|invalid choice|error")


def drive_iran_test_menu(sess: InteractiveSession, iran_ip: str) -> str:
    """Menu 0 -> Iran -> IP -> Turbo preset. Returns the test link."""
    sess.expect([("menu", _rx("connection test"))], PROMPT_TIMEOUT)
    sess.send("0")
    sess.expect([("role", _rx("this server is"))], PROMPT_TIMEOUT)
    sess.send("1")
    sess.expect([("ip", _rx(r"this server.{0,20}ip"))], PROMPT_TIMEOUT)
    sess.send(iran_ip or "")
    hit = sess.expect(
        [("preset", _rx("preset")), ("noaddr", _rx("an address is required"))],
        LINK_TIMEOUT,
    )
    if hit == "noaddr":
        raise RuntimeError("BackPack refused the empty IP and detected none. Enter the Iran IP explicitly.")
    sess.send("2")  # Turbo preset
    hit = sess.expect(
        [("link", LINK_RE), ("noaddr", _rx("an address is required"))],
        LINK_TIMEOUT,
    )
    if hit == "noaddr":
        raise RuntimeError("BackPack refused the empty IP and detected none. Enter the Iran IP explicitly.")
    link = extract_link(sess.transcript)
    if not link:
        raise RuntimeError("Link prompt passed but no backpack:// URL found in output.")
    return link


def _parse_winner(text: str) -> tuple[str | None, str | None]:
    idx = text.find("BEST SETTINGS")
    if idx < 0:
        return None, None
    m = re.search(r"(?m)^Transport\s+(.+?)\s{2,}(.+?)\s*$", text[idx:])
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def _parse_steady(text: str) -> list[tuple[str, str, str]]:
    i = text.find("Works Steadily:")
    if i < 0:
        return []
    j = text.find("Notes:", i)
    section = text[i : j if j >= 0 else len(text)]
    out: list[tuple[str, str, str]] = []
    for line in section.splitlines()[1:]:
        toks = line.strip().split()
        if not toks:
            continue
        ri = next((k for k, t in enumerate(toks) if t.endswith("ms")), None)
        if ri is None or ri < 2:
            continue
        name = " ".join(toks[1:ri]) if not toks[0][0].isascii() else " ".join(toks[:ri])
        speed = " ".join(toks[ri + 1 :])
        if name and speed:
            out.append((name, toks[ri], speed))
    return out


def _norm_transport(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Test-table name -> create selector. Plain values are reverse transports,
# "direct:<carrier>" values are direct carriers, None cannot be created here
# (spoofing needs interactive CLI answers).
TEST_TRANSPORT_MAP = {
    "tcp": "tcp",
    "tcpmux": "tcpmux",
    "stealth": "stealth",
    "tcpstealth": "stealth",
    "pck": "pck",
    "tcppck": "pck",
    "directpck": "direct:pck",
    "udp": "udp",
    "directudp": "direct:udp",
    "kcp": "kcp",
    "udpkcpfec": "kcp",
    "udpkcp": "kcp",
    "quic": "quic",
    "udpquic": "quic",
    "ws": "ws",
    "websocket": "ws",
    "wsmux": "wsmux",
    "websocketmux": "wsmux",
    "wss": "wss",
    "wssmux": "wssmux",
    "naive": "naive",
    "naivehttp2": "naive",
    "http2": "naive",
    "xhttp": "xhttp",
    "xhttptls": "xhttp",
    "reality": "reality",
    "vision": "reality",
    "realityvision": "reality",
    "directxdi": "direct:xdi",
    "directquic": "direct:quic",
    "xdi": "direct:xdi",
    "spoof": None,
    "ipspoofing": None,
    "sni": None,
    "snispoofing": None,
    "directspoof": None,
    "directsni": None,
}


def map_test_transport(name: str) -> str | None:
    """Test-table name -> create selector.

    Exact table first, then ordered substring fallback: BackPack labels carry
    extras (presets, annotations), so "TCP Mux (Turbo)" must still mean tcpmux.
    Plain values are reverse transports, "direct:<carrier>" are direct
    carriers, None cannot be created here (spoofing needs interactive CLI).
    """
    key = _norm_transport(name)
    if key in TEST_TRANSPORT_MAP:
        return TEST_TRANSPORT_MAP[key]
    if key.startswith("direct"):
        rest = key[len("direct"):]
        if "spoof" in rest or "sni" in rest:
            return None
        for cand, val in (
            ("xdi", "direct:xdi"), ("pck", "direct:pck"),
            ("udp", "direct:udp"), ("quic", "direct:quic"),
        ):
            if cand in rest:
                return val
        return None
    if "spoof" in key or key == "sni":
        return None
    for cand, val in (
        ("naive", "naive"), ("xhttp", "xhttp"),
        ("reality", "reality"), ("vision", "reality"),
        ("wssmux", "wssmux"), ("wss", "wss"), ("wsmux", "wsmux"),
        ("tcpmux", "tcpmux"), ("stealth", "stealth"), ("pck", "pck"),
        ("kcp", "kcp"), ("quic", "quic"),
        ("ws", "ws"), ("udp", "udp"), ("tcp", "tcp"),
    ):
        if cand in key:
            return val
    return None


def run_test_flow(iran, kharej, iran_ip: str, log) -> dict:
    """Full connection test. Returns {winner, steady, kharej_rc, failed}."""
    if (iran.host, iran.port) == (kharej.host, kharej.port):
        raise RuntimeError("Iran and Kharej must be different servers.")
    iran_ssh = kh_ssh = None
    iran_sess = None
    iran_out = kh_out = ""
    kh_rc = None
    try:
        log(f"Connecting to Iran {iran.label} ...")
        iran_ssh = SshSession(iran)
        iran_ssh.connect()
        iran_ssh.client.get_transport().set_keepalive(30)
        ensure_backpack(iran_ssh, log)
        iran_mode = resolve_sudo(iran_ssh)
        if iran_mode == "none" and iran.username != "root":
            log("continuing without root (Direct, PCK and spoofing tests are skipped).")
        iran_sess = InteractiveSession(
            iran_ssh, root_prefix(iran_mode) + "backpack", sudo_stdin_lines(iran_ssh, iran_mode)
        )
        log("Iran: starting connection test (menu 0 -> Iran -> IP -> Turbo)...")
        link = drive_iran_test_menu(iran_sess, iran_ip)
        log(f"Test link ready: {link[:26]}...")
        iran_sess.set_on_text(log)

        log(f"Connecting to Kharej {kharej.label} ...")
        kh_ssh = SshSession(kharej)
        kh_ssh.connect()
        kh_ssh.client.get_transport().set_keepalive(30)
        ensure_backpack(kh_ssh, log)
        kh_mode = resolve_sudo(kh_ssh)
        kh_cmd = root_prefix(kh_mode) + "backpack link apply " + shlex.quote(link)
        log("Kharej: joining the test (takes several minutes — timer keeps running while output is quiet)...")
        kh_rc, kh_out = exec_streaming(
            kh_ssh, kh_cmd, TEST_TIMEOUT, sudo_stdin_lines(kh_ssh, kh_mode), on_text=log
        )
        log(f"Kharej finished with exit code {kh_rc}.")

        log("Iran: collecting results...")
        try:
            iran_sess.expect([("done", _rx("press enter to continue"))], TEST_TIMEOUT)
        except RuntimeError as exc:
            log(f"WARNING: {exc}")
        exit_backpack_menu(iran_sess, log)
        iran_out = iran_sess.transcript
    finally:
        if iran_sess is not None:
            try:
                iran_out = iran_sess.transcript
            except Exception:
                pass
            iran_sess.close()
        if iran_ssh is not None:
            iran_ssh.close()
        if kh_ssh is not None:
            kh_ssh.close()

    failed = kh_rc != 0 or bool(FAIL_MARKERS.search(_tail(_before_final_prompt(iran_out), 40)))
    winner, measured = _parse_winner(kh_out)
    if winner is None:
        winner, measured = _parse_winner(iran_out)
    steady = _parse_steady(kh_out) or _parse_steady(iran_out)
    ranked = []
    for name, rtt, speed in steady:
        ranked.append({
            "transport": name, "rtt": rtt, "speed": speed,
            "winner": name == (winner or ""),
            "setup_value": map_test_transport(name),
        })
    return {
        "winner": {"transport": winner, "measured": measured} if winner else None,
        "steady": ranked,
        "kharej_rc": kh_rc,
        "failed": failed,
        "kharej_tail": _tail(kh_out),
        "iran_tail": _tail(_before_final_prompt(iran_out)),
    }


def drive_setup_iran(sess: InteractiveSession, spec: dict, log) -> str:
    """Drive Setup Iran (reverse) wizard. spec: transport value, tunnel_port,
    forwarded_ports, tunnel_name, iran_ip. Returns the setup link."""
    t = TRANSPORT_BY_VALUE.get(spec["transport"])
    if t is None:
        raise RuntimeError(f"Unknown transport '{spec['transport']}'.")
    managed = t["managed"]
    log(f"Transport: {t['family']} / {t['label']}")

    sess.expect([("menu", _rx("connection test"))], PROMPT_TIMEOUT)
    sess.send("1")  # Setup Iran
    sess.expect([("direction", _rx("direction"))], PROMPT_TIMEOUT)
    sess.send("1")  # Reverse
    sess.expect([("family", _rx("select transport family"))], PROMPT_TIMEOUT)
    sess.send(str(t["family_no"]))
    hit = sess.expect(
        [("variant", _rx(r"select .* transport")), ("unavail", _rx("not available yet"))],
        PROMPT_TIMEOUT,
    )
    if hit == "unavail":
        raise RuntimeError("That transport is not available in this BackPack build.")
    sess.send(str(t["variant_no"]))
    if extract_link(sess.transcript):
        raise RuntimeError("Unexpected link before setup finished — menu numbering may have changed. See log.")

    sess.expect([("iranip", _rx("iran ip or domain"))], PROMPT_TIMEOUT)
    detected_ip = prompt_default_from(sess.transcript)
    sess.send(spec.get("iran_ip") or "")

    # Tunnel port (+ optional IPv6 confirm / backup hosts) then forwarded ports.
    sess.expect([("port", _rx("tunnel port"))], PROMPT_TIMEOUT)
    sess.send(spec.get("tunnel_port") or "")
    for _ in range(3):
        hit = sess.expect(
            [
                ("ipv6", _rx("listen on ipv6")),
                ("backup", _rx("backup addresses")),
                ("ports", _rx("forwarded ports")),
                ("porterr", _rx("already in use|invalid|must be|required")),
            ],
            PROMPT_TIMEOUT,
        )
        if hit == "ipv6":
            sess.send("")
            continue
        if hit == "backup":
            sess.send("")
            continue
        if hit == "ports":
            break
        raise RuntimeError(f"Tunnel port rejected: {sess.transcript[-500:]}")
    sess.send(spec["forwarded_ports"])

    hit = sess.expect(
        [
            ("name", _rx("tunnel name")),
            ("ports", _rx("forwarded ports")),
            ("porterr", _rx("no valid ports|already in use|invalid")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit != "name":
        raise RuntimeError(f"Forwarded ports rejected. Tail:\n{_tail(sess.transcript)}")
    sess.send(spec.get("tunnel_name") or "")

    # Token (non-reality) then the per-transport / tuning questions in order.
    # Anything unexpected surfaces as a timeout with the transcript attached.
    if t["value"] != "reality":
        hit = sess.expect(
            [("token", _rx("security token")), ("anothername", _rx("another name"))],
            PROMPT_TIMEOUT,
        )
        if hit == "anothername":
            raise RuntimeError("Tunnel name already exists or is invalid — pick another name.")
        sess.send("")
    if not managed and t["value"] != "udp":
        sess.expect([("udp", _rx("carry udp"))], PROMPT_TIMEOUT)
        sess.send("")
    if t["value"] in ("wss", "wssmux"):
        sess.expect([("tlscert", _rx("tls certificate"))], PROMPT_TIMEOUT)
        sess.send("1")  # Self-Signed
        sess.expect([("certname", _rx("domain or ip in the certificate"))], PROMPT_TIMEOUT)
        sess.send("")
        sess.expect([("simpleauth", _rx("simple token auth"))], PROMPT_TIMEOUT)
        sess.send("")
    if t["value"] == "pck":
        sess.expect([("pck", _rx("tcp flag pattern"))], PROMPT_TIMEOUT)
        sess.send("1")  # default pattern
        sess.expect([("pckoverride", _rx("override the automatic"))], PROMPT_TIMEOUT)
        sess.send("")
    if t["value"] in ("tcp", "tcpmux", "stealth", "ws", "wsmux"):
        try:
            hit = sess.expect([("proxy", _rx("send real client ip"))], 45.0)
            if hit == "proxy":
                sess.send("")
        except RuntimeError:
            pass  # transport asks nothing more before tuning
    if managed:
        host = (spec.get("iran_ip") or "").strip() or detected_ip
        drive_managed_carrier(sess, t["value"], host, log)

    sess.expect([("tuning", _rx("how should the tunnel be tuned"))], PROMPT_TIMEOUT)
    sess.send("2")  # Turbo
    sess.expect([("finetune", _rx("fine-tune the advanced settings"))], PROMPT_TIMEOUT)
    sess.send("")
    sess.expect([("restart", _rx("restart this tunnel on both servers"))], PROMPT_TIMEOUT)
    sess.send("")

    hit = sess.expect(
        [
            ("create", _rx("create this tunnel")),
            ("anothername", _rx("another name")),
            ("clash", _rx("nothing written|already in use")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit == "anothername":
        raise RuntimeError("Tunnel name already exists or is invalid — pick another name.")
    if hit == "clash":
        raise RuntimeError(f"Setup refused (port clash?). Tail:\n{_tail(sess.transcript)}")
    link = extract_link(sess.transcript)
    if not link:
        raise RuntimeError("Setup summary shown but no setup link found.")
    log(f"Setup link ready: {link[:26]}...")
    sess.send("")  # Create This Tunnel (default Yes)
    try:
        sess.expect([("done", _rx("press enter to continue"))], PROMPT_TIMEOUT)
    except RuntimeError as exc:
        raise RuntimeError(f"Tunnel creation failed: {exc}\n{_tail(sess.transcript)}")
    exit_backpack_menu(sess, log)
    return link


def drive_managed_carrier(sess: InteractiveSession, value: str, iran_host: str, log) -> None:
    """Best-effort managed (HTTPS) sub-wizard with safe defaults.

    Private-certificate generation is chosen; the Setup Link carries trust to
    Kharej automatically. Anything unexpected raises with the transcript.
    """
    hit = sess.expect(
        [("internal", _rx("internal reverse port")), ("anothername", _rx("another name"))],
        PROMPT_TIMEOUT,
    )
    if hit == "anothername":
        raise RuntimeError("Tunnel name already exists or is invalid — pick another name.")
    sess.send("")
    if value == "naive":
        sess.expect([("bin", _rx("sing-box binary"))], PROMPT_TIMEOUT)
        sess.send("")
        sess.expect([("user", _rx("naive username"))], PROMPT_TIMEOUT)
        sess.send("")
        sess.expect([("pass", _rx("naive password"))], PROMPT_TIMEOUT)
        sess.send("")
        sess.expect([("cert", _rx("https certificate"))], PROMPT_TIMEOUT)
        sess.send("2")  # Generate Private Certificate
        sess.expect([("certhost", _rx("certificate hostname or ip"))], PROMPT_TIMEOUT)
        host = iran_host or prompt_default_from(sess.transcript)
        if not host:
            raise RuntimeError("Managed certificate needs a hostname/IP and none was detected — enter the Iran IP explicitly.")
        sess.send(host)
    elif value == "xhttp":
        for name, pat in [
            ("uuid", r"vless uuid"),
            ("sni", r"tls server name"),
            ("path", r"xhttp path"),
            ("bin", r"xray binary"),
            ("host", r"http host"),
        ]:
            sess.expect([(name, _rx(pat))], PROMPT_TIMEOUT)
            sess.send("")
        sess.expect([("cert", _rx("https certificate"))], PROMPT_TIMEOUT)
        sess.send("2")
        sess.expect([("certhost", _rx("certificate hostname or ip"))], PROMPT_TIMEOUT)
        host = iran_host or prompt_default_from(sess.transcript)
        if not host:
            raise RuntimeError("Managed certificate needs a hostname/IP and none was detected — enter the Iran IP explicitly.")
        sess.send(host)
    elif value == "reality":
        # Cover endpoint is auto-probed; accept probed defaults. Bounded: at
        # most a handful of default-accepts before tuning must appear.
        for _ in range(8):
            hit = sess.expect(
                [
                    ("tuning", _rx("how should the tunnel be tuned")),
                    ("prompt", _rx(r":\s*$")),
                ],
                PROMPT_TIMEOUT,
            )
            if hit == "tuning":
                return  # caller continues from the tuning prompt
            sess.send("")
        raise RuntimeError("REALITY setup asked more questions than expected. See log.")
    log("managed carrier configured with defaults.")


def drive_setup_direct_iran(sess: InteractiveSession, spec: dict, log) -> str:
    """Drive Setup Iran -> Direct wizard. Returns the Kharej setup link."""
    c = DIRECT_CARRIER_BY_VALUE.get(spec["carrier"])
    if c is None:
        raise RuntimeError(f"Unknown direct carrier '{spec['carrier']}'.")
    kharej_addr = (spec.get("kharej_addr") or "").strip()
    if not kharej_addr:
        raise RuntimeError("Kharej address is required for a direct tunnel.")
    log(f"Carrier: {c['label']} (direct, Iran dials Kharej)")

    sess.expect([("menu", _rx("connection test"))], PROMPT_TIMEOUT)
    sess.send("1")  # Setup Iran
    sess.expect([("direction", _rx("direction"))], PROMPT_TIMEOUT)
    sess.send("2")  # Direct
    sess.expect([("carrier", _rx("how should the packets travel"))], PROMPT_TIMEOUT)
    sess.send(str(c["no"]))

    sess.expect([("kharejaddr", _rx("kharej ip or domain"))], PROMPT_TIMEOUT)
    sess.send(kharej_addr)
    sess.expect([("port", _rx("tunnel port"))], PROMPT_TIMEOUT)
    sess.send(spec.get("tunnel_port") or "")
    hit = sess.expect(
        [
            ("fw", _rx("forwarded ports")),
            ("porterr", _rx("invalid port|an address is required")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit != "fw":
        raise RuntimeError(f"Address/port rejected. Tail:\n{_tail(sess.transcript)}")
    sess.send(spec.get("forwarded_ports") or "")

    hit = sess.expect(
        [
            ("name", _rx("tunnel name")),
            ("anothername", _rx("another name")),
            ("porterr", _rx("invalid|already in use")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit != "name":
        raise RuntimeError(f"Forwarded ports rejected. Tail:\n{_tail(sess.transcript)}")
    sess.send(spec.get("tunnel_name") or "")

    hit = sess.expect(
        [("token", _rx("security token")), ("anothername", _rx("another name"))],
        PROMPT_TIMEOUT,
    )
    if hit == "anothername":
        raise RuntimeError("Tunnel name already exists or is invalid — pick another name.")
    sess.send("")

    if (spec.get("forwarded_ports") or "").strip():
        sess.expect([("udp", _rx("carry udp"))], PROMPT_TIMEOUT)
        sess.send("")
        hit = sess.expect(
            [("fec", _rx("turn on error correction")), ("share", _rx("share these ports"))],
            PROMPT_TIMEOUT,
        )
        if hit == "share":
            sess.send("")
            sess.expect([("fec", _rx("turn on error correction"))], PROMPT_TIMEOUT)
    else:
        sess.expect([("fec", _rx("turn on error correction"))], PROMPT_TIMEOUT)
    sess.send("")
    if spec["carrier"] == "udp":
        hit = sess.expect(
            [("paths", _rx("spread the tunnel over several sockets")), ("tuning", _rx("how should the tunnel be tuned"))],
            PROMPT_TIMEOUT,
        )
        if hit == "paths":
            sess.send("")
            sess.expect([("tuning", _rx("how should the tunnel be tuned"))], PROMPT_TIMEOUT)
    sess.send("1")  # Turbo
    sess.expect([("finetune", _rx("fine-tune the advanced settings"))], PROMPT_TIMEOUT)
    sess.send("")
    sess.expect([("restart", _rx("restart this tunnel on both servers"))], PROMPT_TIMEOUT)
    sess.send("")

    hit = sess.expect(
        [
            ("create", _rx("create this tunnel")),
            ("clash", _rx("nothing written|already in use|invalid|error")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit != "create":
        raise RuntimeError(f"Setup refused. Tail:\n{_tail(sess.transcript)}")
    link = extract_link(sess.transcript)
    if not link:
        raise RuntimeError("Setup summary shown but no setup link found.")
    log(f"Setup link ready: {link[:26]}...")
    sess.send("")  # Create This Tunnel (default Yes)
    try:
        sess.expect([("done", _rx("press enter to continue"))], PROMPT_TIMEOUT)
    except RuntimeError as exc:
        raise RuntimeError(f"Tunnel creation failed: {exc}\n{_tail(sess.transcript)}")
    exit_backpack_menu(sess, log)
    return link


def drive_setup_direct_kharej(sess: InteractiveSession, link: str, spec: dict, log) -> None:
    """Drive Setup Kharej -> Direct -> same carrier -> Setup Link."""
    c = DIRECT_CARRIER_BY_VALUE[spec["carrier"]]
    sess.expect([("menu", _rx("connection test"))], PROMPT_TIMEOUT)
    sess.send("2")  # Setup Kharej
    sess.expect([("direction", _rx("direction"))], PROMPT_TIMEOUT)
    sess.send("2")  # Direct
    sess.expect([("carrier", _rx("how should the packets travel"))], PROMPT_TIMEOUT)
    sess.send(str(c["no"]))
    sess.expect([("how", _rx("how do you want to set up this side"))], PROMPT_TIMEOUT)
    sess.send("1")  # Setup Link
    sess.expect([("linkprompt", _rx("setup link"))], PROMPT_TIMEOUT)
    sess.send(link)
    hit = sess.expect(
        [
            ("name", _rx("tunnel name")),
            ("badlink", _rx("not for the kharej side|invalid|error")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit != "name":
        raise RuntimeError(f"Kharej rejected the setup link. Tail:\n{_tail(sess.transcript)}")
    if spec.get("tunnel_name"):
        sess.send(spec["tunnel_name"])
    else:
        sess.send("")
    hit = sess.expect(
        [
            ("create", _rx("create this tunnel")),
            ("anothername", _rx("another name")),
        ],
        PROMPT_TIMEOUT,
    )
    if hit == "anothername":
        raise RuntimeError("Tunnel name already exists on Kharej — pick another name.")
    sess.send("")
    try:
        sess.expect([("done", _rx("press enter to continue"))], PROMPT_TIMEOUT)
    except RuntimeError as exc:
        raise RuntimeError(f"Kharej tunnel creation failed: {exc}\n{_tail(sess.transcript)}")
    exit_backpack_menu(sess, log)


def run_create_flow(iran, kharej, spec: dict, log) -> dict:
    """Setup Iran via wizard, build the Kharej side. Returns summary."""
    if (iran.host, iran.port) == (kharej.host, kharej.port):
        raise RuntimeError("Iran and Kharej must be different servers.")
    direction = (spec.get("direction") or "reverse").strip().lower()
    if direction not in ("reverse", "direct"):
        raise RuntimeError("direction must be reverse or direct.")
    iran_ssh = kh_ssh = None
    iran_sess = kh_sess = None
    try:
        log(f"Connecting to Iran {iran.label} ...")
        iran_ssh = SshSession(iran)
        iran_ssh.connect()
        iran_ssh.client.get_transport().set_keepalive(30)
        ensure_backpack(iran_ssh, log)
        iran_mode = resolve_sudo(iran_ssh)
        iran_sess = InteractiveSession(
            iran_ssh, root_prefix(iran_mode) + "backpack", sudo_stdin_lines(iran_ssh, iran_mode)
        )
        iran_sess.set_on_text(log)
        name = (spec.get("tunnel_name") or "").strip()
        if direction == "reverse":
            if not (spec.get("forwarded_ports") or "").strip():
                raise RuntimeError("Forwarded ports are required (e.g. 443 or 443=127.0.0.1:2096).")
            t = TRANSPORT_BY_VALUE.get(spec.get("transport") or "")
            if t is None:
                raise RuntimeError(f"Unknown transport '{spec.get('transport')}'.")
            if not (spec.get("tunnel_port") or "").strip() and t["value"] != "reality":
                raise RuntimeError("Tunnel port is required for this transport.")
            log("Iran: running reverse setup wizard...")
            link = drive_setup_iran(iran_sess, spec, log)
            exit_backpack_menu(iran_sess, log)

            log(f"Connecting to Kharej {kharej.label} ...")
            kh_ssh = SshSession(kharej)
            kh_ssh.connect()
            kh_ssh.client.get_transport().set_keepalive(30)
            ensure_backpack(kh_ssh, log)
            kh_mode = resolve_sudo(kh_ssh)
            kh_cmd = root_prefix(kh_mode) + "backpack link apply "
            if name:
                kh_cmd += "--name " + shlex.quote(name) + " "
            kh_cmd += shlex.quote(link)
            log("Kharej: applying setup link (takes minutes)...")
            kh_rc, kh_out = exec_streaming(
                kh_ssh, kh_cmd, APPLY_TIMEOUT, sudo_stdin_lines(kh_ssh, kh_mode), on_text=log
            )
            tail = _tail(strip_ansi(kh_out))
            if kh_rc != 0:
                raise RuntimeError(f"Kharej link apply failed (rc={kh_rc}):\n{tail}")
            log("Kharej link applied.")
            return {"direction": "reverse", "link_prefix": link[:26] + "...", "kharej_tail": tail}

        # ---- direct: Iran dials Kharej (L3 tunnel) ----
        c = DIRECT_CARRIER_BY_VALUE.get(spec.get("carrier") or "")
        if c is None:
            raise RuntimeError(
                f"Unknown direct carrier '{spec.get('carrier')}'. "
                "Spoofing carriers need interactive CLI answers."
            )
        spec["kharej_addr"] = (spec.get("kharej_addr") or kharej.host).strip()
        log("Iran: running direct setup wizard...")
        link = drive_setup_direct_iran(iran_sess, spec, log)
        exit_backpack_menu(iran_sess, log)

        log(f"Connecting to Kharej {kharej.label} ...")
        kh_ssh = SshSession(kharej)
        kh_ssh.connect()
        kh_ssh.client.get_transport().set_keepalive(30)
        ensure_backpack(kh_ssh, log)
        kh_mode = resolve_sudo(kh_ssh)
        kh_sess = InteractiveSession(
            kh_ssh, root_prefix(kh_mode) + "backpack", sudo_stdin_lines(kh_ssh, kh_mode)
        )
        kh_sess.set_on_text(log)
        log("Kharej: building from setup link...")
        drive_setup_direct_kharej(kh_sess, link, spec, log)
        exit_backpack_menu(kh_sess, log)
        log("Kharej side built.")
        return {"direction": "direct", "carrier": c["value"], "link_prefix": link[:26] + "..."}
    finally:
        if iran_sess is not None:
            iran_sess.close()
        if kh_sess is not None:
            kh_sess.close()
        if iran_ssh is not None:
            iran_ssh.close()
        if kh_ssh is not None:
            kh_ssh.close()


def parse_tunnel_number(transcript: str, name: str) -> int | None:
    for line in strip_ansi(transcript).splitlines():
        m = re.match(r"\s*(\d+)\)\s*(\S+)", line)
        if m and m.group(2) == name:
            return int(m.group(1))
    return None


def goto_main_and_exit(sess: InteractiveSession, log=None) -> None:
    """Close out of menus the same way: drain, then EOF (see exit_backpack_menu)."""
    exit_backpack_menu(sess, log)


def delete_tunnel_on_server(ssh: SshSession, name: str, log, label: str) -> str:
    """Delete tunnel `name` via the menus. Returns outcome note."""
    if not check_backpack(ssh):
        return f"{label}: backpack not installed, nothing to delete."
    rc, out, _ = ssh.run_as_root("backpack tunnel list --json", timeout=60.0)
    try:
        tunnels = json.loads(out) if rc == 0 else []
        names = [t.get("name") for t in tunnels if isinstance(t, dict)]
    except Exception:
        names = []
    if name not in names:
        return f"{label}: tunnel '{name}' not present."
    mode = resolve_sudo(ssh)
    sess = InteractiveSession(
        ssh, root_prefix(mode) + "backpack", sudo_stdin_lines(ssh, mode), on_text=log
    )

    def send(value: str) -> None:
        log(f">>> {value!r}")
        sess.send(value)

    try:
        sess.expect([("menu", _rx("connection test|select an option"))], PROMPT_TIMEOUT)
        send("3")  # Manage
        hit = sess.expect(
            [
                ("choice", _rx("enter your choice")),
                ("empty", _rx("no tunnels yet")),
            ],
            PROMPT_TIMEOUT,
        )
        if hit == "empty":
            goto_main_and_exit(sess, log)
            return f"{label}: tunnel '{name}' not present."
        # Choice prompt is showing: submenu (Manage Tunnels) or tunnel list.
        tail = _tail(sess.transcript, 30).lower()
        if "manage tunnels" in tail and "select a tunnel" not in tail.split("manage tunnels")[-1]:
            send("1")  # Manage Tunnels (v1.8.5 layout)
            hit = sess.expect(
                [
                    ("choice", _rx("enter your choice")),
                    ("empty", _rx("no tunnels yet")),
                ],
                PROMPT_TIMEOUT,
            )
            if hit == "empty":
                send("")
                goto_main_and_exit(sess, log)
                return f"{label}: tunnel '{name}' not present."
        num = parse_tunnel_number(sess.transcript, name)
        if num is None:
            raise RuntimeError(f"Could not find '{name}' in the tunnel list.\n{_tail(sess.transcript)}")
        send(str(num))
        sess.expect([("choice", _rx("enter your choice"))], PROMPT_TIMEOUT)
        send("7")  # Delete
        hit = sess.expect(
            [
                ("confirm", _rx(r"\(y/n\)")),
                ("enter", _rx("press enter to continue")),
            ],
            PROMPT_TIMEOUT,
        )
        if hit != "confirm":
            raise RuntimeError(
                "Delete did not ask for confirmation — unexpected screen:\n"
                + _tail(sess.transcript)
            )
        send("y")
        sess.expect([("done", _rx("press enter to continue"))], PROMPT_TIMEOUT)
        send("")
        goto_main_and_exit(sess, log)
        if "deleted" in sess.transcript.lower():
            return f"{label}: tunnel '{name}' deleted."
        return f"{label}: delete finished (verify in Manage)."
    finally:
        sess.close()


def run_delete_flow(iran, kharej, tunnel_name: str, log) -> dict:
    notes = []
    for target, label in ((iran, "Iran"), (kharej, "Kharej")):
        log(f"{label}: connecting to {target.label} ...")
        ssh = SshSession(target)
        try:
            ssh.connect()
            ssh.client.get_transport().set_keepalive(30)
            note = delete_tunnel_on_server(ssh, tunnel_name, log, label)
        except Exception as exc:
            note = f"{label}: delete failed: {exc}"
        log(note)
        notes.append(note)
        ssh.close()
    return {"notes": notes}


def list_tunnels(ssh: SshSession) -> dict:
    """Non-interactive tunnel inventory for the Manage tab."""
    ver = check_backpack(ssh)
    if not ver:
        return {"backpack": None, "tunnels": []}
    rc, out, err = ssh.run_as_root("backpack tunnel list --json", timeout=60.0)
    if rc != 0:
        raise RuntimeError(f"tunnel list failed: {(err or out).strip()[:500]}")
    try:
        data = json.loads(out)
    except Exception as exc:
        raise RuntimeError(f"could not parse tunnel list: {exc}")
    tunnels = data if isinstance(data, list) else data.get("tunnels", data)
    return {"backpack": ver, "tunnels": tunnels if isinstance(tunnels, list) else []}
