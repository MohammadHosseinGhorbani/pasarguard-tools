import { GITHUB_URL, GithubIcon } from '../site';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="docs">{children}</div>
    </div>
  );
}

export default function HomeSection() {
  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Home</h2>
      </div>

      <Section title="What is this?">
        <p>
          Pasarguard Tools is a self-hosted web dashboard for operating{' '}
          <a href="https://github.com/PasarGuard/panel" target="_blank" rel="noreferrer">PasarGuard</a>{' '}
          panels and nodes, <a href="https://github.com/AminMGMT/BackPack" target="_blank" rel="noreferrer">BackPack</a>{' '}
          tunnels and Cloudflare DNS — without juggling SSH sessions and dashboard tabs.
        </p>
        <p className="muted">
          Made for personal use. If a feature annoys you or something is missing, fork it
          and send a pull request — contributions are welcome.
        </p>
      </Section>

      <Section title="Panel — nodes, inbounds, outbounds">
        <ul>
          <li>
            <b>Nodes:</b> pick a saved server, check pg-node status, install it, view credentials,
            register the node into a panel, and run lifecycle actions (up, down, restart, logs…).
          </li>
          <li>
            <b>Inbounds:</b> list every inbound on a panel&apos;s nodes. Quick-add Reality or
            Cloudflare inbounds (keys generated locally, certs pulled from your servers),
            replace configs in place, manage subscription hosts, delete, and assign any
            outbound to an inbound with the <b>Outbound</b> action (one routing rule per inbound).
          </li>
          <li>
            <b>WireGuard outbounds:</b> edit a core&apos;s WireGuard outbounds as a batch — rename,
            paste a new standard <span className="mono">.conf</span> as replacement, delete, add —
            then <b>Save</b> once to push the whole config (nodes restart).
          </li>
        </ul>
      </Section>

      <Section title="Tunnel — exec console and BackPack">
        <ul>
          <li>
            <b>Exec console:</b> run commands with optional sudo on any saved server.
          </li>
          <li>
            <b>Tunnel (BackPack):</b> create reverse or direct tunnels between an Iran and a
            Kharej server, run connection tests with live per-transport progress, and manage
            tunnels per side. Sides with a panel URL + API token (Settings → BackPack API)
            are driven through the API; the rest fall back to SSH menus.
          </li>
        </ul>
      </Section>

      <Section title="Cloudflare — DNS and certificates">
        <ul>
          <li>
            <b>Zones &amp; records:</b> browse zones, edit A-record IPs, toggle proxying, add and
            delete records. Accounts live under Settings → Cloudflare.
          </li>
          <li>
            <b>Certificates:</b> list Let&apos;s Encrypt certificates on a server and issue new
            ones via certbot (needs port 80 free and DNS pointing at the server).
          </li>
        </ul>
      </Section>

      <Section title="Settings — everything saved">
        <ul>
          <li><b>Saved panels:</b> PasarGuard panel URLs + API keys (one active).</li>
          <li><b>Iran / Kharej servers:</b> SSH hosts: tunnel ends, node machines, cert hosts.</li>
          <li><b>Cloudflare:</b> API accounts used by Zones &amp; records.</li>
          <li>
            <b>BackPack API:</b> per-server panel URL (including its path prefix) + write-scope
            API token. Tokens expire upstream — a 401 means re-mint it in the BackPack panel
            under Settings → Security → API tokens.
          </li>
        </ul>
      </Section>

      <Section title="Links">
        <p>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="gh-link">
            <GithubIcon size={15} />
            <span>GitHub — pasarguard-tools</span>
          </a>
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          Upstream projects: PasarGuard panel &amp; node · BackPack tunnels · MIT licensed —
          see LICENSE in the repo.
        </p>
      </Section>
    </div>
  );
}
