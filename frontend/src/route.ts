// Hash routing (#/tab/sub) so refresh + back/forward restore the section.
// Hashes work with the static nginx serve (no server rewrites needed).
import { useEffect, useState } from 'react';

export const DEFAULT_SUB: Record<string, string> = {
  home: '',
  panel: 'nodes',
  tunnel: 'tunnel',
  cloudflare: 'zones',
  settings: 'panels',
};

export function parseHash(): [string, string] {
  try {
    const h = window.location.hash.replace(/^#\/?/, '');
    const [tab = '', sub = ''] = h.split('/');
    return [tab, sub];
  } catch {
    return ['', ''];
  }
}

export function writeHash(tab: string, sub: string) {
  const want = `#/${tab}/${sub}`;
  if (window.location.hash !== want) window.location.hash = want;
}

// Sub-tab state synced with the hash. `tab` is the owning top-level tab.
export function useSub<T extends string>(tab: string, def: T, valid: T[]): [T, (s: T) => void] {
  const [sub, setSub] = useState<T>(() => {
    const [t, s] = parseHash();
    return t === tab && (valid as string[]).includes(s) ? (s as T) : def;
  });

  useEffect(() => {
    const onHash = () => {
      const [t, s] = parseHash();
      if (t === tab && (valid as string[]).includes(s)) setSub(s as T);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [tab]);

  const go = (s: T) => {
    setSub(s);
    writeHash(tab, s);
  };
  return [sub, go];
}
