import { useEffect, useState } from 'react';
import Modal from './Modal';
import {
  Panel,
  activatePanel,
  createPanel,
  deletePanel,
  errMsg,
  isPanelActive,
  listPanels,
  pretty,
  updatePanel,
} from '../api';

export default function PanelsManager() {
  const [panels, setPanels] = useState<Panel[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Panel | null>(null);
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [verifySsl, setVerifySsl] = useState(false);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const p = await listPanels();
      setPanels(p);
      if (p.length > 0 && !selectedId) setSelectedId(String(p[0].id));
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

  const selected = panels.find((p) => String(p.id) === selectedId);

  function openAdd() {
    setEditing(null);
    setName('');
    setBaseUrl('');
    setApiKey('');
    setVerifySsl(false);
    setShowModal(true);
  }

  function openEdit(p: Panel) {
    setEditing(p);
    setName(p.name);
    setBaseUrl((p.base_url as string) ?? '');
    setApiKey('');
    setVerifySsl(Boolean(p.verify_ssl));
    setShowModal(true);
  }

  async function save() {
    setSaving(true);
    try {
      const payload = { name, base_url: baseUrl, api_key: apiKey, verify_ssl: verifySsl };
      if (editing) await updatePanel(editing.id, payload);
      else await createPanel(payload);
      setShowModal(false);
      await load();
    } catch (e) {
      alert(`Save failed: ${errMsg(e)}`);
    } finally {
      setSaving(false);
    }
  }

  async function remove(p: Panel) {
    if (!confirm(`Delete panel "${p.name}"?`)) return;
    try {
      await deletePanel(p.id);
      if (selectedId === String(p.id)) setSelectedId('');
      await load();
    } catch (e) {
      alert(`Delete failed: ${errMsg(e)}`);
    }
  }

  async function activate(p: Panel) {
    try {
      await activatePanel(p.id);
      await load();
    } catch (e) {
      alert(`Activate failed: ${errMsg(e)}`);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Panel connections</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
        <button className="btn primary" onClick={openAdd}>
          + Add panel
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <select className="select" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
            <option value="">Select panel</option>
            {panels.map((p) => (
              <option key={String(p.id)} value={String(p.id)}>
                {p.name} {isPanelActive(p) ? '(active)' : ''}
              </option>
            ))}
          </select>
          {selected && (
            <span className={`badge ${isPanelActive(selected) ? 'green' : 'yellow'}`}>
              {isPanelActive(selected) ? 'active' : 'inactive'}
            </span>
          )}
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>URL</th>
              <th>API key</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {panels.map((p) => (
              <tr key={String(p.id)}>
                <td>{p.name}</td>
                <td className="mono">{(p.base_url as string) || <span className="muted">not set</span>}</td>
                <td className="mono">{(p.api_key_masked as string) || <span className="muted">not set</span>}</td>
                <td>
                  <span className={`badge ${isPanelActive(p) ? 'green' : 'yellow'}`}>
                    {isPanelActive(p) ? 'active' : 'inactive'}
                  </span>
                </td>
                <td>
                  <div className="row">
                    <button className="btn small" onClick={() => setSelectedId(String(p.id))}>
                      Select
                    </button>
                    {!isPanelActive(p) && (
                      <button className="btn small" onClick={() => activate(p)}>
                        Activate
                      </button>
                    )}
                    <button className="btn small" onClick={() => openEdit(p)}>
                      Edit
                    </button>
                    <button className="btn small danger" onClick={() => remove(p)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {panels.length === 0 && !loading && <div className="muted">No panels yet.</div>}
        {selected && (
          <div className="muted" style={{ marginTop: 8 }}>
            Selected: <span className="mono">{pretty(selected)}</span>
          </div>
        )}
      </div>

      {showModal && (
        <Modal onClose={() => setShowModal(false)}>
            <h3 style={{ marginTop: 0 }}>{editing ? 'Edit panel' : 'Add panel'}</h3>
            <div className="form-grid">
              <label className="lbl full">
                Name
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="lbl full">
                Base URL
                <input
                  className="input mono"
                  placeholder="https://panel.example.com:8000"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </label>
              <label className="lbl full">
                API key {editing && <span>(leave blank to keep)</span>}
                <input
                  className="input mono"
                  type="password"
                  placeholder={editing ? '••••••' : 'pg_key_...'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={verifySsl}
                  onChange={(e) => setVerifySsl(e.target.checked)}
                />
                verify SSL
              </label>
            </div>
            <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button className="btn primary" disabled={saving || !name} onClick={save}>
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
        </Modal>
      )}
    </div>
  );
}
