import Icon from './Icon.jsx';
import { REPORTS, REPORT_COUNT } from '../data/site.js';
import './features.css';

const POINTS = [
  {
    icon: 'chart',
    tint: '',
    title: 'Four figures, then the detail',
    body:
      'The dashboard opens on today’s sales, cash and bank, what you are owed and what you owe. Below that: sales and purchases for the period, receivables, payables, top customers and top products.',
  },
  {
    icon: 'clock',
    tint: 'tile-green',
    title: 'Every figure says when it was read',
    body:
      'Each number carries the moment it came out of Tally. A figure with no “as of” is one you might act on believing it is current.',
  },
  {
    icon: 'lock',
    tint: 'tile-violet',
    title: 'It cannot write to Tally',
    body:
      'The connector can only issue Tally export requests. There is no import path in the code, and a test in the build asserts every shipped query is a read.',
  },
  {
    icon: 'branch',
    tint: 'tile-amber',
    title: 'One connector per Tally PC',
    body:
      'Pair each machine separately. When a branch goes offline the app names that branch, instead of quietly showing you a smaller total.',
  },
  {
    icon: 'alert',
    tint: 'tile-rose',
    title: 'It says when it does not know',
    body:
      '“You sold nothing today” and “we could not reach your Tally” are different statements. Neither is ever shown as ₹0.',
  },
];

/* One colour per group, not per row — the same rule the app's report index
   follows. The group is the category; tinting each row separately would turn an
   index into a chart of nothing. The glyph inside the tile is the row's own,
   because that is what tells five reports apart. */
const GROUP_TINT = { Money: '', Transactions: 'tile-violet', Stock: 'tile-amber' };

export default function Features() {
  return (
    <section id="product">
      <div className="shell">
        <div className="head">
          <span className="label">The product</span>
          <h2>A dashboard for your books, not a copy of Tally.</h2>
          <p>
            You should not have to learn a menu path to find out how the day went.
            TallyFlow answers the questions an owner actually asks, in the order they
            ask them.
          </p>
        </div>

        <div className="feat-grid">
          <div className="feat-list">
            {POINTS.map((point) => (
              <div className="feat-item" key={point.title}>
                <span className={`tile ${point.tint}`}>
                  <Icon name={point.icon} />
                </span>
                <div>
                  <h3>{point.title}</h3>
                  <p>{point.body}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="card reports">
            <header>
              <h3>Reports in the app today</h3>
              <span className="meta num">{REPORT_COUNT}</span>
            </header>

            {REPORTS.map((group) => (
              <div className="reports-group" key={group.group}>
                <p>{group.group}</p>
                <ul>
                  {group.items.map(([name, detail, icon]) => (
                    <li key={name}>
                      <span className={`tile ${GROUP_TINT[group.group]}`}>
                        <Icon name={icon} />
                      </span>
                      <div>
                        <strong>{name}</strong>
                        <span>{detail}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
