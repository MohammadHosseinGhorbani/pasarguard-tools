"""Login to Proton through headless Chromium (Selenium).

Ported from the ``proton-switcher`` project
(``proton_switcher/browser_login.py``).

Why a real browser: the raw SRP-over-HTTP handshake trips Proton's bot
defenses (2028 rate-limit, 9001 human-verification) because its TLS/JS
fingerprint differs from a real browser. Driving the real login page in
headless Chromium yields the exact cookies + tokens the dashboard uses.

Flow:
  1. Open ``login_url`` (default https://account.protonvpn.com/login)
     in headless Chromium.
  2. Fill username/password (+ TOTP if the account uses it), submit.
  3. Wait until the ``AUTH-<uid>`` cookie appears (login success).
  4. Build a :class:`~app.proton_lib.ProtonSession` from the cookies.
  5. Best-effort: scrape the ``/api/.../auth`` response from the browser
     performance logs to also grab RefreshToken/ExpiresIn so the session
     lasts ~30 days with auto-refresh. Falls back to cookie-only (~24h)
     when the capture misses.

Headless only: the backend runs on a server with no display, so a CAPTCHA
that needs a human cannot be solved interactively. When Proton shows one we
raise a clear error instead of hanging.

Selenium is imported lazily so unit tests (and every non-login endpoint)
work without a browser installed. Chromium + its driver are installed in
the backend image (see ``backend/Dockerfile``); override the paths with the
``PROTON_CHROME_BINARY`` / ``PROTON_CHROMEDRIVER`` env vars.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
from typing import Any, Callable

from .proton_lib import (
    DEFAULT_APP_VERSION,
    DEFAULT_EXPIRES_IN,
    DEFAULT_LOCALE,
    DEFAULT_USER_AGENT,
    PROTON_API_BASE,
    ProtonError,
    ProtonSession,
    _new_client,
)

DEFAULT_LOGIN_URL = "https://account.protonvpn.com/login"

# CSS selectors tried in order; Proton's markup changes over time so we
# accept several spellings instead of one brittle id.
USERNAME_SELECTORS = [
    "#username",
    "input[name='username']",
    "input[type='email']",
    "input[id*='username' i]",
    "input[autocomplete='username']",
]
PASSWORD_SELECTORS = [
    "#password",
    "input[name='password']",
    "input[type='password']",
]
# (By-kind, locator) pairs for buttons. Proton's login is two-step
# (username -> Continue -> password -> Sign in), so text-based XPaths are
# needed: a bare `button[type=submit]` miss leaves the first step unclicked.
SUBMIT_LOCATORS = [
    ("css", "button[type='submit']"),
    ("xpath", "//button[@type='submit']"),
    ("xpath", "//button[contains(translate(normalize-space(.),"
             " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]"),
    ("xpath", "//button[contains(translate(normalize-space(.),"
             " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]"),
    ("xpath", "//button[contains(translate(normalize-space(.),"
             " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in')]"),
    ("xpath", "//button[contains(translate(normalize-space(.),"
             " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'log in')]"),
    ("css", "button[data-testid='login-button']"),
]
# Back-compat alias (tests / external callers may import it).
SUBMIT_SELECTORS = [loc for _, loc in SUBMIT_LOCATORS]
TOTP_SELECTORS = [
    "input[name='twoFactorCode']",
    "input[name='totp']",
    "#twoFactorCode",
    "input[inputmode='numeric']",
    "input[autocomplete='one-time-code']",
]
# Human-verification / CAPTCHA markers (Proton uses several vendors).
CAPTCHA_MARKERS = [
    "captcha",
    "cf-turnstile",
    "g-recaptcha",
    "human-verification",
    "humanVerification",
]
ERROR_SELECTORS = [
    ".error",
    "[role='alert']",
    ".notification-error",
]


def _dbg(enabled: bool, msg: str) -> None:
    if enabled:
        print(f"[proton-browser] {msg}", flush=True)


def _env_binary(name: str) -> str | None:
    val = (os.environ.get(name) or "").strip()
    return val or None


def build_chrome_options(*, headless: bool = True, chrome_binary: str | None = None,
                         user_agent: str | None = None, proxy: str | None = None):
    """Build Selenium Chrome Options for the login run (lazy import)."""
    try:
        from selenium import webdriver
    except ImportError as e:
        raise ProtonError(
            "selenium is not installed in the backend image. "
            "Rebuild the backend container (see backend/Dockerfile)."
        ) from e
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-setuid-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    # Quiet Chrome's own stderr noise (DevTools/GCM/updater/SSL lines);
    # those are harmless but drown out our own status output.
    opts.add_argument("--disable-logging")
    opts.add_argument("--log-level=3")
    try:
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    except Exception:
        pass
    if user_agent:
        opts.add_argument(f"--user-agent={user_agent}")
    if chrome_binary:
        opts.binary_location = chrome_binary
    if proxy:
        # Strip scheme: --proxy-server takes host:port (scheme:// would break it).
        server = str(proxy).strip()
        if "://" in server:
            server = server.split("://", 1)[1]
        server = server.rstrip("/")
        if server:
            opts.add_argument(f"--proxy-server={server}")
    # Performance logs let us capture the /auth API response body
    # (AccessToken + RefreshToken) without touching page JS internals.
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return opts


def create_driver(*, headless: bool = True, chrome_binary: str | None = None,
                  chromedriver: str | None = None, user_agent: str | None = None,
                  debug: bool = False, proxy: str | None = None):
    """Create a real Chrome webdriver.

    Uses explicit ``chromedriver`` / ``chrome_binary`` paths when given (or
    via the ``PROTON_CHROMEDRIVER`` / ``PROTON_CHROME_BINARY`` env vars), so
    the container does not depend on Selenium Manager reaching the network.
    """
    try:
        from selenium import webdriver
    except ImportError as e:
        raise ProtonError(
            "selenium is not installed in the backend image. "
            "Rebuild the backend container (see backend/Dockerfile)."
        ) from e
    chrome_binary = chrome_binary or _env_binary("PROTON_CHROME_BINARY")
    chromedriver = chromedriver or _env_binary("PROTON_CHROMEDRIVER")
    opts = build_chrome_options(
        headless=headless,
        chrome_binary=chrome_binary,
        user_agent=user_agent or DEFAULT_USER_AGENT,
        proxy=proxy,
    )
    _dbg(debug, f"starting chrome headless={headless} "
                f"binary={chrome_binary or 'auto'} driver={chromedriver or 'auto'} "
                f"proxy={proxy or 'none'}")
    try:
        if chromedriver:
            from selenium.webdriver.chrome.service import Service
            driver = webdriver.Chrome(
                service=Service(executable_path=chromedriver), options=opts)
        else:
            driver = webdriver.Chrome(options=opts)
    except Exception as e:
        raise ProtonError(
            f"Could not start headless Chromium via Selenium: {e}. "
            "Make sure Chromium + chromedriver are installed (they are in the "
            "backend Docker image; set PROTON_CHROME_BINARY / "
            "PROTON_CHROMEDRIVER to override the paths)."
        ) from e
    # Bound page loads so a stuck navigation cannot outlast the reverse proxy.
    try:
        driver.set_page_load_timeout(60)
    except Exception:
        pass
    return driver


# -- cookie / token extraction (pure, unit-tested) ------------------------


def session_from_cookies(cookies: list[dict[str, Any]], *,
                         app_version: str = DEFAULT_APP_VERSION,
                         username: str = "",
                         auth_base: str = "") -> ProtonSession:
    """Build a session from Selenium ``get_cookies()`` output.

    Looks for ``AUTH-<uid>`` (access token) and ``Session-Id``.
    Raises ProtonError when the auth cookie is absent (= not logged in).
    """
    uid = ""
    token = ""
    session_id = ""
    for c in cookies or []:
        name = str(c.get("name", ""))
        value = str(c.get("value", ""))
        if name.startswith("AUTH-") and len(name) > 5 and not token:
            uid = name[len("AUTH-"):]
            token = value
        elif name.lower() in ("session-id", "sessionid", "session_id") and not session_id:
            session_id = value
    if not uid or not token:
        names = sorted(str(c.get("name", "?")) for c in cookies or [])
        raise ProtonError(
            "Login did not produce an AUTH cookie (still on the login page?). "
            f"Cookies seen: {names}. Wrong password or pending 2FA."
        )
    return ProtonSession(
        uid=uid, access_token=token, session_id=session_id,
        app_version=app_version, username=username, auth_base=auth_base,
    )


def _session_id_from_cookies(cookies: list[dict[str, Any]]) -> str:
    for c in cookies or []:
        if str(c.get("name", "")).lower() in ("session-id", "sessionid", "session_id"):
            return str(c.get("value", ""))
    return ""


def parse_auth_token_from_body(body: Any) -> dict[str, Any] | None:
    """Return token fields if `body` looks like an /auth success response."""
    if not isinstance(body, dict):
        return None
    if body.get("Code") not in (1000, 1001):
        return None
    if not body.get("AccessToken") or not body.get("UID"):
        return None
    return body


def _looks_like_blob(value: Any) -> bool:
    """True when a token value is an encrypted envelope, not a bearer token.

    The dashboard's anonymous/bootstrap responses can carry fields shaped
    like tokens (e.g. base64 of ``{"iv":...}``). Saving one produces a
    session that later fails with 9106 "insufficient scope".
    """
    if not isinstance(value, str):
        return True
    v = value.strip()
    if not v or v.startswith("{"):
        return True
    try:
        padded = v + "=" * (-len(v) % 4)
        raw = base64.b64decode(padded, validate=False)
        if raw[:1] == b"{" or b'"iv"' in raw[:64]:
            return True
    except Exception:
        pass
    return False


def _is_auth_response_url(url: str) -> bool:
    u = url or ""
    if "/api/auth" not in u and "/api/core/v4/auth" not in u:
        return False
    # /auth/info and /auth/2fa carry no usable session; /sessions is the
    # anonymous bootstrap (limited scope) — never mistake it for the login.
    tail = u.rstrip("/")
    if tail.endswith("/auth/info") or "/auth/2fa" in u or "/sessions" in u:
        return False
    return True


def collect_auth_tokens_from_perf_logs(
    log_entries: list[Any],
    get_body: Callable[[str], Any | None],
    *,
    debug: bool = False,
) -> dict[str, Any] | None:
    """Scan Selenium perf logs for the /auth response carrying the refresh token.

    `log_entries` are raw ``driver.get_log("performance")`` items.
    `get_body(request_id)` must return the parsed JSON response body
    (normally via CDP ``Network.getResponseBody``). Returns the last
    token-bearing body (the real login beats the earlier anonymous
    bootstrap), else None. Encrypted-blob lookalikes are skipped.
    """
    matches: list[dict[str, Any]] = []
    for entry in log_entries or []:
        try:
            msg = json.loads(entry["message"]) if isinstance(entry, dict) and "message" in entry else entry
            message = msg.get("message", msg) if isinstance(msg, dict) else None
            if not isinstance(message, dict):
                continue
            if message.get("method") != "Network.responseReceived":
                continue
            params = message.get("params", {})
            response = params.get("response", {})
            url = str(response.get("url", ""))
            status = int(response.get("status", 0) or 0)
            if status != 200 or not _is_auth_response_url(url):
                continue
            request_id = str(params.get("requestId", ""))
            if not request_id:
                continue
            body = get_body(request_id)
            token_body = parse_auth_token_from_body(body)
            if token_body is not None:
                if _looks_like_blob(token_body.get("AccessToken")):
                    _dbg(debug, f"skipping blob-like token from {url}")
                    continue
                _dbg(debug, f"captured auth tokens from {url}")
                matches.append(token_body)
        except Exception:
            continue
    return matches[-1] if matches else None


def get_perf_logs(driver: Any) -> list[Any]:
    try:
        return driver.get_log("performance") or []
    except Exception:
        return []


def make_cdp_body_getter(driver: Any) -> Callable[[str], Any | None]:
    """Return get_body(request_id) using CDP Network.getResponseBody."""

    def _get(request_id: str) -> Any | None:
        try:
            data = driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": request_id})
            text = data.get("body", "")
            if data.get("base64Encoded"):
                text = base64.b64decode(text).decode("utf-8", "replace")
            return json.loads(text)
        except Exception:
            return None

    return _get


def _split_cookie_header(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (value or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, val = part.partition("=")
        out[name.strip()] = val.strip()
    return out


def extract_request_credentials_from_perf_logs(
    log_entries: list[Any],
) -> list[dict[str, str]]:
    """Ground truth: auth headers the working page sent to ``/api/vpn/*``.

    Returns [{uid, token, session_id}] in order of appearance — verbatim what
    the logged-in dashboard transmits, so scope mismatches can't happen.
    Prefers the ``AUTH-<uid>`` cookie value, falls back to Bearer.
    """
    creds: list[dict[str, str]] = []
    for entry in log_entries or []:
        try:
            msg = json.loads(entry["message"]) if isinstance(entry, dict) and "message" in entry else entry
            message = msg.get("message", msg) if isinstance(msg, dict) else None
            if not isinstance(message, dict):
                continue
            if message.get("method") != "Network.requestWillBeSent":
                continue
            request = message.get("params", {}).get("request", {})
            url = str(request.get("url", ""))
            if "/api/vpn/" not in url:
                continue
            headers = {str(k).lower(): v for k, v in
                       (request.get("headers", {}) or {}).items()}
            uid = str(headers.get("x-pm-uid", "") or "")
            cookies = _split_cookie_header(str(headers.get("cookie", "") or ""))
            token = ""
            cookie_uid = ""
            for name, val in cookies.items():
                if name.startswith("AUTH-") and len(name) > 5 and val:
                    cookie_uid, token = name[len("AUTH-"):], val
                    break
            if not token:
                auth = str(headers.get("authorization", "") or "")
                if auth.lower().startswith("bearer ") and len(auth) > 7:
                    token = auth[7:].strip()
            session_id = cookies.get("Session-Id", cookies.get("SessionID", ""))
            if _looks_like_blob(token):
                continue
            final_uid = cookie_uid or uid
            if not final_uid or not token:
                continue
            cred = {"uid": final_uid, "token": token,
                    "session_id": str(session_id or "")}
            if cred not in creds:
                creds.append(cred)
        except Exception:
            continue
    return creds


def enrich_session_from_perf_logs(session: ProtonSession, driver: Any,
                                  *, debug: bool = False) -> ProtonSession:
    """Best-effort: upgrade a cookie session with refresh token from perf logs."""
    try:
        logs = get_perf_logs(driver)
        _dbg(debug, f"perf log entries: {len(logs)}")
        if not logs:
            return session
        tokens = collect_auth_tokens_from_perf_logs(
            logs, make_cdp_body_getter(driver), debug=debug)
        if not tokens:
            _dbg(debug, "no /auth token response in perf logs; cookie-only session")
            return session
        now = time.time()
        session.access_token = str(tokens.get("AccessToken", session.access_token))
        session.uid = str(tokens.get("UID", session.uid))
        refresh = str(tokens.get("RefreshToken", "") or "")
        if refresh:
            session.refresh_token = refresh
        try:
            session.expires_in = int(tokens.get("ExpiresIn", 0) or DEFAULT_EXPIRES_IN)
        except (TypeError, ValueError):
            session.expires_in = DEFAULT_EXPIRES_IN
        session.obtained_at = now
        sid = str(tokens.get("SessionID", tokens.get("SessionId", "")) or "")
        if sid:
            session.session_id = sid
        _dbg(debug, f"enriched session with refresh token (expires_in={session.expires_in})")
    except Exception as e:
        _dbg(debug, f"perf-log enrichment skipped: {e}")
    return session


# -- page interaction (needs a real driver) -------------------------------


def _by(css_or_pair):
    """Normalize a selector to a (By, locator) tuple (lazy selenium import)."""
    from selenium.webdriver.common.by import By
    if isinstance(css_or_pair, (tuple, list)):
        kind, loc = css_or_pair
        return (By.XPATH, loc) if str(kind).lower() == "xpath" else (By.CSS_SELECTOR, loc)
    return (By.CSS_SELECTOR, css_or_pair)


def _find_css(driver: Any, selector: str, timeout: float):
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    by, loc = _by(selector)
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, loc)))


def _fill_first(driver: Any, selectors: list[str], value: str,
                *, timeout: float, debug: bool = False) -> bool:
    for sel in selectors:
        try:
            el = _find_css(driver, sel, timeout)
            el.clear()
            el.send_keys(value)
            _dbg(debug, f"filled {sel}")
            return True
        except Exception:
            continue
    return False


def _click_first(driver: Any, selectors: list[Any], *, timeout: float,
                 debug: bool = False) -> bool:
    """Click the first matching submit/continue button (scroll into view first)."""
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    for sel in selectors:
        try:
            by, loc = _by(sel)
            el = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, loc)))
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", el)
            except Exception:
                pass
            el.click()
            _dbg(debug, f"clicked {loc}")
            return True
        except Exception:
            continue
    return False


def _press_enter_in(driver: Any, selectors: list[str], debug: bool = False) -> bool:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        for sel in selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                el.send_keys(Keys.RETURN)
                _dbg(debug, f"submitted via Enter in {sel}")
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _page_text(driver: Any) -> str:
    try:
        return (driver.page_source or "")[:20000]
    except Exception:
        return ""


def _detect_captcha(driver: Any) -> bool:
    """True only when a *visible* human-verification widget is present.

    (A substring check on page_source false-positives on the JS bundles,
    which always mention 'captcha'.)
    """
    try:
        from selenium.webdriver.common.by import By
        for sel in ("iframe[src*='captcha' i]", "iframe[src*='challenge' i]",
                    ".g-recaptcha", ".cf-turnstile", "[data-sitekey]"):
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    try:
                        if el.is_displayed():
                            return True
                    except Exception:
                        return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        url = (driver.current_url or "").lower()
        if "challenge" in url or "verify" in url:
            return True
    except Exception:
        pass
    return False


def _detect_error(driver: Any) -> str | None:
    try:
        from selenium.webdriver.common.by import By
        for sel in ERROR_SELECTORS:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    text = (el.text or "").strip()
                    if text:
                        return text[:300]
            except Exception:
                continue
    except Exception:
        pass
    return None


# -- web-storage token fallback -------------------------------------------
# Proton's SPA sometimes keeps the session in localStorage/sessionStorage
# rather than (or in addition to) cookies, so read those too.


def _find_token_blob(obj: Any, depth: int = 0) -> dict[str, Any] | None:
    """Recursively find a {UID, AccessToken} dict inside parsed JSON.

    Unlike parse_auth_token_from_body (which matches /auth API envelopes
    with a Code field), this also matches raw stored blobs, which carry no
    Code, and nested shapes like {"Session": {...}}.
    """
    if depth > 5:
        return None
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return None
        return _find_token_blob(obj, depth + 1)
    if isinstance(obj, dict):
        if obj.get("AccessToken") and obj.get("UID"):
            return obj
        for val in obj.values():
            found = _find_token_blob(val, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = _find_token_blob(item, depth + 1)
            if found is not None:
                return found
    return None


def _scan_blob_for_tokens(obj: Any) -> dict[str, Any] | None:
    try:
        return _find_token_blob(obj)
    except Exception:
        return None


def _summarize_storage_shapes(driver: Any, max_items: int = 20) -> list[str]:
    """Describe web-storage entries by shape only (never values).

    Returns lines like ``ps-1 -> json dict keys=UID,AccessToken`` so we can
    see where the session lives without touching secrets.
    """
    run = getattr(driver, "execute_script", None)
    if not callable(run):
        return []
    out: list[str] = []
    for bucket in ("localStorage", "sessionStorage"):
        js = (
            "const __B = '%s';"
            "try {"
            "  const store = window[__B] || {};"
            "  return Object.keys(store).slice(0, 40).map(k => {"
            "    let v = ''; try { v = store.getItem(k) || ''; } catch (e) {}"
            "    let shape = 'str len=' + v.length;"
            "    try { const o = JSON.parse(v);"
            "      shape = Array.isArray(o) ? 'json list len=' + o.length"
            "        : (o && typeof o === 'object'"
            "          ? 'json dict keys=' + Object.keys(o).slice(0, 15).join(',')"
            "          : 'json scalar');"
            "    } catch (e) {}"
            "    return (__B + ':' + k).slice(0, 80) + ' -> ' + shape.slice(0, 220);"
            "  });"
            "} catch (e) { return []; }" % bucket)
        try:
            rows = run(js)
        except Exception:
            continue
        if isinstance(rows, list):
            out.extend(str(r) for r in rows[:40])
    return out[:max_items]


def _auth_urls_in_perf_logs(driver: Any) -> list[str]:
    """List distinct /api/auth* response URLs in perf logs (URLs only)."""
    try:
        logs = get_perf_logs(driver)
    except Exception:
        return []
    seen: list[str] = []
    for entry in logs or []:
        try:
            msg = json.loads(entry["message"]) if isinstance(entry, dict) and "message" in entry else entry
            message = msg.get("message", msg) if isinstance(msg, dict) else None
            if not isinstance(message, dict):
                continue
            if message.get("method") != "Network.responseReceived":
                continue
            params = message.get("params", {})
            response = params.get("response", {})
            url = str(response.get("url", ""))
            if "/api/auth" not in url and "/api/core/v4/auth" not in url:
                continue
            tag = f"{response.get('status', '?')} {url}".strip()[:160]
            if tag not in seen:
                seen.append(tag)
        except Exception:
            continue
    return seen


def _has_perf_auth_tokens(driver: Any, *, debug: bool = False) -> bool:
    """True when perf logs already hold an /auth token response."""
    try:
        logs = get_perf_logs(driver)
        if not logs:
            return False
        found = collect_auth_tokens_from_perf_logs(
            logs, make_cdp_body_getter(driver), debug=debug)
        return found is not None
    except Exception:
        return False


def _storage_key_names(driver: Any) -> list[str]:
    """Best-effort list of web-storage key names (names only, no values)."""
    run = getattr(driver, "execute_script", None)
    if not callable(run):
        return []
    try:
        data = run(
            "return {local: Object.keys(window.localStorage || {}),"
            " session: Object.keys(window.sessionStorage || {})};")
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    names: list[str] = []
    for bucket in ("local", "session"):
        keys = data.get(bucket)
        if isinstance(keys, list):
            names.extend(f"{bucket}:{k}" for k in keys)
    return names


def _read_storage_tokens(driver: Any) -> dict[str, Any] | None:
    """Look for an /auth-shaped token blob in web storage (best-effort)."""
    run = getattr(driver, "execute_script", None)
    if not callable(run):
        return None
    try:
        data = run(
            "return {local: Object.assign({}, window.localStorage),"
            " session: Object.assign({}, window.sessionStorage),"
            " url: window.location.href};")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for bucket in ("local", "session"):
        store = data.get(bucket)
        if not isinstance(store, dict):
            continue
        for key, val in store.items():
            try:
                found = _scan_blob_for_tokens(val)
            except Exception:
                continue
            if found is not None:
                return found
    return None


def session_from_storage(blob: dict[str, Any], *,
                         app_version: str = DEFAULT_APP_VERSION,
                         username: str = "",
                         auth_base: str = "",
                         session_id: str = "") -> ProtonSession | None:
    """Build a session from a web-storage token blob, or None."""
    body = _find_token_blob(blob)
    if body is None:
        return None
    try:
        expires_in = int(body.get("ExpiresIn", 0) or DEFAULT_EXPIRES_IN)
    except (TypeError, ValueError):
        expires_in = DEFAULT_EXPIRES_IN
    sid = str(body.get("SessionID", body.get("SessionId", "")) or "") or session_id
    return ProtonSession(
        uid=str(body.get("UID", "")), access_token=str(body.get("AccessToken", "")),
        session_id=sid, app_version=app_version,
        refresh_token=str(body.get("RefreshToken", "") or ""),
        obtained_at=time.time(), expires_in=expires_in,
        username=username, auth_base=auth_base,
    )


def _current_url(driver: Any) -> str:
    try:
        return driver.current_url or ""
    except Exception:
        return ""


def _left_login_page(driver: Any) -> bool:
    """True once the browser is no longer on a login page.

    An AUTH cookie alone is NOT completion: Proton sets a pre-auth cookie
    right after the username step, while still on /login and before the
    password is even filled.
    """
    url = _current_url(driver).lower()
    if not url or url in ("about:blank", "data:,"):
        return False
    return "/login" not in url and "signin" not in url


def _password_visible(driver: Any) -> bool:
    try:
        from selenium.webdriver.common.by import By
        for sel in PASSWORD_SELECTORS:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if any(el.is_displayed() for el in els):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _wait_for_password_or_completion(driver: Any, *, timeout: float = 20,
                                     debug: bool = False) -> str:
    """Wait for the password field; return 'password', 'done', or raise.

    Returns 'done' only when the browser actually left the login page —
    never on an AUTH cookie sighting alone, since Proton sets a pre-auth
    cookie while still on /login.
    """
    deadline = time.time() + max(5, timeout)
    while time.time() < deadline:
        if _password_visible(driver):
            return "password"
        if _left_login_page(driver):
            return "done"
        time.sleep(1)
    if _left_login_page(driver):
        return "done"
    if _password_visible(driver):
        return "password"
    raise ProtonError(
        "Password field not found after the username step. The Continue "
        "click may not have advanced the form (or the page shows a "
        "challenge).")


def _perform_form_login(driver: Any, username: str, password: str,
                        *, totp: str | None, login_timeout: int,
                        debug: bool = False) -> None:
    """Drive Proton's two-step login form (username -> Continue -> password)."""
    # --- step 1: username -------------------------------------------------
    if not _fill_first(driver, USERNAME_SELECTORS, username,
                       timeout=20, debug=debug):
        # Maybe the site skipped straight to password; continue rather than fail.
        _dbg(debug, "username field not found; assuming already past step 1")
    else:
        if not _click_first(driver, SUBMIT_LOCATORS, timeout=10, debug=debug):
            if not _press_enter_in(driver, USERNAME_SELECTORS, debug=debug):
                _dbg(debug, "continue button not clickable; waiting to advance")
        else:
            _dbg(debug, "submitted username step")

    # --- step 2: wait for the password field (page advanced) ---------------
    # NOTE: an AUTH cookie here means nothing by itself — Proton sets a
    # pre-auth cookie after the username step while still on /login. Only a
    # visible password field (or leaving the login page entirely) lets us
    # proceed.
    if _wait_for_password_or_completion(driver, timeout=20, debug=debug) == "done":
        return

    # --- step 3: password --------------------------------------------------
    if not _fill_first(driver, PASSWORD_SELECTORS, password,
                       timeout=10, debug=debug):
        raise ProtonError("Password field not found on the login page.")
    # Prefer persistent session ("stay signed in") so cookies live longer.
    try:
        from selenium.webdriver.common.by import By
        for sel in ("input[type='checkbox'][name*='remember' i]",
                    "input[type='checkbox'][name*='persistent' i]",
                    "#rememberMe", "#staySignedIn"):
            try:
                box = driver.find_element(By.CSS_SELECTOR, sel)
                if box and not box.is_selected():
                    box.click()
                    _dbg(debug, f"checked persistent-login {sel}")
                    break
            except Exception:
                continue
    except Exception:
        pass
    if not _click_first(driver, SUBMIT_LOCATORS, timeout=10, debug=debug):
        _press_enter_in(driver, PASSWORD_SELECTORS, debug=debug)
        _dbg(debug, "submitted password step (click or Enter)")

    # TOTP stage appears only for 2FA accounts; wait briefly for it.
    deadline = time.time() + 15
    totp_seen = False
    while time.time() < deadline:
        try:
            from selenium.webdriver.common.by import By
            for sel in TOTP_SELECTORS:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    if els and els[0].is_displayed():
                        totp_seen = True
                        break
                except Exception:
                    continue
            if totp_seen:
                break
        except Exception:
            pass
        if _has_auth_cookie(driver) and _left_login_page(driver):
            return  # logged straight in, no 2FA (page left /login)
        time.sleep(1)
    if totp_seen:
        if not totp:
            raise ProtonError(
                "Account asks for a 2FA code — enter a TOTP code and try again.")
        _dbg(debug, "submitting TOTP code")
        if not _fill_first(driver, TOTP_SELECTORS, totp,
                           timeout=10, debug=debug):
            raise ProtonError("2FA field appeared but could not be filled.")
        _click_first(driver, SUBMIT_LOCATORS, timeout=10, debug=debug)
        _press_enter_in(driver, TOTP_SELECTORS, debug=debug)


def _has_auth_cookie(driver: Any) -> bool:
    return bool(_nonempty_auth_cookies(driver))


def _cdp_cookies(driver: Any) -> list[dict[str, Any]]:
    """All browser cookies via CDP (includes HttpOnly/partitioned ones that
    Selenium's cookie jar sometimes hides). Names/values only."""
    run = getattr(driver, "execute_cdp_cmd", None)
    if not callable(run):
        return []
    for method in ("Network.getAllCookies", "Storage.getCookies"):
        try:
            data = run(method, {})
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("cookies"), list):
            out = []
            for c in data["cookies"]:
                if isinstance(c, dict) and c.get("name"):
                    out.append({"name": str(c.get("name", "")),
                                "value": str(c.get("value", "")),
                                "domain": str(c.get("domain", ""))})
            if out:
                return out
    return []


def _all_cookies(driver: Any) -> list[dict[str, Any]]:
    """Selenium jar + CDP merged, deduped by name (non-empty values win)."""
    merged: dict[str, dict[str, Any]] = {}
    for source in (getattr(driver, "get_cookies", None) and driver.get_cookies() or [],
                   _cdp_cookies(driver)):
        try:
            items = source or []
        except Exception:
            continue
        for c in items:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            name = str(c["name"])
            prev = merged.get(name)
            if prev is None or (not prev.get("value") and c.get("value")):
                merged[name] = {"name": name,
                                "value": str(c.get("value", "")),
                                "domain": str(c.get("domain", prev.get("domain", "") if prev else ""))}
    return list(merged.values())


def _cookie_debug_names(driver: Any) -> list[str]:
    try:
        return sorted(f"{c.get('name', '?')}@{c.get('domain', '?') or '?'}"
                      for c in _all_cookies(driver))
    except Exception:
        return []


def _nonempty_auth_cookies(driver: Any) -> list[dict[str, Any]]:
    """AUTH-* cookies with a non-empty value (ignores transient empties)."""
    try:
        return [c for c in _all_cookies(driver)
                if str(c.get("name", "")).startswith("AUTH-") and c.get("value")]
    except Exception:
        return []


def _settle_auth_cookies(driver: Any, *, settle_seconds: float = 4,
                         debug: bool = False) -> list[dict[str, Any]] | None:
    """Return AUTH cookies only once they survive a settle window.

    Proton rotates cookies mid-login (redirects, bootstrap refresh), so the
    first sighting can be a transient that vanishes a second later. Waiting
    a few seconds and re-reading avoids exiting on a cookie that is gone by
    the time we assemble the session.
    """
    first = _nonempty_auth_cookies(driver)
    if not first:
        return None
    _dbg(debug, "auth cookie sighted, waiting for it to settle ...")
    try:
        time.sleep(max(0, settle_seconds))
    except Exception:
        pass
    second = _nonempty_auth_cookies(driver)
    if not second:
        _dbg(debug, "auth cookie was transient, keep waiting")
        return None
    return second


def _wait_for_auth_cookies(driver: Any, *, login_timeout: int,
                           debug: bool = False) -> list[dict[str, Any]]:
    """Poll until login completes (AUTH cookie or storage tokens) or timeout."""
    deadline = time.time() + max(10, login_timeout)
    last_url = ""
    _dbg(debug, "waiting for the browser login to complete ...")
    polls = 0
    while time.time() < deadline:
        polls += 1
        try:
            last_url = driver.current_url or ""
        except Exception:
            last_url = ""
        settled = _settle_auth_cookies(driver, debug=debug)
        if settled:
            on_login_page = "/login" in (last_url or "")
            corroborated = (_read_storage_tokens(driver) is not None
                            or _has_perf_auth_tokens(driver, debug=debug))
            if not on_login_page or corroborated:
                _dbg(debug, f"auth cookie settled url={last_url}")
                try:
                    return _all_cookies(driver)
                except Exception as e:
                    raise ProtonError(f"Could not read cookies: {e}") from e
            _dbg(debug, "auth cookie sighted on the login page but login has "
                        "not visibly completed; keep waiting")
        storage = _read_storage_tokens(driver)
        if storage is not None:
            _dbg(debug, f"storage tokens present url={last_url}")
            try:
                return _all_cookies(driver)
            except Exception:
                return []
        if polls % 5 == 0 and _has_perf_auth_tokens(driver, debug=debug):
            _dbg(debug, f"/auth tokens in perf logs url={last_url}")
            try:
                return _all_cookies(driver)
            except Exception:
                return []
        if _detect_captcha(driver):
            raise ProtonError(
                "Proton shows human-verification/CAPTCHA. This backend runs "
                "headless on a server, so it cannot be solved here. Sign in "
                "once at account.proton.me from a residential connection, or "
                "set a proxy (Settings → Proton VPN) that is not a flagged "
                "datacenter IP, then try Login again.")
        err = _detect_error(driver)
        if err:
            _dbg(debug, f"page error text: {err}")
            low = err.lower()
            if any(k in low for k in ("incorrect", "invalid", "wrong")):
                raise ProtonError(f"Proton rejected the credentials: {err}")
        if debug and polls % 10 == 0:
            names = _cookie_debug_names(driver)
            try:
                perf_total = len(get_perf_logs(driver))
            except Exception:
                perf_total = -1
            _dbg(debug, f"still waiting url={last_url or '?'} cookies={names} "
                        f"storage={_storage_key_names(driver)} perf_entries={perf_total}")
            for line in _summarize_storage_shapes(driver):
                _dbg(debug, f"storage shape: {line}")
            for tag in _auth_urls_in_perf_logs(driver)[:10]:
                _dbg(debug, f"auth api seen: {tag}")
        time.sleep(1)
    names = _cookie_debug_names(driver)
    raise ProtonError(
        f"Login did not finish within {login_timeout}s (still at {last_url or '?'}). "
        f"Cookies seen: {names}. Wrong password or pending 2FA.")


def _validate_session(session: ProtonSession, auth_base: str, *,
                      proxy: str | None = None,
                      timeout: int = 20, debug: bool = False) -> str:
    """Read-only scope check: 'ok', 'auth' (bad scope/expired), or 'inconclusive'.

    Uses the certificate listing with Limit=1 — read-only, and it requires
    the exact scopes the app needs, so a dud token (e.g. an anonymous
    bootstrap token) is caught here instead of at the next command.
    """
    base = (auth_base or PROTON_API_BASE).rstrip("/")
    cookie = f"AUTH-{session.uid}={session.access_token}"
    if session.session_id:
        cookie += f"; Session-Id={session.session_id}"
    try:
        with _new_client(base=base, timeout=float(timeout), proxy=proxy) as client:
            resp = client.get(
                "/api/vpn/v1/certificate/all",
                params={"Mode": "persistent", "Offset": 0, "Limit": 1},
                headers={
                    "x-pm-appversion": session.app_version or DEFAULT_APP_VERSION,
                    "x-pm-uid": session.uid,
                    "x-pm-locale": DEFAULT_LOCALE,
                    "Accept": "application/vnd.protonmail.v1+json",
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Cookie": cookie,
                },
            )
    except Exception as e:
        _dbg(debug, f"validation inconclusive (network): {type(e).__name__}")
        return "inconclusive"
    if resp.status_code in (401, 403):
        return "auth"
    if resp.status_code != 200:
        _dbg(debug, f"validation inconclusive (HTTP {resp.status_code})")
        return "inconclusive"
    try:
        code = resp.json().get("Code", 1000)
    except ValueError:
        return "inconclusive"
    if code in (1000, 1001):
        return "ok"
    if code in (401, 403, 9106):
        return "auth"
    _dbg(debug, f"validation inconclusive (api code {code})")
    return "inconclusive"


def _pick_valid_candidate(cands: list[tuple[str, ProtonSession]], auth_base: str, *,
                          proxy: str | None = None,
                          app_version: str = DEFAULT_APP_VERSION,
                          debug: bool = False) -> ProtonSession:
    """Return the first candidate that passes the scope check.

    Falls back to the first candidate when validation is inconclusive
    (network hiccup) rather than failing a good login.
    """
    fallback: ProtonSession | None = None
    for label, sess in cands:
        verdict = _validate_session(sess, auth_base, proxy=proxy, debug=debug)
        _dbg(debug, f"candidate {label}: {verdict}")
        if verdict == "ok":
            return sess
        if verdict == "inconclusive" and fallback is None:
            fallback = sess
    if fallback is not None:
        return fallback
    raise ProtonError(
        "The browser login completed, but every extracted session was "
        "rejected by the API (wrong scope — usually an anonymous/bootstrap "
        "token got picked up). Please try Login again.")


def selenium_login(
    username: str,
    password: str,
    *,
    totp: str | None = None,
    app_version: str = DEFAULT_APP_VERSION,
    login_url: str = DEFAULT_LOGIN_URL,
    headless: bool = True,
    chrome_binary: str | None = None,
    chromedriver: str | None = None,
    login_timeout: int = 120,
    driver: Any | None = None,
    debug: bool = False,
    proxy: str | None = None,
) -> ProtonSession:
    """Log in through headless Chromium; return an unsaved session.

    Pass `driver` (a Selenium webdriver or test double) to reuse an
    existing browser; otherwise one is started and quit automatically.
    """
    if not username or not password:
        raise ProtonError("username/password are required")
    parsed = urllib.parse.urlparse(login_url)
    auth_base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    own_driver = driver is None
    if own_driver:
        driver = create_driver(headless=headless, chrome_binary=chrome_binary,
                               chromedriver=chromedriver, debug=debug, proxy=proxy)
    assert driver is not None
    try:
        try:
            driver.get(login_url)
        except Exception as e:
            raise ProtonError(f"Could not open login page {login_url}: {e}") from e
        _dbg(debug, f"opened {login_url}")
        _perform_form_login(driver, username, password, totp=totp,
                            login_timeout=login_timeout, debug=debug)
        cookies = _wait_for_auth_cookies(driver, login_timeout=login_timeout,
                                         debug=debug)
        try:
            logs = get_perf_logs(driver)
        except Exception:
            logs = []
        _dbg(debug, f"perf log entries: {len(logs)}")
        session_id = _session_id_from_cookies(cookies)
        cands: list[tuple[str, ProtonSession]] = []

        def _add(label: str, sess: ProtonSession | None) -> None:
            if sess is not None and sess.uid and sess.access_token \
                    and not _looks_like_blob(sess.access_token):
                if not any(s.uid == sess.uid and s.access_token == sess.access_token
                           for _, s in cands):
                    cands.append((label, sess))
                    _dbg(debug, f"candidate from {label} uid={sess.uid} "
                                f"refresh={'yes' if sess.refresh_token else 'no'}")

        # 1. Verbatim traffic the working page sent (can't be wrong-scope).
        try:
            for cred in extract_request_credentials_from_perf_logs(logs):
                _add("page request headers", ProtonSession(
                    uid=cred["uid"], access_token=cred["token"],
                    session_id=cred["session_id"] or session_id,
                    app_version=app_version, username=username,
                    auth_base=auth_base))
        except Exception as e:
            _dbg(debug, f"request-header scan skipped: {e}")
        # 2. Classic dashboard shape: AUTH-<uid> + Session-Id cookies.
        try:
            _add("cookies", session_from_cookies(
                cookies, app_version=app_version, username=username,
                auth_base=auth_base))
        except ProtonError as e:
            _dbg(debug, f"no cookie session: {e}")
        # 3. The /auth API response (UID + AccessToken + RefreshToken).
        try:
            tokens = collect_auth_tokens_from_perf_logs(
                logs, make_cdp_body_getter(driver), debug=debug)
            if tokens is not None:
                _add("/auth response", session_from_storage(
                    tokens, app_version=app_version, username=username,
                    auth_base=auth_base, session_id=session_id))
        except Exception as e:
            _dbg(debug, f"perf-log session build skipped: {e}")
        # 4. Web storage (live session when cookies are partitioned away).
        try:
            blob = _read_storage_tokens(driver)
            if blob is not None:
                _add("web storage", session_from_storage(
                    blob, app_version=app_version, username=username,
                    auth_base=auth_base, session_id=session_id))
        except Exception as e:
            _dbg(debug, f"storage session build skipped: {e}")
        if not cands:
            raise ProtonError(
                "Login completed in the browser but no usable session could "
                "be extracted (no AUTH cookie, no API credentials in perf "
                "logs, no storage tokens).")
        session = _pick_valid_candidate(
            cands, auth_base, proxy=proxy, app_version=app_version, debug=debug)
        enrich_session_from_perf_logs(session, driver, debug=debug)
        left = " + refresh token (~30 days)" if session.refresh_token else " (cookie-only)"
        _dbg(debug, f"login ok uid={session.uid}{left}")
        return session
    finally:
        if own_driver:
            try:
                driver.quit()
            except Exception:
                pass
