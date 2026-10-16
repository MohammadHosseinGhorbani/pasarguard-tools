"""Shared pydantic schemas."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---- auth ----
class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=4)


# ---- servers ----
class ServerIn(BaseModel):
    name: str
    host: str
    username: str = "root"
    port: int = 22
    password: str = ""
    key_path: str = ""
    key_content: str = ""
    notes: str = ""
    role: Literal["iran", "kharej"] = "kharej"
    bp_url: str = ""
    bp_token: str = ""
    bp_verify: bool = False
    bp_node: str = ""


class ServerOut(ServerIn):
    id: int
    has_password: bool = False
    has_key: bool = False
    has_bp_token: bool = False


class BpSettingsIn(BaseModel):
    bp_url: str = ""
    bp_token: str = ""
    bp_verify: bool = False
    bp_node: str = ""


class ExecIn(BaseModel):
    command: str
    use_sudo: bool = False
    timeout: float = Field(default=60.0, le=600.0)


class NodeCertsIn(BaseModel):
    server_id: int


class NodeCertIssueIn(BaseModel):
    server_id: int
    domain: str
    email: str = ""


# ---- hosts (subscription hosts per inbound) ----
class HostCreateIn(BaseModel):
    panel_id: int
    inbound_tag: str
    remark: str = ""
    address: str = ""
    ech_config: str = ""


class HostUpdateIn(BaseModel):
    panel_id: int
    remark: str | None = None
    address: str | None = None
    ech_config: str | None = None


# ---- tunnel (BackPack) ----
class TunnelTestIn(BaseModel):
    iran_server_id: int
    kharej_server_id: int
    iran_ip: str = ""


class TunnelCreateIn(BaseModel):
    iran_server_id: int
    kharej_server_id: int
    direction: Literal["reverse", "direct"] = "reverse"
    transport: str = "tcpmux"
    carrier: str = "udp"
    tunnel_port: str = ""
    forwarded_ports: str = ""
    tunnel_name: str = ""
    iran_ip: str = ""
    kharej_addr: str = ""


class TunnelDeleteIn(BaseModel):
    server_id: int
    tunnel_name: str


class TunnelListIn(BaseModel):
    server_id: int


# ---- BackPack web-panel API (each server stands alone; our app drives both sides) ----
class BpTestIn(BaseModel):
    server_id: int


class BpCreateIn(BaseModel):
    iran_server_id: int
    kharej_server_id: int
    direction: Literal["reverse", "direct"] = "reverse"
    transport: str = "tcpmux"
    carrier: str = "udp"
    tunnel_port: str = ""
    forwarded_ports: str = ""
    tunnel_name: str = ""
    iran_ip: str = ""


class BpDeleteIn(BaseModel):
    server_id: int
    name: str


class BpActionIn(BaseModel):
    server_id: int
    name: str
    action: Literal["start", "stop", "restart"]


class BpTunnelsIn(BaseModel):
    server_id: int


class BpConnTestIn(BaseModel):
    iran_server_id: int
    kharej_server_id: int
    host: str = ""
    preset: str = "turbo"


class BpConnTestStopIn(BaseModel):
    server_id: int


# ---- panels ----
class PanelIn(BaseModel):
    name: str
    base_url: str = ""
    api_key: str = ""
    verify_ssl: bool = False


class PanelOut(BaseModel):
    id: int
    name: str
    base_url: str = ""
    verify_ssl: bool = False
    is_active: bool = False
    api_key_masked: str = ""


class PanelOpsRunIn(BaseModel):
    server_id: int
    action: Literal["status", "logs", "up", "down", "restart", "update"]


class PanelOpsInstallIn(BaseModel):
    server_id: int
    database: Literal["sqlite", "mysql", "mariadb", "postgresql", "timescaledb"] = "sqlite"
    ssl_mode: Literal["none", "no-ssl", "domain"] = "none"
    ssl_domain: str = ""


# ---- nodes ----
class NodeStatusIn(BaseModel):
    server_id: int


class NodeInstallIn(BaseModel):
    server_id: int
    service_port: int | None = None
    api_port: int | None = None


class NodeCredentialsIn(BaseModel):
    server_id: int


class NodeRegisterIn(BaseModel):
    server_id: int
    panel_id: int
    node_name: str | None = None


class NodeActionIn(BaseModel):
    server_id: int
    action: Literal["up", "down", "restart", "logs", "core-update", "renew-cert"]


# ---- cloudflare accounts ----
class CfAccountIn(BaseModel):
    name: str
    token: str = ""


class CfAccountOut(BaseModel):
    id: int
    name: str
    token_masked: str = ""


class CfRecordCreate(BaseModel):
    name: str
    content: str
    type: str = "A"
    proxied: bool = False
    ttl: int = 1


class CfRecordUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    proxied: bool | None = None
    ttl: int | None = None


class CfProxyPatch(BaseModel):
    proxied: bool


# ---- inbounds ----
class RealityIn(BaseModel):
    server_id: int
    panel_id: int
    sni: str = "icloud.com:443"
    port: int = 443
    network: Literal["xhttp", "tcp"] = "xhttp"
    node_name: str | None = None
    tag: str | None = None
    core_name: str | None = None


class CloudflareInboundIn(BaseModel):
    server_id: int
    panel_id: int
    cf_account_id: int
    domain: str
    server_ip: str | None = None
    email: str = ""
    port: int = 2096
    network: Literal["xhttp", "ws"] = "xhttp"


class RealityQuickIn(BaseModel):
    """Panel-only: add a Reality inbound to an EXISTING node. No SSH."""

    panel_id: int
    node_id: int
    sni: str = "icloud.com:443"
    port: int = 443
    network: Literal["xhttp", "tcp"] = "xhttp"
    tag: str | None = None


class CloudflareQuickIn(BaseModel):
    """Panel-only: add a TLS inbound to an EXISTING node.

    Certificate comes from the node's own server: pass cert_domain and the
    backend reads /etc/letsencrypt/live/<cert_domain>/ over SSH from the
    saved server matching the node address. (Issuing belongs to the
    Certificates tab.) Raw PEM paste remains as a fallback."""

    panel_id: int
    node_id: int
    domain: str
    port: int = 2096
    network: Literal["xhttp", "ws"] = "xhttp"
    cert_pem: str = ""
    key_pem: str = ""
    cert_domain: str | None = None
    tag: str | None = None


class CloudflareCertIn(BaseModel):
    """Fetch a domain's cert/key from a server over SSH (certbot)."""

    server_id: int
    domain: str
    issue: bool = False
    email: str = ""


class CloudflareCertAutoIn(BaseModel):
    """Same, but the server is auto-resolved from the node's address."""

    panel_id: int
    node_id: int
    domain: str
    issue: bool = False
    email: str = ""


class OutboundSaveItem(BaseModel):
    # Empty wireguard_conf = keep the existing outbound (rename only).
    # Empty original_tag = brand-new outbound (config required).
    tag: str = ""
    original_tag: str = ""
    wireguard_conf: str = ""


class OutboundsSaveIn(BaseModel):
    panel_id: int
    core_id: int
    outbounds: list[OutboundSaveItem] = []


class RoutingSaveIn(BaseModel):
    panel_id: int
    core_id: int
    inbound_tag: str = ""
    # Empty = remove the assignment (routing rule) for this inbound.
    outbound_tag: str = ""


# ---- Proton VPN ----
class ProtonSettingsIn(BaseModel):
    username: str = ""
    password: str = ""
    app_version: str = ""
    proxy: str = ""


class ProtonLoginIn(BaseModel):
    # Optional: log in with these credentials and persist them. Empty = use stored ones.
    username: str = ""
    password: str = ""
    totp: str = ""


class ProtonConfigIn(BaseModel):
    name: str
    country: str = ""
    tier: int = 0


def model_to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [model_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: model_to_dict(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        try:
            return model_to_dict(obj.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: model_to_dict(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)
