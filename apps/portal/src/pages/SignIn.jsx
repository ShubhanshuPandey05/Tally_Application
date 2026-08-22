import { useState } from 'react';
import { api, token } from '../api.js';
import './gate.css';

export default function SignIn({ onSignedIn }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
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
    <div className="gate">
      <form className="gate-card card" onSubmit={submit}>
        <div className="gate-brand">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--accent)" />
            <path
              d="M8 20.5 13 14l4 4.5L24 9"
              fill="none"
              stroke="var(--accent-ink)"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <div className="stack">
            <h1>Partner Desk</h1>
            <p className="muted">Sign in to manage TallyFlow accounts.</p>
          </div>
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
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>

        {error ? <p className="error-text">{error}</p> : null}

        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? <span className="spinner" /> : null}
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="hint">
          Portal accounts are created by a TallyFlow owner. There is no sign-up.
        </p>
      </form>
    </div>
  );
}
