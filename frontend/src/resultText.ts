import { pretty } from './api';

// Turn API results into short human-readable summaries (emoji included).
// Long command outputs are tailed; the full JSON stays one click away
// via ResultBlock. Unknown shapes fall back to pretty JSON.

type Dict = Record<string, any>;

function tail(text: unknown, n = 40): string {
  const lines = String(text ?? '').split('\n');
  if (lines.length <= n) return String(text ?? '');
  return `… (${lines.length - n} earlier lines hidden)\n` + lines.slice(-n).join('\n');
}

function firstLine(text: unknown): string {
  return String(text ?? '').split('\n').map((l) => l.trim()).find((l) => l) ?? '';
}

function oneLine(text: unknown, max = 120): string {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max) + '…' : s;
}

export function humanize(kind: string, data: unknown): string {
  const d = (data ?? {}) as Dict;
  switch (kind) {
    case 'server-test': {
      if (!d.ok) return `❌ SSH failed\n${oneLine(d.error ?? d.outputs ?? 'unknown error')}`;
      const o = (d.outputs ?? {}) as Dict;
      const rows = [
        `✅ SSH OK · ${d.latency_ms ?? '?'}ms`,
        o.basic ? `🖥️ ${oneLine(firstLine(o.basic))}` : '',
        o.pg_node ? `📦 pg-node: ${oneLine(firstLine(o.pg_node))}` : '',
        o.panel ? `🛡️ panel: ${oneLine(firstLine(o.panel))}` : '',
      ].filter(Boolean);
      return rows.join('\n');
    }
    case 'exec': {
      const rc = Number(d.rc ?? -1);
      const head = rc === 0 ? '✅ exit 0' : `❌ exit ${d.rc ?? '?'}`;
      const body = [d.out ? String(d.out) : '', d.err ? `--- stderr ---\n${d.err}` : '']
        .filter(Boolean)
        .join('\n');
      return body ? `${head}\n${body}` : `${head} (no output)`;
    }
    case 'node-status':
      return `${d.up ? '🟢 pg-node is up' : '🔴 pg-node is down'}\n${tail(d.output ?? '', 15)}`;
    case 'node-install': {
      if (d.already_installed) return `✅ pg-node already installed and running\n${tail(d.output ?? '', 10)}`;
      const ok = d.ok !== false && d.up;
      const ports = d.ports ? ` (ports ${JSON.stringify(d.ports)})` : '';
      return `${ok ? '✅ pg-node installed and running' : '❌ install failed'}${ports}\n${tail(d.output ?? d.status_output ?? '', 25)}`;
    }
    case 'node-credentials':
      return `🔑 service port ${d.service_port ?? '?'} · api port ${d.api_port ?? '?'} · ${d.protocol ?? '?'}`;
    case 'node-register': {
      const n = (d.node ?? {}) as Dict;
      if (d.existing) return `🔗 node already in panel: ${n.name ?? '?'} (id ${n.id ?? '?'}, core ${d.core_id ?? '?'})`;
      const c = (d.core ?? {}) as Dict;
      const reused = d.reused_core ? ' (reused existing core)' : '';
      return `✅ registered node ${n.name ?? '?'} (id ${n.id ?? '?'}) with core ${c.name ?? ''} (id ${c.id ?? d.core_id ?? '?'})${reused}`.replace('  ', ' ');
    }
    case 'node-action':
    case 'panel-action': {
      const rc = Number(d.rc ?? -1);
      const head = rc === 0 ? `✅ ${d.action ?? 'done'}` : `❌ ${d.action ?? 'failed'} (rc=${d.rc ?? '?'})`;
      return d.output ? `${head}\n${tail(d.output, 40)}` : head;
    }
    case 'panel-install': {
      const rc = Number(d.rc ?? -1);
      const head = rc === 0 ? '✅ panel installed' : `❌ install failed (rc=${d.rc ?? '?'})`;
      return d.output ? `${head}\n${tail(d.output, 30)}` : head;
    }
    case 'inbound': {
      const lines = [`✅ inbound “${d.tag ?? '?'}” (${d.network ?? '?'})`];
      if (d.server) lines.push(`🌐 ${d.server}`);
      if (d.domain && !d.server) lines.push(`🌐 ${d.domain}:${d.port ?? ''}`);
      if (d.sni) lines.push(`🔒 SNI ${d.sni}`);
      if (d.public_key) lines.push(`🔑 pubkey ${d.public_key}`);
      if (d.short_ids) lines.push(`🆔 shortIds ${Array.isArray(d.short_ids) ? d.short_ids.join(', ') : d.short_ids}`);
      if (d.encryption) lines.push(`🔐 encryption ${oneLine(d.encryption)}`);
      const n = (d.node ?? {}) as Dict;
      if (n.id !== undefined) lines.push(`🖥️ node ${n.name ?? ''} (id ${n.id}) · core ${d.core_id ?? '?'}`);
      for (const note of d.notes ?? []) lines.push(`• ${note}`);
      if (d.hosts) lines.push(`🏠 hosts: ${JSON.stringify(d.hosts)}`);
      return lines.join('\n');
    }
    case 'host': {
      const h = (d.host ?? {}) as Dict;
      return `✅ host “${h.remark ?? '?'}” saved (id ${h.id ?? '?'})${d.model ? ` · ${d.model}` : ''}`;
    }
    case 'record': {
      const r = (d.result ?? d.record ?? {}) as Dict;
      if (d.deleted) return `✅ record deleted`;
      if (r.name) return `✅ A ${r.name} → ${r.content ?? '?'} (${r.proxied ? 'proxied 🟠' : 'DNS-only ⚪'})`;
      return `✅ done`;
    }
    case 'cert': {
      if (d.cert_pem) {
        const n = String(d.cert_pem).split('\n').length;
        return [`✅ certificate loaded (${n} lines)`, ...((d.notes ?? []) as string[])].join('\n');
      }
      return [`✅ certificate issued for ${d.domain ?? '?'}`, ...((d.notes ?? []) as string[])].join('\n');
    }
    case 'notes': {
      const notes = (d.notes ?? []) as string[];
      return notes.length > 0 ? notes.map((x) => `• ${x}`).join('\n') : '✅ done';
    }
    case 'bp-create': {
      const r = (d.iran ?? d.pair ?? d) as Dict;
      const kh = (d.kharej ?? {}) as Dict;
      const side = (x: Dict) =>
        x && (x.status !== undefined || x.active !== undefined)
          ? `${x.status ?? ''} ${x.active ? '(running)' : '(not running)'}`
          : '';
      return [
        `✅ ${d.direction ?? 'tunnel'} tunnel created on both panels`,
        r.name ? `• Iran: “${r.name}” ${side(r)}` : '',
        kh.name || kh.status ? `• Kharej: “${kh.name ?? '?'}” ${side(kh)}` : '',
      ]
        .filter(Boolean)
        .join('\n');
    }
    case 'bp-delete':
      return `✅ ${d.note ?? 'tunnel deleted'}`;
    case 'bp-action': {
      const peer = d.peer ? `\n🔗 peer: ${oneLine(JSON.stringify(d.peer))}` : '';
      const perr = d.peerError ? `\n⚠️ ${oneLine(d.peerError)}` : '';
      return `✅ ${d.action ?? 'action'} ${d.name ?? ''}${peer}${perr}`;
    }
    case 'bp-pair':
      return `✅ Kharej paired as managed server “${d.node ?? '?'}”`;
    default:
      return pretty(data);
  }
}
