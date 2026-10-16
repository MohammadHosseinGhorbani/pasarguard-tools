# Pasarguard Tools (web app)

One self-hosted dashboard for operating **PasarGuard** panels and nodes,
**BackPack** tunnels and **Cloudflare** DNS — without juggling SSH sessions
and dashboard tabs.

> Made for personal use. If a feature annoys you or something is missing,
> fork it and send a pull request — contributions are welcome.

## What you can do with it

**Home** — overview of every section and how they fit together.

**Panel**
- *Nodes* — check pg-node status, install it, view credentials, register the
  node into a panel, lifecycle actions (up, down, restart, logs…).
- *Inbounds* — list every inbound on a panel's nodes. Quick-add Reality or
  Cloudflare inbounds, replace configs in place, manage subscription hosts,
  delete, and point any inbound at any outbound with the **Outbound** action.
- *WireGuard outbounds* — batch-edit a core's WireGuard outbounds: rename,
  paste a standard `.conf` as replacement (or **Pick from Proton VPN**),
  delete, add — then **Save** once.

**Proton VPN**
- *Configs* — log in with your Proton username/password (SRP, no browser),
  then create, rotate and delete WireGuard configs: the best free server per
  country is picked, a fresh certificate is registered and its `.conf` is
  stored. Rotate deletes the previous certificate and issues a new one.
  The saved configs feed **Pick from Proton VPN** in the WireGuard outbounds
  editor.

**Tunnel**
- *Exec console* — run commands (optional sudo) on any saved server.
- *Tunnel (BackPack)* — create reverse/direct tunnels between Iran and Kharej
  servers, run connection tests with live per-transport progress, manage
  tunnels per side. Sides with a panel URL + API token use the API, the rest
  fall back to SSH menus.

**Cloudflare**
- *Zones & records* — browse zones, edit A-record IPs, toggle proxying,
  add/delete records.
- *Certificates* — list and issue Let's Encrypt certificates via certbot.

**Settings** — everything saved: panel connections, Iran/Kharej servers,
Cloudflare accounts, Proton VPN credentials, and per-server BackPack panel
URLs + API tokens.
Note: BackPack tokens expire upstream — a 401 means re-minting one in the
BackPack panel (Settings → Security → API tokens).

## Run it

```
cp .env.example backend/.env   # set SECRET_KEY + ADMIN_PASS
docker compose up --build -d
```

- App: `http://<host>:8080`
- Login with `ADMIN_USER` / `ADMIN_PASS` (change it from the sidebar).

Local dev: backend `cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 8000`;
frontend `cd frontend && npm install && npm run dev` (`VITE_API_URL=http://localhost:8000`).

## Links

- This project: <https://github.com/MohammadHosseinGhorbani/pasarguard-tools>
- Panel / node: <https://github.com/PasarGuard/panel>, <https://github.com/PasarGuard/node>
- Tunnels: <https://github.com/AminMGMT/BackPack>

## Project docs

- [docs/upstream.md](docs/upstream.md) — upstream APIs this app depends on,
  exact versions verified, and fallback behavior.
- [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
