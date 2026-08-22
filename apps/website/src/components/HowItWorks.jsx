import Icon from './Icon.jsx';
import { SETUP_STEPS } from '../data/downloads.js';
import { Link } from '../router.jsx';
import './how.css';

const NODES = [
  {
    icon: 'phone',
    tint: '',
    title: 'TallyFlow app',
    where: 'Your phone',
    note: 'Never talks to Tally directly.',
  },
  {
    icon: 'cloud',
    tint: 'tile-violet',
    title: 'TallyFlow server',
    where: 'Sign-in, stored figures',
    note: 'Holds no password and no pairing secret in the clear.',
  },
  {
    icon: 'windows',
    tint: 'tile-green',
    title: 'Connector',
    where: 'Your Windows PC',
    note: 'Opens the connection outwards. Nothing listens for one.',
  },
  {
    icon: 'bank',
    tint: 'tile-amber',
    title: 'TallyPrime',
    where: 'localhost:9000',
    note: 'Stays exactly where it is.',
  },
];

export default function HowItWorks() {
  return (
    <section id="how" className="band">
      <div className="shell">
        <div className="head">
          <span className="label">How it works</span>
          <h2>Tally stays on your PC.</h2>
          <p>
            The connector dials out to us, the same direction your browser reaches a
            website. No port is opened on your machine, no static IP is needed, and
            TallyPrime is never reachable from the internet.
          </p>
        </div>

        <div className="flow">
          {NODES.map((node) => (
            <div className="flow-node" key={node.title}>
              <span className={`tile ${node.tint}`}>
                <Icon name={node.icon} />
              </span>
              <h3>{node.title}</h3>
              <p className="flow-where">{node.where}</p>
              <p className="flow-note">{node.note}</p>
            </div>
          ))}
        </div>

        <p className="flow-caption">
          Reads are served from figures already fetched and stored, so ten people
          looking at the dashboard cost your Tally the same as one. Your PC only does
          work when something is actually out of date.
        </p>

        <div className="steps">
          {SETUP_STEPS.map((step) => (
            <div className="step" key={step.n}>
              <span className="tile tile-ink">{step.n}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </div>
          ))}
          <p className="steps-foot">
            The full version, with the exact keys to press in TallyPrime and what
            to do when it does not connect, is in the{' '}
            <Link className="link" to="/docs">
              setup guide
            </Link>
            .
          </p>
        </div>
      </div>
    </section>
  );
}
