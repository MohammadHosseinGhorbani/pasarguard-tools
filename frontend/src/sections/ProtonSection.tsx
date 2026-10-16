import ProtonPage from '../pages/ProtonPage';
import { useSub } from '../route';

const SUBS = ['configs'] as const;
type Sub = (typeof SUBS)[number];

// New top-level "Proton VPN" section — a single tab for the WireGuard
// config manager (create / delete / rotate). Credentials live in Settings.
export default function ProtonSection() {
  const [sub, setSub] = useSub<Sub>('proton', 'configs', [...SUBS]);
  return (
    <div>
      <div className="subtabs">
        <button
          className={`subtab-btn ${sub === 'configs' ? 'active' : ''}`}
          onClick={() => setSub('configs')}
        >
          WireGuard configs
        </button>
      </div>
      <ProtonPage />
    </div>
  );
}
