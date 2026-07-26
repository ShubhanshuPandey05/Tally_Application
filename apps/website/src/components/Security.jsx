import './security.css';

const PILLARS = [
  {
    k: 'No write path',
    v: 'The connector can only ask Tally to export. Import requests cannot be constructed, and the build fails if one ever could be.',
  },
  {
    k: 'Nothing exposed',
    v: 'No inbound port, no static IP, no port forwarding. The connector dials out and holds the line open.',
  },
  {
    k: 'Argon2id passwords',
    v: 'Refresh tokens are single-use. Replaying a spent one revokes the whole family, on the assumption that replay means it leaked.',
  },
  {
    k: 'Encrypted pairing secrets',
    v: 'Stored encrypted with a key held outside the database, so a database dump on its own yields no usable credential.',
  },
  {
    k: 'Tenant isolation that fails closed',
    v: 'Another organisation’s company returns 404, never 403 — the API cannot be used to probe for valid identifiers.',
  },
  {
    k: 'Reads are audited',
    v: 'In a read-only accounting product the sensitive act is the read. Every one is logged with who, what and when.',
  },
];

const GUARDS = [
  ['Snapshot reads', 'Users do not multiply the load on your PC'],
  ['Request coalescing', 'Identical concurrent reads share one round trip'],
  ['Job cap per connector', 'Never more than two exports in flight'],
  ['Refresh throttle', 'Pull-to-refresh has a floor, by design'],
  ['Shrinking deadlines', 'A queued job never gets a fresh full timeout'],
  ['Batched warming', 'A restart cannot stampede your Tally'],
];

export default function Security() {
  return (
    <section id="security" className="sec">
      <div className="orb sec-orb-1" />
      <div className="orb sec-orb-2" />
      <div className="shell sec-grid">
        <div>
          <div className="section-head reveal">
            <span className="eyebrow">
              <span className="dot" />
              Security &amp; trust
            </span>
            <h2>
              Accounting data deserves
              <br />
              <span className="grad-text">more than a login screen.</span>
            </h2>
            <p>
              Six decisions we made before writing the first dashboard, because
              retro-fitting any of them means asking customers to re-pair every PC.
            </p>
          </div>

          <div className="sec-list">
            {PILLARS.map((pillar, i) => (
              <div className="sec-item reveal" key={pillar.k} style={{ '--delay': `${i * 60}ms` }}>
                <span className="sec-tick">
                  <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
                    <path
                      d="M5 13l4 4L19 7"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <div>
                  <h4>{pillar.k}</h4>
                  <p>{pillar.v}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <aside className="card card-glow sec-panel reveal" style={{ '--delay': '140ms' }}>
          <h3>Guarding the PC your shop runs on</h3>
          <p className="sec-panel-lead">
            TallyPrime answers one request at a time, and it lives on the machine your
            staff bill from. Six mechanisms exist purely so that people looking at a
            dashboard never slow down the counter.
          </p>
          <ul>
            {GUARDS.map(([name, effect]) => (
              <li key={name}>
                <b>{name}</b>
                <span>{effect}</span>
              </li>
            ))}
          </ul>
          <div className="sec-meter">
            <div className="sec-meter-head">
              <span>Load on your Tally PC</span>
              <span className="num">10 staff = 1 staff</span>
            </div>
            <div className="sec-meter-bar">
              <i style={{ '--w': '14%' }} />
            </div>
            <small>Scales with the number of companies, not the number of people.</small>
          </div>
        </aside>
      </div>
    </section>
  );
}
