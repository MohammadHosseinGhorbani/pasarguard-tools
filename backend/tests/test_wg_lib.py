"""wg_lib: conversion matches the dashboard shape; errors are explicit."""
import pytest

from app.wg_lib import parse_wg_conf, wg_conf_to_outbound

CONF = """[Interface]
PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
Address = 10.2.0.2/32, 2a07:b944::2:2/128
DNS = 10.2.0.1, 2a07:b944::2:1
MTU = 1280

[Peer]
PublicKey = gucaLaM/mgJQbHVvnZNtW+1L4Mi7E2mtTMrhS0K4miU=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 146.70.230.146:51820
PersistentKeepalive = 25
"""


def test_convert_full():
    ob = wg_conf_to_outbound("proton-us", CONF)
    assert ob["protocol"] == "wireguard"
    assert ob["tag"] == "proton-us"
    st = ob["settings"]
    assert st["address"] == ["10.2.0.2/32", "2a07:b944::2:2/128"]
    assert st["DNS"] == "10.2.0.1,2a07:b944::2:1"
    assert st["mtu"] == 1280
    assert st["domainStrategy"] == "ForceIP"
    assert st["noKernelTun"] is True
    assert st["reserved"] == [0, 0, 0]
    (peer,) = st["peers"]
    assert peer["endpoint"] == "146.70.230.146:51820"
    assert peer["allowedIPs"] == ["0.0.0.0/0", "::/0"]
    assert peer["keepAlive"] == 25


def test_convert_minimal_defaults():
    ob = wg_conf_to_outbound(
        "wg", "[Interface]\nPrivateKey = x\n[Peer]\nPublicKey = y\nEndpoint = h:1\n"
    )
    st = ob["settings"]
    assert "address" not in st and "mtu" not in st and "DNS" not in st
    assert st["peers"][0]["allowedIPs"] == ["0.0.0.0/0", "::/0"]
    assert "keepAlive" not in st["peers"][0]


def test_ipv6_endpoint_keeps_brackets():
    ob = wg_conf_to_outbound(
        "wg", "[Interface]\nPrivateKey = x\n[Peer]\nPublicKey = y\nEndpoint = [::1]:51820\n"
    )
    assert ob["settings"]["peers"][0]["endpoint"] == "[::1]:51820"


@pytest.mark.parametrize(
    "text,missing",
    [
        ("", "Interface"),
        ("[Interface]\nAddress = 1.1.1.1\n", "Peer"),
        (
            "[Interface]\nPrivateKey = x\n[Peer]\nPublicKey = y\nEndpoint = h:1\n"
            "[Peer]\nPublicKey = z\nEndpoint = h:2\n",
            "one \\[Peer\\]",
        ),
        ("[Interface]\nPrivateKey = x\n[Peer]\nPublicKey = y\nEndpoint = nope\n", "Endpoint"),
    ],
)
def test_convert_errors(text, missing):
    with pytest.raises(ValueError, match=missing):
        wg_conf_to_outbound("t1", text)


def test_tag_required():
    with pytest.raises(ValueError, match="name is required"):
        wg_conf_to_outbound("", CONF)


def test_name_comment_and_inline_comments():
    text = "# name: myvpn\n[Interface] # the iface\nPrivateKey = x # secret\n[Peer]\nPublicKey = y\nEndpoint = h:1\n"
    groups = parse_wg_conf(text)
    assert groups[0]["name"] == "myvpn"
    assert groups[0]["iface"]["privatekey"] == "x"
