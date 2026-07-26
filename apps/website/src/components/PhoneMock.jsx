import './phone.css';

const BARS = [42, 58, 35, 74, 61, 88, 96];
const DAYS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

const RECEIVABLES = [
  { name: 'Anand Enterprises', amount: '4,82,000', days: '62 days', tone: 'bad' },
  { name: 'Kirti Steel Co.', amount: '2,15,400', days: '31 days', tone: 'warn' },
  { name: 'Vasant Traders', amount: '1,08,750', days: '9 days', tone: 'ok' },
];

/**
 * The hero device. Not a screenshot -- a live rebuild of the app's dashboard,
 * so it animates, stays crisp at any size, and shows the one thing that makes
 * this product different from a remote desktop: every figure carries its "as of".
 */
export default function PhoneMock() {
  return (
    <div className="phone-stage">
      <div className="phone-halo" />

      <div className="phone">
        <div className="phone-notch" />
        <div className="phone-screen">
          <div className="ph-status">
            <span className="num">9:41</span>
            <span className="ph-status-icons">
              <i />
              <i />
              <i />
            </span>
          </div>

          <div className="ph-head">
            <div>
              <p className="ph-hello">Good morning, Rakesh</p>
              <p className="ph-company">Sharma Traders &amp; Co.</p>
            </div>
            <div className="ph-avatar">RS</div>
          </div>

          <div className="ph-fresh">
            <span className="ph-live" />
            Live from Tally · read 2 min ago
          </div>

          <div className="ph-tiles">
            <div className="ph-tile ph-tile-hero">
              <p className="ph-label">Today&apos;s sales</p>
              <p className="ph-value num">₹4,82,650</p>
              <p className="ph-delta up">▲ 18.4% vs yesterday</p>
              <svg className="ph-spark" viewBox="0 0 200 54" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5AF0C8" stopOpacity="0.42" />
                    <stop offset="100%" stopColor="#5AF0C8" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <path
                  className="ph-spark-fill"
                  d="M0,42 L28,36 L56,44 L84,26 L112,31 L140,16 L168,20 L200,6 L200,54 L0,54 Z"
                  fill="url(#sparkFill)"
                />
                <path
                  className="ph-spark-line"
                  d="M0,42 L28,36 L56,44 L84,26 L112,31 L140,16 L168,20 L200,6"
                  fill="none"
                  stroke="#5AF0C8"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>

            <div className="ph-tile">
              <p className="ph-label">Cash + bank</p>
              <p className="ph-value sm num">₹12.84 L</p>
              <p className="ph-delta up">▲ 3.1%</p>
            </div>

            <div className="ph-tile">
              <p className="ph-label">Receivables</p>
              <p className="ph-value sm num warn">₹18.42 L</p>
              <p className="ph-delta down">▼ 2 overdue</p>
            </div>
          </div>

          <div className="ph-card">
            <div className="ph-card-head">
              <p>Sales this week</p>
              <span className="num">₹21.6 L</span>
            </div>
            <div className="ph-bars">
              {BARS.map((height, i) => (
                <div className="ph-bar-col" key={DAYS[i] + i}>
                  <div
                    className="ph-bar"
                    style={{ '--h': `${height}%`, '--d': `${i * 90 + 350}ms` }}
                  />
                  <span>{DAYS[i]}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="ph-card">
            <div className="ph-card-head">
              <p>Who owes me money</p>
              <span className="ph-link">All</span>
            </div>
            {RECEIVABLES.map((row) => (
              <div className="ph-row" key={row.name}>
                <span className={`ph-pip ${row.tone}`} />
                <div className="ph-row-main">
                  <p>{row.name}</p>
                  <small>{row.days}</small>
                </div>
                <span className="num">₹{row.amount}</span>
              </div>
            ))}
          </div>

          <div className="ph-tabbar">
            <span className="on">
              <b />
              Home
            </span>
            <span>
              <b />
              Reports
            </span>
            <span>
              <b />
              Stock
            </span>
            <span>
              <b />
              More
            </span>
          </div>
        </div>
      </div>

      {/* Floating proof-points that orbit the device. */}
      <div className="chip chip-a">
        <span className="chip-dot ok" />
        <div>
          <strong>Connector online</strong>
          <small>DESKTOP-SHOP1 · 12 ms</small>
        </div>
      </div>

      <div className="chip chip-b">
        <span className="chip-ico">🔒</span>
        <div>
          <strong>Read-only</strong>
          <small>No write path exists</small>
        </div>
      </div>

      <div className="chip chip-c">
        <span className="chip-ico">⚡</span>
        <div>
          <strong>Dashboard in 0.4s</strong>
          <small>Served from snapshot</small>
        </div>
      </div>
    </div>
  );
}
