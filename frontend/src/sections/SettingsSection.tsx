import BpPanelsManager from '../components/BpPanelsManager';
import CfAccountsManager from '../components/CfAccountsManager';
import PanelsManager from '../components/PanelsManager';
import ProtonSettings from '../components/ProtonSettings';
import ServerManager from '../components/ServerManager';
import { useSub } from '../route';

type Sub = 'panels' | 'iran' | 'kharej' | 'cloudflare' | 'backpack' | 'proton';

const SUBS: Sub[] = ['panels', 'iran', 'kharej', 'cloudflare', 'backpack', 'proton'];

export default function SettingsSection() {
  const [sub, setSub] = useSub<Sub>('settings', 'panels', SUBS);
  return (
    <div>
      <div className="subtabs">
        <button className={`subtab-btn ${sub === 'panels' ? 'active' : ''}`} onClick={() => setSub('panels')}>
          Saved panels
        </button>
        <button className={`subtab-btn ${sub === 'iran' ? 'active' : ''}`} onClick={() => setSub('iran')}>
          Iran servers
        </button>
        <button className={`subtab-btn ${sub === 'kharej' ? 'active' : ''}`} onClick={() => setSub('kharej')}>
          Kharej servers
        </button>
        <button className={`subtab-btn ${sub === 'cloudflare' ? 'active' : ''}`} onClick={() => setSub('cloudflare')}>
          Cloudflare
        </button>
        <button className={`subtab-btn ${sub === 'backpack' ? 'active' : ''}`} onClick={() => setSub('backpack')}>
          BackPack API
        </button>
        <button className={`subtab-btn ${sub === 'proton' ? 'active' : ''}`} onClick={() => setSub('proton')}>
          Proton VPN
        </button>
      </div>
      {sub === 'panels' && <PanelsManager />}
      {sub === 'iran' && (
        <ServerManager
          role="iran"
          title="Iran servers"
          subtitle="Tunnel entry servers. SSH test included; run commands from Tunnel > Exec console."
        />
      )}
      {sub === 'kharej' && (
        <ServerManager
          role="kharej"
          title="Kharej servers"
          subtitle="Abroad servers: pg-node hosts, tunnel far ends, inbound targets. SSH test included; run commands from Tunnel > Exec console."
        />
      )}
      {sub === 'cloudflare' && <CfAccountsManager />}
      {sub === 'backpack' && <BpPanelsManager />}
      {sub === 'proton' && <ProtonSettings />}
    </div>
  );
}
