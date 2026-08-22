import { useState } from 'react';

import { CONTACT_EMAIL } from '../data/site.js';
import './faq.css';

const ITEMS = [
  {
    q: 'Can TallyFlow change anything inside Tally?',
    a: 'No. The connector only ever asks Tally to export. There is no code path that writes, so even someone holding your credentials has nothing to write with.',
  },
  {
    q: 'Do I need a static IP, or to open a port on my PC?',
    a: 'Neither. The connector makes an outbound connection to us and keeps it open. Nothing on the internet can reach your machine, because nothing on your machine is listening.',
  },
  {
    q: 'What happens when my Tally PC is switched off?',
    a: 'The app keeps working and shows the figures it last read, labelled with when it read them and a note that your PC is offline. It picks up again on its own when the PC comes back.',
  },
  {
    q: 'Will this slow down Tally while my staff are billing?',
    a: 'TallyPrime answers one request at a time, so the connector never has more than two exports in flight, and the app reads stored figures rather than asking your PC every time. Ten people on the dashboard cost your Tally the same as one.',
  },
  {
    q: 'How far back can I see?',
    a: 'The financial year currently open in TallyPrime. Tally only exposes the open company’s period over its gateway, so earlier years genuinely cannot be read — we would rather say that than let you find out from a chart that stops.',
  },
  {
    q: 'I have Tally on more than one PC. Does that work?',
    a: 'Yes. Each PC gets its own connector and is paired separately, which is what lets the app name the branch that has gone offline instead of quietly showing a smaller total.',
  },
  {
    q: 'Which TallyPrime do I need?',
    a: 'Any TallyPrime that can act as a server on port 9000 — F1 → Settings → Connectivity → Client/Server configuration. The connector ships a “Check Tally Connection” shortcut that tells you in one click whether yours is set up right.',
  },
  {
    q: 'Is there an iPhone app?',
    a: 'Not yet. Android today; iOS later. The app is not on Google Play either — you install the APK from this site.',
  },
];

export default function Faq() {
  const [open, setOpen] = useState(0);

  return (
    <section id="faq" className="band">
      <div className="shell-narrow">
        <div className="head">
          <span className="label">Questions</span>
          <h2>What people ask before installing.</h2>
        </div>

        <div className="faq-list">
          {ITEMS.map((item, i) => (
            <div className={`faq-item ${open === i ? 'is-open' : ''}`} key={item.q}>
              <button onClick={() => setOpen(open === i ? -1 : i)} aria-expanded={open === i}>
                <span>{item.q}</span>
                <i aria-hidden="true" />
              </button>
              {open === i && <p className="faq-answer">{item.a}</p>}
            </div>
          ))}
        </div>

        <p className="faq-foot">
          Something not covered here?{' '}
          <a className="link" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          .
        </p>
      </div>
    </section>
  );
}
