import { useEffect, useState } from 'react';
import Modal from './Modal';
import { Server, bpTest, errMsg, listServers, saveBpSettings } from '../api';

export default function BpPanelsManager() {
  const [servers, setServers] = useState<Server[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<Server | null>(null);
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [verify, setVerify] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      setServers(await listServers());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function openEdit(s: Server) {
    setEditing(s);
    setUrl((s.bp_url as string) ?? '');
    setToken('');
    setVerify(Boolean(s.bp_verify));
    setMsg('');
  }

  async function save() {
    if (!editing) return;
    setSaving(true);
    setMsg('');
    try {
      const saved = await saveBpSettings(editing.id, {
        bp_url: url,
        bp_token: token || undefined,
        bp_verify: verify,
        bp_node: (editing.bp_node as string) ?? '',
      });
      setToken('');
      setEditing(null);
      await load();
      setMsg(
        `Saved: ${saved.bp_url || '(no url)'} · token: ${(saved as { has_bp_token?: boolean }).has_bp_token ? 'yes' : 'MISSING — paste one'}`,
      );
    } catch (e) {
      setMsg(`ERROR: ${errMsg(e)}`);
    } finally {
      setSaving(false);
    }
  }

  async function test(s: Server) {
    setMsg('');
    try {
      const r = (await bpTest(s.id)) as { tunnel_count?: number };
      setMsg(`✅ ${s.name}: panel reachable — ${r.tunnel_count ?? '?'} tunnel(s) listed.`);
    } catch (e) {
      setMsg(`ERROR ${s.name}: ${errMsg(e)}`);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>BackPack panels</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>
      <div className="muted" style={{ marginBottom: 12 }}>
        Give Iran and Kharej each a panel URL + write-scope API token (BackPack panel → Settings →
        Security → API tokens) to drive them through the API instead of SSH menus. The URL must
        include the panel&apos;s path prefix: copy the full address from the <span className="mono">backpack</span> menu
        → Web Panel screen (or <span className="mono">journalctl -u backpack-webui | grep listening</span>).
        Sides without a token keep using SSH.
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}
      {msg && (
        <pre className="out" style={{ marginBottom: 12 }}>
          {msg}
        </pre>
      )}

      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>Server</th>
              <th>Panel URL</th>
              <th>Token</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {servers.map((s) => (
              <tr key={String(s.id)}>
                <td>
                  {s.name} <span className="muted mono">({s.host})</span>
                </td>
                <td className="mono">{(s.bp_url as string) || <span className="muted">not set</span>}</td>
                <td>
                  {s.has_bp_token ? (
                    <span className="badge green">saved</span>
                  ) : (
                    <span className="badge yellow">missing</span>
                  )}
                </td>
                <td>
                  <div className="row">
                    <button className="btn small" onClick={() => openEdit(s)}>
                      Edit
                    </button>
                    <button className="btn small" onClick={() => test(s)}>
                      Test
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {servers.length === 0 && !loading && <div className="muted">No servers yet.</div>}
      </div>

      {editing && (
        <Modal onClose={() => !saving && setEditing(null)}>
          <h3 style={{ marginTop: 0 }}>
            BackPack panel for {editing.name} <span className="muted mono">({editing.host})</span>
          </h3>
          <div className="form-grid">
            <label className="lbl full">
              Panel URL
              <input
                className="input mono"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://server-ip:7777/prefix-shown-by-web-panel-screen"
              />
            </label>
            <label className="lbl full">
              API token {editing.has_bp_token && <span>(saved — blank keeps it)</span>}
              <input
                className="input mono"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={editing.has_bp_token ? '••••••' : 'paste token'}
              />
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} />
              verify panel TLS (off for self-signed)
            </label>
          </div>
          <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
            <button className="btn" disabled={saving} onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button className="btn primary" disabled={saving || !url} onClick={save}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
