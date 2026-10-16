# Security policy

Pasarguard Tools holds SSH credentials, panel API keys, and Cloudflare /
BackPack tokens. Operate it accordingly.

## Current posture (honest)

- Single admin user, bcrypt-hashed password, JWT sessions (12 h).
- Saved server/panel/token secrets are stored **unencrypted** in SQLite.
- No login rate limiting yet; no exec audit log yet.
- Serve it on localhost / behind a VPN, SSH tunnel, or authenticated TLS
  reverse proxy — do not expose `8080` to the internet.

## Reporting a vulnerability

Please **do not** open a public issue. Use GitHub's private vulnerability
reporting (Security tab → Report a vulnerability) or contact the maintainer
privately. Include the affected version/commit and steps to reproduce.
