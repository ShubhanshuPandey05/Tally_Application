import Icon from './Icon.jsx';
import './preview.css';

const OWING = [
  ['Sample Distributors', '4,82,000', ''],
  ['Sample Steel Co.', '2,15,400', 'warn'],
  ['Sample Traders', '1,08,750', ''],
];

/**
 * A drawing of the dashboard, not a screenshot and not a claim.
 *
 * It is built from the same pieces as the app itself — one dark card carrying
 * the figure the screen is about, soft grey tiles beneath it, a floating pill
 * for navigation — so the site is showing the product rather than an
 * illustrator's idea of it. The figures are captioned as samples, because an
 * invented number presented as a customer's is exactly the dishonesty this
 * rewrite was for.
 */
export default function AppPreview() {
  return (
    <figure className="preview">
      <div className="preview-screen">
        <div className="preview-top">
          <div>
            <p className="preview-company">Sample Traders</p>
            <p className="preview-fresh">
              <span className="pip" />
              Read from Tally 2 minutes ago
            </p>
          </div>
          <span className="preview-avatar">S</span>
        </div>

        <div className="preview-hero">
          <div className="preview-hero-top">
            <span className="preview-hero-tile">
              <Icon name="chart" />
            </span>
            <span>Today’s sales</span>
            <span className="preview-delta">+18.4%</span>
          </div>
          <p className="preview-figure">₹4,82,650</p>
          <p className="preview-sub">compared with yesterday</p>
          <div className="preview-hero-foot">
            <span>Cash &amp; bank</span>
            <b>₹12,84,100</b>
          </div>
        </div>

        <div className="preview-tiles">
          <div className="preview-tile">
            <span className="tile tile-amber">
              <Icon name="wallet" />
            </span>
            <small>You are owed</small>
            <b>₹18,42,300</b>
          </div>
          <div className="preview-tile">
            <span className="tile tile-violet">
              <Icon name="receipt" />
            </span>
            <small>You owe</small>
            <b>₹6,10,900</b>
          </div>
        </div>

        <div className="preview-list">
          <p>Who owes you</p>
          {OWING.map(([name, amount, tone]) => (
            <div className="preview-row" key={name}>
              <span className={`pip ${tone}`} />
              <span className="preview-name">{name}</span>
              <span className="num">₹{amount}</span>
            </div>
          ))}
        </div>

        <div className="preview-nav">
          <span className="on">
            <Icon name="chart" /> Home
          </span>
          <span>
            <Icon name="receipt" />
          </span>
          <span>
            <Icon name="users" />
          </span>
        </div>
      </div>

      <figcaption className="preview-caption">
        Illustration of the dashboard. Figures are samples.
      </figcaption>
    </figure>
  );
}
