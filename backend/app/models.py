"""SQLAlchemy models."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)


class Server(Base):
    __tablename__ = "servers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(64), default="root")
    port: Mapped[int] = mapped_column(Integer, default=22)
    password: Mapped[str] = mapped_column(String(255), default="")
    key_path: Mapped[str] = mapped_column(String(512), default="")
    key_content: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(16), default="kharej")
    # BackPack web-panel API (Iran servers): full URL, write-scope token,
    # TLS verify flag, and the paired Kharej node name in its fleet.
    bp_url: Mapped[str] = mapped_column(String(512), default="")
    bp_token: Mapped[str] = mapped_column(String(512), default="")
    bp_verify: Mapped[bool] = mapped_column(Boolean, default=False)
    bp_node: Mapped[str] = mapped_column(String(128), default="")


class Panel(Base):
    __tablename__ = "panels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(512), default="")
    api_key: Mapped[str] = mapped_column(String(512), default="")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class CfAccount(Base):
    __tablename__ = "cf_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token: Mapped[str] = mapped_column(String(512), default="")


class ProtonAccount(Base):
    """Single-row Proton VPN account: credentials + SRP session tokens."""

    __tablename__ = "proton_account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    password: Mapped[str] = mapped_column(String(255), default="")
    app_version: Mapped[str] = mapped_column(String(64), default="")
    # Optional proxy for filtered networks (same reason the CLI has --proxy).
    proxy: Mapped[str] = mapped_column(String(255), default="")
    uid: Mapped[str] = mapped_column(String(128), default="")
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    session_id: Mapped[str] = mapped_column(String(255), default="")
    obtained_at: Mapped[float] = mapped_column(Float, default=0.0)
    expires_in: Mapped[int] = mapped_column(Integer, default=0)


class ProtonConfig(Base):
    """A created Proton WireGuard config (kept so it can be rotated and reused)."""

    __tablename__ = "proton_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    country: Mapped[str] = mapped_column(String(8), default="")
    server_name: Mapped[str] = mapped_column(String(128), default="")
    entry_ip: Mapped[str] = mapped_column(String(128), default="")
    port: Mapped[int] = mapped_column(Integer, default=51820)
    address: Mapped[str] = mapped_column(String(255), default="10.2.0.2/32")
    dns: Mapped[str] = mapped_column(String(255), default="10.2.0.1")
    # Client X25519 private key (base64) — only returned by Proton at creation.
    private_key: Mapped[str] = mapped_column(String(255), default="")
    server_pubkey: Mapped[str] = mapped_column(String(255), default="")
    serial: Mapped[str] = mapped_column(String(128), default="")
    expires_at: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
