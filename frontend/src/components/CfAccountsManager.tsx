import { useEffect, useState } from 'react';
import Modal from './Modal';
import {
  CfAccount,
  createCfAccount,
  deleteCfAccount,
  errMsg,
  listCfAccounts,
  updateCfAccount,
} from '../api';

export default function CfAccountsManager() {
  const [accounts, setAccounts] = useState<CfAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<CfAccount | null>(null);
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [email, setEmail] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      setAccounts(await listCfAccounts());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function openAdd() {
    setEditing(null);
    setName('');
    setToken('');
    setEmail('');
    setShowModal(true);
  }

  function openEdit(a: CfAccount) {
    setEditing(a);
    setName(a.name ?? '');
    setToken('');
    setEmail((a.email as string) ?? (a as Record<string, unknown>).account_email as string ?? '');
    setShowModal(true);
  }

  async function save() {
    try {
      const payload: Record<string, unknown> = { name };
      if (token) {
        payload.token = token;
        payload.api_token = token;
      }
      if (email) {
        payload.email = email;
        payload.account_email = email;
      }
      if (editing) await updateCfAccount(editing.id, payload);
      else await createCfAccount(payload);
      setShowModal(false);
      await load();
    } catch (e) {
      alert(`Save failed: ${errMsg(e)}`);
    }
  }

  async function remove(a: CfAccount) {
    if (!confirm(`Delete Cloudflare account "${a.name}"?`)) return;
    try {
      await deleteCfAccount(a.id);
      await load();
    } catch (e) {
      alert(`Delete failed: ${errMsg(e)}`);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Cloudflare accounts</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
        <button className="btn primary" onClick={openAdd}>
          + Add account
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={String(a.id)}>
                <td>{a.name}</td>
                <td>
                  <div className="row">
                    <button className="btn small" onClick={() => openEdit(a)}>
                      Edit
                    </button>
                    <button className="btn small danger" onClick={() => remove(a)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {accounts.length === 0 && !loading && <div className="muted">No accounts yet.</div>}
      </div>

      {showModal && (
        <Modal onClose={() => setShowModal(false)}>
          <h3 style={{ marginTop: 0 }}>{editing ? 'Edit account' : 'Add account'}</h3>
          <div className="form-grid">
            <label className="lbl full">
              Name
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="lbl full">
              API token {editing && <span>(leave blank to keep)</span>}
              <input
                className="input"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            <label className="lbl full">
              Email (if required by backend)
              <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
          </div>
          <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
            <button className="btn" onClick={() => setShowModal(false)}>
              Cancel
            </button>
            <button className="btn primary" disabled={!name} onClick={save}>
              Save
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
