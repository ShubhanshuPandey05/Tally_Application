import { CONTACT_EMAIL } from '../data/site.js';
import './access.css';

export default function Access() {
  return (
    <section id="access">
      <div className="shell">
        <div className="head">
          <span className="label">Getting access</span>
          <h2>Early access, activated by hand.</h2>
          <p>
            TallyFlow is in use with a small number of businesses while the first
            reports settle. Registering in the app does not switch anything on by
            itself — we activate each account, and agree what it covers, with the
            person who will use it.
          </p>
        </div>

        <div className="access-grid">
          <ol className="access-steps">
            <li>
              <b>You register in the app</b>
              An account is created for your business. It starts with no Tally PCs, no
              companies and no extra users.
            </li>
            <li>
              <b>We activate it</b>
              We agree how many companies and how many people it covers, and turn it
              on. Until then the app tells you it is waiting for approval rather than
              showing you an error.
            </li>
            <li>
              <b>You pair your Tally PC</b>
              Add the computer in the app, run the connector installer on it, and link
              the companies you want on your phone.
            </li>
          </ol>

          <div className="card access-card">
            <h3>Ask for an account</h3>
            <p>
              Tell us how many shops or companies you run and which version of
              TallyPrime you are on. We will reply with what to install and set the
              account up with you.
            </p>
            <a className="btn btn-primary" href={`mailto:${CONTACT_EMAIL}`}>
              Email us
            </a>
            <p className="access-fine">
              There is no published price list yet, and nothing on this site takes a
              payment. When pricing exists it will be written here, not quoted from a
              placeholder.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
