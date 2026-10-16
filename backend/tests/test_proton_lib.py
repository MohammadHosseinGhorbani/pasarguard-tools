"""proton_lib: wireguard rendering, picker/country logic and the HTTP client."""
import base64
import hashlib

import pytest

from app.proton_lib import (
    DEFAULT_APP_VERSION,
    ProtonError,
    ProtonSession,
    ed25519_priv_body_to_x25519,
    infer_country_code,
    pick_best_server,
    render_conf,
    resolve_country_code,
    resolve_country_list,
    sanitize_config_name,
    suggest_outbound_tag,
)


def test_x25519_conversion_matches_spec():
    # seed = 32 zero bytes -> deterministic
    seed = bytes(32)
    priv_body = base64.b64encode(b"\x00" * 16 + seed).decode()
    got = ed25519_priv_body_to_x25519(priv_body)
    h = bytearray(hashlib.sha512(seed).digest()[:32])
    h[0] &= 0xF8
    h[31] &= 0x7F
    h[31] |= 0x40
    assert got == base64.b64encode(bytes(h)).decode()


LOGICALS = [
    {"Name": "NL#1", "Tier": 0, "Status": 1, "EntryCountry": "NL", "ExitCountry": "NL",
     "Score": 5.0, "Load": 80, "Servers": [{"EntryIP": "1.1.1.1", "X25519PublicKey": "k1"}]},
    {"Name": "US#1", "Tier": 0, "Status": 1, "EntryCountry": "US", "ExitCountry": "US",
     "Score": 1.2, "Load": 60, "Servers": [{"EntryIP": "2.2.2.2", "X25519PublicKey": "k2"}]},
    {"Name": "US#2", "Tier": 0, "Status": 1, "EntryCountry": "US", "ExitCountry": "US",
     "Score": 1.2, "Load": 10, "Servers": [{"EntryIP": "3.3.3.3", "X25519PublicKey": "k3"}]},
    {"Name": "CH#1", "Tier": 2, "Status": 1, "EntryCountry": "CH", "ExitCountry": "CH",
     "Score": 0.1, "Load": 1, "Servers": [{"EntryIP": "4.4.4.4", "X25519PublicKey": "k4"}]},
    {"Name": "DE#1", "Tier": 0, "Status": 0, "EntryCountry": "DE", "ExitCountry": "DE",
     "Score": 0.0, "Load": 0, "Servers": [{"EntryIP": "5.5.5.5", "X25519PublicKey": "k5"}]},
]


def test_pick_best_free_server():
    assert pick_best_server(LOGICALS, tier=0)["Name"] == "US#2"  # score, then load
    assert pick_best_server(LOGICALS, tier=0, countries=["nl"])["Name"] == "NL#1"


def test_pick_best_server_skips_disabled_and_wrong_tier():
    assert pick_best_server(LOGICALS, tier=2)["Name"] == "CH#1"
    # DE#1 is disabled (Status 0) so it must never win despite score 0
    with pytest.raises(ProtonError, match="No servers match"):
        pick_best_server(LOGICALS, tier=0, countries=["germany"])


def test_pick_best_server_no_match_raises():
    with pytest.raises(ProtonError, match="No servers match"):
        pick_best_server(LOGICALS, tier=0, countries=["jp"])


def test_country_resolution():
    assert resolve_country_code("netherlands") == "NL"
    assert resolve_country_code("nl") == "NL"
    assert resolve_country_code("DE") == "DE"
    assert resolve_country_list("netherlands, us ,JP") == ["NL", "US", "JP"]
    assert resolve_country_list("") == []
    assert infer_country_code("germany") == "DE"
    assert infer_country_code("my-node") is None
    with pytest.raises(ValueError):
        resolve_country_code("atlantis")


def test_render_conf_shape():
    conf = render_conf(
        client_priv_x25519="AAA=",
        server_pubkey="BBB=",
        entry_ip="185.1.2.3",
        server_name="NL#99",
        key_label="netherlands",
    )
    assert conf.startswith("[Interface]\n")
    assert "PrivateKey = AAA=" in conf
    assert "Address = 10.2.0.2/32" in conf
    assert "DNS = 10.2.0.1" in conf
    assert "[Peer]" in conf
    assert "# NL#99" in conf
    assert "Endpoint = 185.1.2.3:51820" in conf


def test_name_helpers():
    assert sanitize_config_name("  my box ") == "my-box"
    assert suggest_outbound_tag("Netherlands") == "proton-netherlands"
    assert suggest_outbound_tag("NL #1") == "proton-nl-1"
    assert suggest_outbound_tag("") == "proton"


def test_session_refresh_window():
    s = ProtonSession(
        access_token="a", refresh_token="r",
        app_version=DEFAULT_APP_VERSION, expires_in=30 * 24 * 3600,
    )
    s.obtained_at = 1000.0
    assert not s.needs_refresh(now=1000.0)  # just issued
    assert s.needs_refresh(now=1000.0 + 29 * 24 * 3600)  # <7 days left
    s.refresh_token = ""
    assert not s.needs_refresh(now=1000.0 + 29 * 24 * 3600)  # cookie mode never refreshes


def test_srp_primitives_removed():
    """The SRP login path is gone; only the Selenium (browser) login remains."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.proton_srp")
    from app import proton_lib
    assert not hasattr(proton_lib, "srp_login")


def _mock_client(monkeypatch, handler):
    """Route a ProtonClient through an httpx MockTransport, capturing headers."""
    import httpx

    from app import proton_lib

    seen: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def fake_new_client(*, base: str, timeout: float, proxy, **kwargs):
        return httpx.Client(transport=transport, base_url=base.rstrip("/"), **kwargs)

    monkeypatch.setattr(proton_lib, "_new_client", fake_new_client)
    return seen


def test_proton_client_roundtrip(monkeypatch):
    import httpx

    from app.proton_lib import ProtonClient, ProtonSession

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/logicals"):
            return httpx.Response(200, json={"LogicalServers": [{"Name": "NL#1"}]})
        if path.endswith("/key/EC"):
            return httpx.Response(200, json={
                "PrivateKey": "-----BEGIN PRIVATE KEY-----\nprivbody\n-----END PRIVATE KEY-----",
                "PublicKey": "-----BEGIN PUBLIC KEY-----\npubbody\n-----END PUBLIC KEY-----",
            })
        if path.endswith("/certificate/all"):
            return httpx.Response(200, json={"Certificates": [{"SerialNumber": "S1"}]})
        if request.method == "POST":
            return httpx.Response(200, json={"Code": 1000, "SerialNumber": "S2", "ExpirationTime": 99})
        if request.method == "DELETE":
            return httpx.Response(200, json={"Code": 1000, "Count": 1})
        return httpx.Response(404, json={"Code": 404})

    seen = _mock_client(monkeypatch, handler)
    sess = ProtonSession(uid="U", access_token="T", refresh_token="R", session_id="SID")
    with ProtonClient(sess) as client:
        assert client.get_logicals()[0]["Name"] == "NL#1"
        _, pub_body, priv_body = client.get_ec_keypair()
        assert (pub_body, priv_body) == ("pubbody", "privbody")
        assert client.list_configs()[0]["SerialNumber"] == "S1"
        reg = client.register_config("NL#1", "1.2.3.4", "spub", "cpub", "nl")
        assert reg["SerialNumber"] == "S2"
        assert client.delete_config_by_serial("S1") == 1

    assert any("AUTH-U=T" in h.get("cookie", "") and "Session-Id=SID" in h.get("cookie", "") for h in seen)


def test_proton_client_reports_stale_app_version(monkeypatch):
    import httpx

    from app.proton_lib import ProtonClient, ProtonError, ProtonSession

    seen = _mock_client(
        monkeypatch,
        lambda request: httpx.Response(200, json={"Code": 5003, "Error": "out of date"}),
    )
    with ProtonClient(ProtonSession(uid="U", access_token="T")) as client:
        with pytest.raises(ProtonError, match="stale app version"):
            client.get_logicals()
    assert seen  # request was made


def test_send_maps_network_errors_to_proton_error():
    import httpx

    from app.proton_lib import ProtonError, _send

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(boom)
    with httpx.Client(transport=transport, base_url="https://account.protonvpn.com") as c:
        with pytest.raises(ProtonError, match="Could not reach the Proton API"):
            _send(c, "GET", "/api/vpn/logicals")
