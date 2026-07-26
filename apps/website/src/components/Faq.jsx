import { useState } from 'react';
import { PRICING_FAQ } from '../data/pricing.js';
import './faq.css';

export default function Faq() {
  const [open, setOpen] = useState(0);

  return (
    <section id="faq" className="faq">
      <div className="shell faq-grid">
        <div className="section-head reveal">
          <span className="eyebrow">
            <span className="dot" />
            Questions
          </span>
          <h2>
            The things owners
            <br />
            <span className="grad-text">actually ask us.</span>
          </h2>
          <p>
            Still unsure about something? Write to{' '}
            <a href="mailto:hello@tallyflow.in" className="faq-mail">
              hello@tallyflow.in
            </a>{' '}
            — a person answers.
          </p>
        </div>

        <div className="faq-list reveal">
          {PRICING_FAQ.map((item, i) => (
            <div className={`faq-item ${open === i ? 'is-open' : ''}`} key={item.q}>
              <button
                onClick={() => setOpen(open === i ? -1 : i)}
                aria-expanded={open === i}
              >
                <span>{item.q}</span>
                <i />
              </button>
              <div className="faq-body">
                <p>{item.a}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
