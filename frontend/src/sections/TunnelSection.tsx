import ExecConsole from '../components/ExecConsole';
import TunnelTab from '../components/TunnelTab';
import { useSub } from '../route';

export default function TunnelSection() {
  const [sub, setSub] = useSub('tunnel', 'tunnel' as const, ['exec', 'tunnel'] as const);
  return (
    <div>
      <div className="subtabs">
        <button className={`subtab-btn ${sub === 'exec' ? 'active' : ''}`} onClick={() => setSub('exec')}>
          Exec console
        </button>
        <button className={`subtab-btn ${sub === 'tunnel' ? 'active' : ''}`} onClick={() => setSub('tunnel')}>
          Tunnel (BackPack)
        </button>
      </div>
      {sub === 'exec' ? <ExecConsole /> : <TunnelTab />}
    </div>
  );
}
