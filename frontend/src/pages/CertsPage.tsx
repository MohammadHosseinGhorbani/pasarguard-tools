import { useEffect, useState } from 'react';
import {
  LiveCert,
  Server,
  errMsg,
  issueCertificate,
  listServers,
  nodeCertificates,
} from '../api';
import ResultBlock from '../components/ResultBlock';
import { humanize } from '../resultText';

export default function CertsPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [serverId, setServerId] = useState('');
  const [certbot, setCertbot] = useState('');
  const [certs, setCerts] = useState<LiveCert[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [out, setOut] = useState('');
  const [outRaw, setOutRaw] = useState<unknown>(null);

  const [domain, setDomain] = useState('');
  const [email, setEmail] = useState('');

  async function loadServers() {
    setLoading(true);
    setError('');
    try {
      const s = await listServers();
      setServers(s);
      if (s.length > 0 && !serverId) setServerId(String(s[0].id));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadServers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refresh() {
    if (!serverId) {
      alert('Select a server first');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const r = await nodeCertificates(serverId);
      setCertbot(r.certbot);
      setCerts(r.certs ?? []);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function issue() {
    if (!serverId || !domain) {
      alert('Server and domain are required');
      return;
    }
    if (!confirm(`Issue a certificate for ${domain} via certbot? (needs port 80 free + DNS pointing here)`)) return;
    setBusy(true);
    setOut('');
    try {
      const r = await issueCertificate({ server_id: serverId, domain, email: email || undefined });
      setOut(humanize('cert', r));
      setOutRaw(r);
      await refresh();
    } catch (e) {
      setOut(`ERROR: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Certificates</h2>
        <span style={{ flex: 1 }} />
        <select className="select" value={serverId} onChange={(e) => setServerId(e.target.value)}>
          <option value="">Select server</option>
          {servers.map((s) => (
            <option key={String(s.id)} value={String(s.id)}>
              {s.name} ({s.host})
            </option>
          ))}
        </select>
        <button className="btn primary" disabled={busy || !serverId || loading} onClick={refresh}>
          {busy ? 'Loading…' : 'List certs'}
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <h3>Let&apos;s Encrypt certificates {certbot && <span className="muted">({certbot})</span>}</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>Domain</th>
              <th>Valid from</th>
              <th>Valid until</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {certs.map((c) => (
              <tr key={c.domain}>
                <td className="mono">{c.domain}</td>
                <td className="mono">{c.valid_from ?? ''}</td>
                <td className="mono">{c.valid_until ?? ''}</td>
                <td>
                  <span className={`badge ${c.valid ? 'green' : 'red'}`}>
                    {c.valid ? 'valid' : 'expired/invalid'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {certs.length === 0 && !busy && <div className="muted">No certificates found on this server.</div>}
      </div>

      <div className="card">
        <h3>Issue certificate</h3>
        <div className="muted" style={{ marginBottom: 10 }}>
          Runs <span className="mono">certbot certonly --standalone</span> on the server (installs certbot if
          missing). Re-issuing also renews.
        </div>
        <div className="form-grid">
          <label className="lbl">
            Domain
            <input
              className="input"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="sub.example.com"
            />
          </label>
          <label className="lbl">
            LE email (optional)
            <input
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="optional"
            />
          </label>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={busy || !serverId || !domain} onClick={issue}>
            {busy ? 'Working…' : 'Issue'}
          </button>
        </div>
        {out && (
          <div style={{ marginTop: 10 }}>
            <ResultBlock text={out} raw={outRaw} />
          </div>
        )}
      </div>
    </div>
  );
}
