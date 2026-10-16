# Changelog

## Unreleased

- Proton VPN: new top-level **Proton VPN** section to create, rotate and
  delete WireGuard configs (best free server per country). Credentials live
  in a new **Settings → Proton VPN** tab (SRP username/password login with a
  ~30-day auto-refreshing session, optional proxy, app-version override).
- Panel: the WireGuard outbounds editor gains a **Pick from Proton VPN**
  button per outbound that fills the config from a saved Proton config.

## 0.1.0 — 2026-10-14 (first public push)

- Panel: node management, Reality/Cloudflare inbound quick-add + replace,
  subscription hosts, inbound→outbound routing rules, WireGuard outbound batch editor.
- Tunnel: exec console, BackPack tunnel create/test/manage over panel API
  with SSH-menu fallback, live connection-test progress.
- Cloudflare: zones/records + certbot certificates.
- Settings: panels, servers, Cloudflare accounts, BackPack API keys.
- Hash routing, collapsible sidebar, Home docs, dark/light themes.
