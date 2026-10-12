import { useEffect, useState } from 'react';
import Modal from './Modal';
import {
  Server,
  ServerTestResult,
  createServer,
  deleteServer,
  errMsg,
  listServers,
  testServer,
  updateServer,
} from '../api';
import ResultBlock from './ResultBlock';
import { humanize } from '../resultText';

const emptyForm = {
  name: '',
  host: '',
  username: 'root',
  port: 22,
  password: '',
  key_path: '',
  key_content: '',
  notes: '',
  role: '' as string,
};

export type ServerRole = 'iran' | 'kharej';

export default function ServerManager({
  role,
  title,
  subtitle,
}: {
  role: ServerRole;
  title: string;
  subtitle?: string;
}) {
  const [servers, setServers] = useState<Server[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Server | null>(null);
  const [form, setForm] = useState({ ...emptyForm, port: 22 as number });
  const [bp, setBp] = useState({ url: '', verify: false, node: '', token: '' });
  const [saving, setSaving] = useState(false);

  const [testResults, setTestResults] = useState<Record<string, ServerTestResult>>({});
  const [testingId, setTestingId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const data = await listServers(role);
      setServers(data);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  function openAdd() {
    setEditing(null);
    setForm({ ...emptyForm, port: 22 });
    setBp({ url: '', verify: false, node: '', token: '' });
    setShowModal(true);
  }

  function openEdit(s: Server) {
    setEditing(s);
    setBp({
      url: (s.bp_url as string) ?? '',
      verify: Boolean(s.bp_verify),
      node: (s.bp_node as string) ?? '',
      token: '',
    });
    setForm({
      name: s.name ?? '',
      host: s.host ?? '',
      username: s.username ?? 'root',
      port: Number(s.port ?? 22),
      password: (s.password as string) ?? '',
      key_path: (s.key_path as string) ?? '',
      key_content: (s.key_content as string) ?? '',
      notes: (s.notes as string) ?? '',
      role: (s.role as string) ?? role,
    });
    setShowModal(true);
  }

  async function save() {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        host: form.host,
        username: form.username,
        port: Number(form.port),
        notes: form.notes || undefined,
        role: (form.role || (editing?.role as string) || role) as ServerRole,
        bp_url: bp.url,
        bp_verify: bp.verify,
        bp_node: bp.node,
      };
      if (bp.token) payload.bp_token = bp.token;
      if (form.password) payload.password = form.password;
      if (form.key_path) payload.key_path = form.key_path;
      if (form.key_content) payload.key_content = form.key_content;
      if (editing) await updateServer(editing.id, payload);
      else await createServer(payload);
      setShowModal(false);
      await load();
    } catch (e) {
      alert(`Save failed: ${errMsg(e)}`);
    } finally {
      setSaving(false);
    }
  }

  async function remove(s: Server) {
    if (!confirm(`Delete server "${s.name}"?`)) return;
    try {
      await deleteServer(s.id);
      await load();
    } catch (e) {
      alert(`Delete failed: ${errMsg(e)}`);
    }
  }

  async function runTest(s: Server) {
    setTestingId(String(s.id));
    try {
      const r = await testServer(s.id);
      setTestResults((m) => ({ ...m, [String(s.id)]: r }));
    } catch (e) {
      setTestResults((m) => ({
        ...m,
        [String(s.id)]: { ok: false, outputs: errMsg(e) } as ServerTestResult,
      }));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
        <button className="btn primary" onClick={openAdd}>
          + Add server
        </button>
      </div>
      {subtitle && (
        <div className="muted" style={{ marginBottom: 12 }}>
          {subtitle}
        </div>
      )}

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>Host</th>
              <th>User@Port</th>
              <th>Notes</th>
              <th>Test</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {servers.map((s) => {
              const tr = testResults[String(s.id)];
              return (
                <tr key={String(s.id)}>
                  <td>{s.name}</td>
                  <td className="mono">{s.host}</td>
                  <td className="mono">
                    {s.username}@{String(s.port)}
                  </td>
                  <td className="muted">{(s.notes as string) ?? ''}</td>
                  <td>
                    <button
                      className="btn small"
                      disabled={testingId === String(s.id)}
                      onClick={() => runTest(s)}
                    >
                      {testingId === String(s.id) ? '…' : 'Test'}
                    </button>{' '}
                    {tr && (
                      <span className={`badge ${tr.ok ? 'green' : 'red'}`}>
                        {tr.ok ? `ok ${tr.latency_ms ?? ''}ms` : 'fail'}
                      </span>
                    )}
                    {tr && (
                      <div style={{ marginTop: 6 }}>
                        <ResultBlock text={humanize('server-test', tr)} raw={tr} />
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="row">
                      <button className="btn small" onClick={() => openEdit(s)}>
                        Edit
                      </button>
                      <button className="btn small danger" onClick={() => remove(s)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {servers.length === 0 && !loading && <div className="muted">No servers yet.</div>}
      </div>

      {showModal && (
        <Modal onClose={() => setShowModal(false)}>
            <h3 style={{ marginTop: 0 }}>{editing ? 'Edit server' : 'Add server'}</h3>
            <div className="form-grid">
              <label className="lbl">
                Name
                <input
                  className="input"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label className="lbl">
                Host
                <input
                  className="input"
                  value={form.host}
                  onChange={(e) => setForm({ ...form, host: e.target.value })}
                />
              </label>
              <label className="lbl">
                Username
                <input
                  className="input"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </label>
              <label className="lbl">
                Port
                <input
                  className="input"
                  type="number"
                  value={form.port}
                  onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
                />
              </label>
              <label className="lbl">
                Group
                <select
                  className="select"
                  value={form.role || role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                >
                  <option value="iran">Iran</option>
                  <option value="kharej">Kharej</option>
                </select>
              </label>
              <label className="lbl">
                Password
                <input
                  className="input"
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
              </label>
              <label className="lbl">
                Key path
                <input
                  className="input"
                  value={form.key_path}
                  onChange={(e) => setForm({ ...form, key_path: e.target.value })}
                />
              </label>
              <label className="lbl full">
                Key content
                <textarea
                  className="textarea"
                  value={form.key_content}
                  onChange={(e) => setForm({ ...form, key_content: e.target.value })}
                />
              </label>
              <label className="lbl full">
                Notes
                <input
                  className="input"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
              </label>
            </div>
            <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button className="btn primary" disabled={saving || !form.name || !form.host} onClick={save}>
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
        </Modal>
      )}
    </div>
  );
}
