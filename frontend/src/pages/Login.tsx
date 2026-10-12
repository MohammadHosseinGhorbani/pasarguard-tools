import { useState } from 'react';
import { login, errMsg } from '../api';
import { APP_NAME, GITHUB_URL, GithubIcon } from '../site';

export default function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await login(username, password);
      onDone();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-bg" aria-hidden="true">
        <span className="orb orb-a" />
        <span className="orb orb-b" />
        <span className="orb orb-c" />
        <span className="grid" />
      </div>
      <div className="login-inner">
        <div className="login-brand">
          <div className="brand big">{APP_NAME}</div>
          <p className="muted">
            One dashboard for your PasarGuard panels and nodes, BackPack tunnels,
            and Cloudflare — panels, inbounds, outbounds, tests and certificates.
          </p>
          <a className="gh-link" href={GITHUB_URL} target="_blank" rel="noreferrer">
            <GithubIcon size={15} />
            <span>GitHub — pasarguard-tools</span>
          </a>
        </div>
        <div className="card login-card">
          <h3>Login</h3>
          {error && <div className="alert error">{error}</div>}
          <form onSubmit={submit} style={{ display: 'grid', gap: 10 }}>
            <label className="lbl">
              Username
              <input
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
              />
            </label>
            <label className="lbl">
              Password
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button className="btn primary" disabled={loading || !username || !password}>
              {loading ? 'Logging in…' : 'Login'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
