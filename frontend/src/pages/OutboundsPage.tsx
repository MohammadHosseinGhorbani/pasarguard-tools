import { useEffect, useState } from 'react';
import {
  Panel,
  PanelNode,
  WgOutbound,
  errMsg,
  listPanels,
  loadOutbounds,
  loadPanelNodes,
  saveOutbounds,
} from '../api';
import ResultBlock from '../components/ResultBlock';

interface Row {
  key: number;
  original_tag: string;
  tag: string;
  conf: string;
  current: string;
}

let nextKey = 1;

function summarize(o: WgOutbound): string {
  const peers = o.settings?.peers ?? [];
  const ep = peers.map((p) => p.endpoint).filter(Boolean).join(', ');
  const addr = (o.settings?.address ?? []).join(', ');
  return [ep, addr].filter(Boolean).join(' · ');
}

export default function OutboundsPage() {
  const [panels, setPanels] = useState<Panel[]>([]);
  const [panelId, setPanelId] = useState('');
  const [nodes, setNodes] = useState<PanelNode[]>([]);
  const [coreId, setCoreId] = useState('');
  const [coreName, setCoreName] = useState('');
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [resultRaw, setResultRaw] = useState<unknown>(null);

  async function loadPanels() {
    setLoading(true);
    setError('');
    try {
      const p = await listPanels();
      setPanels(p);
      if (p.length > 0 && !panelId) setPanelId(String(p[0].id));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadPanels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doLoadNodes() {
    if (!panelId) {
      alert('Select a panel first');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const n = await loadPanelNodes(panelId);
      setNodes(n.filter((x) => x.core));
      setCoreId('');
      setRows([]);
      setResult('');
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function doLoadOutbounds() {
    if (!panelId || !coreId) {
      alert('Select a panel and a core first');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const r = await loadOutbounds(panelId, coreId);
      setCoreName(String(r.core?.name ?? ''));
      setRows(
        (r.outbounds ?? []).map((o) => ({
          key: nextKey++,
          original_tag: o.tag,
          tag: o.tag,
          conf: '',
          current: summarize(o),
        })),
      );
      setResult('');
      setResultRaw(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function setRow(key: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function removeRow(key: number) {
    setRows((prev) => prev.filter((r) => r.key !== key));
  }

  function addRow() {
    setRows((prev) => [...prev, { key: nextKey++, original_tag: '', tag: '', conf: '', current: '' }]);
  }

  async function save() {
    if (!panelId || !coreId) return;
    if (rows.some((r) => !r.tag.trim())) {
      alert('Every outbound needs a name.');
      return;
    }
    if (!confirm(`Save ${rows.length} WireGuard outbound(s) to core "${coreName || coreId}"? (nodes restart)`)) return;
    setBusy(true);
    setResult('');
    setResultRaw(null);
    try {
      const r = await saveOutbounds({
        panel_id: panelId,
        core_id: coreId,
        outbounds: rows.map((x) => ({
          tag: x.tag.trim(),
          original_tag: x.original_tag,
          wireguard_conf: x.conf,
        })),
      });
      const d = r as { outbounds?: unknown[]; recovered?: boolean };
      setResult(
        `✅ saved ${(d.outbounds ?? []).length} WireGuard outbound(s) to core "${coreName || coreId}".` +
          (d.recovered ? ' (Panel dropped the connection during restart, but the change was verified.)' : ''),
      );
      setResultRaw(r);
      await doLoadOutbounds();
    } catch (e) {
      setResult(`ERROR: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const cores = nodes
    .filter((n) => n.core)
    .map((n) => ({
      id: String(n.core?.id ?? ''),
      label: `${String(n.node.name ?? n.node.id)} — ${String(n.core?.name ?? n.core?.id)}`,
    }));

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>WireGuard outbounds</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={loadPanels} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <h3>Target core</h3>
        <div className="row">
          <select className="select" value={panelId} onChange={(e) => setPanelId(e.target.value)}>
            <option value="">Select panel</option>
            {panels.map((p) => (
              <option key={String(p.id)} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
          <button className="btn" disabled={busy || !panelId} onClick={doLoadNodes}>
            {busy ? 'Loading…' : 'Load cores'}
          </button>
          <select className="select" value={coreId} onChange={(e) => setCoreId(e.target.value)}>
            <option value="">Select core</option>
            {cores.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
          <button className="btn primary" disabled={busy || !coreId} onClick={doLoadOutbounds}>
            {busy ? 'Loading…' : 'Load outbounds'}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          Only WireGuard outbounds are listed. Renames, replacements, deletes and adds are staged
          here — nothing is sent until Save.
        </div>
      </div>

      {coreId && (
        <div className="card">
          <div className="row" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>
              Outbounds {coreName && <span className="muted mono">{coreName}</span>}
            </h3>
            <span style={{ flex: 1 }} />
            <button className="btn" disabled={busy} onClick={addRow}>
              + Add outbound
            </button>
          </div>
          {rows.length === 0 && <div className="muted">No WireGuard outbounds. Add one above.</div>}
          {rows.map((r) => (
            <div className="card" key={r.key} style={{ marginBottom: 10 }}>
              <div className="form-grid">
                <label className="lbl">
                  Name
                  <input
                    className="input mono"
                    value={r.tag}
                    onChange={(e) => setRow(r.key, { tag: e.target.value })}
                    placeholder="proton-us"
                  />
                </label>
                {r.original_tag ? (
                  <div className="muted" style={{ alignSelf: 'end' }}>
                    current: <span className="mono">{r.original_tag}</span>
                    {r.current && <span className="mono"> · {r.current}</span>}
                  </div>
                ) : (
                  <div className="muted" style={{ alignSelf: 'end' }}>new outbound</div>
                )}
                <label className="lbl full">
                  New WireGuard config (empty = keep current)
                  <textarea
                    className="textarea mono"
                    rows={8}
                    value={r.conf}
                    onChange={(e) => setRow(r.key, { conf: e.target.value })}
                    placeholder={'[Interface]\nPrivateKey = ...\nAddress = 10.0.0.2/32\n\n[Peer]\nPublicKey = ...\nEndpoint = vpn.example.com:51820'}
                  />
                </label>
              </div>
              <div className="row" style={{ marginTop: 10, justifyContent: 'flex-end' }}>
                <button className="btn small danger" disabled={busy} onClick={() => removeRow(r.key)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
          {rows.length > 0 && (
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={busy} onClick={save}>
                {busy ? 'Saving…' : `Save ${rows.length} outbound(s)`}
              </button>
            </div>
          )}
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
