import { api } from '../api.js';
import { Pill, useLoad } from '../components/ui.jsx';
import { fmtCount, fmtDateTime, initials } from '../format.js';
import ChangePassword from './ChangePassword.jsx';
import './accounts.css';

export default function Settings({ me, onUpdated }) {
  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
        <p className="muted">Your portal account.</p>
      </div>

      <section className="card card-pad row" style={{ gap: 14 }}>
        <span className="avatar" style={{ width: 44, height: 44, fontSize: 15 }}>
          {initials(me.full_name, me.email)}
        </span>
        <div className="stack grow">
          <strong style={{ fontSize: 15 }}>{me.full_name || me.email}</strong>
          <span className="muted" style={{ fontSize: 13 }}>{me.email}</span>
        </div>
        <Pill tone={me.role === 'owner' ? 'accent' : 'off'}>
          {me.role === 'owner' ? 'Owner' : 'Partner'}
        </Pill>
      </section>

      <div className="drawer-grid">
        <div className="fact">
          <span className="fact-label">Accounts you handle</span>
          <span className="fact-value">{me.accounts}</span>
        </div>
        <div className="fact">
          <span className="fact-label">Last signed in</span>
          <span className="fact-value">{fmtDateTime(me.last_login_at)}</span>
        </div>
        <div className="fact">
          <span className="fact-label">What you can see</span>
          <span className="fact-value">
            {me.role === 'owner' ? 'Every account' : 'Only your own accounts'}
          </span>
        </div>
      </div>

      {me.role === 'owner' ? <Diagnostics /> : null}

      <div className="stack" style={{ gap: 10 }}>
        <h2>Change password</h2>
        <ChangePassword onDone={onUpdated} />
      </div>

      <p className="hint" style={{ maxWidth: 620 }}>
        This portal never shows a customer&rsquo;s figures. It counts how much of the
        product an account uses — people, companies, Tally PCs — and nothing in it
        reaches anybody&rsquo;s books.
      </p>
    </>
  );
}

/**
 * What the diagnostic tables are holding, and for how long.
 *
 * Here rather than on the log screens because it is not a support question. It
 * is the answer to "are these logs going to fill the disk?", which is asked
 * once, by the person who runs the box — and the retention figure beside each
 * count is what makes the count readable. Eighty thousand connector lines
 * sounds alarming until it is two days of the whole fleet, ageing out by
 * itself.
 */
function Diagnostics() {
  const usage = useLoad((signal) => api('/logs/usage', { signal }), []);
  if (usage.loading || usage.error || !usage.data) return null;

  const rows = [
    ['Server logs', usage.data.server_logs, usage.data.server_retention_days],
    ['Connector logs', usage.data.connector_logs, usage.data.connector_retention_days],
    ['Activity', usage.data.audit_logs, usage.data.audit_retention_days],
  ];

  return (
    <div className="stack" style={{ gap: 10 }}>
      <h2>Diagnostics storage</h2>
      <div className="drawer-grid">
        {rows.map(([label, count, days]) => (
          <div className="fact" key={label}>
            <span className="fact-label">{label}</span>
            <span className="fact-value">{fmtCount(count)} rows</span>
            <span className="dim">
              kept {days === 1 ? '1 day' : `${days} days`}, then deleted
            </span>
          </div>
        ))}
      </div>
      <p className="hint" style={{ maxWidth: 620 }}>
        These tables grow with traffic rather than with customers, so they age out on
        their own. Clearing one by hand — from the log screens — is for the day a single
        machine fills it faster than that.
      </p>
    </div>
  );
}
