import CertsPage from '../pages/CertsPage';
import CloudflarePage from '../pages/CloudflarePage';
import { useSub } from '../route';

export default function CloudflareSection() {
  const [sub, setSub] = useSub('cloudflare', 'zones' as const, ['zones', 'certs'] as const);
  return (
    <div>
      <div className="subtabs">
        <button className={`subtab-btn ${sub === 'zones' ? 'active' : ''}`} onClick={() => setSub('zones')}>
          Zones & records
        </button>
        <button className={`subtab-btn ${sub === 'certs' ? 'active' : ''}`} onClick={() => setSub('certs')}>
          Certificates
        </button>
      </div>
      {sub === 'zones' ? <CloudflarePage /> : <CertsPage />}
    </div>
  );
}
