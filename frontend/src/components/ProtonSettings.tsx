import { useEffect, useState } from 'react';
import {
  ProtonStatus,
  clearProtonAccount,
  errMsg,
  getProtonStatus,
  protonLogin,
  protonLogout,
  saveProtonSettings,
} from '../api';
import ResultBlock from './ResultBlock';

// The "Proton VPN" setting: credentials + session for the Proton account
// whose WireGuard configs the Proton VPN section creates/rotates.
export default function ProtonSettings() {
  const [status, setStatus] = useState<ProtonStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [resultRaw, setResultRaw] = useState<unknown>(null);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [appVersion, setAppVersion] = useState('');
  const [proxy, setProxy] = useState('');
  const [totp, setTotp] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      const s = await getProtonStatus();
      setStatus(s);
      setUsername(s.username || '');
      setAppVersion(s.app_version || '');
      setProxy(s.proxy || '');
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

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setResult('');
    setResultRaw(null);
    try {
      const r = await fn();
      setResultRaw(r);
      await load();
      return r;
    } catch (e) {
      setResult(`ERROR: ${errMsg(e)}`);
      return null;
    } finally {
      setBusy('');
    }
  }

  async function save() {
    const r = await run('save', () =>
      saveProtonSettings({ username, app_version: appVersion, proxy, password: password || undefined }),
    );
    if (r) {
      setPassword('');
      setResult('Saved Proton VPN settings.');
    }
  }

  async function login() {
    const r = (await run('login', () =>
      protonLogin({ username, password: password || undefined, totp: totp || undefined }),
    )) as ProtonStatus | null;
    if (r) {
      setPassword('');
      setTotp('');
      setResult(
        `Logged in as ${r.username || username}${r.days_left != null ? ` · token valid ~${r.days_left} day(s)` : ''}.`,
      );
    }
  }

  async function logout() {
    const r = await run('logout', () => protonLogout());
    if (r) setResult('Logged out (stored tokens cleared; credentials kept).');
  }

  async function clearAll() {
    if (!confirm('Delete the saved Proton username, password and tokens?')) return;
    const r = await run('clear', () => clearProtonAccount());
    if (r) {
      setUsername('');
      setPassword('');
      setTotp('');
      setProxy('');
      setAppVersion('');
      setResult('Proton account cleared.');
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Proton VPN</h2>
        <span style={{ flex: 1 }} />
        {status && (
          <span className={`badge ${status.logged_in ? 'green' : 'yellow'}`}>
            {status.logged_in ? 'logged in' : 'not logged in'}
          </span>
        )}
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <h3>Account</h3>
        <div className="form-grid">
          <label className="lbl">
            Proton username (email)
            <input
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="you@example.com"
              autoComplete="off"
            />
          </label>
          <label className="lbl">
            Password {status?.has_password && <span className="muted">(saved — blank keeps it)</span>}
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={status?.has_password ? '••••••••' : ''}
              autoComplete="new-password"
            />
          </label>
          <label className="lbl">
            App version
            <input
              className="input mono"
              value={appVersion}
              onChange={(e) => setAppVersion(e.target.value)}
              placeholder="web-vpn-settings@5.0.422.0"
            />
          </label>
          <label className="lbl">
            Proxy (optional)
            <input
              className="input mono"
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:10808"
            />
          </label>
          <label className="lbl">
            TOTP code (only if 2FA is on)
            <input
              className="input mono"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              placeholder="123456"
              autoComplete="one-time-code"
            />
          </label>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          Login drives a real headless Chromium (Selenium) in the backend — the same
          page you would use in a browser — then stores the session and, when it can
          scrape it, a refresh token (~30-day session, auto-refreshed). It takes a
          few seconds and may hit Proton's CAPTCHA: this runs headless on the server,
          so if that happens sign in once manually or set a proxy. Credentials are
          stored on this server — use it only on a machine you trust.
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" disabled={!!busy} onClick={save}>
            {busy === 'save' ? 'Saving…' : 'Save'}
          </button>
          <button className="btn primary" disabled={!!busy || !username} onClick={login}>
            {busy === 'login' ? 'Logging in…' : 'Login'}
          </button>
          <span style={{ flex: 1 }} />
          <button className="btn" disabled={!!busy || !status?.logged_in} onClick={logout}>
            Logout
          </button>
          <button className="btn danger" disabled={!!busy} onClick={clearAll}>
            Clear account
          </button>
        </div>
      </div>

      {status && (
        <div className="card">
          <h3>Session</h3>
          <table className="tbl">
            <tbody>
              <tr>
                <td className="muted">Status</td>
                <td>
                  <span className={`badge ${status.logged_in ? 'green' : 'yellow'}`}>
                    {status.logged_in ? 'logged in' : 'not logged in'}
                  </span>
                </td>
              </tr>
              <tr>
                <td className="muted">Mode</td>
                <td className="mono">{status.mode}</td>
              </tr>
              <tr>
                <td className="muted">UID</td>
                <td className="mono">{status.uid || <span className="muted">—</span>}</td>
              </tr>
              <tr>
                <td className="muted">Token</td>
                <td className="mono">
                  {status.logged_in
                    ? status.days_left != null
                      ? `~${status.days_left} day(s) left`
                      : 'expiry unknown'
                    : '—'}
                </td>
              </tr>
              <tr>
                <td className="muted">Proxy</td>
                <td className="mono">{status.proxy || <span className="muted">none</span>}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {result && (
        <div className="card">
          <h3>Result</h3>
          <ResultBlock text={result} raw={resultRaw} />
        </div>
      )}
    </div>
  );
}
