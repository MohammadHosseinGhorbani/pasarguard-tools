// Central fetch wrapper + API helpers. Token stored in localStorage.

export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) || '';

const TOKEN_KEY = 'pg_token';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type FetchOptions = Omit<RequestInit, 'body'> & { body?: unknown };

export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, `Network error: ${e instanceof Error ? e.message : String(e)}`);
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    if (typeof data === 'object' && data !== null) {
      const d = data as Record<string, unknown>;
      if (typeof d.detail === 'string') msg = d.detail;
      else if (typeof d.message === 'string') msg = d.message;
      else if (typeof d.error === 'string') msg = d.error;
      else msg = JSON.stringify(data);
    } else if (typeof data === 'string' && data) {
      msg = data;
    }
    if (res.status === 401) clearToken();
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

// ---------- Types (kept loose to tolerate backend variations) ----------

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface User {
  username?: string;
  [k: string]: unknown;
}

export interface Server {
  id: number | string;
  name: string;
  host: string;
  username: string;
  port: number;
  password?: string;
  key_path?: string;
  key_content?: string;
  notes?: string;
  role?: string;
  bp_url?: string;
  bp_verify?: boolean;
  bp_node?: string;
  has_bp_token?: boolean;
  [k: string]: unknown;
}

export interface ServerTestResult {
  ok: boolean;
  latency_ms?: number;
  outputs?: unknown;
  [k: string]: unknown;
}

export interface ExecResult {
  rc: number;
  out: string;
  err: string;
  [k: string]: unknown;
}

export interface Panel {
  id: number | string;
  name: string;
  base_url?: string;
  verify_ssl?: boolean;
  api_key_masked?: string;
  active?: boolean;
  is_active?: boolean;
  [k: string]: unknown;
}

export function isPanelActive(p: Panel): boolean {
  return Boolean(p.active ?? p.is_active ?? (p as Record<string, unknown>).isActive);
}

export interface CfAccount {
  id: number | string;
  name: string;
  [k: string]: unknown;
}

export interface Zone {
  id: string;
  name: string;
}

export interface DnsRecord {
  id: string;
  name: string;
  type: string;
  content: string;
  proxied: boolean;
  ttl: number;
}

export interface InboundInfo {
  tag: string;
  port?: number;
  protocol?: string;
  network?: string;
  security?: string;
  sni?: string;
  raw?: unknown;
  [k: string]: unknown;
}

export interface InboundsResponse {
  node?: unknown;
  core?: unknown;
  inbounds: InboundInfo[];
  [k: string]: unknown;
}

// ---------- Auth ----------

export async function login(username: string, password: string): Promise<LoginResponse> {
  const data = await apiFetch<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: { username, password },
  });
  setToken(data.access_token);
  return data;
}

export function logout() {
  clearToken();
}

export async function fetchMe(): Promise<User> {
  return apiFetch<User>('/api/auth/me');
}

export async function changePassword(old_password: string, new_password: string): Promise<unknown> {
  return apiFetch('/api/auth/change-password', {
    method: 'POST',
    body: { old_password, new_password },
  });
}

// ---------- Servers ----------

export async function listServers(role?: string): Promise<Server[]> {
  const qs = role ? `?role=${encodeURIComponent(role)}` : '';
  return apiFetch<Server[]>(`/api/servers${qs}`);
}

export async function createServer(payload: Partial<Server>): Promise<Server> {
  return apiFetch<Server>('/api/servers', { method: 'POST', body: payload });
}

export async function updateServer(id: number | string, payload: Partial<Server>): Promise<Server> {
  return apiFetch<Server>(`/api/servers/${id}`, { method: 'PUT', body: payload });
}

export async function deleteServer(id: number | string): Promise<unknown> {
  return apiFetch(`/api/servers/${id}`, { method: 'DELETE' });
}

export async function testServer(id: number | string): Promise<ServerTestResult> {
  return apiFetch<ServerTestResult>(`/api/servers/${id}/test`, { method: 'POST' });
}

export async function execOnServer(
  id: number | string,
  command: string,
  use_sudo: boolean,
): Promise<ExecResult> {
  return apiFetch<ExecResult>(`/api/servers/${id}/exec`, {
    method: 'POST',
    body: { command, use_sudo },
  });
}

// ---------- Panels ----------

export async function listPanels(): Promise<Panel[]> {
  return apiFetch<Panel[]>('/api/panels');
}

export async function createPanel(payload: Record<string, unknown>): Promise<Panel> {
  return apiFetch<Panel>('/api/panels', { method: 'POST', body: payload });
}

export async function updatePanel(id: number | string, payload: Record<string, unknown>): Promise<Panel> {
  return apiFetch<Panel>(`/api/panels/${id}`, { method: 'PUT', body: payload });
}

export async function deletePanel(id: number | string): Promise<unknown> {
  return apiFetch(`/api/panels/${id}`, { method: 'DELETE' });
}

export async function activatePanel(id: number | string): Promise<unknown> {
  return apiFetch(`/api/panels/${id}/activate`, { method: 'POST' });
}

export async function installPanel(payload: {
  server_id: number | string;
  database: string;
  ssl_mode?: string;
  ssl_domain?: string;
}): Promise<unknown> {
  return apiFetch('/api/panels/ops/install', { method: 'POST', body: payload });
}

export async function runPanelAction(server_id: number | string, action: string): Promise<unknown> {
  return apiFetch('/api/panels/ops/run', { method: 'POST', body: { server_id, action } });
}

// ---------- Nodes ----------

export async function nodeStatus(server_id: number | string): Promise<unknown> {
  return apiFetch('/api/nodes/status', { method: 'POST', body: { server_id } });
}

export async function nodeInstall(
  server_id: number | string,
  service_port?: number,
  api_port?: number,
): Promise<unknown> {
  const body: Record<string, unknown> = { server_id };
  if (service_port !== undefined) body.service_port = service_port;
  if (api_port !== undefined) body.api_port = api_port;
  return apiFetch('/api/nodes/install', { method: 'POST', body });
}

export async function nodeCredentials(server_id: number | string): Promise<unknown> {
  return apiFetch('/api/nodes/credentials', { method: 'POST', body: { server_id } });
}

export async function nodeRegister(
  server_id: number | string,
  panel_id: number | string,
  node_name?: string,
): Promise<unknown> {
  return apiFetch('/api/nodes/register', {
    method: 'POST',
    body: { server_id, panel_id, node_name },
  });
}

export async function nodeAction(server_id: number | string, action: string): Promise<unknown> {
  return apiFetch('/api/nodes/action', { method: 'POST', body: { server_id, action } });
}

export interface LiveCert {
  domain: string;
  valid_from?: string;
  valid_until?: string;
  subject?: string;
  valid?: boolean;
}

export async function nodeCertificates(
  server_id: number | string,
): Promise<{ certbot: string; certs: LiveCert[] }> {
  return apiFetch('/api/nodes/certificates', { method: 'POST', body: { server_id } });
}

export async function issueCertificate(payload: {
  server_id: number | string;
  domain: string;
  email?: string;
}): Promise<unknown> {
  return apiFetch('/api/nodes/cert-issue', { method: 'POST', body: payload });
}

// ---------- Tunnel (BackPack, Iran <-> Kharej) ----------

export interface TransportOption {
  family: string;
  family_no: number;
  label: string;
  value: string;
  variant_no: number;
  managed: boolean;
}

export interface DirectCarrier {
  label: string;
  value: string;
  no: number;
  needs_root: boolean;
}

export async function listTransports(): Promise<{ transports: TransportOption[]; carriers: DirectCarrier[] }> {
  const data = await apiFetch<{ transports: TransportOption[]; direct_carriers: DirectCarrier[] }>(
    '/api/tunnel/transports',
  );
  return { transports: data.transports ?? [], carriers: data.direct_carriers ?? [] };
}

export async function startTunnelTest(payload: {
  iran_server_id: number | string;
  kharej_server_id: number | string;
  iran_ip?: string;
}): Promise<{ job_id: string }> {
  return apiFetch('/api/tunnel/test', { method: 'POST', body: payload });
}

export async function startTunnelCreate(payload: Record<string, unknown>): Promise<{ job_id: string }> {
  return apiFetch('/api/tunnel/create', { method: 'POST', body: payload });
}

export async function startTunnelDelete(payload: {
  server_id: number | string;
  tunnel_name: string;
}): Promise<{ job_id: string }> {
  return apiFetch('/api/tunnel/delete', { method: 'POST', body: payload });
}

export interface SideTunnels {
  backpack: string | null;
  tunnels: Array<Record<string, unknown>>;
}

export async function listSideTunnels(server_id: number | string): Promise<SideTunnels> {
  return apiFetch('/api/tunnel/list', { method: 'POST', body: { server_id } });
}

export interface JobLine {
  t: number;
  line: string;
}

export interface JobState {
  id: string;
  kind: string;
  title: string;
  status: 'queued' | 'running' | 'done' | 'error';
  started_at: number;
  ended_at: number | null;
  elapsed_s: number;
  lines: JobLine[];
  next: number;
  result: Record<string, unknown>;
  error: string;
}

export async function getJob(job_id: string, since = 0): Promise<JobState> {
  return apiFetch<JobState>(`/api/tunnel/jobs/${job_id}?since=${since}`);
}

export function fmtElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// ---------- BackPack panel API (Iran server as hub) ----------

export async function saveBpSettings(
  server_id: number | string,
  payload: { bp_url: string; bp_token?: string; bp_verify: boolean; bp_node: string },
): Promise<Server> {
  return apiFetch<Server>(`/api/servers/${server_id}/bp`, { method: 'PATCH', body: payload });
}

export async function bpTest(server_id: number | string): Promise<unknown> {
  return apiFetch('/api/bp/test', { method: 'POST', body: { server_id } });
}

export async function bpTunnels(server_id: number | string): Promise<{ tunnels: Record<string, unknown>[] }> {
  const data = await apiFetch<{ tunnels: Record<string, unknown>[] } | Record<string, unknown>[]>(
    '/api/bp/tunnels',
    { method: 'POST', body: { server_id } },
  );
  if (Array.isArray(data)) return { tunnels: data };
  return { tunnels: data.tunnels ?? [] };
}

export async function bpAction(payload: {
  server_id: number | string;
  name: string;
  action: string;
}): Promise<unknown> {
  return apiFetch('/api/bp/action', { method: 'POST', body: payload });
}

export async function bpCreate(payload: {
  iran_server_id: number | string;
  kharej_server_id: number | string;
  direction?: string;
  transport?: string;
  carrier?: string;
  tunnel_port?: string;
  forwarded_ports?: string;
  tunnel_name?: string;
}): Promise<{ job_id: string }> {
  return apiFetch('/api/bp/create', { method: 'POST', body: payload });
}

export async function bpDelete(payload: {
  server_id: number | string;
  name: string;
}): Promise<{ job_id: string }> {
  return apiFetch('/api/bp/delete', { method: 'POST', body: payload });
}

export async function bpConnTest(payload: {
  iran_server_id: number | string;
  kharej_server_id: number | string;
  host?: string;
  preset?: string;
}): Promise<{ job_id: string }> {
  return apiFetch('/api/bp/conntest', { method: 'POST', body: payload });
}

export async function bpConnTestStop(server_id: number | string): Promise<unknown> {
  return apiFetch('/api/bp/conntest/stop', { method: 'POST', body: { server_id } });
}

// ---------- Cloudflare ----------

export async function listCfAccounts(): Promise<CfAccount[]> {
  return apiFetch<CfAccount[]>('/api/cf-accounts');
}

export async function createCfAccount(payload: Record<string, unknown>): Promise<CfAccount> {
  return apiFetch<CfAccount>('/api/cf-accounts', { method: 'POST', body: payload });
}

export async function updateCfAccount(
  id: number | string,
  payload: Record<string, unknown>,
): Promise<CfAccount> {
  return apiFetch<CfAccount>(`/api/cf-accounts/${id}`, { method: 'PUT', body: payload });
}

export async function deleteCfAccount(id: number | string): Promise<unknown> {
  return apiFetch(`/api/cf-accounts/${id}`, { method: 'DELETE' });
}

export async function listZones(accountId: number | string): Promise<Zone[]> {
  const data = await apiFetch<Zone[] | { zones: Zone[] }>(
    `/api/cf-accounts/${accountId}/zones`,
  );
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.zones)) return data.zones;
  return [];
}

export async function listRecords(accountId: number | string, zoneId: string): Promise<DnsRecord[]> {
  const data = await apiFetch<DnsRecord[] | { records: DnsRecord[] }>(
    `/api/cf-accounts/${accountId}/zones/${zoneId}/records?type=A`,
  );
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.records)) return data.records;
  return [];
}

export async function createRecord(
  accountId: number | string,
  zoneId: string,
  payload: { name: string; content: string; proxied: boolean },
): Promise<unknown> {
  return apiFetch(`/api/cf-accounts/${accountId}/zones/${zoneId}/records`, {
    method: 'POST',
    body: payload,
  });
}

export async function updateRecord(
  accountId: number | string,
  zoneId: string,
  recordId: string,
  payload: { content: string; proxied: boolean; name?: string },
): Promise<unknown> {
  return apiFetch(`/api/cf-accounts/${accountId}/zones/${zoneId}/records/${recordId}`, {
    method: 'PUT',
    body: payload,
  });
}

export async function toggleProxy(
  accountId: number | string,
  zoneId: string,
  recordId: string,
  proxied: boolean,
): Promise<unknown> {
  return apiFetch(`/api/cf-accounts/${accountId}/zones/${zoneId}/records/${recordId}/proxy`, {
    method: 'PATCH',
    body: { proxied },
  });
}

export async function deleteRecord(
  accountId: number | string,
  zoneId: string,
  recordId: string,
): Promise<unknown> {
  return apiFetch(`/api/cf-accounts/${accountId}/zones/${zoneId}/records/${recordId}`, {
    method: 'DELETE',
  });
}

// ---------- Inbounds ----------

export async function loadInbounds(
  server_id?: number | string,
  panel_id?: number | string,
): Promise<InboundsResponse> {
  const q = new URLSearchParams();
  if (server_id !== undefined && server_id !== '') q.set('server_id', String(server_id));
  if (panel_id !== undefined && panel_id !== '') q.set('panel_id', String(panel_id));
  const qs = q.toString() ? `?${q.toString()}` : '';
  return apiFetch<InboundsResponse>(`/api/inbounds${qs}`);
}

export async function deleteInbound(panel_id: number | string, core_id: string, tag: string) {
  const q = new URLSearchParams({
    panel_id: String(panel_id),
    core_id,
    tag,
  });
  return apiFetch(`/api/inbounds?${q.toString()}`, { method: 'DELETE' });
}

export async function createRealityInbound(payload: Record<string, unknown>): Promise<unknown> {
  return apiFetch('/api/inbounds/reality', { method: 'POST', body: payload });
}

export async function createCloudflareInbound(payload: Record<string, unknown>): Promise<unknown> {
  return apiFetch('/api/inbounds/cloudflare', { method: 'POST', body: payload });
}

export interface PanelNode {
  node: Record<string, unknown> & { id: number | string; name?: string; address?: string };
  core: { id: number | string; name?: string } | null;
  inbounds: InboundInfo[];
}

// Panel-only: list every node of a panel with its core + inbounds. No server needed.
export async function loadPanelNodes(panel_id: number | string): Promise<PanelNode[]> {
  const data = await apiFetch<{ nodes: PanelNode[] } | PanelNode[]>(
    `/api/inbounds?panel_id=${panel_id}`,
  );
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.nodes)) return data.nodes;
  return [];
}

// Panel-only quick adds to an EXISTING node (keys generated server-side
// locally, no SSH). Reusing an existing tag replaces that inbound.
export async function quickRealityInbound(payload: Record<string, unknown>): Promise<unknown> {
  return apiFetch('/api/inbounds/reality/quick', { method: 'POST', body: payload });
}

export async function quickCloudflareInbound(payload: Record<string, unknown>): Promise<unknown> {
  return apiFetch('/api/inbounds/cloudflare/quick', { method: 'POST', body: payload });
}

// ---------- Outbounds (WireGuard) ----------

// ---------- Outbounds (WireGuard) ----------

export interface WgOutbound {
  tag: string;
  protocol?: string;
  settings?: {
    address?: string[];
    DNS?: string;
    peers?: { endpoint?: string; publicKey?: string; allowedIPs?: string[]; keepAlive?: number }[];
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

export interface OutboundsResponse {
  core: { id: number | string; name?: string; type?: string };
  outbounds: WgOutbound[];
}

export async function loadOutbounds(
  panel_id: number | string,
  core_id: number | string,
): Promise<OutboundsResponse> {
  return apiFetch<OutboundsResponse>(`/api/outbounds?panel_id=${panel_id}&core_id=${core_id}`);
}

export interface OutboundSaveItem {
  tag: string;
  original_tag?: string;
  wireguard_conf?: string;
}

export async function saveOutbounds(payload: {
  panel_id: number | string;
  core_id: number | string;
  outbounds: OutboundSaveItem[];
}): Promise<unknown> {
  return apiFetch('/api/outbounds', { method: 'PUT', body: payload });
}

export interface RoutingRule {
  inboundTag?: string[];
  outboundTag?: string;
  [k: string]: unknown;
}

export interface RoutingResponse {
  core: { id: number | string; name?: string };
  outbounds: { tag: string; protocol: string }[];
  rules: RoutingRule[];
}

export async function loadRouting(
  panel_id: number | string,
  core_id: number | string,
): Promise<RoutingResponse> {
  return apiFetch<RoutingResponse>(`/api/outbounds/routing?panel_id=${panel_id}&core_id=${core_id}`);
}

export async function saveRoutingAssignment(payload: {
  panel_id: number | string;
  core_id: number | string;
  inbound_tag: string;
  outbound_tag: string;
}): Promise<unknown> {
  return apiFetch('/api/outbounds/routing', { method: 'PUT', body: payload });
}

// ---------- Proton VPN ----------

export interface ProtonStatus {
  username: string;
  has_password: boolean;
  app_version: string;
  proxy: string;
  logged_in: boolean;
  mode: 'refresh' | 'cookie' | 'none' | string;
  uid: string;
  expires_at: number;
  seconds_left: number;
  days_left: number | null;
}

export interface ProtonConfig {
  id: number;
  name: string;
  country: string;
  server_name: string;
  entry_ip: string;
  port: number;
  address: string;
  dns: string;
  serial: string;
  expires_at: number;
  created_at: number;
  outbound_tag: string;
  conf: string;
}

export interface ProtonServer {
  name: string;
  exit_country: string;
  entry_country: string;
  load: number | null;
  score: number | null;
  status: number | null;
  entry_ip: string;
}

export async function getProtonStatus(): Promise<ProtonStatus> {
  return apiFetch<ProtonStatus>('/api/proton/settings');
}

export async function saveProtonSettings(payload: {
  username?: string;
  password?: string;
  app_version?: string;
  proxy?: string;
}): Promise<ProtonStatus> {
  return apiFetch<ProtonStatus>('/api/proton/settings', { method: 'PUT', body: payload });
}

export async function protonLogin(payload: {
  username?: string;
  password?: string;
  totp?: string;
} = {}): Promise<ProtonStatus> {
  return apiFetch<ProtonStatus>('/api/proton/login', { method: 'POST', body: payload });
}

export async function protonLogout(): Promise<ProtonStatus> {
  return apiFetch<ProtonStatus>('/api/proton/logout', { method: 'POST' });
}

export async function clearProtonAccount(): Promise<unknown> {
  return apiFetch('/api/proton/account', { method: 'DELETE' });
}

export async function listProtonServers(
  tier = 0,
  countries = '',
  limit = 0,
): Promise<{ servers: ProtonServer[]; tier: number }> {
  const q = new URLSearchParams({ tier: String(tier), countries, limit: String(limit) });
  return apiFetch(`/api/proton/servers?${q.toString()}`);
}

export async function listProtonConfigs(): Promise<{ configs: ProtonConfig[]; logged_in: boolean }> {
  return apiFetch('/api/proton/configs');
}

export async function createProtonConfig(payload: {
  name: string;
  country?: string;
  tier?: number;
}): Promise<{ ok: boolean; config: ProtonConfig; conf: string }> {
  return apiFetch('/api/proton/configs', { method: 'POST', body: payload });
}

export async function rotateProtonConfig(payload: {
  name: string;
  country?: string;
  tier?: number;
}): Promise<{ ok: boolean; config: ProtonConfig; conf: string; notes?: string[] }> {
  return apiFetch('/api/proton/configs/rotate', { method: 'POST', body: payload });
}

export async function deleteProtonConfig(name: string): Promise<{ ok: boolean; name: string; notes?: string[] }> {
  return apiFetch(`/api/proton/configs/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

// ---------- Hosts (subscription hosts per inbound) ----------

export interface PanelHost {
  id: number;
  remark: string;
  address?: string[];
  inbound_tag?: string;
  port?: number;
  sni?: string[];
  priority?: number;
  is_disabled?: boolean;
  [k: string]: unknown;
}

export async function listHosts(panel_id: number | string, inbound_tag?: string): Promise<PanelHost[]> {
  const q = inbound_tag
    ? `?panel_id=${panel_id}&inbound_tag=${encodeURIComponent(inbound_tag)}`
    : `?panel_id=${panel_id}`;
  const data = await apiFetch<{ hosts: PanelHost[] }>(`/api/hosts${q}`);
  return data.hosts ?? [];
}

export async function createHost(payload: Record<string, unknown>): Promise<unknown> {
  return apiFetch('/api/hosts', { method: 'POST', body: payload });
}

export async function updateHost(id: number | string, payload: Record<string, unknown>): Promise<unknown> {
  return apiFetch(`/api/hosts/${id}`, { method: 'PUT', body: payload });
}

export async function deleteHost(id: number | string, panel_id: number | string): Promise<unknown> {
  return apiFetch(`/api/hosts/${id}?panel_id=${panel_id}`, { method: 'DELETE' });
}

// Fetch a domain's cert/key from a saved server over SSH (certbot).
// issue=true runs certbot when no valid cert exists (needs port 80 + DNS).
export async function fetchCloudflareCert(payload: {
  server_id: number | string;
  domain: string;
  issue?: boolean;
  email?: string;
}): Promise<{ cert_pem: string; key_pem: string; notes?: string[] }> {
  return apiFetch('/api/inbounds/cloudflare/cert', { method: 'POST', body: payload });
}

// Same, but the server is auto-resolved from the node's address
// (the cert must live where the domain points).
export async function fetchCloudflareCertAuto(payload: {
  panel_id: number | string;
  node_id: number | string;
  domain: string;
  issue?: boolean;
  email?: string;
}): Promise<{ cert_pem: string; key_pem: string; server_name?: string; notes?: string[] }> {
  return apiFetch('/api/inbounds/cloudflare/cert-auto', { method: 'POST', body: payload });
}

// Raw inbound dicts nest transport details; flatten for the table.
export function summarizeInbound(r: InboundInfo): { network: string; security: string; sni: string } {
  const raw = (r.raw ?? r) as Record<string, unknown>;
  const ss = (raw.streamSettings ?? {}) as Record<string, unknown>;
  const network = String((raw.network ?? ss.network) ?? '');
  const security = String((raw.security ?? ss.security) ?? '');
  let sni = String((raw.sni ?? '') ?? '');
  if (!sni) {
    const tls = ss.tlsSettings as Record<string, unknown> | undefined;
    const reality = ss.realitySettings as Record<string, unknown> | undefined;
    const xhttp = ss.xhttpSettings as Record<string, unknown> | undefined;
    const ws = ss.wsSettings as Record<string, unknown> | undefined;
    const names = reality?.serverNames as string[] | undefined;
    sni = String(tls?.serverName ?? names?.[0] ?? xhttp?.host ?? (ws?.headers as Record<string, unknown> | undefined)?.Host ?? '');
  }
  return { network, security, sni };
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export function pretty(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
