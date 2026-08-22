import { useState } from 'react';
import { api } from '../api.js';
import { useToast } from '../components/ui.jsx';
import './gate.css';

/**
 * Set a new password.
 *
 * Two modes from one component. `forced` is the gate a seeded or newly issued
 * account lands on and cannot leave: no current password is asked for, because
 * somebody else chose it and retyping it proves nothing about who is at the
 * keyboard. The unforced mode lives on Settings and does ask, because there the
 * session is already established and the question is whether it is still the
 * same person at the desk.
 */
export default function ChangePassword({ forced = false, onDone }) {
  const toast = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (next !== confirm) {
      setError('Those two passwords are not the same.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api('/me/password', {
        method: 'POST',
        body: {
          new_password: next,
          current_password: forced ? undefined : current,
        },
      });
      // Re-read rather than patching the local copy: `must_change_password` is
      // the flag the whole gate turns on, and inferring that it flipped is one
      // more thing that can be wrong.
      onDone(await api('/me'));
      toast('Password changed.');
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  const form = (
    <>
      {forced ? null : (
        <label className="field">
          <span className="label">Current password</span>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </label>
      )}
      <label className="field">
        <span className="label">New password</span>
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          minLength={10}
          required
          autoFocus={forced}
          value={next}
          onChange={(event) => setNext(event.target.value)}
        />
        <span className="hint">At least 10 characters.</span>
      </label>
      <label className="field">
        <span className="label">Repeat new password</span>
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
        />
      </label>
      {error ? <p className="error-text">{error}</p> : null}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? <span className="spinner" /> : null}
        {busy ? 'Saving…' : 'Set password'}
      </button>
    </>
  );

  if (!forced) {
    return (
      <form className="card card-pad" style={{ display: 'grid', gap: 14, maxWidth: 420 }} onSubmit={submit}>
        {form}
      </form>
    );
  }

  return (
    <div className="gate">
      <form className="gate-card card" onSubmit={submit}>
        <div className="stack" style={{ gap: 6, marginBottom: 4 }}>
          <h1>Choose a password</h1>
          <p className="muted">
            This account is still on the password somebody else set. Replace it before
            going any further.
          </p>
        </div>
        {form}
      </form>
    </div>
  );
}
