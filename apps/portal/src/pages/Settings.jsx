import { Pill } from '../components/ui.jsx';
import { fmtDateTime, initials } from '../format.js';
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
