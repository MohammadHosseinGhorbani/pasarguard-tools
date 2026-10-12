import { useEffect, useState } from 'react';
import {
  DnsRecord,
  Zone,
  createRecord,
  deleteRecord,
  errMsg,
  listCfAccounts,
  listRecords,
  listZones,
  toggleProxy,
  updateRecord,
} from '../api';

export default function CloudflarePage() {
  const [accounts, setAccounts] = useState<{ id: number | string; name: string }[]>([]);
  const [accountId, setAccountId] = useState('');
  const [zones, setZones] = useState<Zone[]>([]);
  const [zoneId, setZoneId] = useState('');
  const [records, setRecords] = useState<DnsRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const [newName, setNewName] = useState('');
  const [newContent, setNewContent] = useState('');
  const [newProxied, setNewProxied] = useState(false);
  const [editIp, setEditIp] = useState<Record<string, string>>({});

  async function loadAccounts() {
    setLoading(true);
    setError('');
    try {
      const a = await listCfAccounts();
      setAccounts(a);
      if (a.length > 0 && !accountId) setAccountId(String(a[0].id));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  async function loadZones(accId: string) {
    if (!accId) {
      setZones([]);
      return;
    }
    setBusy(true);
    try {
      const z = await listZones(accId);
      setZones(z);
      if (z.length > 0) setZoneId(z[0].id);
      else setZoneId('');
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function loadRecords() {
    if (!accountId || !zoneId) {
      setRecords([]);
      return;
    }
    setBusy(true);
    setError('');
    try {
      const r = await listRecords(accountId, zoneId);
      setRecords(r);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    loadAccounts();
  }, []);

  useEffect(() => {
    if (accountId) loadZones(accountId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  useEffect(() => {
    if (accountId && zoneId) loadRecords();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneId]);

  async function addRecord() {
    if (!accountId || !zoneId || !newName || !newContent) return;
    setBusy(true);
    try {
      await createRecord(accountId, zoneId, {
        name: newName,
        content: newContent,
        proxied: newProxied,
      });
      setNewName('');
      setNewContent('');
      setNewProxied(false);
      await loadRecords();
    } catch (e) {
      alert(`Create failed: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function saveIp(r: DnsRecord) {
    const ip = editIp[r.id];
    if (!ip) return;
    setBusy(true);
    try {
      await updateRecord(accountId, zoneId, r.id, { content: ip, proxied: r.proxied });
      await loadRecords();
    } catch (e) {
      alert(`Update failed: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function flipProxy(r: DnsRecord) {
    setBusy(true);
    try {
      await toggleProxy(accountId, zoneId, r.id, !r.proxied);
      await loadRecords();
    } catch (e) {
      alert(`Proxy toggle failed: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function removeRecord(r: DnsRecord) {
    if (!confirm(`Delete record ${r.name}?`)) return;
    setBusy(true);
    try {
      await deleteRecord(accountId, zoneId, r.id);
      await loadRecords();
    } catch (e) {
      alert(`Delete failed: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Zones &amp; records</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={loadAccounts} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}
      {accounts.length === 0 && !loading && (
        <div className="muted" style={{ marginBottom: 12 }}>
          No Cloudflare accounts yet — add one under Settings &gt; Cloudflare first.
        </div>
      )}

      <div className="card">
        <h3>Zones &amp; A-records</h3>
        <div className="row" style={{ marginBottom: 10 }}>
          <select className="select" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Select account</option>
            {accounts.map((a) => (
              <option key={String(a.id)} value={String(a.id)}>
                {a.name}
              </option>
            ))}
          </select>
          <select className="select" value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
            <option value="">Select zone</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </select>
          <button className="btn" disabled={busy || !accountId || !zoneId} onClick={loadRecords}>
            Load records
          </button>
        </div>

        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>Content (IP)</th>
              <th>Proxied</th>
              <th>TTL</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.name}</td>
                <td>
                  <div className="row">
                    <input
                      className="input"
                      style={{ width: 150 }}
                      defaultValue={r.content}
                      placeholder={r.content}
                      onChange={(e) => setEditIp((m) => ({ ...m, [r.id]: e.target.value }))}
                    />
                  </div>
                  <span className="muted mono">{r.content}</span>
                </td>
                <td>
                  <span className={`badge ${r.proxied ? 'yellow' : 'green'}`}>
                    {r.proxied ? 'proxied' : 'dns-only'}
                  </span>
                </td>
                <td className="mono">{r.ttl}</td>
                <td>
                  <div className="row">
                    <button className="btn small" disabled={busy} onClick={() => saveIp(r)}>
                      Save IP
                    </button>
                    <button className="btn small" disabled={busy} onClick={() => flipProxy(r)}>
                      {r.proxied ? 'Unproxy' : 'Proxy'}
                    </button>
                    <button className="btn small danger" disabled={busy} onClick={() => removeRecord(r)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {records.length === 0 && <div className="muted">No records. Select account + zone and load.</div>}

        <h3 style={{ marginTop: 16 }}>Add record</h3>
        <div className="row">
          <input
            className="input"
            placeholder="name (sub or @)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            className="input"
            placeholder="content (IPv4)"
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
          />
          <label className="checkbox-row">
            <input type="checkbox" checked={newProxied} onChange={(e) => setNewProxied(e.target.checked)} />
            proxied
          </label>
          <button className="btn primary" disabled={busy || !newName || !newContent} onClick={addRecord}>
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
