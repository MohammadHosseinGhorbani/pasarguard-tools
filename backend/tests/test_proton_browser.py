"""proton_browser (Selenium login) unit tests — no real browser needed.

Ported from the ``proton-switcher`` project's ``tests/test_browser_login.py``
(SRP/CLI cases dropped; the login is browser-only here).
"""
import json

import pytest

from app import proton_browser as bl
from app.proton_lib import ProtonError


def test_session_from_cookies_happy():
    cookies = [
        {"name": "AUTH-abc123", "value": "TOK"},
        {"name": "Session-Id", "value": "SID"},
        {"name": "other", "value": "x"},
    ]
    s = bl.session_from_cookies(cookies, username="u@x.com",
                                app_version="v", auth_base="https://h")
    assert (s.uid, s.access_token, s.session_id) == ("abc123", "TOK", "SID")
    assert s.username == "u@x.com" and s.auth_base == "https://h"


def test_session_from_cookies_missing_auth():
    with pytest.raises(ProtonError, match="AUTH cookie"):
        bl.session_from_cookies([{"name": "Session-Id", "value": "SID"}])


def test_parse_auth_token_from_body():
    good = {"Code": 1000, "UID": "U", "AccessToken": "A",
            "RefreshToken": "R", "ExpiresIn": 100}
    assert bl.parse_auth_token_from_body(good) == good
    assert bl.parse_auth_token_from_body({"Code": 1000}) is None
    assert bl.parse_auth_token_from_body({"Code": 8002}) is None
    assert bl.parse_auth_token_from_body("nope") is None


def _perf_entry(url, request_id, status=200, method="Network.responseReceived"):
    return {"message": json.dumps({"message": {
        "method": method,
        "params": {"requestId": request_id,
                   "response": {"url": url, "status": status}}}})}


def test_collect_auth_tokens_skips_info_and_2fa():
    bodies = {
        "r1": {"Code": 1000, "UID": "U", "AccessToken": "A2FA"},
        "r2": {"Code": 1000, "UID": "U1", "AccessToken": "ACC1",
               "RefreshToken": "REF1", "ExpiresIn": 999},
    }
    logs = [
        _perf_entry("https://x/api/core/v4/auth/2fa", "r1"),
        _perf_entry("https://x/api/core/v4/auth", "r2"),
    ]
    got = bl.collect_auth_tokens_from_perf_logs(logs, bodies.get)
    assert got["AccessToken"] == "ACC1"


def test_collect_auth_tokens_returns_none_when_absent():
    logs = [_perf_entry("https://x/api/core/v4/auth/info", "r9")]
    assert bl.collect_auth_tokens_from_perf_logs(
        logs, lambda rid: {"Code": 1000}) is None


class FakeDriver:
    """Minimal webdriver double for selenium_login's cookie path."""

    def __init__(self, cookies, perf_logs=()):
        self._cookies = cookies
        self._logs = list(perf_logs)
        self.visited = []
        self.quit_called = False
        self.current_url = "https://account.protonvpn.com/login"

    def get(self, url):
        self.visited.append(url)

    def get_cookies(self):
        return self._cookies

    def get_log(self, kind):
        assert kind == "performance"
        return self._logs

    def execute_cdp_cmd(self, *a, **k):
        raise AssertionError("no CDP expected in cookie-only test")

    def execute_script(self, *a, **k):
        return {"local": {}, "session": {}, "url": self.current_url}

    def find_elements(self, *a, **k):
        return []

    def quit(self):
        self.quit_called = True


def test_selenium_login_cookie_only_with_injected_driver(monkeypatch):
    monkeypatch.setattr(bl, "_perform_form_login", lambda *a, **k: None)
    monkeypatch.setattr(bl, "_wait_for_auth_cookies",
                        lambda driver, **k: driver.get_cookies())
    monkeypatch.setattr(bl, "enrich_session_from_perf_logs",
                        lambda session, driver, **k: session)
    monkeypatch.setattr(bl, "_pick_valid_candidate",
                        lambda cands, *a, **k: cands[0][1])
    driver = FakeDriver([{"name": "AUTH-UID1", "value": "ACC1"},
                         {"name": "Session-Id", "value": "SID1"}])
    s = bl.selenium_login("u", "p", driver=driver)
    assert (s.uid, s.access_token, s.session_id) == ("UID1", "ACC1", "SID1")
    assert driver.visited == [bl.DEFAULT_LOGIN_URL]
    assert not driver.quit_called  # caller-owned driver is not quit

    with pytest.raises(ProtonError):
        bl.selenium_login("", "p", driver=driver)


def test_selenium_login_uses_auth_response_when_no_auth_cookie(monkeypatch):
    monkeypatch.setattr(bl, "_perform_form_login", lambda *a, **k: None)
    monkeypatch.setattr(bl, "_wait_for_auth_cookies",
                        lambda driver, **k: driver.get_cookies())
    monkeypatch.setattr(bl, "enrich_session_from_perf_logs",
                        lambda session, driver, **k: session)
    monkeypatch.setattr(bl, "_pick_valid_candidate",
                        lambda cands, *a, **k: cands[0][1])
    logs = [_perf_entry("https://account.protonvpn.com/api/core/v4/auth", "r1")]
    driver = FakeDriver([{"name": "Session-Id", "value": "SID1"}],
                        perf_logs=logs)
    monkeypatch.setattr(bl, "make_cdp_body_getter",
                        lambda driver: lambda rid: {
                            "Code": 1000, "UID": "UID1", "AccessToken": "ACC2",
                            "RefreshToken": "REF2", "ExpiresIn": 1234,
                            "SessionID": "SID2"})
    s = bl.selenium_login("u", "p", driver=driver)
    assert (s.access_token, s.refresh_token, s.session_id) == ("ACC2", "REF2", "SID2")
    assert s.expires_in == 1234


def test_session_from_storage_happy():
    blob = {"Code": 1000, "UID": "U9", "AccessToken": "A9",
            "RefreshToken": "R9", "ExpiresIn": 500, "SessionID": "S9"}
    s = bl.session_from_storage(blob, username="u")
    assert s is not None
    assert (s.uid, s.access_token, s.refresh_token, s.session_id) == ("U9", "A9", "R9", "S9")
    assert bl.session_from_storage({"Code": 1000}) is None


def test_read_storage_tokens_finds_json_blob():
    blob = json.dumps({"Code": 1000, "UID": "U", "AccessToken": "A"})

    class StorageDriver(FakeDriver):
        def execute_script(self, *a, **k):
            return {"local": {"k": blob}, "session": {}, "url": self.current_url}

    assert bl._read_storage_tokens(StorageDriver([]))["AccessToken"] == "A"
    assert bl._read_storage_tokens(FakeDriver([])) is None


def test_read_storage_tokens_accepts_raw_blob_without_code():
    """LocalStorage blobs carry no API Code envelope — must still match."""
    raw = json.dumps({"UID": "U-raw", "AccessToken": "A-raw",
                      "RefreshToken": "R-raw"})

    class StorageDriver(FakeDriver):
        def execute_script(self, *a, **k):
            return {"local": {"sess": raw}, "session": {}, "url": self.current_url}

    got = bl._read_storage_tokens(StorageDriver([]))
    assert got is not None and got["UID"] == "U-raw"

    nested = json.dumps({"Session": {"UID": "U-n", "AccessToken": "A-n"}})

    class NestedDriver(FakeDriver):
        def execute_script(self, *a, **k):
            return {"local": {"x": nested}, "session": {}, "url": self.current_url}

    assert bl._read_storage_tokens(NestedDriver([]))["UID"] == "U-n"
    s = bl.session_from_storage({"UID": "U", "AccessToken": "A",
                                 "RefreshToken": "R", "ExpiresIn": 10,
                                 "SessionID": "S"})
    assert s is not None and (s.uid, s.refresh_token) == ("U", "R")


def test_detect_captcha_ignores_js_bundle_text():
    class BundleDriver(FakeDriver):
        page_source = "<script>/* captcha client */</script>"

    assert bl._detect_captcha(BundleDriver([])) is False

    class ShownDriver(FakeDriver):
        current_url = "https://account.protonvpn.com/login"

        def find_elements(self, *a, **k):
            class El:
                def is_displayed(self):
                    return True
            return [El()]

    assert bl._detect_captcha(ShownDriver([])) is True


def test_selenium_login_storage_fallback_without_auth_cookie(monkeypatch):
    monkeypatch.setattr(bl, "_perform_form_login", lambda *a, **k: None)
    monkeypatch.setattr(bl, "_wait_for_auth_cookies",
                        lambda driver, **k: driver.get_cookies())
    monkeypatch.setattr(bl, "enrich_session_from_perf_logs",
                        lambda session, driver, **k: session)
    blob = {"Code": 1000, "UID": "UID9", "AccessToken": "ACC9",
            "RefreshToken": "REF9", "ExpiresIn": 777}
    monkeypatch.setattr(bl, "_read_storage_tokens", lambda driver: blob)
    monkeypatch.setattr(bl, "_pick_valid_candidate",
                        lambda cands, *a, **k: cands[0][1])
    driver = FakeDriver([{"name": "Session-Id", "value": "SID9"}])
    s = bl.selenium_login("u", "p", driver=driver)
    assert (s.uid, s.access_token, s.refresh_token) == ("UID9", "ACC9", "REF9")


def test_summarize_storage_shapes_quotes_bucket():
    """The diagnostic JS must use window['localStorage'], not window[localStorage]."""
    seen_js = []

    class JsDriver(FakeDriver):
        def execute_script(self, js, *a, **k):
            seen_js.append(js)
            if "localStorage" in js:
                return ["localStorage:ps-1 -> json dict keys=UID,AccessToken"]
            return []

    shapes = bl._summarize_storage_shapes(JsDriver([]))
    assert shapes == ["localStorage:ps-1 -> json dict keys=UID,AccessToken"]
    assert any("window[__B]" in js for js in seen_js)
    assert not any("window[localStorage]" in js for js in seen_js)


def test_auth_urls_in_perf_logs_lists_only_auth():
    logs = [
        {"message": json.dumps({"message": {"method": "Network.responseReceived",
                "params": {"requestId": "1", "response": {
                    "url": "https://x/api/core/v4/auth", "status": 200}}}})},
        {"message": json.dumps({"message": {"method": "Network.responseReceived",
                "params": {"requestId": "2", "response": {
                    "url": "https://x/api/vpn/logicals", "status": 200}}}})},
    ]

    class LogDriver(FakeDriver):
        def __init__(self):
            super().__init__([], perf_logs=logs)

    urls = bl._auth_urls_in_perf_logs(LogDriver())
    assert urls == ["200 https://x/api/core/v4/auth"]


def test_all_cookies_reveals_cdp_hidden_auth_cookie():
    """AUTH cookie invisible to the Selenium jar but present via CDP."""

    class CdpDriver(FakeDriver):
        def __init__(self):
            super().__init__([{"name": "Session-Id", "value": "SID"}])

        def execute_cdp_cmd(self, method, params):
            assert method in ("Network.getAllCookies", "Storage.getCookies")
            return {"cookies": [
                {"name": "AUTH-HIDDENUID", "value": "ACCTOK",
                 "domain": ".protonvpn.com"},
                {"name": "Session-Id", "value": "SID",
                 "domain": ".protonvpn.com"},
            ]}

    driver = CdpDriver()
    assert bl._has_auth_cookie(driver) is True
    merged = bl._all_cookies(driver)
    s = bl.session_from_cookies(merged)
    assert (s.uid, s.access_token, s.session_id) == ("HIDDENUID", "ACCTOK", "SID")
    names = bl._cookie_debug_names(driver)
    assert "AUTH-HIDDENUID@.protonvpn.com" in names


def test_looks_like_blob():
    import base64

    assert bl._looks_like_blob("") is True
    assert bl._looks_like_blob("{raw-json}") is True
    blob = base64.b64encode(b'{"iv":"abc","data":"x"}').decode()
    assert bl._looks_like_blob(blob) is True
    assert bl._looks_like_blob("opaque-bearer-token-123 admittedly") is False


def _will_be_sent(url, headers):
    return {"message": json.dumps({"message": {
        "method": "Network.requestWillBeSent",
        "params": {"requestId": "7", "request": {
            "url": url, "headers": headers}}}})}


def test_extract_request_credentials_prefers_auth_cookie():
    logs = [_will_be_sent(
        "https://account.protonvpn.com/api/vpn/v1/certificate/all",
        {"x-pm-uid": "U1",
         "Cookie": "Session-Id=SID1; AUTH-U1=TOK1; Tag=t",
         "Authorization": "Bearer TOK1"})]
    got = bl.extract_request_credentials_from_perf_logs(logs)
    assert got == [{"uid": "U1", "token": "TOK1", "session_id": "SID1"}]


def test_extract_request_credentials_skips_non_vpn_and_blobs():
    import base64
    blob = base64.b64encode(b'{"iv":"x"}').decode()
    logs = [
        _will_be_sent("https://x/api/core/v4/auth",
                      {"x-pm-uid": "U", "Cookie": "AUTH-U=BAD"}),
        _will_be_sent("https://x/api/vpn/logicals",
                      {"x-pm-uid": "U", "Cookie": f"AUTH-U={blob}"}),
    ]
    assert bl.extract_request_credentials_from_perf_logs(logs) == []


def test_collect_skips_sessions_and_takes_last():
    bodies = {
        "anon": {"Code": 1000, "UID": "ANON", "AccessToken": "A0"},
        "real": {"Code": 1000, "UID": "U", "AccessToken": "A1",
                 "RefreshToken": "R1"},
    }
    logs = [
        _perf_entry("https://x/api/auth/v4/sessions", "anon"),
        _perf_entry("https://x/api/core/v4/auth", "real"),
    ]
    got = bl.collect_auth_tokens_from_perf_logs(logs, bodies.get)
    assert got["UID"] == "U"


def test_pick_valid_candidate_skips_rejected(monkeypatch):
    from app.proton_lib import ProtonSession

    bad = ProtonSession(uid="B", access_token="T", session_id="S")
    good = ProtonSession(uid="G", access_token="T", session_id="S")
    verdicts = {"B": "auth", "G": "ok"}
    monkeypatch.setattr(bl, "_validate_session",
                        lambda sess, *a, **k: verdicts[sess.uid])
    picked = bl._pick_valid_candidate([("x", bad), ("y", good)], "https://h")
    assert picked.uid == "G"
    with pytest.raises(ProtonError):
        bl._pick_valid_candidate([("x", bad)], "https://h")


def test_settle_accepts_stable_cookie():
    class Stable(FakeDriver):
        def __init__(self):
            super().__init__([{"name": "AUTH-U", "value": "T",
                               "domain": "d"}])

        def execute_cdp_cmd(self, *a, **k):
            return {"cookies": []}

    got = bl._settle_auth_cookies(Stable(), settle_seconds=0)
    assert got and got[0]["value"] == "T"


def test_settle_rejects_transient_cookie():
    class Flaky(FakeDriver):
        def __init__(self):
            super().__init__([{"name": "AUTH-U", "value": "T"}])
            self.reads = 0

        def get_cookies(self):
            self.reads += 1
            return super().get_cookies() if self.reads == 1 else []

        def execute_cdp_cmd(self, *a, **k):
            return {"cookies": []}

    assert bl._settle_auth_cookies(Flaky(), settle_seconds=0) is None
    assert bl._settle_auth_cookies(FakeDriver([]), settle_seconds=0) is None


def test_left_login_page():
    assert bl._left_login_page(FakeDriver([])) is False  # default url is /login
    done = FakeDriver([])
    done.current_url = "https://account.protonvpn.com/dashboardV2"
    assert bl._left_login_page(done) is True


def _visible_pw_driver(url):
    class PwDriver(FakeDriver):
        def find_elements(self, *a, **k):
            sel = a[1] if len(a) > 1 else ""
            if "password" in str(sel):
                class El:
                    def is_displayed(self):
                        return True
                return [El()]
            return []
    d = PwDriver([])
    d.current_url = url
    return d


def test_wait_for_password_ignores_pre_auth_cookie():
    """Regression: a pre-auth AUTH cookie on /login must NOT skip the
    password step — the password field is visible, so proceed to fill it."""
    d = _visible_pw_driver("https://account.protonvpn.com/login")
    d._cookies = [{"name": "AUTH-pre", "value": "T"}]
    assert bl._wait_for_password_or_completion(d, timeout=5) == "password"


def test_wait_for_password_returns_done_off_login():
    d = FakeDriver([])
    d.current_url = "https://account.protonvpn.com/dashboardV2"
    assert bl._wait_for_password_or_completion(d, timeout=5) == "done"


def test_wait_for_password_raises_when_stuck(monkeypatch):
    d = FakeDriver([])
    d.current_url = "https://account.protonvpn.com/login"
    ticks = iter([0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(bl.time, "time", lambda: next(ticks, 100.0))
    with pytest.raises(ProtonError, match="Password field not found"):
        bl._wait_for_password_or_completion(d, timeout=5)
