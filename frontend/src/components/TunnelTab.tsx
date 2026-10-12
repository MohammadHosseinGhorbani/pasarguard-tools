import { useEffect, useRef, useState } from 'react';
import {
  DirectCarrier,
  JobState,
  Server,
  TransportOption,
  bpAction,
  bpConnTest,
  bpConnTestStop,
  bpCreate,
  bpDelete,
  bpTunnels,
  errMsg,
  fmtElapsed,
  getJob,
  listServers,
  listSideTunnels,
  listTransports,
  pretty,
  startTunnelCreate,
  startTunnelDelete,
  startTunnelTest,
} from '../api';
import ResultBlock from '../components/ResultBlock';
import { humanize } from '../resultText';

interface SteadyRow {
  transport: string;
  rtt: string;
  speed: string;
  winner: boolean;
  setup_value: string | null;
}

export default function TunnelTab() {
  const [iranServers, setIranServers] = useState<Server[]>([]);
  const [kharejServers, setKharejServers] = useState<Server[]>([]);
  const [iranId, setIranId] = useState('');
  const [kharejId, setKharejId] = useState('');
  const [iranIp, setIranIp] = useState('');
  const [transports, setTransports] = useState<TransportOption[]>([]);
  const [carriers, setCarriers] = useState<DirectCarrier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Create form
  const [cDirection, setCDirection] = useState<'reverse' | 'direct'>('reverse');
  const [cFamily, setCFamily] = useState('TCP');
  const [cTransport, setCTransport] = useState('tcpmux');
  const [cCarrier, setCCarrier] = useState('udp');
  const [cPort, setCPort] = useState('');
  const [cFw, setCFw] = useState('');
  const [cName, setCName] = useState('');
  const [cKharejAddr, setCKharejAddr] = useState('');

  // Active job viewer (SSH and API jobs share it)
  const [job, setJob] = useState<JobState | null>(null);
  const [lines, setLines] = useState<{ t: number; line: string }[]>([]);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const cursor = useRef(0);
  const logRef = useRef<HTMLPreElement | null>(null);

  // Manage lists (SSH mode: per side; API mode: single panel list)
  const [iranTunnels, setIranTunnels] = useState<{ backpack: string | null; tunnels: Record<string, unknown>[] } | null>(null);
  const [khTunnels, setKhTunnels] = useState<{ backpack: string | null; tunnels: Record<string, unknown>[] } | null>(null);
  const [iranApiTunnels, setIranApiTunnels] = useState<Record<string, unknown>[]>([]);
  const [khApiTunnels, setKhApiTunnels] = useState<Record<string, unknown>[]>([]);
  const [listing, setListing] = useState(false);

  const iranServer = iranServers.find((s) => String(s.id) === iranId);
  const kharejServer = kharejServers.find((s) => String(s.id) === kharejId);
  const iranApi = !!(iranServer?.bp_url && iranServer?.has_bp_token);
  const khApi = !!(kharejServer?.bp_url && kharejServer?.has_bp_token);
  const bothApi = iranApi && khApi;

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [ir, kh, tr] = await Promise.all([
        listServers('iran'),
        listServers('kharej'),
        listTransports(),
      ]);
      setIranServers(ir);
      setKharejServers(kh);
      setTransports(tr.transports);
      setCarriers(tr.carriers);
      if (ir.length > 0 && !iranId) setIranId(String(ir[0].id));
      if (kh.length > 0 && !kharejId) setKharejId(String(kh[0].id));
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

  // Poll the active job for live transcript.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return;
    const id = setInterval(async () => {
      try {
        const j = await getJob(job.id, cursor.current);
        cursor.current = j.next;
        if (j.lines.length > 0) setLines((prev) => [...prev, ...j.lines]);
        setJob((prev) => (prev ? { ...prev, ...j, lines: [] } : j));
      } catch {
        /* keep polling; a dropped poll is not fatal */
      }
    }, 2000);
    return () => clearInterval(id);
  }, [job?.id, job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live elapsed timer.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [job]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  async function launch(kind: string, starter: () => Promise<{ job_id: string }>) {
    try {
      const { job_id } = await starter();
      cursor.current = 0;
      setLines([]);
      const j = await getJob(job_id, 0);
      cursor.current = j.next;
      setLines(j.lines);
      setJob({ ...j, lines: [] });
      setNow(Date.now() / 1000);
      document.getElementById('tunnel-job')?.scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
      alert(`${kind} failed to start: ${errMsg(e)}`);
    }
  }

  function needServers(apiNeedsKharej: boolean): boolean {
    if (!iranId) {
      alert('Select an Iran server');
      return false;
    }
    if (apiNeedsKharej && !kharejId) {
      alert('Select a Kharej server');
      return false;
    }
    return true;
  }

  // ---------- Test ----------

  const startTest = () => {
    if (iranApi) {
      if (!needServers(true)) return;
      return launch('test', () =>
        bpConnTest({ iran_server_id: iranId, kharej_server_id: kharejId, host: iranIp || undefined }),
      );
    }
    if (!needServers(true)) return;
    return launch('test', () =>
      startTunnelTest({ iran_server_id: iranId, kharej_server_id: kharejId, iran_ip: iranIp || undefined }),
    );
  };

  const stopTest = async () => {
    if (!iranId) return;
    try {
      await bpConnTestStop(iranId);
    } catch (e) {
      alert(`Stop failed: ${errMsg(e)}`);
    }
  };

  // ---------- Create ----------

  const startCreate = () => {
    if (iranApi && khApi) {
      if (!iranId || !kharejId) {
        alert('Select both servers');
        return;
      }
      return launch('create', () =>
        bpCreate({
          iran_server_id: iranId,
          kharej_server_id: kharejId,
          direction: cDirection,
          transport: cTransport,
          carrier: cCarrier,
          tunnel_port: cPort || undefined,
          forwarded_ports: cFw || undefined,
          tunnel_name: cName || undefined,
        }),
      );
    }
    if (!needServers(true)) return;
    return launch('create', () =>
      startTunnelCreate({
        iran_server_id: iranId,
        kharej_server_id: kharejId,
        direction: cDirection,
        transport: cTransport,
        carrier: cCarrier,
        tunnel_port: cPort || undefined,
        forwarded_ports: cFw || undefined,
        tunnel_name: cName || undefined,
        iran_ip: iranIp || undefined,
        kharej_addr: cKharejAddr || undefined,
      }),
    );
  };

  function useResultForCreate(row: SteadyRow) {
    if (!row.setup_value) return;
    if (row.setup_value.startsWith('direct:')) {
      setCDirection('direct');
      setCCarrier(row.setup_value.slice('direct:'.length));
    } else {
      setCDirection('reverse');
      const t = transports.find((x) => x.value === row.setup_value);
      if (t) {
        setCFamily(t.family);
        setCTransport(t.value);
      }
    }
    document.getElementById('tunnel-create')?.scrollIntoView({ behavior: 'smooth' });
  }

  // ---------- Manage (per side: panel API when configured, else SSH) ----------

  async function loadApiSide(which: 'iran' | 'kharej'): Promise<void> {
    const id = which === 'iran' ? iranId : kharejId;
    const r = await bpTunnels(id);
    if (which === 'iran') setIranApiTunnels(r.tunnels ?? []);
    else setKhApiTunnels(r.tunnels ?? []);
  }

  async function refreshManage() {
    if (!iranId || !kharejId) return;
    setListing(true);
    try {
      await Promise.all([
        iranApi ? loadApiSide('iran') : listSideTunnels(iranId).then(setIranTunnels),
        khApi ? loadApiSide('kharej') : listSideTunnels(kharejId).then(setKhTunnels),
      ]);
    } catch (e) {
      alert(`List failed: ${errMsg(e)}`);
    } finally {
      setListing(false);
    }
  }

  async function apiSideAction(serverId: string, side: string, name: string, action: string) {
    if (action === 'delete' && !confirm(`Delete tunnel "${name}" on ${side}?`)) return;
    try {
      if (action === 'delete') {
        const { job_id } = await bpDelete({ server_id: serverId, name });
        cursor.current = 0;
        setLines([]);
        const j = await getJob(job_id, 0);
        cursor.current = j.next;
        setLines(j.lines);
        setJob({ ...j, lines: [] });
        setNow(Date.now() / 1000);
        document.getElementById('tunnel-job')?.scrollIntoView({ behavior: 'smooth' });
      } else {
        const r = await bpAction({ server_id: serverId, name, action });
        alert(humanize('bp-action', { ...(r as object), action, name }));
        await loadApiSide(side === 'Iran' ? 'iran' : 'kharej');
      }
    } catch (e) {
      alert(`${action} failed: ${errMsg(e)}`);
    }
  }

  const startDelete = (tunnelName: string, serverId: string, side: string) => {
    if (!confirm(`Delete tunnel "${tunnelName}" on ${side} only?`)) return;
    void launch('delete', () =>
      startTunnelDelete({ server_id: serverId, tunnel_name: tunnelName }),
    );
  };

  const families = [...new Set(transports.map((t) => t.family))];
  const variants = transports.filter((t) => t.family === cFamily);
  const steady: SteadyRow[] = ((job?.result?.steady as SteadyRow[]) ?? []).filter(Boolean);
  const jobActive = job && (job.status === 'queued' || job.status === 'running');
  const liveRows = Boolean((job?.result as { live?: boolean } | undefined)?.live) && !!jobActive;
  const liveState = (job?.result as { state?: string } | undefined)?.state;
  const elapsed = job ? fmtElapsed((job.ended_at ?? now) - job.started_at) : '00:00';
  const isConnTest = job?.kind === 'tunnel-test' || job?.kind === 'bp-conntest';

  function targetLabel(v: string): string {
    if (v.startsWith('direct:')) {
      const c = carriers.find((x) => x.value === v.slice('direct:'.length));
      return `Direct ${c ? c.label : v}`;
    }
    const t = transports.find((x) => x.value === v);
    return t ? `${t.family} ${t.label}` : v;
  }

  function jobResultText(): string | null {
    if (!job || job.status !== 'done') return null;
    if (job.kind === 'bp-create') return humanize('bp-create', job.result);
    if (job.kind === 'bp-delete') return humanize('bp-delete', job.result);
    if (job.kind === 'tunnel-delete') return humanize('notes', job.result);
    return null;
  }
  const jobText = jobResultText();

  function manageSideTable(
    title: string,
    which: 'iran' | 'kharej',
    serverId: string,
    side: string,
    useApi: boolean,
    sshData: { backpack: string | null; tunnels: Record<string, unknown>[] } | null,
    apiRows: Record<string, unknown>[],
  ) {
    if (useApi) {
      return (
        <div className="card">
          <h3>
            {title} <span className="badge green">API</span>
          </h3>
          {apiRows.length === 0 ? (
            <div className="muted">No tunnels loaded yet.</div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Transport</th>
                  <th>State</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {apiRows.map((t, i) => (
                  <tr key={String(t.name ?? i)}>
                    <td className="mono">{String(t.name ?? '')}</td>
                    <td>{String(t.transport ?? t.tr ?? '')}</td>
                    <td>
                      <span className={`badge ${['online', 'running', 'active'].includes(String(t.state ?? '').toLowerCase()) ? 'green' : 'yellow'}`}>
                        {String(t.state ?? '')}
                      </span>
                    </td>
                    <td>
                      <div className="row">
                        {['start', 'stop', 'restart'].map((a) => (
                          <button key={a} className="btn small" onClick={() => apiSideAction(serverId, side, String(t.name ?? ''), a)}>
                            {a}
                          </button>
                        ))}
                        <button
                          className="btn small danger"
                          onClick={() => apiSideAction(serverId, side, String(t.name ?? ''), 'delete')}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      );
    }
    return tunnelTable(title, sshData, serverId, side);
  }

  function tunnelTable(
    title: string,
    data: { backpack: string | null; tunnels: Record<string, unknown>[] } | null,
    serverId: string,
    side: string,
  ) {
    return (
      <div className="card">
        <h3>
          {title}{' '}
          {data && (
            <span className="muted">
              {data.backpack ? `backpack ${data.backpack}` : 'backpack not installed'}
            </span>
          )}
        </h3>
        {!data ? (
          <div className="muted">Not loaded yet.</div>
        ) : data.tunnels.length === 0 ? (
          <div className="muted">No tunnels.</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th>
                <th>Transport</th>
                <th>State</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.tunnels.map((t, i) => (
                <tr key={String(t.name ?? i)}>
                  <td className="mono">{String(t.name ?? '')}</td>
                  <td>{String(t.transport ?? '')}</td>
                  <td>
                    <span className={`badge ${String(t.state) === 'online' ? 'green' : 'yellow'}`}>
                      {String(t.state ?? '')}
                    </span>
                  </td>
                  <td>
                    <button
                      className="btn small danger"
                      disabled={!serverId}
                      onClick={() => startDelete(String(t.name ?? ''), serverId, side)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  }

  return (
    <div>
      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <h3>Servers (test & create drive both)</h3>
        <div className="form-grid">
          <label className="lbl">
            Iran server (hosts)
            <select className="select" value={iranId} onChange={(e) => setIranId(e.target.value)}>
              <option value="">Select server</option>
              {iranServers.map((s) => (
                <option key={String(s.id)} value={String(s.id)}>
                  {s.name} ({s.host})
                </option>
              ))}
            </select>
          </label>
          <label className="lbl">
            Kharej server (joins)
            <select className="select" value={kharejId} onChange={(e) => setKharejId(e.target.value)}>
              <option value="">Select server</option>
              {kharejServers.map((s) => (
                <option key={String(s.id)} value={String(s.id)}>
                  {s.name} ({s.host})
                </option>
              ))}
            </select>
          </label>
          <label className="lbl">
            Iran IP override (optional, default auto-detected)
            <input
              className="input mono"
              value={iranIp}
              onChange={(e) => setIranIp(e.target.value)}
              placeholder="auto"
            />
          </label>
        </div>
        {iranServers.length === 0 && !loading && (
          <div className="muted" style={{ marginTop: 8 }}>
            No Iran servers yet — add them under Settings &gt; Iran servers first.
          </div>
        )}
      </div>

      <div className="card">
        <h3>
          Mode{' '}
          {bothApi ? (
            <span className="badge green">API</span>
          ) : iranApi || khApi ? (
            <span className="badge yellow">mixed API + SSH</span>
          ) : (
            <span className="badge yellow">SSH</span>
          )}
        </h3>
        <div className="muted">
          Iran side: {iranApi ? 'panel API' : 'SSH'} · Kharej side: {khApi ? 'panel API' : 'SSH'}.
          Set panel URLs + tokens under Settings &gt; BackPack to switch a side to API.
        </div>
      </div>

      <div className="card">
        <h3>Connection test {iranApi ? <span className="badge green">API</span> : <span className="badge yellow">SSH</span>}{liveRows && <span className="badge yellow">live{liveState ? ` · ${liveState}` : ''}</span>}</h3>
        <div className="muted" style={{ marginBottom: 10 }}>
          {iranApi
            ? 'Runs via the panel API with structured per-transport results; Kharej still joins with one link-apply over SSH. The log streams live and the timer runs through quiet phases.'
            : 'Runs every transport for real between the two servers over SSH. The log streams live and the timer runs through quiet phases.'}{' '}
          Ranked results each get a Create button that prefills the form below.
        </div>
        <div className="row">
          <button className="btn primary" disabled={!iranId || !kharejId || !!jobActive} onClick={startTest}>
            {jobActive ? 'Working…' : 'Run test'}
          </button>
          {iranApi && (
            <button className="btn" disabled={!iranId} onClick={stopTest}>
              Stop test
            </button>
          )}
        </div>

        {steady.length > 0 && (
          <table className="tbl" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Transport</th>
                <th>RTT</th>
                <th>Speed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {steady.map((r, i) => (
                <tr key={`${r.transport}-${i}`}>
                  <td>
                    {r.transport} {r.winner && <span className="badge green">winner</span>}
                  </td>
                  <td className="mono">{r.rtt}</td>
                  <td className="mono">{r.speed}</td>
                  <td>
                    {r.setup_value ? (
                      <button className="btn small primary" disabled={!!jobActive} onClick={() => useResultForCreate(r)}>
                        Create → {targetLabel(r.setup_value)}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {steady.some((r) => !r.setup_value) && (
          <div className="muted" style={{ marginTop: 6 }}>
            Rows without Create use route-specific carriers (spoofing) — build those in the
            server&apos;s own <span className="mono">backpack</span> menu.
          </div>
        )}
        {job && job.status === 'done' && isConnTest && steady.length === 0 && (
          <div className="muted" style={{ marginTop: 8 }}>
            No steady transport found — see the log.
          </div>
        )}
      </div>

      <div className="card" id="tunnel-create">
        <h3>Create tunnel (protocol selected manually)</h3>
        <div className="form-grid">
          <label className="lbl">
            Direction
            <select
              className="select"
              value={cDirection}
              onChange={(e) => setCDirection(e.target.value as 'reverse' | 'direct')}
            >
              <option value="reverse">Reverse (Kharej dials Iran)</option>
              <option value="direct">Direct (Iran dials Kharej, full IP tunnel)</option>
            </select>
          </label>
          {cDirection === 'reverse' ? (
            <>
              <label className="lbl">
                Protocol family
                <select
                  className="select"
                  value={cFamily}
                  onChange={(e) => {
                    setCFamily(e.target.value);
                    const first = transports.find((t) => t.family === e.target.value);
                    if (first) setCTransport(first.value);
                  }}
                >
                  {families.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
              <label className="lbl">
                Protocol
                <select className="select" value={cTransport} onChange={(e) => setCTransport(e.target.value)}>
                  {variants.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
            </>
          ) : (
            <>
              <label className="lbl">
                Carrier
                <select className="select" value={cCarrier} onChange={(e) => setCCarrier(e.target.value)}>
                  {carriers.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
              {!bothApi && (
                <label className="lbl">
                  Kharej address (default: server host)
                  <input
                    className="input mono"
                    value={cKharejAddr}
                    onChange={(e) => setCKharejAddr(e.target.value)}
                    placeholder={kharejServers.find((s) => String(s.id) === kharejId)?.host ?? ''}
                  />
                </label>
              )}
            </>
          )}
          <label className="lbl">
            Tunnel port{cDirection === 'reverse' && cTransport !== 'reality' ? ' *' : ''}
            <input
              className="input"
              value={cPort}
              onChange={(e) => setCPort(e.target.value)}
              placeholder={cDirection === 'direct' ? '9000' : cTransport === 'reality' ? 'auto' : 'e.g. 443'}
            />
          </label>
          <label className="lbl">
            Forwarded ports{cDirection === 'reverse' ? ' *' : ' (blank = plain TUN network)'}
            <input
              className="input mono"
              value={cFw}
              onChange={(e) => setCFw(e.target.value)}
              placeholder="443 or 443=127.0.0.1:2096"
            />
          </label>
          <label className="lbl">
            Tunnel name (optional)
            <input
              className="input"
              value={cName}
              onChange={(e) => setCName(e.target.value)}
              placeholder="auto"
            />
          </label>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          {bothApi
            ? 'Created through both panels at once — same tunnel name on each side, no SSH wizards.'
            : cDirection === 'reverse'
              ? 'Sets up Iran via the BackPack wizard (Turbo tuning, generated token), then applies the setup link on Kharej automatically. HTTPS protocols need the helpers — installed automatically if missing.'
              : 'Builds a full IP tunnel (Iran dials Kharej): Iran wizard first, then Kharej is built from the setup link automatically. Needs root on both servers.'}
          {!bothApi && (!iranApi || !khApi) && (
            <> Tip: give both servers panel tokens under Settings &gt; BackPack to create via API instead of SSH.</>
          )}
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button
            className="btn primary"
            disabled={
              !iranId ||
              !kharejId ||
              (cDirection === 'reverse' && (!cFw || (!cPort && cTransport !== 'reality'))) ||
              !!jobActive
            }
            onClick={startCreate}
          >
            {jobActive ? 'Working…' : 'Create tunnel'}
          </button>
        </div>
      </div>

      <div className="card" id="tunnel-job">
        <h3>
          Live progress{' '}
          {job && (
            <span className="muted mono">
              {job.title} · <span className="badge">{elapsed}</span>{' '}
              <span className={`badge ${job.status === 'done' ? 'green' : job.status === 'error' ? 'red' : 'yellow'}`}>
                {job.status}
              </span>
            </span>
          )}
        </h3>
        {!job ? (
          <div className="muted">No job yet — run a test, create or delete above.</div>
        ) : (
          <>
            <pre ref={logRef} className="out" style={{ maxHeight: 420 }}>
              {lines.map((l) => l.line).join('\n')}
            </pre>
            {jobText && (
              <pre className="out" style={{ marginTop: 8 }}>
                {jobText}
              </pre>
            )}
            {job.status === 'done' && job.result && Object.keys(job.result).length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary className="muted" style={{ cursor: 'pointer' }}>
                  Result details
                </summary>
                <pre className="out">{pretty(job.result)}</pre>
              </details>
            )}
            {job.status === 'error' && <div className="alert error">{job.error}</div>}
          </>
        )}
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Manage tunnels</h3>
          <span style={{ flex: 1 }} />
          <button
            className="btn"
            disabled={listing || !iranId || !kharejId}
            onClick={refreshManage}
          >
            {listing ? 'Loading…' : 'Refresh both'}
          </button>
        </div>
        <div className="muted" style={{ marginBottom: 10 }}>
          Each side uses its panel API when a token is set, SSH menus otherwise.
          {iranApi || khApi
            ? ' API sides support start/stop/restart; delete is always per side.'
            : ' Set panel tokens under Settings > BackPack to manage via API.'}
        </div>
        <div className="grid2">
          {manageSideTable('Iran side', 'iran', iranId, 'Iran', iranApi, iranTunnels, iranApiTunnels)}
          {manageSideTable('Kharej side', 'kharej', kharejId, 'Kharej', khApi, khTunnels, khApiTunnels)}
        </div>
      </div>
    </div>
  );
}
