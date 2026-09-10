import { useState } from 'react';

import Icon from './Icon.jsx';
import { Link } from '../router.jsx';
import { useManifest, mb, shortHash } from '../hooks.js';
import { CONTACT_EMAIL } from '../data/site.js';
import './downloads.css';

function Facts({ artefact }) {
  const hash = shortHash(artefact.sha256);
  return (
    <div className="dl-facts">
      <span className="num">{artefact.file}</span>
      {hash && (
        <span>
          <b>SHA-256</b> <span className="num">{hash}</span>
        </span>
      )}
    </div>
  );
}

export default function Downloads() {
  const { connector, android } = useManifest();
  const [copied, setCopied] = useState(false);

  const silent = `${connector.file} /VERYSILENT /ID=<id> /SECRET=<secret>`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(silent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section id="download" className="band dl">
      <div className="shell">
        <div className="head">
          <span className="label">Download</span>
          <h2>Two things to install, in this order.</h2>
          <p>
            The connector goes on the PC where TallyPrime already runs. The app goes on
            your phone. They find each other by camera: the PC shows a code and the app
            reads it. Nothing to type.
          </p>
        </div>

        <div className="dl-grid">
          <article className="card dl-card">
            <span className="tile">
              <Icon name="windows" />
            </span>
            <span className="dl-step">First · on your Tally PC</span>
            <h3>Windows connector</h3>
            <p className="meta">
              Windows 10 / 11, 64-bit · v{connector.version} · {mb(connector.size_bytes)}
            </p>

            <ul className="dl-points">
              <li>Installs for your user only — no admin rights, no UAC prompt</li>
              <li>Shows a code for the app to scan — nothing to type in</li>
              <li>Starts at every logon and reconnects on its own</li>
              <li>Does not need Tally to be open while it installs</li>
            </ul>

            <div className="dl-actions">
              <a className="btn btn-primary" href={connector.url} download>
                Download .exe
              </a>
              <Link className="btn btn-ghost" to="/docs#connector">
                Installation steps
              </Link>
            </div>

            <Facts artefact={connector} />

            <div className="dl-bulk">
              <div>
                <span>Rolling out to several shops?</span>
                <button onClick={copy}>{copied ? 'Copied' : 'Copy command'}</button>
              </div>
              <code>{silent}</code>
            </div>
          </article>

          <article className="card dl-card">
            <span className="tile tile-green">
              <Icon name="android" />
            </span>
            <span className="dl-step">Then · on your phone</span>
            <h3>Android app</h3>
            <p className="meta">
              Android 8.0 and later · v{android.version} · {mb(android.size_bytes)}
            </p>

            <ul className="dl-points">
              <li>Dashboard, ten reports, stock and outstanding</li>
              <li>Pairs a PC by scanning it — no IP addresses, ports or keys</li>
              <li>Shows the last figures read when your PC is off, and says so</li>
              <li>Tells you when a newer version is available</li>
            </ul>

            <div className="dl-actions">
              <a className="btn btn-primary" href={android.url} download>
                Download .apk
              </a>
              <span className="btn btn-ghost" aria-disabled="true">
                iPhone — coming later
              </span>
            </div>

            <Facts artefact={android} />

            <div className="dl-bulk">
              <div>
                <span>Not on Google Play</span>
              </div>
              <p className="meta">
                Install the APK directly. Android will ask you to allow installs from
                your browser once — that prompt is the system’s, and cannot be skipped.
              </p>
            </div>
          </article>
        </div>

        <p className="dl-note">
          Both files are unsigned for now, so Windows SmartScreen and Android will warn
          you the first time. Check the SHA-256 above against the file you downloaded if
          you want to be sure it is ours. Stuck on either?{' '}
          <a className="link" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          .
        </p>
      </div>
    </section>
  );
}
