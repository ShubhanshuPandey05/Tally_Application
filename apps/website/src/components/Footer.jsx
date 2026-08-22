import Logo from './Logo.jsx';
import { Link } from '../router.jsx';
import { CONTACT_EMAIL } from '../data/site.js';
import './footer.css';

export default function Footer() {
  return (
    <footer className="foot">
      <div className="shell foot-grid">
        <div className="foot-brand">
          <Logo />
          <p>
            A read-only mobile companion for TallyPrime, for owners who want the
            numbers without opening the software.
          </p>
        </div>

        <nav className="foot-col">
          <h3>Product</h3>
          <Link to="/#product">What it shows</Link>
          <Link to="/#how">How it works</Link>
          <Link to="/#security">Security</Link>
          <Link to="/#download">Download</Link>
        </nav>

        <nav className="foot-col">
          <h3>Help</h3>
          <Link to="/docs">Setup guide</Link>
          <Link to="/docs#trouble">Troubleshooting</Link>
          <Link to="/#faq">Questions</Link>
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        </nav>
      </div>

      <div className="shell foot-bar">
        <span>© {new Date().getFullYear()} TallyFlow</span>
        <span>
          TallyPrime is a trademark of Tally Solutions Pvt. Ltd. TallyFlow is an
          independent product and is not affiliated with Tally Solutions.
        </span>
      </div>
    </footer>
  );
}
