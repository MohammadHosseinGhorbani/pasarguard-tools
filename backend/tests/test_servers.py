"""Panel URL normalization keeps the BackPack path prefix; schemas stay whole."""
from app.routers.servers import _normalize_bp_url
from app.schemas import ServerOut


def test_normalize_cases():
    assert _normalize_bp_url("http://h:7777") == "http://h:7777"
    assert _normalize_bp_url("http://h:7777/") == "http://h:7777"
    assert _normalize_bp_url("http://h:7777/prefix/") == "http://h:7777/prefix"
    assert _normalize_bp_url("h:7777/abc") == "https://h:7777/abc"
    assert _normalize_bp_url("") == ""


def test_server_out_reports_bp_token():
    # Regression: ServerOut was once defined twice and the shadow dropped
    # has_bp_token, so tokens saved fine but never reported.
    assert "has_bp_token" in ServerOut.model_fields
    o = ServerOut(id=1, name="t", host="h", has_bp_token=True)
    assert o.model_dump()["has_bp_token"] is True
