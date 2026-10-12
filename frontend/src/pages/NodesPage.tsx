import { useEffect, useState } from 'react';
import {
  Panel,
  Server,
  errMsg,
  listPanels,
  listServers,
  nodeAction,
  nodeCredentials,
  nodeInstall,
  nodeRegister,
  nodeStatus,
} from '../api';
import ResultBlock from '../components/ResultBlock';
import { humanize } from '../resultText';

const ACTIONS = ['up', 'down', 'restart', 'logs', 'core-update', 'renew-cert'];

export default function NodesPage({ role }: { role?: string }) {
  const [servers, setServers] = useState<Server[]>([]);
  const [panels, setPanels] = useState<Panel[]>([]);
  const [serverId, setServerId] = useState('');
  const [panelId, setPanelId] = useState('');
  const [nodeName, setNodeName] = useState('');
  const [servicePort, setServicePort] = useState('');
  const [apiPort, setApiPort] = useState('');
  const [action, setAction] = useState('status');
  const [out, setOut] = useState('');
  const [outRaw, setOutRaw] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [s, p] = await Promise.all([listServers(role), listPanels()]);
      setServers(s);
      setPanels(p);
      if (s.length > 0 && !serverId) setServerId(String(s[0].id));
      if (p.length > 0 && !panelId) setPanelId(String(p[0].id));
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

  async function run(label: string, kind: string, fn: () => Promise<unknown>) {
    if (!serverId) {
      alert('Select a server first');
      return;
    }
    setBusy(true);
    setOut('');
    setOutRaw(null);
    try {
      const r = await fn();
      setOut(`$ ${label}\n` + humanize(kind, r));
      setOutRaw(r);
    } catch (e) {
      setOut(`ERROR: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Nodes</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <h3>Target</h3>
        <div className="form-grid">
          <label className="lbl">
            Server
            <select className="select" value={serverId} onChange={(e) => setServerId(e.target.value)}>
              <option value="">Select server</option>
              {servers.map((s) => (
                <option key={String(s.id)} value={String(s.id)}>
                  {s.name} ({s.host})
                </option>
              ))}
            </select>
          </label>
          <label className="lbl">
            Panel (for register)
            <select className="select" value={panelId} onChange={(e) => setPanelId(e.target.value)}>
              <option value="">Select panel</option>
              {panels.map((p) => (
                <option key={String(p.id)} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="lbl">
            Node name (register)
            <input
              className="input"
              placeholder="optional"
              value={nodeName}
              onChange={(e) => setNodeName(e.target.value)}
            />
          </label>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <label className="lbl">
              Service port
              <input
                className="input"
                type="number"
                placeholder="optional"
                value={servicePort}
                onChange={(e) => setServicePort(e.target.value)}
              />
            </label>
            <label className="lbl">
              API port
              <input
                className="input"
                type="number"
                placeholder="optional"
                value={apiPort}
                onChange={(e) => setApiPort(e.target.value)}
              />
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Operations</h3>
        <div className="row">
          <button className="btn" disabled={busy || !serverId} onClick={() => run('status', 'node-status', () => nodeStatus(serverId))}>
            Status
          </button>
          <button
            className="btn"
            disabled={busy || !serverId}
            onClick={() => {
              if (!confirm('Install node on selected server?')) return Promise.resolve();
              return run(
                'install',
                'node-install',
                () =>
                  nodeInstall(
                    serverId,
                    servicePort ? Number(servicePort) : undefined,
                    apiPort ? Number(apiPort) : undefined,
                  ),
              );
            }}
          >
            Install
          </button>
          <button
            className="btn"
            disabled={busy || !serverId}
            onClick={() => run('credentials', 'node-credentials', () => nodeCredentials(serverId))}
          >
            Credentials
          </button>
          <button
            className="btn primary"
            disabled={busy || !serverId || !panelId}
            onClick={() => run('register', 'node-register', () => nodeRegister(serverId, panelId, nodeName || undefined))}
          >
            Register
          </button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <select className="select" value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <button
            className="btn"
            disabled={busy || !serverId}
            onClick={() => run(`action ${action}`, 'node-action', () => nodeAction(serverId, action))}
          >
            Run action
          </button>
        </div>
      </div>

      <div className="card">
        <h3>Output</h3>
        {busy && <div className="spinner">Working…</div>}
        {out ? (
          <ResultBlock text={out} raw={outRaw} />
        ) : (
          <div className="muted">No output yet.</div>
        )}
      </div>
    </div>
  );
}
