import PhoneMock from './PhoneMock.jsx';
import { useCountUp } from '../hooks.js';
import './hero.css';

const ROTATING = [
  'How much did I sell today?',
  'Who owes me money?',
  'What is my cash position?',
  'Which products are running out?',
  'What happened since yesterday?',
];

function Stat({ to, suffix = '', prefix = '', label, decimals = 0 }) {
  const [ref, value] = useCountUp(to, { decimals });
  return (
    <div className="hero-stat" ref={ref}>
      <p className="hero-stat-value num">
        {prefix}
        {decimals ? value.toFixed(decimals) : value}
        {suffix}
      </p>
      <p className="hero-stat-label">{label}</p>
    </div>
  );
}

export default function Hero() {
  return (
    <section className="hero" id="top">
      <div className="grid-lines" />
      <div className="orb hero-orb-1" />
      <div className="orb hero-orb-2" />
      <div className="orb hero-orb-3" />

      <div className="shell hero-grid">
        <div className="hero-copy">
          <span className="eyebrow reveal">
            <span className="dot" />
            Read-only by design · Tally never touches the internet
          </span>

          <h1 className="reveal" style={{ '--delay': '80ms' }}>
            Your whole business,
            <br />
            <span className="grad-text">on the phone in your pocket.</span>
          </h1>

          <p className="hero-sub reveal" style={{ '--delay': '160ms' }}>
            TallyFlow turns TallyPrime into a modern mobile dashboard. Sales, cash,
            receivables and stock — live from your own Tally, in the time it takes to
            unlock your phone. No remote desktop. No data entry. Nothing to break.
          </p>

          <div className="hero-rotator reveal" style={{ '--delay': '220ms' }}>
            <span className="hero-rotator-label">Answers in 10 seconds:</span>
            <span className="hero-rotator-track">
              {ROTATING.map((line, i) => (
                <em key={line} style={{ '--i': i, '--n': ROTATING.length }}>
                  {line}
                </em>
              ))}
            </span>
          </div>

          <div className="hero-cta reveal" style={{ '--delay': '300ms' }}>
            <a className="btn btn-primary btn-lg" href="#download">
              Download for Windows &amp; mobile
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 4v12m0 0 5-5m-5 5-5-5M4 20h16"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </a>
            <a className="btn btn-ghost btn-lg" href="#how">
              See how it works
            </a>
          </div>

          <div className="hero-stats reveal" style={{ '--delay': '380ms' }}>
            <Stat to={0.4} decimals={1} suffix="s" label="Dashboard open time" />
            <Stat to={17} label="Reports on day one" />
            <Stat to={0} label="Ports opened on your PC" />
            <Stat to={100} suffix="%" label="Read-only, enforced" />
          </div>
        </div>

        <div className="hero-device reveal" style={{ '--delay': '200ms' }}>
          <PhoneMock />
        </div>
      </div>
    </section>
  );
}
