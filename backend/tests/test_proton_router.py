"""Proton router: settings CRUD + guards (no network, no TestClient).

TestClient is unusable with this env's starlette/httpx pair, so endpoint
functions are called directly with an isolated in-memory DB session.
"""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ProtonConfig
from app.routers import proton as proton_router
from app.schemas import ProtonConfigIn, ProtonLoginIn, ProtonSettingsIn


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()


def test_settings_roundtrip(db):
    assert proton_router.get_settings(db=db) == {
        "username": "",
        "has_password": False,
        "app_version": "web-vpn-settings@5.0.422.0",
        "proxy": "",
        "logged_in": False,
        "mode": "none",
        "uid": "",
        "expires_at": 0,
        "seconds_left": -1,
        "days_left": None,
    }

    status = proton_router.save_settings(
        ProtonSettingsIn(
            username="me@example.com",
            password="secret",
            app_version="",
            proxy="http://127.0.0.1:10808",
        ),
        db=db,
    )
    assert status["username"] == "me@example.com"
    assert status["has_password"] is True
    assert status["proxy"] == "http://127.0.0.1:10808"
    assert status["logged_in"] is False

    # Saving without a password keeps the stored one.
    status = proton_router.save_settings(ProtonSettingsIn(username="me@example.com"), db=db)
    assert status["has_password"] is True

    assert proton_router.logout(db=db)["mode"] == "none"
    assert proton_router.clear_account(db=db)["ok"] is True
    assert proton_router.get_settings(db=db)["username"] == ""


def test_login_requires_credentials(db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(proton_router.login(ProtonLoginIn(), db=db))
    assert exc.value.status_code == 400
    assert "username and password" in exc.value.detail.lower()


def test_guards_require_login(db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(proton_router.create_config(ProtonConfigIn(name="nl"), db=db))
    assert exc.value.status_code == 400 and "not logged in" in exc.value.detail.lower()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(proton_router.rotate_config(ProtonConfigIn(name="nl"), db=db))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        asyncio.run(proton_router.list_servers(db=db))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        asyncio.run(proton_router.delete_config("nope", db=db))
    assert exc.value.status_code == 404


def test_name_is_sanitized_on_create(db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(proton_router.create_config(ProtonConfigIn(name="  "), db=db))
    assert exc.value.status_code == 400


def test_list_configs_renders_conf(db):
    db.add(
        ProtonConfig(
            name="netherlands",
            country="NL",
            server_name="NL#7",
            entry_ip="185.1.2.3",
            port=51820,
            address="10.2.0.2/32",
            dns="10.2.0.1",
            private_key="AAA=",
            server_pubkey="BBB=",
            serial="SERIAL1",
            expires_at=123,
            created_at=456.0,
        )
    )
    db.commit()

    data = proton_router.list_configs(db=db)
    assert data["logged_in"] is False
    (c,) = data["configs"]
    assert c["outbound_tag"] == "proton-netherlands"
    assert c["serial"] == "SERIAL1"
    assert "[Interface]" in c["conf"]
    assert "PrivateKey = AAA=" in c["conf"]
    assert "Endpoint = 185.1.2.3:51820" in c["conf"]
    assert c["conf"].count("[Peer]") == 1
