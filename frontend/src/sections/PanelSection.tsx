import InboundsPage from '../pages/InboundsPage';
import NodesPage from '../pages/NodesPage';
import OutboundsPage from '../pages/OutboundsPage';
import { useSub } from '../route';

const SUBS = ['nodes', 'inbounds', 'outbounds'] as const;
type Sub = (typeof SUBS)[number];

export default function PanelSection() {
  const [sub, setSub] = useSub<Sub>('panel', 'nodes', [...SUBS]);
  return (
    <div>
      <div className="subtabs">
        <button className={`subtab-btn ${sub === 'nodes' ? 'active' : ''}`} onClick={() => setSub('nodes')}>
          Nodes
        </button>
        <button className={`subtab-btn ${sub === 'inbounds' ? 'active' : ''}`} onClick={() => setSub('inbounds')}>
          Inbounds
        </button>
        <button className={`subtab-btn ${sub === 'outbounds' ? 'active' : ''}`} onClick={() => setSub('outbounds')}>
          WireGuard outbounds
        </button>
      </div>
      {sub === 'nodes' ? <NodesPage /> : sub === 'inbounds' ? <InboundsPage /> : <OutboundsPage />}
    </div>
  );
}
