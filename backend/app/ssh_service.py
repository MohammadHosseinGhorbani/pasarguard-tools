"""SSH service based on paramiko (password + key file + key text).

Adapted from the original CLI ``ssh.py``: no prints/prompts, raises
RuntimeError with messages, and supports a private key supplied as text
(``Server.key_content``) in addition to a key file path.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass

import paramiko


@dataclass
class TargetServer:
    host: str
    username: str = "root"
    port: int = 22
    password: str | None = None
    key_path: str | None = None
    key_content: str | None = None

    @property
    def label(self) -> str:
        return f"{self.username}@{self.host}:{self.port}"


class SshSession:
    """Thin wrapper around paramiko.SSHClient."""

    def __init__(self, target: TargetServer):
        self.target = target
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._tmp_key: tempfile.NamedTemporaryFile | None = None

    def connect(self, timeout: float = 15.0) -> None:
        kwargs: dict = {
            "hostname": self.target.host,
            "port": self.target.port,
            "username": self.target.username,
            "timeout": timeout,
            "banner_timeout": timeout,
            "auth_timeout": timeout,
        }
        key_file = self.target.key_path
        if self.target.key_content and self.target.key_content.strip():
            self._tmp_key = tempfile.NamedTemporaryFile(
                mode="w", suffix="_pg_key", delete=False, encoding="utf-8"
            )
            content = self.target.key_content.strip() + "\n"
            self._tmp_key.write(content)
            self._tmp_key.flush()
            try:
                import os

                os.chmod(self._tmp_key.name, 0o600)
            except OSError:
                pass
            key_file = self._tmp_key.name
        if key_file:
            kwargs["key_filename"] = key_file
        if self.target.password:
            kwargs["password"] = self.target.password
        kwargs["allow_agent"] = True
        kwargs["look_for_keys"] = True
        try:
            self.client.connect(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"SSH connect to {self.target.label} failed: {exc}")

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        if self._tmp_key is not None:
            try:
                import os

                os.unlink(self._tmp_key.name)
            except OSError:
                pass
            self._tmp_key = None

    def __enter__(self) -> "SshSession":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run_raw(self, command: str, timeout: float = 60.0) -> tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout, get_pty=False)
        if self.target.username != "root" and self.target.password and command.lstrip().startswith("sudo "):
            try:
                stdin.write(self.target.password + "\n")
                stdin.flush()
            except Exception:
                pass
        rc = stdout.channel.recv_exit_status()
        return rc, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")

    def run(self, command: str, timeout: float = 60.0, use_sudo: bool = False) -> tuple[int, str, str]:
        """Run a command. If use_sudo and user is not root, wrap with sudo -S."""
        if use_sudo and self.target.username != "root":
            if command.lstrip().startswith("sudo "):
                cmd = command
            else:
                cmd = f"sudo -S -p '' {command}"
        else:
            cmd = command
        return self._run_raw(cmd, timeout=timeout)

    def run_as_root(self, command: str, timeout: float = 60.0) -> tuple[int, str, str]:
        """Run command with sudo when connected as non-root."""
        if self.target.username == "root":
            return self._run_raw(command, timeout=timeout)
        return self._run_raw(f"sudo -S -p '' {command}", timeout=timeout)

    def read_file(self, path: str) -> str:
        rc, out, err = self.run_as_root(f"cat {path}")
        if rc != 0:
            raise RuntimeError(f"cannot read {path}: {(err.strip() or out.strip())}")
        return out


def target_from_server(srv) -> TargetServer:
    return TargetServer(
        host=srv.host,
        username=srv.username or "root",
        port=srv.port or 22,
        password=srv.password or None,
        key_path=srv.key_path or None,
        key_content=getattr(srv, "key_content", "") or None,
    )
