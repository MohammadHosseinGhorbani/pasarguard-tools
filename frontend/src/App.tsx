import { Component, ReactNode, useEffect, useState } from 'react';
import { User, changePassword, errMsg, fetchMe, getToken, logout } from './api';
import Login from './pages/Login';
import CloudflareSection from './sections/CloudflareSection';
import Footer from './components/Footer';
import HomeSection from './sections/HomeSection';
import Modal from './components/Modal';
import PanelSection from './sections/PanelSection';
import ProtonSection from './sections/ProtonSection';
import { DEFAULT_SUB, parseHash } from './route';
import SettingsSection from './sections/SettingsSection';
import TunnelSection from './sections/TunnelSection';

type Tab = 'home' | 'panel' | 'proton' | 'tunnel' | 'cloudflare' | 'settings';

const TABS: { id: Tab; label: string }[] = [
  { id: 'home', label: 'Home' },
  { id: 'panel', label: 'Panel' },
  { id: 'proton', label: 'Proton VPN' },
  { id: 'tunnel', label: 'Tunnel' },
  { id: 'cloudflare', label: 'Cloudflare' },
  { id: 'settings', label: 'Settings' },
];

// A crashing tab must never blank the whole app: show the error instead.
class TabBoundary extends Component<{ tab: Tab; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidUpdate(prev: { tab: Tab }) {
    if (prev.tab !== this.props.tab && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      return (
        <div className="alert error">
          This tab crashed while rendering: {String(this.state.error.message || this.state.error)}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn small" onClick={() => this.setState({ error: null })}>
              Retry
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const THEME_KEY = 'pg_theme';
const NAV_KEY = 'pg_nav';

function initialNav(): boolean {
  try {
    if (window.matchMedia?.('(max-width: 760px)').matches) return false;
    return localStorage.getItem(NAV_KEY) !== 'closed';
  } catch {
    return true;
  }
}

function initialTheme(): 'dark' | 'light' {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    if (window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light';
  } catch {
    /* ignore */
  }
  return 'dark';
}

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => !!getToken());
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(false);
  const [tab, setTab] = useState<Tab>(() => {
    const [t] = parseHash();
    return (TABS as { id: string }[]).some((x) => x.id === t) ? (t as Tab) : 'home';
  });
  const [navOpen, setNavOpen] = useState<boolean>(initialNav);
  const [theme, setTheme] = useState<'dark' | 'light'>(initialTheme);

  const [showPw, setShowPw] = useState(false);
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [pwMsg, setPwMsg] = useState('');

  async function checkMe() {
    if (!getToken()) {
      setAuthed(false);
      return;
    }
    setChecking(true);
    try {
      const me = await fetchMe();
      setUser(me);
      setAuthed(true);
    } catch {
      setAuthed(false);
      setUser(null);
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    checkMe();
  }, []);

  // Hash <-> tab sync (back/forward + refresh restore).
  useEffect(() => {
    if (!window.location.hash) {
      history.replaceState(null, '', `#/home/${DEFAULT_SUB.home}`);
      return;
    }
    const [t] = parseHash();
    if ((TABS as { id: string }[]).some((x) => x.id === t)) setTab(t as Tab);
    const onHash = () => {
      const [tt] = parseHash();
      if ((TABS as { id: string }[]).some((x) => x.id === tt)) setTab(tt as Tab);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  function goTab(id: Tab) {
    if (id === tab) return; // re-clicking the active tab keeps its sub-tab
    setTab(id);
    const hash = `#/${id}/${DEFAULT_SUB[id]}`;
    if (window.location.hash !== hash) window.location.hash = hash;
  }

  function toggleNav() {
    setNavOpen((v) => {
      const next = !v;
      try {
        localStorage.setItem(NAV_KEY, next ? 'open' : 'closed');
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  function closeNavMobile() {
    try {
      if (window.matchMedia?.('(max-width: 760px)').matches) setNavOpen(false);
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeNavMobile();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  async function doChangePw() {
    setPwMsg('');
    try {
      await changePassword(oldPw, newPw);
      setPwMsg('Password changed.');
      setOldPw('');
      setNewPw('');
    } catch (e) {
      setPwMsg(`Failed: ${errMsg(e)}`);
    }
  }

  if (!authed) {
    return (
      <Login
        onDone={() => {
          setAuthed(true);
          checkMe();
        }}
      />
    );
  }

  return (
    <div className={`app ${navOpen ? 'nav-open' : ''}`}>
      {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} />}
      <nav className="sidebar" aria-label="Sections">
        <div className="brand">Pasarguard Tools</div>
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`nav-btn ${tab === t.id ? 'active' : ''}`}
            onClick={() => {
              goTab(t.id);
              closeNavMobile();
            }}
          >
            {t.label}
          </button>
        ))}
        <div className="nav-footer">
          {user?.username && <span className="user">{String(user.username)}</span>}
          <button className="btn small" onClick={() => { setShowPw(true); setPwMsg(''); closeNavMobile(); }}>
            Change password
          </button>
          <button
            className="btn small"
            onClick={() => {
              logout();
              setAuthed(false);
              setUser(null);
            }}
          >
            Logout
          </button>
        </div>
      </nav>
      <div className="main">
        <header className="header">
          <span className="row" style={{ flexWrap: 'nowrap' }}>
            <button
              className="icon-btn"
              title={navOpen ? 'Hide sidebar' : 'Show sidebar'}
              onClick={toggleNav}
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <span className="crumb">
              <span>Pasarguard Tools / </span>
              {checking ? '…' : TABS.find((t) => t.id === tab)?.label}
            </span>
          </span>
          <span className="row" style={{ flexWrap: 'nowrap' }}>
            <button
              className="btn small"
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
              style={{ padding: '5px 8px', lineHeight: 0 }}
            >
              {theme === 'dark' ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
                </svg>
              )}
            </button>
          </span>
        </header>
        <div className="content">
          <TabBoundary tab={tab}>
            <div hidden={tab !== 'home'}>
              <HomeSection />
            </div>
            <div hidden={tab !== 'panel'}>
              <PanelSection />
            </div>
            <div hidden={tab !== 'proton'}>
              <ProtonSection />
            </div>
            <div hidden={tab !== 'tunnel'}>
              <TunnelSection />
            </div>
            <div hidden={tab !== 'cloudflare'}>
              <CloudflareSection />
            </div>
            <div hidden={tab !== 'settings'}>
              <SettingsSection />
            </div>
          </TabBoundary>
          <Footer />
        </div>
      </div>

      {showPw && (
        <Modal onClose={() => setShowPw(false)} width={420}>
            <h3 style={{ marginTop: 0 }}>Change password</h3>
            <div style={{ display: 'grid', gap: 10 }}>
              <label className="lbl">
                Old password
                <input
                  className="input"
                  type="password"
                  value={oldPw}
                  onChange={(e) => setOldPw(e.target.value)}
                />
              </label>
              <label className="lbl">
                New password
                <input
                  className="input"
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                />
              </label>
              {pwMsg && <div className="muted">{pwMsg}</div>}
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button className="btn" onClick={() => setShowPw(false)}>
                  Close
                </button>
                <button className="btn primary" disabled={!oldPw || !newPw} onClick={doChangePw}>
                  Change
                </button>
              </div>
            </div>
        </Modal>
      )}
    </div>
  );
}
