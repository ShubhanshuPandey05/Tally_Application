import './features.css';

const FEATURES = [
  {
    icon: '📊',
    tone: 'indigo',
    title: 'A dashboard, not a ledger',
    body: 'Eighteen widgets tuned for an owner, not an auditor: today’s sales, cash and bank position, receivables ageing, stock value, top customers, profit at a glance.',
    span: 'wide',
    art: 'tiles',
  },
  {
    icon: '⏱️',
    tone: 'mint',
    title: 'Every number says when',
    body: 'Each figure carries the moment it was read from Tally. A number without an “as of” is a number you might act on believing it is current.',
    art: 'freshness',
  },
  {
    icon: '🛡️',
    tone: 'violet',
    title: 'It cannot write. At all.',
    body: 'The request builder can only emit Tally exports. A test in the build asserts every shipped query is read-only, so a write path cannot arrive by accident.',
    art: 'lock',
  },
  {
    icon: '🏬',
    tone: 'amber',
    title: 'Every branch, one screen',
    body: 'Pair a connector per shop PC. Consolidated figures across companies, and when one branch goes dark the app tells you exactly which one.',
    art: 'branches',
  },
  {
    icon: '📉',
    tone: 'rose',
    title: 'Honest empty states',
    body: '“You sold nothing today” and “we could not reach your Tally” are different claims. TallyFlow never renders a grid of ₹0 tiles to cover a failure.',
    art: 'empty',
  },
  {
    icon: '₹',
    tone: 'indigo',
    title: 'Money the way you read it',
    body: 'Indian grouping with lakh and crore compaction, tabular figures that line up in a column, and decimals that are exact — amounts are never floats.',
    span: 'wide',
    art: 'money',
  },
];

function Art({ kind }) {
  if (kind === 'tiles') {
    return (
      <div className="art art-tiles">
        {[
          ['Today', '₹4.82 L', 'up'],
          ['Month', '₹1.14 Cr', 'up'],
          ['Cash', '₹12.8 L', ''],
          ['Stock', '₹68.4 L', 'down'],
        ].map(([label, value, tone]) => (
          <div className="art-tile" key={label}>
            <small>{label}</small>
            <b className="num">{value}</b>
            <i className={tone} />
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'freshness') {
    return (
      <div className="art art-fresh">
        <span className="art-chip ok">
          <em /> read 2 min ago
        </span>
        <span className="art-chip warn">
          <em /> stale · pull to refresh
        </span>
        <span className="art-chip bad">
          <em /> your Tally PC is offline
        </span>
      </div>
    );
  }

  if (kind === 'lock') {
    return (
      <div className="art art-lock">
        <code>TALLYREQUEST=Export</code>
        <code className="struck">TALLYREQUEST=Import</code>
      </div>
    );
  }

  if (kind === 'branches') {
    return (
      <div className="art art-branches">
        {[
          ['Head office', 'ok'],
          ['Andheri', 'ok'],
          ['Surat depot', 'off'],
        ].map(([name, state]) => (
          <span key={name} className={state}>
            <em />
            {name}
          </span>
        ))}
      </div>
    );
  }

  if (kind === 'empty') {
    return (
      <div className="art art-empty">
        <span className="bad">₹0.00 ✕</span>
        <span className="ok">Couldn’t read stock ↻</span>
      </div>
    );
  }

  return (
    <div className="art art-money">
      <b className="num">₹1,23,45,678.90</b>
      <small>Decimal, not double</small>
    </div>
  );
}

export default function Features() {
  return (
    <section id="product">
      <div className="orb feat-orb" />
      <div className="shell">
        <div className="section-head reveal">
          <span className="eyebrow">
            <span className="dot" />
            The product
          </span>
          <h2>
            Google Analytics for Tally.
            <br />
            <span className="grad-text">Not remote desktop for Tally.</span>
          </h2>
          <p>
            You should never feel like you are using accounting software on a 6-inch
            screen. TallyFlow reads your data and presents it the way an owner actually
            thinks about the business.
          </p>
        </div>

        <div className="feat-grid">
          {FEATURES.map((feature, i) => (
            <article
              className={`card card-glow feat ${feature.span === 'wide' ? 'feat-wide' : ''} tone-${feature.tone} reveal`}
              style={{ '--delay': `${i * 70}ms` }}
              key={feature.title}
            >
              <span className="feat-icon">{feature.icon}</span>
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
              <Art kind={feature.art} />
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
