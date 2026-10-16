# Upstream dependencies

This app drives three upstream projects whose APIs can change under us.
Every integration below lists the exact source it was built against and how
the code degrades when shapes drift. When touching any of these, update the
"verified" version and re-check the fallback.

## PasarGuard panel + SDK

- Upstream: <https://github.com/PasarGuard/panel> (panel API), PyPI `pasarguard`
- Pinned: `pasarguard==2.4.0` in `backend/requirements.txt` — do NOT float it:
  SDK 2.4.0 flattens Host ECH (`ech:{xray:{...}}`), so ECH hosts are sent via
  raw httpx (`panel_sdk.raw_host_request` / `create_host_raw`).
- Version-sensitive behaviors (all in `backend/app/panel_sdk.py`):
  - modify/create core MUST send `exclude_inbound_tags=[]` + `fallbacks_inbound_tags=[]`
    (absent/null → panel 400).
  - Core configs must be dashboard-shaped (`inbound_lib.build_dashboard_config()`);
    the panel rejects empty inbounds, so registration seeds `build_mimic_inbound()`.
  - The panel drops connections mid node-restart → `modify_core_resilient()`
    re-reads and verifies instead of failing.
- Core PUT shape (`PUT /api/core/{id}?restart_nodes=true`) verified by capture.

## BackPack tunnels

- Upstream: <https://github.com/AminMGMT/BackPack>
- Panel API verified against: main branch at v1.9.0 docs + `internal/webui/server.go`
  (route table, `guard` scopes) and `internal/webui/handlers_conntest.go`
  (conntest states `idle/starting/waiting/running/done/failed/stopped`, row keys).
  User servers run v1.8.5; the v1.8.5 `handlers_conntest.go` was diffed and matches
  on states and row keys.
- Gotchas baked in (`backend/app/bp_api.py`, `backend/app/tunnel_lib.py`):
  - The panel answers ONLY under an unguessable path prefix (`withBasePath`
    404s everything else) — panel URLs must keep their path; see
    `_normalize_bp_url` in `backend/app/routers/servers.py`.
  - Auth is `Authorization: Bearer <write-scope token>`; tokens expire (401 → re-mint).
  - SSH menu texts are baked for v1.8.5 (`tunnel_lib.py`): main menu 0–10 literal,
    Manage(3) opens a submenu, tunnel list is 1-based, exit by closing the channel.
- Fallback: every side without a panel token uses SSH menus (`TunnelTab` API/SSH badges).

## Cloudflare

- Zone/record CRUD via API token (`Zone:DNS:Edit`); certbot over SSH for certificates.
- Defensive parsing (`cf.py`): list endpoints accept both bare arrays and `{zones}/{records}` wrappers.

## Proton VPN

- Upstream: Proton `account.protonvpn.com` API (the same one the web
  dashboard uses). The config/rotation logic is ported from the
  `proton-switcher` project into `backend/app/proton_lib.py`; the login is
  ported into `backend/app/proton_browser.py`.
- Endpoints: `GET /api/vpn/logicals`, `GET /api/vpn/v1/certificate/key/EC`,
  `POST /api/vpn/v1/certificate`, `GET /api/vpn/v1/certificate/all`,
  `DELETE /api/vpn/v1/certificate`.
- Auth is a **real headless Chromium (Selenium)** driving
  `https://account.protonvpn.com/login` — the same page a user would use —
  because a hand-rolled SRP-over-HTTP handshake trips Proton's bot defenses
  (2028 rate-limit, 9001 human-verification) due to its TLS/JS fingerprint.
  The login returns an `AUTH-<uid>` session and, scraped from the browser
  performance logs, the RefreshToken; `POST /api/auth/refresh` keeps the
  ~30-day session alive (refreshed when <7 days remain).
- Chromium + its driver are installed in `backend/Dockerfile`; Selenium is
  pointed at them via the `PROTON_CHROME_BINARY` / `PROTON_CHROMEDRIVER`
  env vars (no Selenium-Manager download at runtime).
- Version-sensitive bits:
  - `x-pm-appversion` (`DEFAULT_APP_VERSION`) must match the live dashboard or
    the API answers `5003` "web page is out of date" — bump it when it breaks.
  - Login is rate-limited (`2028`) and CAPTCHA-gated (`9001`) from datacenter
    IPs. The backend is **headless**, so a CAPTCHA cannot be solved in the
    app: sign in once manually or set the per-account **proxy** in
    Settings → Proton VPN.
  - Certificate creation is throttled (`KEY_THROTTLE_SECONDS`, 5s) between
    key-gen and register. Free servers are `Tier == 0`; best = lowest
    `Score`, tie-break lowest `Load`.
- Logins can take up to ~120s (browser + redirects), so the nginx front end
  raises `proxy_read_timeout` to 300s (`frontend/nginx.conf`) to avoid a 504.
- The client private key is only returned once, at creation, so each config
  is persisted (`models.ProtonConfig`) and its `.conf` re-rendered from it.

## Rule of thumb

If an upstream call starts 404/400/422-ing after an upstream upgrade: capture the
new shape, update this file, add a regression case under `backend/tests/`,
and keep the old shape accepted where cheap.
