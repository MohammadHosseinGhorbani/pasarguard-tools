import { useEffect, useState } from 'react';
import Modal from '../components/Modal';
import {
  CfAccount,
  InboundInfo,
  LiveCert,
  Panel,
  PanelHost,
  PanelNode,
  Server,
  createCloudflareInbound,
  createHost,
  createRealityInbound,
  deleteHost,
  deleteInbound,
  errMsg,
  listCfAccounts,
  listHosts,
  listPanels,
  listServers,
  loadInbounds,
  loadPanelNodes,
  loadRouting,
  nodeCertificates,
  quickCloudflareInbound,
  quickRealityInbound,
  saveRoutingAssignment,
  summarizeInbound,
  updateHost,
} from '../api';
import ResultBlock from '../components/ResultBlock';
import { humanize } from '../resultText';

const CF_PORTS = [443, 2053, 2083, 2087, 2096, 8443];

export default function InboundsPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [panels, setPanels] = useState<Panel[]>([]);
  const [cfAccounts, setCfAccounts] = useState<CfAccount[]>([]);
  const [serverId, setServerId] = useState('');
  const [panelId, setPanelId] = useState('');
  const [nodes, setNodes] = useState<PanelNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Quick-add (panel only, no SSH)
  const [qNode, setQNode] = useState('');
  const [qType, setQType] = useState<'reality' | 'cloudflare'>('reality');
  const [qSni, setQSni] = useState('icloud.com:443');
  const [qPort, setQPort] = useState('443');
  const [qNetwork, setQNetwork] = useState('xhttp');
  const [qTag, setQTag] = useState('');
  const [qDomain, setQDomain] = useState('');
  const [qCerts, setQCerts] = useState<LiveCert[]>([]);
  const [qCertDomain, setQCertDomain] = useState('');
  const [qCertsBusy, setQCertsBusy] = useState(false);

  // Full setup via SSH (first-time server)
  const [rSni, setRSni] = useState('');
  const [rPort, setRPort] = useState('443');
  const [rNetwork, setRNetwork] = useState('xhttp');
  const [rNode, setRNode] = useState('');
  const [rTag, setRTag] = useState('');
  const [rCore, setRCore] = useState('');
  const [cAccount, setCAccount] = useState('');
  const [cDomain, setCDomain] = useState('');
  const [cIp, setCIp] = useState('');
  const [cEmail, setCEmail] = useState('');
  const [cPort, setCPort] = useState('443');
  const [cNetwork, setCNetwork] = useState('xhttp');

  const [result, setResult] = useState('');
  const [resultRaw, setResultRaw] = useState<unknown>(null);

  // Routing (outbound assignment) per core, loaded automatically with inbounds.
  const [routing, setRouting] = useState<
    Record<string, { outbounds: { tag: string; protocol: string }[]; rules: { inboundTag?: string[]; outboundTag?: string }[] }>
  >({});

  function showResult(r: unknown) {
    setResult(humanize('inbound', r));
    setResultRaw(r);
  }

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [s, p, c] = await Promise.all([listServers(), listPanels(), listCfAccounts()]);
      setServers(s);
      setPanels(p);
      setCfAccounts(c);
      if (p.length > 0 && !panelId) setPanelId(String(p[0].id));
      if (c.length > 0 && !cAccount) setCAccount(String(c[0].id));
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

  async function doLoad() {
    if (!panelId) {
      alert('Select a panel first (no server needed)');
      return;
    }
    setBusy(true);
    setError('');
    try {
      let list: PanelNode[];
      if (serverId) {
        // Narrow to the node matching the server IP (original behavior).
        const r = await loadInbounds(serverId, panelId);
        if (!r.node) {
          list = [];
        } else {
          list = [{ node: r.node as PanelNode['node'], core: r.core as PanelNode['core'], inbounds: r.inbounds ?? [] }];
        }
      } else {
        list = await loadPanelNodes(panelId);
      }
      setNodes(list);
      setResult('');
      // Outbounds + routing rules follow automatically — no second button.
      void loadRoutingFor(panelId, list);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function loadRoutingFor(pid: string, list: PanelNode[]) {
    const entries = await Promise.all(
      list
        .filter((n) => n.core?.id !== undefined && n.core?.id !== null)
        .map(async (n) => {
          const cid = String(n.core?.id ?? '');
          try {
            const r = await loadRouting(pid, cid);
            return [cid, { outbounds: r.outbounds ?? [], rules: r.rules ?? [] }] as const;
          } catch {
            return [cid, { outbounds: [], rules: [] }] as const;
          }
        }),
    );
    setRouting(Object.fromEntries(entries));
  }

  function assignmentFor(coreId: string | number, tag: string): string | null {
    const r = routing[String(coreId)];
    if (!r) return null;
    const hit = r.rules.find((x) => (x.inboundTag ?? []).includes(tag));
    return hit?.outboundTag ?? null;
  }

  async function remove(node: PanelNode, row: InboundInfo) {
    const coreId = String(node.core?.id ?? '');
    if (!panelId || !coreId || !row.tag) {
      alert('Need panel + core + tag to delete.');
      return;
    }
    if (!confirm(`Delete inbound "${row.tag}" from node "${String(node.node?.name ?? node.node?.id)}"?`)) return;
    setBusy(true);
    try {
      await deleteInbound(panelId, coreId, row.tag);
      await doLoad();
    } catch (e) {
      alert(`Delete failed: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  function prefillReplace(node: PanelNode, row: InboundInfo) {
    openReplace(node, row);
  }

  // ---- Replace modal (separate big dialog) ----
  const [repl, setRepl] = useState<{ node: PanelNode; row: InboundInfo; type: 'reality' | 'cloudflare' } | null>(null);
  const [mSni, setMSni] = useState('');
  const [mPort, setMPort] = useState('443');
  const [mNetwork, setMNetwork] = useState('xhttp');
  const [mTag, setMTag] = useState('');
  const [mDomain, setMDomain] = useState('');
  const [mCerts, setMCerts] = useState<LiveCert[]>([]);
  const [mCertDomain, setMCertDomain] = useState('');
  const [mCertsBusy, setMCertsBusy] = useState(false);
  const [mBusy, setMBusy] = useState(false);
  const [mMsg, setMMsg] = useState('');

  function openReplace(node: PanelNode, row: InboundInfo) {
    const { network, security, sni } = summarizeInbound(row);
    const type = security === 'tls' ? 'cloudflare' : 'reality';
    setRepl({ node, row, type });
    setMTag(row.tag);
    setMPort(String(row.port ?? 443));
    setMNetwork(type === 'reality' ? (network === 'tcp' ? 'tcp' : 'xhttp') : network === 'ws' ? 'ws' : 'xhttp');
    setMSni(sni || 'icloud.com:443');
    setMDomain(sni || '');
    setMCerts([]);
    setMCertDomain('');
    setMMsg('');
    if (type === 'cloudflare') {
      // The cert lives where the domain points (the node's server):
      // list that server's certs so the user just selects one.
      const match = servers.find((s) => s.host === String(node.node.address ?? ''));
      if (match) void loadModalCerts(String(match.id), sni || '');
      else setMMsg(`No saved server matches node IP ${String(node.node.address ?? '')} — certs can't be listed.`);
    }
  }

  async function loadModalCerts(serverId: string, preselectDomain: string) {
    setMCertsBusy(true);
    try {
      const r = await nodeCertificates(serverId);
      const list = r.certs ?? [];
      setMCerts(list);
      const hit = list.find((c) => c.domain === preselectDomain) ?? list[0];
      setMCertDomain(hit ? hit.domain : '');
      if (list.length === 0) setMMsg('No certificates on that server — issue one from Cloudflare > Certificates first.');
    } catch (e) {
      setMMsg(`ERROR listing certs: ${errMsg(e)}`);
    } finally {
      setMCertsBusy(false);
    }
  }

  async function submitReplace() {
    if (!repl || !panelId) return;
    const nodeId = repl.node.node.id;
    const clash = portConflict(String(nodeId), mPort, mTag || undefined);
    if (clash && !confirm(`Port ${mPort} is already used by inbound "${clash}" on this node. Replace anyway?`)) return;
    if (repl.type === 'reality') {
      if (!mSni || !mPort || !mTag) {
        setMMsg('SNI, port and tag are required.');
        return;
      }
      if (!confirm(`Replace inbound "${mTag}" with a new Reality config?`)) return;
    } else {
      if (!mDomain || !mCertDomain || !mTag) {
        setMMsg('Domain, certificate and tag are required — select a certificate above.');
        return;
      }
      if (!confirm(`Replace inbound "${mTag}" with a new Cloudflare config?`)) return;
    }
    setMBusy(true);
    setMMsg('');
    try {
      let r: unknown;
      if (repl.type === 'reality') {
        r = await quickRealityInbound({
          panel_id: panelId, node_id: nodeId, sni: mSni,
          port: Number(mPort), network: mNetwork, tag: mTag,
        });
      } else {
        r = await quickCloudflareInbound({
          panel_id: panelId, node_id: nodeId, domain: mDomain,
          port: Number(mPort), network: mNetwork, cert_domain: mCertDomain,
        });
      }
      setRepl(null);
      showResult(r);
      await doLoad();
    } catch (e) {
      setMMsg(`ERROR: ${errMsg(e)}`);
    } finally {
      setMBusy(false);
    }
  }

  async function loadQuickCerts(nodeId: string) {
    const node = nodes.find((n) => String(n.node.id) === String(nodeId));
    if (!node) return;
    const match = servers.find((s) => s.host === String(node.node.address ?? ''));
    if (!match) {
      setQCerts([]);
      setQCertDomain('');
      return;
    }
    setQCertsBusy(true);
    try {
      const r = await nodeCertificates(match.id);
      const list = r.certs ?? [];
      setQCerts(list);
      setQCertDomain((prev) => (list.some((c) => c.domain === prev) ? prev : (list[0]?.domain ?? '')));
    } catch {
      setQCerts([]);
    } finally {
      setQCertsBusy(false);
    }
  }

  useEffect(() => {
    if (qType === 'cloudflare' && qNode) void loadQuickCerts(qNode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qNode, qType, nodes]);

  // Port already in use on a node (by a *different* tag — same tag = replace in place).
  function portConflict(nodeId: string | number, port: string, excludeTag?: string): string | null {
    const p = Number(port);
    if (!port || Number.isNaN(p)) return null;
    const node = nodes.find((n) => String(n.node.id) === String(nodeId));
    if (!node) return null;
    const hit = (node.inbounds ?? []).find((r) => Number(r.port) === p && r.tag !== excludeTag);
    return hit ? hit.tag : null;
  }

  // ---- Hosts per inbound (subscription hosts) ----
  const [hostsFor, setHostsFor] = useState<{ node: PanelNode; row: InboundInfo } | null>(null);
  const [hosts, setHosts] = useState<PanelHost[]>([]);
  const [hostsBusy, setHostsBusy] = useState(false);
  const [hostsMsg, setHostsMsg] = useState('');
  const [hEditing, setHEditing] = useState<PanelHost | null>(null);
  const [hRemark, setHRemark] = useState('');
  const [hAddress, setHAddress] = useState('');
  const [hEchConfig, setHEchConfig] = useState('');

  // ---- Outbound assignment per inbound (routing rule) ----
  const [obFor, setObFor] = useState<{ node: PanelNode; row: InboundInfo } | null>(null);
  const [obSel, setObSel] = useState('');
  const [obBusy, setObBusy] = useState(false);
  const [obMsg, setObMsg] = useState('');

  function openOutbound(node: PanelNode, row: InboundInfo) {
    setObFor({ node, row });
    setObSel(assignmentFor(String(node.core?.id ?? ''), row.tag) ?? '');
    setObMsg('');
  }

  async function reloadRoutingFor(node: PanelNode) {
    if (!panelId || node.core?.id === undefined || node.core?.id === null) return;
    const cid = String(node.core.id);
    try {
      const r = await loadRouting(panelId, cid);
      setRouting((m) => ({ ...m, [cid]: { outbounds: r.outbounds ?? [], rules: r.rules ?? [] } }));
    } catch (e) {
      setObMsg(`ERROR reloading outbounds: ${errMsg(e)}`);
    }
  }

  async function saveOutbound() {
    if (!obFor || !panelId) return;
    const cid = String(obFor.node.core?.id ?? '');
    if (!cid) {
      setObMsg('This node has no core attached.');
      return;
    }
    setObBusy(true);
    setObMsg('');
    try {
      const r = await saveRoutingAssignment({
        panel_id: panelId,
        core_id: cid,
        inbound_tag: obFor.row.tag,
        outbound_tag: obSel,
      });
      await reloadRoutingFor(obFor.node);
      const d = r as { outbound_tag?: string | null };
      showResult({ ok: true, message: d.outbound_tag ? `Inbound "${obFor.row.tag}" routes via "${d.outbound_tag}".` : `Routing rule for "${obFor.row.tag}" removed.` });
      setObFor(null);
    } catch (e) {
      setObMsg(`ERROR: ${errMsg(e)}`);
    } finally {
      setObBusy(false);
    }
  }

  function defaultHostForm() {
    if (!hostsFor) return;
    setHEditing(null);
    setHRemark(hostsFor.row.tag);
    setHAddress(String(hostsFor.node.node.address ?? ''));
    setHEchConfig('');
  }

  async function openHosts(node: PanelNode, row: InboundInfo) {
    setHostsFor({ node, row });
    setHosts([]);
    setHostsMsg('');
    setHEditing(null);
    setHRemark(row.tag);
    setHAddress(String(node.node.address ?? ''));
    setHEchConfig('');
    await loadHosts(row.tag);
  }

  async function loadHosts(tag: string) {
    if (!panelId) return;
    setHostsBusy(true);
    try {
      setHosts(await listHosts(panelId, tag));
    } catch (e) {
      setHostsMsg(`ERROR: ${errMsg(e)}`);
    } finally {
      setHostsBusy(false);
    }
  }

  function startEditHost(h: PanelHost) {
    setHEditing(h);
    setHRemark(h.remark ?? '');
    setHAddress(Array.isArray(h.address) ? h.address.join(', ') : String(h.address ?? ''));
    const raw = h as unknown as Record<string, unknown>;
    const flat = typeof raw.ech_config_list === 'string' ? (raw.ech_config_list as string) : '';
    const nested = (raw.ech as { xray?: { config_list?: string } } | undefined)?.xray?.config_list ?? '';
    setHEchConfig(flat || nested);
    setHostsMsg('');
  }

  function resetHostForm() {
    defaultHostForm();
  }

  async function saveHost() {
    if (!hostsFor || !panelId) return;
    if (!hRemark.trim()) {
      setHostsMsg('Remark is required.');
      return;
    }
    setHostsBusy(true);
    setHostsMsg('');
    try {
      let saved: unknown;
      if (hEditing) {
        saved = await updateHost(hEditing.id, {
          panel_id: panelId, remark: hRemark, address: hAddress, ech_config: hEchConfig,
        });
      } else {
        saved = await createHost({
          panel_id: panelId,
          inbound_tag: hostsFor.row.tag,
          remark: hRemark,
          address: hAddress,
          ech_config: hEchConfig,
        });
      }
      resetHostForm();
      await loadHosts(hostsFor.row.tag);
      setHostsMsg(humanize('host', saved));
    } catch (e) {
      setHostsMsg(`ERROR: ${errMsg(e)}`);
    } finally {
      setHostsBusy(false);
    }
  }

  async function removeHost(h: PanelHost) {
    if (!panelId) return;
    if (!confirm(`Delete host "${h.remark}"?`)) return;
    setHostsBusy(true);
    try {
      await deleteHost(h.id, panelId);
      if (hostsFor) await loadHosts(hostsFor.row.tag);
    } catch (e) {
      setHostsMsg(`ERROR: ${errMsg(e)}`);
    } finally {
      setHostsBusy(false);
    }
  }

  async function submitQuick() {    if (!panelId || !qNode) {
    alert('panel and node are required');
    return;
  }
    const qClash = portConflict(qNode, qPort, qTag || undefined);
    if (qClash && !confirm(`Port ${qPort} is already used by inbound "${qClash}" on this node. Create anyway?`)) return;
    if (!confirm(qType === 'reality' ? 'Add Reality inbound? (same tag = replace)' : 'Add Cloudflare inbound? (same tag = replace)')) return;
    setBusy(true);
    setResult('');
    try {
      let r: unknown;
      if (qType === 'reality') {
        if (!qSni || !qPort) {
          alert('sni and port are required');
          return;
        }
        r = await quickRealityInbound({
          panel_id: panelId, node_id: qNode, sni: qSni,
          port: Number(qPort), network: qNetwork, tag: qTag || undefined,
        });
      } else {
        if (!qDomain || !qCertDomain) {
          alert('domain and certificate are required');
          return;
        }
        r = await quickCloudflareInbound({
          panel_id: panelId, node_id: qNode, domain: qDomain,
          port: Number(qPort), network: qNetwork, cert_domain: qCertDomain,
          tag: qTag || undefined,
        });
      }
      showResult(r);
      await doLoad();
    } catch (e) {
      setResult(`ERROR: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function submitReality() {
    if (!serverId || !panelId || !rSni || !rPort) {
      alert('server, panel, sni and port are required');
      return;
    }
    if (!confirm('Full Reality setup via SSH?')) return;
    setBusy(true);
    setResult('');
    try {
      const payload: Record<string, unknown> = {
        server_id: serverId,
        panel_id: panelId,
        sni: rSni,
        port: Number(rPort),
        network: rNetwork,
      };
      if (rNode) payload.node_name = rNode;
      if (rTag) payload.tag = rTag;
      if (rCore) payload.core_name = rCore;
      const r = await createRealityInbound(payload);
      showResult(r);
      await doLoad();
    } catch (e) {
      setResult(`ERROR: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function submitCloudflare() {
    if (!serverId || !panelId || !cAccount || !cDomain) {
      alert('server, panel, cf account and domain are required');
      return;
    }
    if (!confirm('Full Cloudflare setup via SSH?')) return;
    setBusy(true);
    setResult('');
    try {
      const payload: Record<string, unknown> = {
        server_id: serverId,
        panel_id: panelId,
        cf_account_id: cAccount,
        domain: cDomain,
        port: Number(cPort),
        network: cNetwork,
      };
      if (cIp) payload.server_ip = cIp;
      if (cEmail) payload.email = cEmail;
      const r = await createCloudflareInbound(payload);
      showResult(r);
      await doLoad();
    } catch (e) {
      setResult(`ERROR: ${errMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Inbounds</h2>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {loading && <div className="spinner">Loading…</div>}
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <h3>Load inbounds (panel only)</h3>
        <div className="row">
          <select className="select" value={panelId} onChange={(e) => setPanelId(e.target.value)}>
            <option value="">Select panel</option>
            {panels.map((p) => (
              <option key={String(p.id)} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
          <select className="select" value={serverId} onChange={(e) => setServerId(e.target.value)}>
            <option value="">All nodes</option>
            {servers.map((s) => (
              <option key={String(s.id)} value={String(s.id)}>
                match node IP: {s.name} ({s.host})
              </option>
            ))}
          </select>
          <button className="btn primary" disabled={busy || !panelId} onClick={doLoad}>
            {busy ? 'Loading…' : 'Load'}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          No server needed — listing and deleting use only the panel URL + API key. Pick a server only to
          narrow to one node, or for the full SSH setup below. Outbounds and routing rules load
          automatically with the inbounds.
        </div>
      </div>

      {nodes.map((n) => (
        <div className="card" key={String(n.node.id)}>
          <h3>
            {String(n.node.name ?? n.node.id)}{' '}
            <span className="muted mono">{String(n.node.address ?? '')}</span>{' '}
            <span className="badge">{String(n.core?.name ?? 'no core')}</span>
          </h3>
          <table className="tbl">
            <thead>
              <tr>
                <th>Tag</th>
                <th>Port</th>
                <th>Protocol</th>
                <th>Network</th>
                <th>Security</th>
                <th>SNI</th>
                <th>Outbound</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {n.inbounds.map((r, i) => {
                const s = summarizeInbound(r);
                const assigned = assignmentFor(String(n.core?.id ?? ''), r.tag);
                return (
                  <tr key={`${r.tag}-${i}`}>
                    <td className="mono">{r.tag}</td>
                    <td className="mono">{r.port ?? ''}</td>
                    <td>{r.protocol ?? ''}</td>
                    <td>{s.network}</td>
                    <td>{s.security}</td>
                    <td className="mono">{s.sni}</td>
                    <td className="mono">{assigned ?? <span className="muted">—</span>}</td>
                    <td>
                      <div className="row">
                        <button className="btn small" disabled={busy} onClick={() => prefillReplace(n, r)}>
                          Replace…
                        </button>
                        <button className="btn small" disabled={busy} onClick={() => openHosts(n, r)}>
                          Hosts
                        </button>
                        <button className="btn small" disabled={busy} onClick={() => openOutbound(n, r)}>
                          Outbound
                        </button>
                        <button className="btn small danger" disabled={busy} onClick={() => remove(n, r)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {n.inbounds.length === 0 && <div className="muted">No inbounds on this node.</div>}
        </div>
      ))}
      {nodes.length === 0 && !loading && <div className="muted">Nothing loaded yet.</div>}

      <div className="card" id="quick-add">
        <h3>Quick add (panel only, no SSH)</h3>
        <div className="muted" style={{ marginBottom: 10 }}>
          Adds to an existing node directly via the panel API. Keys are generated locally;
          the certificate is auto-fetched from the node's server (certbot). Using an existing tag replaces that inbound.
        </div>
        <div className="form-grid">
          <label className="lbl">
            Node
            <select className="select" value={qNode} onChange={(e) => setQNode(e.target.value)}>
              <option value="">Select node</option>
              {nodes.map((n) => (
                <option key={String(n.node.id)} value={String(n.node.id)}>
                  {String(n.node.name ?? n.node.id)} ({String(n.node.address ?? '')})
                </option>
              ))}
            </select>
          </label>
          <label className="lbl">
            Type
            <select className="select" value={qType} onChange={(e) => setQType(e.target.value as 'reality' | 'cloudflare')}>
              <option value="reality">Reality</option>
              <option value="cloudflare">Cloudflare</option>
            </select>
          </label>
          <label className="lbl">
            Port
            {qType === 'reality' ? (
              <input className="input" type="number" value={qPort} onChange={(e) => setQPort(e.target.value)} />
            ) : (
              <select className="select" value={qPort} onChange={(e) => setQPort(e.target.value)}>
                {CF_PORTS.map((p) => (
                  <option key={p} value={String(p)}>
                    {p}
                  </option>
                ))}
              </select>
            )}
          </label>
          <label className="lbl">
            Network
            <select className="select" value={qNetwork} onChange={(e) => setQNetwork(e.target.value)}>
              {(qType === 'reality' ? ['xhttp', 'tcp'] : ['xhttp', 'ws']).map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </label>
          {qType === 'reality' ? (
            <>
              <label className="lbl">
                SNI
                <input className="input" value={qSni} onChange={(e) => setQSni(e.target.value)} placeholder="icloud.com:443" />
              </label>
              <label className="lbl">
                Tag (existing = replace)
                <input className="input" value={qTag} onChange={(e) => setQTag(e.target.value)} placeholder="optional" />
              </label>
            </>
          ) : (
            <>
              <label className="lbl">
                Domain
                <input className="input" value={qDomain} onChange={(e) => setQDomain(e.target.value)} placeholder="sub.example.com" />
              </label>
              <label className="lbl">
                Tag (existing = replace)
                <input className="input" value={qTag} onChange={(e) => setQTag(e.target.value)} placeholder="optional" />
              </label>
              <label className="lbl">
                Certificate (from the node&apos;s server)
                <select
                  className="select"
                  value={qCertDomain}
                  onChange={(e) => {
                    setQCertDomain(e.target.value);
                    if (e.target.value) setQDomain(e.target.value);
                  }}
                >
                  <option value="">Select certificate</option>
                  {qCerts.map((c) => (
                    <option key={c.domain} value={c.domain}>
                      {c.domain} {c.valid === false ? '(expired)' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <div className="row" style={{ alignItems: 'end' }}>
                <button className="btn small" disabled={qCertsBusy || !qNode} onClick={() => qNode && void loadQuickCerts(qNode)}>
                  {qCertsBusy ? '…' : 'Reload certs'}
                </button>
              </div>
              {qType === 'cloudflare' && qNode && qCerts.length === 0 && !qCertsBusy && (
                <div className="muted full" style={{ gridColumn: '1 / -1' }}>
                  No certificates listed — the node&apos;s IP matches no saved server, or it has none yet.
                  Issue one from Cloudflare &gt; Certificates.
                </div>
              )}
            </>
          )}
        </div>
        {qNode && portConflict(qNode, qPort, qTag || undefined) && (
          <div className="alert error" style={{ marginTop: 10 }}>
            Port {qPort} is already used by “{portConflict(qNode, qPort, qTag || undefined)}” on this
            node. Pick a free port, or keep the same tag to replace it.
          </div>
        )}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={busy || !qNode} onClick={submitQuick}>
            {busy ? 'Working…' : qType === 'reality' ? 'Add Reality inbound' : 'Add Cloudflare inbound'}
          </button>
        </div>
      </div>

      <details className="card">
        <summary style={{ cursor: 'pointer' }}>Full setup via SSH (new server: install + register + inbound)</summary>
        <div className="muted" style={{ margin: '8px 0' }}>
          Needs a saved server. Installs pg-node, registers it into the panel and creates the inbound in one run.
        </div>
        <div className="grid2">
          <div className="card">
            <h3>Reality</h3>
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
                SNI
                <input className="input" value={rSni} onChange={(e) => setRSni(e.target.value)} placeholder="example.com" />
              </label>
              <label className="lbl">
                Port
                <input className="input" type="number" value={rPort} onChange={(e) => setRPort(e.target.value)} />
              </label>
              <label className="lbl">
                Network
                <select className="select" value={rNetwork} onChange={(e) => setRNetwork(e.target.value)}>
                  <option value="xhttp">xhttp</option>
                  <option value="tcp">tcp</option>
                </select>
              </label>
              <label className="lbl">
                Node name
                <input className="input" value={rNode} onChange={(e) => setRNode(e.target.value)} placeholder="optional" />
              </label>
              <label className="lbl">
                Tag
                <input className="input" value={rTag} onChange={(e) => setRTag(e.target.value)} placeholder="optional" />
              </label>
              <label className="lbl">
                Core name
                <input className="input" value={rCore} onChange={(e) => setRCore(e.target.value)} placeholder="optional" />
              </label>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={busy} onClick={submitReality}>
                Create Reality
              </button>
            </div>
          </div>

          <div className="card">
            <h3>Cloudflare</h3>
            <div className="form-grid">
              <label className="lbl">
                CF account
                <select className="select" value={cAccount} onChange={(e) => setCAccount(e.target.value)}>
                  <option value="">Select account</option>
                  {cfAccounts.map((a) => (
                    <option key={String(a.id)} value={String(a.id)}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="lbl">
                Domain
                <input className="input" value={cDomain} onChange={(e) => setCDomain(e.target.value)} placeholder="sub.example.com" />
              </label>
              <label className="lbl">
                Server IP
                <input className="input" value={cIp} onChange={(e) => setCIp(e.target.value)} placeholder="optional" />
              </label>
              <label className="lbl">
                Email
                <input className="input" value={cEmail} onChange={(e) => setCEmail(e.target.value)} placeholder="optional" />
              </label>
              <label className="lbl">
                Port
                <select className="select" value={cPort} onChange={(e) => setCPort(e.target.value)}>
                  {CF_PORTS.map((p) => (
                    <option key={p} value={String(p)}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
              <label className="lbl">
                Network
                <select className="select" value={cNetwork} onChange={(e) => setCNetwork(e.target.value)}>
                  <option value="xhttp">xhttp</option>
                  <option value="ws">ws</option>
                </select>
              </label>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={busy} onClick={submitCloudflare}>
                Create Cloudflare
              </button>
            </div>
          </div>
        </div>
      </details>

      {repl && (
        <Modal onClose={() => !mBusy && setRepl(null)} width={720}>
            <h3 style={{ marginTop: 0 }}>
              Replace inbound <span className="mono">“{repl.row.tag}”</span> on{' '}
              {String(repl.node.node.name ?? repl.node.node.id)} —{' '}
              {repl.type === 'reality' ? 'Reality' : 'Cloudflare'}
            </h3>
            <div className="muted" style={{ marginBottom: 12 }}>
              Same tag = replaced in place. Keys are generated fresh; the old config is overwritten.
            </div>
            <div className="form-grid">
              <label className="lbl">
                Port
                {repl.type === 'reality' ? (
                  <input className="input" type="number" value={mPort} onChange={(e) => setMPort(e.target.value)} />
                ) : (
                  <select className="select" value={mPort} onChange={(e) => setMPort(e.target.value)}>
                    {CF_PORTS.map((p) => (
                      <option key={p} value={String(p)}>
                        {p}
                      </option>
                    ))}
                  </select>
                )}
              </label>
              <label className="lbl">
                Network
                <select className="select" value={mNetwork} onChange={(e) => setMNetwork(e.target.value)}>
                  {(repl.type === 'reality' ? ['xhttp', 'tcp'] : ['xhttp', 'ws']).map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </label>
              <label className="lbl">
                Tag
                <input className="input mono" value={mTag} onChange={(e) => setMTag(e.target.value)} />
              </label>
              {repl.type === 'reality' ? (
                <label className="lbl">
                  SNI
                  <input className="input" value={mSni} onChange={(e) => setMSni(e.target.value)} />
                </label>
              ) : (
                <>
                  <label className="lbl">
                    Domain
                    <input className="input" value={mDomain} onChange={(e) => setMDomain(e.target.value)} />
                  </label>
                </>
              )}
            </div>

            {repl.type === 'cloudflare' && (
              <div className="card" style={{ marginTop: 12, marginBottom: 0 }}>
                <h3>Certificate (from the node&apos;s server)</h3>
                <div className="muted" style={{ marginBottom: 10 }}>
                  Listed from the saved server matching the node IP. Missing one? Issue it from
                  Cloudflare &gt; Certificates first.
                </div>
                <div className="row">
                  <select
                    className="select"
                    style={{ flex: 1 }}
                    value={mCertDomain}
                    onChange={(e) => {
                      setMCertDomain(e.target.value);
                      if (e.target.value) setMDomain(e.target.value);
                    }}
                  >
                    <option value="">Select certificate</option>
                    {mCerts.map((c) => (
                      <option key={c.domain} value={c.domain}>
                        {c.domain} {c.valid === false ? '(expired)' : ''}
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn small"
                    disabled={mCertsBusy}
                    onClick={() => {
                      const match = servers.find((s) => s.host === String(repl.node.node.address ?? ''));
                      if (match) void loadModalCerts(String(match.id), mDomain);
                      else setMMsg('No saved server matches this node IP.');
                    }}
                  >
                    {mCertsBusy ? '…' : 'Reload'}
                  </button>
                </div>
              </div>
            )}

            {repl && portConflict(String(repl.node.node.id), mPort, mTag || undefined) && (
              <div className="alert error" style={{ marginTop: 12 }}>
                Port {mPort} is already used by “{portConflict(String(repl.node.node.id), mPort, mTag || undefined)}”
                on this node. Change the port, or keep the same tag to replace it.
              </div>
            )}

            {mMsg && (
              <pre className="out" style={{ marginTop: 12 }}>
                {mMsg}
              </pre>
            )}
            <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
              <button className="btn" disabled={mBusy} onClick={() => setRepl(null)}>
                Cancel
              </button>
              <button className="btn primary" disabled={mBusy} onClick={submitReplace}>
                {mBusy ? 'Working…' : 'Replace inbound'}
              </button>
            </div>
        </Modal>
      )}

      {hostsFor && (
        <Modal onClose={() => !hostsBusy && setHostsFor(null)} width={720}>
          <h3 style={{ marginTop: 0 }}>
            Hosts for inbound <span className="mono">“{hostsFor.row.tag}”</span>
          </h3>
          <div className="muted" style={{ marginBottom: 12 }}>
            Subscription hosts attached to this inbound. Create one so clients get links for it.
          </div>
          {hostsBusy && hosts.length === 0 ? (
            <div className="spinner">Loading…</div>
          ) : hosts.length === 0 ? (
            <div className="muted">No hosts yet for this inbound.</div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Remark</th>
                  <th>Address</th>
                  <th>Port</th>
                  <th>SNI</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {hosts.map((h) => (
                  <tr key={String(h.id)}>
                    <td>{h.remark}</td>
                    <td className="mono">{Array.isArray(h.address) ? h.address.join(', ') : String(h.address ?? '')}</td>
                    <td className="mono">{h.port ?? ''}</td>
                    <td className="mono">{Array.isArray(h.sni) ? h.sni.join(', ') : String(h.sni ?? '')}</td>
                    <td>
                      {h.is_disabled ? (
                        <span className="badge red">disabled</span>
                      ) : (
                        <span className="badge green">active</span>
                      )}
                    </td>
                    <td>
                      <div className="row">
                        <button className="btn small" disabled={hostsBusy} onClick={() => startEditHost(h)}>
                          Edit
                        </button>
                        <button className="btn small danger" disabled={hostsBusy} onClick={() => removeHost(h)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3 style={{ marginTop: 16 }}>{hEditing ? 'Edit host' : 'New host'}</h3>
          <div className="form-grid">
            <label className="lbl">
              Remark
              <input className="input" value={hRemark} onChange={(e) => setHRemark(e.target.value)} placeholder={hostsFor.row.tag} />
            </label>
            <label className="lbl">
              Address
              <input
                className="input mono"
                value={hAddress}
                onChange={(e) => setHAddress(e.target.value)}
                placeholder={String(hostsFor.node.node.address ?? '')}
              />
            </label>
            <label className="lbl full">
              ECH config (empty = none)
              <input
                className="input mono"
                list="ech-presets"
                value={hEchConfig}
                onChange={(e) => setHEchConfig(e.target.value)}
                placeholder="cloudflare-ech.com+udp://1.1.1.1"
              />
              <datalist id="ech-presets">
                <option value="cloudflare-ech.com+udp://1.1.1.1" />
                <option value="ip.gs+udp://8.8.8.8" />
              </datalist>
            </label>
          </div>
          {hostsMsg && (
            <pre className="out" style={{ marginTop: 12 }}>
              {hostsMsg}
            </pre>
          )}
          <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
            {hEditing && (
              <button className="btn" disabled={hostsBusy} onClick={() => resetHostForm()}>
                Cancel edit
              </button>
            )}
            <button className="btn" disabled={hostsBusy} onClick={() => setHostsFor(null)}>
              Close
            </button>
            <button className="btn primary" disabled={hostsBusy || !hRemark} onClick={saveHost}>
              {hostsBusy ? 'Working…' : hEditing ? 'Save host' : 'Create host'}
            </button>
          </div>
        </Modal>
      )}

      {obFor && (
        <Modal onClose={() => !obBusy && setObFor(null)} width={560}>
          <h3 style={{ marginTop: 0 }}>
            Outbound for inbound <span className="mono">“{obFor.row.tag}”</span>
          </h3>
          <div className="muted" style={{ marginBottom: 12 }}>
            Saved as one routing rule on core{' '}
            <span className="mono">{String(obFor.node.core?.name ?? obFor.node.core?.id ?? '')}</span>.
            Saving replaces any existing rule for this inbound (nodes restart).
          </div>
          {(() => {
            const cid = String(obFor.node.core?.id ?? '');
            const opts = routing[cid]?.outbounds ?? [];
            if (!routing[cid]) {
              return (
                <div className="row">
                  <div className="muted">Outbounds not loaded yet.</div>
                  <button className="btn small" disabled={obBusy} onClick={() => reloadRoutingFor(obFor.node)}>
                    Retry
                  </button>
                </div>
              );
            }
            return (
              <label className="lbl full">
                Outbound
                <select className="select" value={obSel} onChange={(e) => setObSel(e.target.value)}>
                  <option value="">None (remove rule)</option>
                  {opts.map((o) => (
                    <option key={o.tag} value={o.tag}>
                      {o.tag} ({o.protocol || 'outbound'})
                    </option>
                  ))}
                </select>
              </label>
            );
          })()}
          {obMsg && (
            <pre className="out" style={{ marginTop: 12 }}>
              {obMsg}
            </pre>
          )}
          <div className="row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
            <button className="btn" disabled={obBusy} onClick={() => setObFor(null)}>
              Cancel
            </button>
            <button className="btn primary" disabled={obBusy} onClick={saveOutbound}>
              {obBusy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </Modal>
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
