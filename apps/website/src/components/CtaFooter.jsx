import Logo from './Logo.jsx';
import { CONNECTOR } from '../data/downloads.js';
import './cta-footer.css';

const COLUMNS = [
  {
    title: 'Product',
    links: [
      ['Dashboard', '#product'],
      ['How it works', '#how'],
      ['Security', '#security'],
      ['Pricing', '#pricing'],
      ['Questions', '#faq'],
    ],
  },
  {
    title: 'Download',
    links: [
      ['Windows connector', '#download'],
      ['Android app', '#download'],
      ['iOS app', '#download'],
      ['Release notes', '#download'],
    ],
  },
  {
    title: 'Company',
    links: [
      ['About', '#top'],
      ['Support', 'mailto:support@tallyflow.in'],
      ['Privacy', '#top'],
      ['Terms', '#top'],
    ],
  },
];

export default function CtaFooter() {
  return (
    <>
      <section className="cta">
        <div className="cta-glow" />
        <div className="shell">
          <div className="cta-box reveal">
            <div className="cta-rings" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <h2>
              Open your phone.
              <br />
              <span className="grad-text">Know your business.</span>
            </h2>
            <p>
              Ten seconds to today’s sales, your cash position, who owes you money and
              what is running out of stock — without opening TallyPrime.
            </p>
            <div className="cta-actions">
              <a className="btn btn-primary btn-lg" href={CONNECTOR.href} download>
                Download the connector
              </a>
              <a className="btn btn-ghost btn-lg" href="#pricing">
                See pricing
              </a>
            </div>
            <p className="cta-fine">
              14-day trial · no card · read-only, so nothing in Tally can change
            </p>
          </div>
        </div>
      </section>

      <footer className="foot">
        <div className="shell foot-grid">
          <div className="foot-brand">
            <Logo size={30} />
            <p>
              A mobile companion for TallyPrime. Built for owners who want the numbers,
              not the software.
            </p>
            <div className="foot-badges">
              <span>Read-only</span>
              <span>No inbound ports</span>
              <span>Made in India</span>
            </div>
          </div>

          {COLUMNS.map((column) => (
            <nav className="foot-col" key={column.title}>
              <h4>{column.title}</h4>
              {column.links.map(([label, href]) => (
                <a key={label} href={href}>
                  {label}
                </a>
              ))}
            </nav>
          ))}
        </div>

        <div className="shell foot-bar">
          <span>© {new Date().getFullYear()} TallyFlow. All rights reserved.</span>
          <span>
            TallyPrime is a trademark of Tally Solutions Pvt. Ltd. TallyFlow is an
            independent product and is not affiliated with Tally Solutions.
          </span>
        </div>
      </footer>
    </>
  );
}
