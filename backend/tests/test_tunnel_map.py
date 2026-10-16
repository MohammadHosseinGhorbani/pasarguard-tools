"""Transport name mapping tolerates panel label variations, never raises."""
from app.tunnel_lib import map_test_transport


def test_known():
    assert map_test_transport("tcpmux") == "tcpmux"
    assert map_test_transport("TCP Mux (Turbo)") == "tcpmux"
    assert map_test_transport("directxdi") == "direct:xdi"
    assert map_test_transport("spoof") is None


def test_unknown_returns_none():
    assert map_test_transport("iran-kharej") is None
    assert map_test_transport("") is None
