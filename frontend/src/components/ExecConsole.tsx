import { useEffect, useState } from 'react';
import { Server, errMsg, execOnServer, listServers } from '../api';
import ResultBlock from './ResultBlock';
import { humanize } from '../resultText';

export default function ExecConsole({ role, title }: { role?: 'iran' | 'kharej'; title?: string }) {
  const [servers, setServers] = useState<Server[]>([]);
  const [serverId, setServerId] = useState('');
  const [command, setCommand] = useState('');
  const [useSudo, setUseSudo] = useState(false);
  const [out, setOut] = useState('');
  const [raw, setRaw] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    try {
      const data = await listServers(role);
      setServers(data);
      if (data.length > 0 && !serverId) setServerId(String(data[0].id));
    } catch {
      /* selector just stays empty */
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  async function run() {
    if (!serverId || !command) return;
    setLoading(true);
    setOut('');
    setRaw(null);
    try {
      const r = await execOnServer(serverId, command, useSudo);
      setOut(humanize('exec', r));
      setRaw(r);
    } catch (e) {
      setOut(`ERROR: ${errMsg(e)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3>{title ?? 'Exec console'}</h3>
      <div className="row" style={{ marginBottom: 10 }}>
        <select className="select" value={serverId} onChange={(e) => setServerId(e.target.value)}>
          <option value="">Select server</option>
          {servers.map((s) => (
            <option key={String(s.id)} value={String(s.id)}>
              {s.name} ({s.host})
            </option>
          ))}
        </select>
        <input
          className="input"
          style={{ flex: 1, minWidth: 220 }}
          placeholder="command, e.g. uname -a"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') run();
          }}
        />
        <label className="checkbox-row">
          <input type="checkbox" checked={useSudo} onChange={(e) => setUseSudo(e.target.checked)} /> sudo
        </label>
        <button className="btn primary" disabled={loading || !serverId || !command} onClick={run}>
          {loading ? 'Running…' : 'Run'}
        </button>
      </div>
      {out ? <ResultBlock text={out} raw={raw} /> : null}
    </div>
  );
}
