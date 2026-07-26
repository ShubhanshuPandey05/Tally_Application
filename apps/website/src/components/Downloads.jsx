import { useState } from 'react';
import { CONNECTOR, MOBILE, CONNECTOR_VERSION, APP_VERSION } from '../data/downloads.js';
import './downloads.css';

const SILENT = `TallyFlowConnector-Setup-${CONNECTOR_VERSION}.exe /VERYSILENT /ID=<id> /SECRET=<secret>`;

function WindowsIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" fill="currentColor">
      <path d="M3 5.5 10.2 4.5v7.1H3V5.5Zm0 13 7.2 1v-7H3v6ZM11.4 4.3 21 3v8.6h-9.6V4.3Zm0 8.5H21V21l-9.6-1.3v-6.9Z" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d="M3.6 2.2 14 12 3.6 21.8a1.6 1.6 0 0 1-.6-1.2V3.4c0-.5.2-.9.6-1.2Z" fill="#5AF0C8" />
      <path d="M14 12 3.6 2.2a1.5 1.5 0 0 1 1.6.1l12.3 7-3.5 2.7Z" fill="#6D8BFF" />
      <path d="M14 12l3.5 2.7-12.3 7a1.5 1.5 0 0 1-1.6.1L14 12Z" fill="#F0435F" />
      <path d="m17.5 9.3 3 1.7c.9.5.9 1.5 0 2l-3 1.7L14 12l3.5-2.7Z" fill="#F0A83A" />
    </svg>
  );
}

function AppleIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="currentColor">
      <path d="M16.4 12.7c0-2.4 2-3.6 2.1-3.6-1.1-1.7-2.9-1.9-3.5-1.9-1.5-.2-2.9.9-3.6.9-.8 0-1.9-.9-3.1-.8-1.6 0-3 .9-3.8 2.4-1.7 2.9-.4 7.1 1.2 9.4.8 1.1 1.7 2.4 3 2.3 1.2 0 1.6-.7 3.1-.7s1.9.7 3.1.7c1.3 0 2.1-1.1 2.9-2.3.9-1.3 1.3-2.6 1.3-2.6s-2.5-1-2.7-3.8ZM14.2 5.6c.7-.8 1.1-1.9 1-3.1-1 0-2.2.7-2.9 1.5-.6.7-1.2 1.9-1 3 1.1.1 2.2-.6 2.9-1.4Z" />
    </svg>
  );
}

export default function Downloads() {
  const [copied, setCopied] = useState(false);

  const copySilent = async () => {
    try {
      await navigator.clipboard.writeText(SILENT);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section id="download" className="dl">
      <div className="orb dl-orb" />
      <div className="shell">
        <div className="section-head center reveal">
          <span className="eyebrow">
            <span className="dot" />
            Download
          </span>
          <h2>
            Two pieces. <span className="grad-text">Four minutes.</span>
          </h2>
          <p>
            The connector goes on the PC where TallyPrime already runs. The app goes in
            your pocket. They find each other through a six-character pairing code.
          </p>
        </div>

        <div className="dl-grid">
          {/* ---------- connector ---------- */}
          <article className="card card-glow dl-card dl-connector reveal">
            <div className="dl-ribbon">Step 1 · on your Tally PC</div>

            <header className="dl-head">
              <span className="dl-icon win">
                <WindowsIcon />
              </span>
              <div>
                <h3>{CONNECTOR.name}</h3>
                <p className="dl-meta">
                  {CONNECTOR.platform} · v{CONNECTOR_VERSION} · {CONNECTOR.size}
                </p>
              </div>
            </header>

            <ul className="dl-points">
              {CONNECTOR.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>

            <a className="btn btn-primary btn-lg dl-btn" href={CONNECTOR.href} download>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 4v12m0 0 5-5m-5 5-5-5M4 20h16"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Download for Windows
            </a>

            <p className="dl-file num">{CONNECTOR.file}</p>
            <p className="dl-hash num">{CONNECTOR.checksum}</p>

            <div className="dl-silent">
              <div className="dl-silent-head">
                <span>Deploying to several shops?</span>
                <button onClick={copySilent} className={copied ? 'is-copied' : ''}>
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <code>{SILENT}</code>
            </div>
          </article>

          {/* ---------- mobile ---------- */}
          <article className="card card-glow dl-card dl-mobile reveal" style={{ '--delay': '110ms' }}>
            <div className="dl-ribbon alt">Step 2 · on your phone</div>

            <header className="dl-head">
              <span className="dl-icon app">📱</span>
              <div>
                <h3>TallyFlow app</h3>
                <p className="dl-meta">
                  Android &amp; iOS · v{APP_VERSION} · Free with any plan
                </p>
              </div>
            </header>

            <ul className="dl-points">
              <li>Dashboard, reports, stock and receivables</li>
              <li>Guided pairing wizard — no typing of IPs or ports</li>
              <li>Works offline against the last figures we read</li>
              <li>Biometric unlock and per-device revocation</li>
            </ul>

            <div className="dl-stores">
              <a className="store" href={MOBILE.android.href}>
                <PlayIcon />
                <span>
                  <small>Get it on</small>
                  <b>Google Play</b>
                </span>
              </a>
              <a className="store" href={MOBILE.ios.href}>
                <AppleIcon />
                <span>
                  <small>Download on the</small>
                  <b>App Store</b>
                </span>
              </a>
            </div>

            <a className="dl-apk" href={MOBILE.apk.href} download>
              Or download the APK directly
              <span className="num">
                {MOBILE.apk.name} · {MOBILE.apk.size}
              </span>
            </a>

            <div className="dl-req">
              <span>Android 8.0+</span>
              <span>iOS 14+</span>
              <span>TallyPrime 2.x+</span>
            </div>
          </article>
        </div>

        <p className="dl-foot reveal">
          Need the connector on Windows Server, or a build for an older Tally? Write to{' '}
          <a href="mailto:support@tallyflow.in">support@tallyflow.in</a> and we will send
          you one.
        </p>
      </div>
    </section>
  );
}
