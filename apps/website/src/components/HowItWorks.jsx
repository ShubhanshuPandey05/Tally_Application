import { SETUP_STEPS } from '../data/downloads.js';
import './how.css';

const NODES = [
  {
    id: 'app',
    icon: '📱',
    title: 'TallyFlow app',
    sub: 'Your phone',
    note: 'Never talks to Tally directly.',
  },
  {
    id: 'cloud',
    icon: '☁️',
    title: 'TallyFlow cloud',
    sub: 'Auth · snapshots · reports',
    note: 'Holds no plaintext credentials.',
  },
  {
    id: 'connector',
    icon: '🖥️',
    title: 'Connector',
    sub: 'Your Windows PC',
    note: 'Dials out. No inbound port.',
  },
  {
    id: 'tally',
    icon: '📒',
    title: 'TallyPrime',
    sub: 'localhost:9000',
    note: 'Stays exactly where it is.',
  },
];

export default function HowItWorks() {
  return (
    <section id="how">
      <div className="orb how-orb" />
      <div className="shell">
        <div className="section-head center reveal">
          <span className="eyebrow">
            <span className="dot" />
            How it works
          </span>
          <h2>
            Your Tally never leaves <span className="grad-text">your building.</span>
          </h2>
          <p>
            The connector on your PC opens an outbound connection to us — the same
            direction your browser reaches a website. Nothing on the internet can dial
            into your machine, because there is nothing listening.
          </p>
        </div>

        <div className="flow reveal">
          <div className="flow-line">
            <span className="flow-packet" />
            <span className="flow-packet delay" />
          </div>

          {NODES.map((node, i) => (
            <div className="flow-node" key={node.id} style={{ '--delay': `${i * 110}ms` }}>
              <div className="flow-badge">{node.icon}</div>
              <h4>{node.title}</h4>
              <p className="flow-sub">{node.sub}</p>
              <p className="flow-note">{node.note}</p>
            </div>
          ))}
        </div>

        <div className="how-legend reveal">
          <span>
            <i className="l-out" />
            Outbound only
          </span>
          <span>
            <i className="l-tls" />
            TLS end to end
          </span>
          <span>
            <i className="l-ro" />
            Export requests only
          </span>
        </div>

        <div className="steps">
          {SETUP_STEPS.map((step, i) => (
            <div
              className="card card-glow step reveal"
              key={step.n}
              style={{ '--delay': `${i * 90}ms` }}
            >
              <span className="step-n num">{step.n}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </div>
          ))}
          <p className="steps-foot reveal">
            Roughly four minutes, once, on the PC where Tally already runs.
          </p>
        </div>
      </div>
    </section>
  );
}
