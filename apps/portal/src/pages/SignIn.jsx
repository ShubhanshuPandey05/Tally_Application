import { useState } from 'react';
import { api, token } from '../api.js';
import './gate.css';

/* What the desk is for, in the order somebody uses it. Three, because that is
   how many jobs it has -- not a feature list to fill a panel. */
const JOBS = [
  {
    tint: 'var(--tile-amber)',
    icon: 'M5 12.5l4.5 4.5L19 7.5',
    title: 'Approve new businesses',
    text: 'Every signup waits here until somebody says what it covers.',
  },
  {
    tint: 'var(--tile-blue)',
    icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
    title: 'See who is using it',
    text: 'Active people, reports opened and entries made, per account.',
  },
  {
    tint: 'var(--tile-green)',
    icon: 'M9 3v5M15 3v5M6 8h12v5a6 6 0 0 1-12 0zM12 19v3',
    title: 'Find out why it stopped',
    text: 'Connector and server logs, without asking for a remote session.',
  },
];

export default function SignIn({ onSignedIn }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [shown, setShown] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const session = await api('/auth/login', { method: 'POST', body: { email, password } });
      token.set(session.access_token);
      onSignedIn(session.user);
    } catch (failure) {
      setError(failure.message);
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      {/* The app's one dark card, at the size of a page. It stays dark on every
          skin: it is the product's face, not a surface of this screen. */}
      <aside className="signin-aside">
        <div className="signin-brand">
          {/* The product's own mark, as in the sidebar -- not a logo of the
              portal's own, which read as a different product. */}
          <img
            src={`${import.meta.env.BASE_URL}favicon.svg`}
            alt=""
            width="40"
            height="40"
          />
          <div className="stack">
            <strong>TallyFlow</strong>
            <span>Partner Desk</span>
          </div>
        </div>

        <div className="signin-pitch">
          <h1>The accounts behind the app.</h1>
          {/* The three jobs in one line, for a phone, where the list below
              would push the form off the screen. */}
          <p className="signin-sub">
            Approve businesses, see who is using it, and find out why it stopped.
          </p>
          <ul className="signin-jobs">
            {JOBS.map((job) => (
              <li key={job.title}>
                <span className="signin-tile" style={{ background: job.tint }}>
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path
                      d={job.icon}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <div className="stack">
                  <strong>{job.title}</strong>
                  <span>{job.text}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="signin-foot">
          Counts only. Nothing here opens a customer&rsquo;s books.
        </p>
      </aside>

      <main className="signin-main">
        <form className="signin-form" onSubmit={submit}>
          <div className="stack" style={{ gap: 6 }}>
            <h2>Sign in</h2>
            <p className="muted">Use the email your portal account was created with.</p>
          </div>

          <label className="field">
            <span className="label">Email</span>
            <input
              className="input"
              type="email"
              autoComplete="username"
              required
              autoFocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>

          <label className="field">
            <span className="label">Password</span>
            <span className="signin-secret">
              <input
                className="input"
                type={shown ? 'text' : 'password'}
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-pressed={shown}
                onClick={() => setShown((value) => !value)}
              >
                {shown ? 'Hide' : 'Show'}
              </button>
            </span>
          </label>

          {error ? <p className="error-text">{error}</p> : null}

          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            {busy ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="hint">
            Portal accounts are created by a TallyFlow owner. There is no sign-up, and an
            owner can reissue a forgotten password.
          </p>
        </form>
      </main>
    </div>
  );
}
