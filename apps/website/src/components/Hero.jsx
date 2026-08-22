import AppPreview from './AppPreview.jsx';
import { Link } from '../router.jsx';
import { useManifest, useStats, mb } from '../hooks.js';
import './hero.css';

/*
 * The four counts, in the order they answer "is anyone actually using this?".
 * Every one comes from /v1/public/stats and is whatever the database says,
 * including zero. Nothing here is rounded up, and nothing is a target.
 */
const STAT_ROW = [
  ['businesses', 'Businesses'],
  ['tally_pcs', 'Tally PCs paired'],
  ['companies', 'Companies read'],
  ['connected_now', 'Online right now'],
];

function Stats() {
  const stats = useStats();

  // No row at all until the real numbers arrive. Rendering zeros while the
  // request is in flight would put the worst possible claim on the page for
  // anyone whose connection is slow, and it would be a claim we had not checked.
  if (!stats) return null;

  return (
    <dl className="hero-stats">
      {STAT_ROW.map(([key, label]) => (
        <div key={key}>
          <dd>{stats[key]}</dd>
          <dt>{label}</dt>
        </div>
      ))}
    </dl>
  );
}

export default function Hero() {
  const { connector } = useManifest();

  return (
    <section className="hero" id="top">
      <div className="shell hero-grid">
        <div>
          <span className="chip chip-accent">Early access</span>

          <h1 style={{ marginTop: 18 }}>Your TallyPrime numbers, on your phone.</h1>

          <p className="hero-sub">
            TallyFlow shows you sales, cash, receivables and stock from your own
            TallyPrime — without opening Tally, and without exposing it to the
            internet. It can only read. Nothing it does can change a voucher.
          </p>

          <div className="hero-cta">
            <a className="btn btn-primary" href={connector.url} download>
              Download for Windows
            </a>
            <Link className="btn btn-ghost" to="/docs">
              Read the setup guide
            </Link>
          </div>

          <Stats />

          <div className="hero-strip">
            <span>
              <b>Windows connector</b> · v{connector.version} · {mb(connector.size_bytes)}
            </span>
            <span>Needs TallyPrime on the same PC</span>
          </div>
        </div>

        <div className="hero-art">
          <AppPreview />
        </div>
      </div>
    </section>
  );
}
