import { useState } from 'react';
import { PLANS, YEARLY_MONTHS, BILLING_NOTE } from '../data/pricing.js';
import { inr } from '../hooks.js';
import './pricing.css';

export default function Pricing() {
  const [yearly, setYearly] = useState(false);

  return (
    <section id="pricing" className="pr">
      <div className="orb pr-orb-1" />
      <div className="orb pr-orb-2" />

      <div className="shell">
        <div className="section-head center reveal">
          <span className="eyebrow">
            <span className="dot" />
            Pricing
          </span>
          <h2>
            Priced per business,
            <br />
            <span className="grad-text warm">not per person looking.</span>
          </h2>
          <p>
            Your accountant, your partner and your branch manager all see the same
            dashboard without costing your Tally PC a thing. Every plan starts with a
            14-day trial and needs no card.
          </p>
        </div>

        <div className="pr-toggle reveal">
          <button
            className={!yearly ? 'on' : ''}
            onClick={() => setYearly(false)}
            aria-pressed={!yearly}
          >
            Monthly
          </button>
          <button
            className={yearly ? 'on' : ''}
            onClick={() => setYearly(true)}
            aria-pressed={yearly}
          >
            Yearly
            <em>2 months free</em>
          </button>
          <span className="pr-toggle-pill" data-yearly={yearly} />
        </div>

        <div className="pr-grid">
          {PLANS.map((plan, i) => {
            const monthlyRate = yearly
              ? Math.round((plan.monthly * YEARLY_MONTHS) / 12)
              : plan.monthly;
            const billed = plan.monthly * YEARLY_MONTHS;

            return (
              <article
                className={`card card-glow plan ${plan.popular ? 'plan-popular' : ''} reveal`}
                key={plan.id}
                style={{ '--delay': `${i * 90}ms`, '--accent': plan.accent }}
              >
                {plan.popular && <span className="plan-flag">Most popular</span>}

                <h3>{plan.name}</h3>
                <p className="plan-tagline">{plan.tagline}</p>

                <div className="plan-price">
                  <span className="plan-currency">₹</span>
                  <span className="plan-amount num" key={`${plan.id}-${yearly}`}>
                    {inr(monthlyRate)}
                  </span>
                  <span className="plan-per">/month</span>
                </div>
                <p className="plan-billed">
                  {yearly ? (
                    <>
                      billed ₹{inr(billed)} yearly · you save ₹{inr(plan.monthly * 2)}
                    </>
                  ) : (
                    <>billed monthly · cancel any time</>
                  )}
                </p>

                <a
                  className={`btn ${plan.popular ? 'btn-primary' : 'btn-ghost'} plan-cta`}
                  href="#download"
                >
                  {plan.cta}
                </a>

                <dl className="plan-limits">
                  {plan.limits.map(([k, v]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>

                <ul className="plan-features">
                  {plan.features.map((feature) => (
                    <li key={feature}>{feature}</li>
                  ))}
                </ul>
              </article>
            );
          })}
        </div>

        <div className="pr-strip reveal">
          <span>✱ {BILLING_NOTE}</span>
          <span>All prices exclusive of GST · INR only for now</span>
        </div>
      </div>
    </section>
  );
}
