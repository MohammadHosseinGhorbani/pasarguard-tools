import { useEffect, useState } from 'react';
import {
  ProtonConfig,
  ProtonServer,
  createProtonConfig,
  deleteProtonConfig,
  errMsg,
  listProtonConfigs,
  listProtonServers,
  rotateProtonConfig,
} from '../api';
import ResultBlock from '../components/ResultBlock';

function fmtDate(unix: number): string {
  if (!unix) return '—';
  try {
    return new Date(unix * 1000).toLocaleString();
  } catch {
    return String(unix);
  }
}

// The "Proton VPN" section: create / delete / rotate WireGuard configs only.
export default function ProtonPage() {
  const [configs, setConfigs] = useState<ProtonConfig[]>([]);
  const [loggedIn, setLoggedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [resultRaw, setResultRaw] = useState<unknown>(null);

  const [name, setName] = useState('');
  const [country, setCountry] = useState('');
  const [tier, setTier] = useState(0);

  const [servers, setServers] = useState<ProtonServer[] | null>(null);
  const [serversTier, setServersTier] = useState(0);
  const [serversLoading, setServersLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const r = await listProtonConfigs();
      setConfigs(r.configs ?? []);
      setLoggedIn(Boolean(r.logged_in));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setOutcome(text: string, raw: unknown) {
    setResult(text);
    setResultRaw(raw);
  }

  async function create() {
    const n = name.trim();
    if (!n) {
      alert('Enter a config name (e.g. netherlands).');
      return;
    }
    setBusy('create');
    setResult('');
    setResultRaw(null);
    try {
      const r = await createProtonConfig({ name: n, country, tier });
      setOutcome(
        `Created WireGuard config "${r.config.name}"` +
          (r.config.server_name ? ` on ${r.config.server_name} (${r.config.entry_ip}).` : '.'),
        r,
      );
      setName('');
      setCountry('');
      await load();
    } catch (e) {
      setOutcome(`ERROR: ${errMsg(e)}`, null);
    } finally {
      setBusy('');
    }
  }

  async function rotate(c: ProtonConfig) {
    if (!confirm(`Rotate "${c.name}"? The previous Proton certificate is deleted and a fresh one is created.`))
      return;
    setBusy('rotate:' + c.name);
    setResult('');
    setResultRaw(null);
    try {
      const r = await rotateProtonConfig({ name: c.name, country: c.country, tier });
      setOutcome(
        `Rotated "${r.config.name}" → ${r.config.server_name} (${r.config.entry_ip}).` +
          (r.notes?.length ? `\n${r.notes.join('\n')}` : ''),
        r,
      );
      await load();
    } catch (e) {
      setOutcome(`ERROR: ${errMsg(e)}`, null);
    } finally {
      setBusy('');
    }
  }

  async function remove(c: ProtonConfig) {
    if (!confirm(`Delete "${c.name}"? This also deletes the certificate on Proton.`)) return;
    setBusy('delete:' + c.name);
    setResult('');
    setResultRaw(null);
    try {
      const r = await deleteProtonConfig(c.name);
      setOutcome(`Deleted "${c.name}".` + (r.notes?.length ? `\n${r.notes.join('\n')}` : ''), r);
      await load();
    } catch (e) {
      setOutcome(`ERROR: ${errMsg(e)}`, null);
    } finally {
      setBusy('');
    }
  }

  async function rotateAll() {
    if (configs.length === 0) {
      alert('No configs to rotate.');
      return;
    }
    if (!confirm(`Rotate all ${configs.length} config(s)? Each is deleted and recreated with a fresh certificate.`))
      return;
    setBusy('rotate-all');
    setResult('');
    setResultRaw(null);
    const lines: string[] = [];
    for (const c of configs) {
      try {
        const r = await rotateProtonConfig({ name: c.name, country: c.country, tier });
        lines.push(`✔ ${c.name} → ${r.config.server_name} (${r.config.entry_ip})`);
      } catch (e) {
        lines.push(`✖ ${c.name}: ${errMsg(e)}`);
      }
    }
    setOutcome(`Rotated ${configs.length} config(s):\n${lines.join('\n')}`, { lines });
    await load();
    setBusy('');
  }

  async function copyConf(c: ProtonConfig) {
    try {
      await navigator.clipboard.writeText(c.conf);
      setResult(`Copied the WireGuard config for "${c.name}" to the clipboard.`);
      setResultRaw(c);
    } catch {
      setResult(`Clipboard blocked — config text for "${c.name}":\n\n${c.conf}`);
      setResultRaw(c);
    }
  }

  async function loadServers(t: number) {
    setServersTier(t);
    setServersLoading(true);
    setError('');
    try {
      const r = await listProtonServers(t, '', 0);
      setServers(r.servers ?? []);
    } catch (e) {
      setError(errMsg(e));
      setServers([]);
    } finally {
      setServersLoading(false);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Proton VPN configs</h2>
        <span style={{ flex: 1 }} />
        <span className={`badge ${loggedIn ? 'green' : 'yellow'}`}>
          {loggedIn ? 'logged in' : 'not logged in'}
        </span>
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}
      {!loading && !loggedIn && (
        <div className="alert">
          Not logged in to Proton. Set your credentials in <b>Settings → Proton VPN</b>, then press Login.
        </div>
      )}

      <div className="card">
        <h3>Create config</h3>
        <div className="row">
          <label className="lbl" style={{ flex: '1 1 220px' }}>
            Name
            <input
              className="input mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="netherlands"
            />
          </label>
          <label className="lbl" style={{ flex: '1 1 180px' }}>
            Country (optional)
            <input
              className="input mono"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              placeholder="NL / netherlands — blank = infer from name"
            />
          </label>
          <label className="lbl" style={{ flex: '0 0 140px' }}>
            Tier
            <select className="select" value={tier} onChange={(e) => setTier(Number(e.target.value))}>
              <option value={0}>Free (0)</option>
              <option value={2}>Plus (2)</option>
            </select>
          </label>
          <button className="btn primary" disabled={!!busy || !loggedIn} onClick={create}>
            {busy === 'create' ? 'Creating…' : 'Create'}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          Picks the best server (lowest score/load) for the country, registers a fresh
          WireGuard certificate named after the config and stores the private key here.
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Saved configs</h3>
          <span style={{ flex: 1 }} />
          <button
            className="btn small"
            disabled={!!busy || !loggedIn || configs.length === 0}
            onClick={rotateAll}
          >
            {busy === 'rotate-all' ? 'Rotating…' : 'Rotate all'}
          </button>
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>Country</th>
              <th>Server</th>
              <th>Endpoint</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((c) => (
              <tr key={c.id}>
                <td className="mono">{c.name}</td>
                <td className="mono">{c.country || <span className="muted">—</span>}</td>
                <td className="mono">{c.server_name || <span className="muted">—</span>}</td>
                <td className="mono">
                  {c.entry_ip ? `${c.entry_ip}:${c.port}` : <span className="muted">—</span>}
                </td>
                <td className="mono">{fmtDate(c.created_at)}</td>
                <td>
                  <div className="row">
                    <button className="btn small" disabled={!!busy} onClick={() => rotate(c)}>
                      {busy === 'rotate:' + c.name ? '…' : 'Rotate'}
                    </button>
                    <button className="btn small" disabled={!!busy} onClick={() => copyConf(c)}>
                      Copy .conf
                    </button>
                    <button className="btn small danger" disabled={!!busy} onClick={() => remove(c)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {configs.length === 0 && !loading && (
          <div className="muted">No Proton configs yet. Create one above.</div>
        )}
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Servers</h3>
          <span style={{ flex: 1 }} />
          <button className="btn small" disabled={serversLoading || !loggedIn} onClick={() => loadServers(0)}>
            {serversLoading && serversTier === 0 ? 'Loading…' : 'List free servers'}
          </button>
          <button className="btn small" disabled={serversLoading || !loggedIn} onClick={() => loadServers(2)}>
            {serversLoading && serversTier === 2 ? 'Loading…' : 'List Plus servers'}
          </button>
        </div>
        {servers && servers.length > 0 ? (
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th>
                <th>Exit</th>
                <th>Load</th>
                <th>Score</th>
                <th>Entry IP</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((s) => (
                <tr key={s.name}>
                  <td className="mono">{s.name}</td>
                  <td className="mono">{s.exit_country}</td>
                  <td className="mono">{s.load ?? '—'}</td>
                  <td className="mono">{s.score ?? '—'}</td>
                  <td className="mono">{s.entry_ip || <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !serversLoading && (
            <div className="muted">
              Not loaded. Free servers are Tier 0; only those work on a free Proton account.
            </div>
          )
        )}
      </div>

      {result && (
        <div className="card">
          <h3>Result</h3>
          <ResultBlock text={result} raw={resultRaw} />
        </div>
      )}
    </div>
  );
}
