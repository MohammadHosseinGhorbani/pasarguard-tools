"""SQLAlchemy models."""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
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
