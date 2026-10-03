import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { Empty, Field, Loading, Modal, Pill, useLoad, useToast } from '../components/ui.jsx';
import { fmtAgo, initials } from '../format.js';
import './accounts.css';

/**
 * Everyone with portal access.
 *
 * Owner-only, and there is no other way in — portal accounts are never
 * self-service. The one guard worth knowing about is enforced server-side: the
 * last active owner cannot be demoted or switched off, because nobody could
 * then create a portal owner from inside the portal and the only way back is a
 * database.
 */
export default function Partners({ me }) {
  const toast = useToast();
  const partners = useLoad((signal) => api('/partners', { signal }), []);
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [resetting, setResetting] = useState(null);
  const [busy, setBusy] = useState('');

  async function update(partner, patch) {
    setBusy(partner.id);
    try {
      await api(`/partners/${partner.id}`, { method: 'PATCH', body: patch });
      partners.reload();
      toast('Saved.');
    } catch (failure) {
      toast(failure.message, 'bad');
    } finally {
      setBusy('');
    }
  }

  return (
    <>
      <div className="page-head row-between">
        <div className="stack">
          <h1>Partners</h1>
          <p className="muted">
            Who can sign in here. A partner sees only the accounts assigned to them.
          </p>
        </div>
        <button className="btn btn-primary" type="button" onClick={() => setAdding(true)}>
          Add someone
        </button>
      </div>

      <section className="card">
        {partners.loading && !partners.data ? (
          <Loading />
        ) : partners.error ? (
          <Empty title="Could not load partners">{partners.error.message}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Role</th>
                  <th>Accounts</th>
                  <th>Last signed in</th>
                  <th className="right">Manage</th>
                </tr>
              </thead>
              <tbody>
                {partners.data.map((partner) => (
                  <tr key={partner.id}>
                    <td>
                      <div className="row">
                        <span className="avatar">
                          {initials(partner.full_name, partner.email)}
                        </span>
                        <div className="stack">
                          <strong>{partner.full_name || partner.email}</strong>
                          <span className="dim" style={{ fontSize: 12 }}>{partner.email}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <select
                        className="select"
                        style={{ width: 'auto' }}
                        value={partner.role}
                        disabled={busy === partner.id}
                        onChange={(event) => update(partner, { role: event.target.value })}
                      >
                        <option value="partner">Partner</option>
                        <option value="owner">Owner</option>
                      </select>
                    </td>
                    <td className="muted">{partner.accounts}</td>
                    <td className="muted nowrap">
                      {partner.last_login_at ? fmtAgo(partner.last_login_at) : 'never'}
                      {partner.must_change_password ? (
                        <>
                          {' '}
                          <Pill tone="wait">Temporary password</Pill>
                        </>
                      ) : null}
                    </td>
                    <td className="right">
                      {partner.id === me.id ? (
                        // Your own name and password are changed where the
                        // current password is asked for, not from a list.
                        <Link className="btn btn-sm" to="/settings">
                          Your settings
                        </Link>
                      ) : (
                        <div className="row-actions">
                          <button
                            type="button"
                            className="btn btn-sm"
                            disabled={busy === partner.id}
                            onClick={() => setRenaming(partner)}
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm"
                            disabled={busy === partner.id}
                            onClick={() => setResetting(partner)}
                          >
                            Reset password
                          </button>
                          <button
                            type="button"
                            className={`btn btn-sm${partner.is_active ? '' : ' btn-primary'}`}
                            disabled={busy === partner.id}
                            onClick={() => update(partner, { is_active: !partner.is_active })}
                          >
                            {partner.is_active ? 'Switch off' : 'Switch on'}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {adding ? (
        <AddPartner
          onClose={() => setAdding(false)}
          onCreated={(created) => {
            setAdding(false);
            setIssued(created);
            partners.reload();
          }}
        />
      ) : null}

      {renaming ? (
        <RenamePartner
          partner={renaming}
          onClose={() => setRenaming(null)}
          onSaved={() => {
            setRenaming(null);
            partners.reload();
            toast('Saved.');
          }}
        />
      ) : null}

      {resetting ? (
        <ResetPassword
          partner={resetting}
          onClose={() => setResetting(null)}
          onReset={(created) => {
            setResetting(null);
            setIssued(created);
            partners.reload();
          }}
        />
      ) : null}

      {issued ? <ShowSecret created={issued} onClose={() => setIssued(null)} /> : null}
    </>
  );
}

function RenamePartner({ partner, onClose, onSaved }) {
  const [name, setName] = useState(partner.full_name || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api(`/partners/${partner.id}`, { method: 'PATCH', body: { full_name: name } });
      onSaved();
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Rename"
      subtitle={partner.email}
      onClose={onClose}
      footer={
        <>
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" type="submit" form="rename-partner" disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            Save
          </button>
        </>
      }
    >
      <form id="rename-partner" onSubmit={submit} style={{ display: 'grid', gap: 14 }}>
        <Field label="Full name" hint="Leave empty to show the email instead.">
          <input
            className="input"
            autoFocus
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        {error ? <p className="error-text">{error}</p> : null}
      </form>
    </Modal>
  );
}

/**
 * Asked before, never after: the moment this is confirmed the old password
 * stops working, and the person it belongs to may be mid-call with a customer.
 */
function ResetPassword({ partner, onClose, onReset }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function confirm() {
    setBusy(true);
    setError('');
    try {
      onReset(await api(`/partners/${partner.id}/reset-password`, { method: 'POST' }));
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Reset password"
      subtitle={`For ${partner.full_name || partner.email}.`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" type="button" disabled={busy} onClick={confirm}>
            {busy ? <span className="spinner" /> : null}
            Reset password
          </button>
        </>
      }
    >
      <p style={{ margin: 0 }}>
        Their current password stops working straight away. You get a temporary one to
        pass on, shown once, and they must replace it when they next sign in.
      </p>
      {error ? <p className="error-text">{error}</p> : null}
    </Modal>
  );
}

function AddPartner({ onClose, onCreated }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('partner');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onCreated(
        await api('/partners', {
          method: 'POST',
          body: { email, full_name: name || null, role },
        }),
      );
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Add someone to the portal"
      subtitle="They get a temporary password, shown once, that they must replace on first sign-in."
      onClose={onClose}
      footer={
        <>
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" type="submit" form="add-partner" disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            Create
          </button>
        </>
      }
    >
      <form id="add-partner" onSubmit={submit} style={{ display: 'grid', gap: 14 }}>
        <Field label="Email">
          <input
            className="input"
            type="email"
            required
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        <Field label="Full name">
          <input
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field
          label="Role"
          hint="An owner sees every account and can add portal people. A partner sees only their own."
        >
          <select
            className="select"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            <option value="partner">Partner</option>
            <option value="owner">Owner</option>
          </select>
        </Field>
        {error ? <p className="error-text">{error}</p> : null}
      </form>
    </Modal>
  );
}

/** The one and only time the temporary password is visible. */
function ShowSecret({ created, onClose }) {
  const toast = useToast();
  return (
    <Modal
      title="Temporary password"
      subtitle={`For ${created.partner.email}. This is the only time it is shown.`}
      onClose={onClose}
      footer={
        <>
          <button
            className="btn"
            type="button"
            onClick={() => {
              navigator.clipboard
                .writeText(created.temporary_password)
                .then(() => toast('Copied.'))
                .catch(() => toast('The browser would not allow copying.', 'bad'));
            }}
          >
            Copy
          </button>
          <button className="btn btn-primary" type="button" onClick={onClose}>
            Done
          </button>
        </>
      }
    >
      <div className="secret">{created.temporary_password}</div>
      <p className="hint" style={{ margin: 0 }}>
        Send it over a channel you trust. They will be made to replace it before they can
        do anything.
      </p>
    </Modal>
  );
}
