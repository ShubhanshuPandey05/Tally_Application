import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { Field, Modal, Pill, StatusPill, useToast } from '../components/ui.jsx';
import { fmtAgo, fmtDate, fmtDateTime, fromDateInput, initials, toDateInput } from '../format.js';
import './accounts.css';

/**
 * One account, and the decisions that can be made about it.
 *
 * The form insists on both ceilings for an approval because they *are* the
 * decision -- see `ApproveAccountRequest`. Prefilled from the deployment's
 * suggested defaults so the common case is fast, but never submitted without
 * having been looked at.
 *
 * Approval is also the way back from suspended and rejected, deliberately: a
 * reinstatement is the same decision as the original one and has to restate the
 * numbers rather than silently resurrect whatever they used to be.
 */
export default function AccountDrawer({ account, me, partners, onClose, onChanged }) {
  const toast = useToast();
  const owner = me.role === 'owner';
  const pending = account.status !== 'active';

  const [maxUsers, setMaxUsers] = useState(account.max_users || 5);
  const [maxCompanies, setMaxCompanies] = useState(account.max_companies || 3);
  const [expires, setExpires] = useState(toDateInput(account.expires_at));
  const [partnerId, setPartnerId] = useState(account.partner_id || (owner ? '' : me.id));
  const [notes, setNotes] = useState(account.notes || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState('');

  async function save(approving) {
    setBusy(true);
    setError('');
    try {
      const body = {
        max_users: Number(maxUsers),
        max_companies: Number(maxCompanies),
        expires_at: fromDateInput(expires),
        partner_id: partnerId || null,
        notes,
      };
      const updated = approving
        ? await api(`/accounts/${account.id}/approve`, { method: 'POST', body })
        : await api(`/accounts/${account.id}`, {
            method: 'PATCH',
            // A nullable field cannot say "set this back to nothing" and "leave
            // it alone" at once, so clearing the date needs its own flag.
            body: { ...body, clear_expiry: !expires },
          });
      onChanged(updated);
      toast(approving ? `${updated.name} is now active.` : 'Saved.');
      if (approving) onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(action, reason) {
    setBusy(true);
    try {
      const updated = await api(`/accounts/${account.id}/${action}`, {
        method: 'POST',
        body: { reason: reason || null },
      });
      onChanged(updated);
      toast(action === 'suspend' ? 'Account suspended.' : 'Signup rejected.');
      setConfirming('');
      onClose();
    } catch (failure) {
      toast(failure.message, 'bad');
    } finally {
      setBusy(false);
    }
  }

  if (confirming) {
    return (
      <ConfirmStatus
        action={confirming}
        account={account}
        busy={busy}
        onCancel={() => setConfirming('')}
        onConfirm={(reason) => changeStatus(confirming, reason)}
      />
    );
  }

  return (
    <Modal
      wide
      title={account.name}
      subtitle={`Signed up ${fmtDate(account.created_at)} · ${fmtAgo(account.created_at)}`}
      onClose={onClose}
      footer={
        <>
          {account.status === 'pending' ? (
            <button
              className="btn btn-danger"
              type="button"
              disabled={busy}
              onClick={() => setConfirming('reject')}
            >
              Reject
            </button>
          ) : null}
          {account.status === 'active' ? (
            <button
              className="btn btn-danger"
              type="button"
              disabled={busy}
              onClick={() => setConfirming('suspend')}
            >
              Suspend
            </button>
          ) : null}
          <span className="grow" />
          <button className="btn" type="button" onClick={onClose}>
            Close
          </button>
          <button
            className="btn btn-primary"
            type="button"
            disabled={busy}
            onClick={() => save(pending)}
          >
            {busy ? <span className="spinner" /> : null}
            {pending ? 'Approve and activate' : 'Save changes'}
          </button>
        </>
      }
    >
      <div className="row wrap" style={{ gap: 8 }}>
        <StatusPill account={account} />
        {account.partner_name ? <Pill tone="accent">{account.partner_name}</Pill> : null}
        <span className="grow" />
        <Link className="btn btn-sm" to={`/logs/connector?org=${account.id}`}>
          Connector logs
        </Link>
        <Link className="btn btn-sm" to={`/activity?org=${account.id}`}>
          Activity
        </Link>
      </div>

      {pending ? (
        <p className="hint" style={{ margin: 0 }}>
          Until this is approved the business has no users and no companies — both
          ceilings are zero. It can sign in and see nothing, which is a better first
          launch than an error screen.
        </p>
      ) : null}

      <div className="drawer-grid">
        <div className="fact">
          <span className="fact-label">Users</span>
          <span className="fact-value">
            {account.users_used} of {account.max_users}
          </span>
        </div>
        <div className="fact">
          <span className="fact-label">Companies</span>
          <span className="fact-value">
            {account.companies_used} of {account.max_companies}
          </span>
        </div>
        <div className="fact">
          <span className="fact-label">Tally PCs</span>
          <span className="fact-value">
            {account.connectors}
            {account.connectors_online > 0 ? (
              <span className="dim"> · one online</span>
            ) : account.connectors ? (
              <span className="dim"> · none online</span>
            ) : null}
          </span>
        </div>
        <div className="fact">
          <span className="fact-label">Approved</span>
          <span className="fact-value">
            {account.approved_at ? fmtDate(account.approved_at) : '—'}
            {account.approved_by_name ? (
              <span className="dim"> · {account.approved_by_name}</span>
            ) : null}
          </span>
        </div>
      </div>

      <div>
        <h3 className="label" style={{ marginBottom: 6 }}>Who to ring</h3>
        {account.admins.length ? (
          account.admins.map((admin) => (
            <div className="admin-row" key={admin.id}>
              <span className="avatar">{initials(admin.full_name, admin.email)}</span>
              <div className="stack grow">
                <strong style={{ fontSize: 13 }}>{admin.full_name || admin.email}</strong>
                <span className="dim" style={{ fontSize: 12 }}>{admin.email}</span>
              </div>
              <span className="dim nowrap" style={{ fontSize: 12 }}>
                {admin.last_login_at ? `last in ${fmtAgo(admin.last_login_at)}` : 'never signed in'}
              </span>
            </div>
          ))
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            Nobody has registered as an admin on this account yet.
          </p>
        )}
      </div>

      <hr className="divider" />

      <div className="drawer-grid">
        <Field label="Max users" hint="How many people may sign in.">
          <input
            className="input"
            type="number"
            min="1"
            max="10000"
            value={maxUsers}
            onChange={(event) => setMaxUsers(event.target.value)}
          />
        </Field>
        <Field label="Max companies" hint="Sets of books they may link.">
          <input
            className="input"
            type="number"
            min="1"
            max="10000"
            value={maxCompanies}
            onChange={(event) => setMaxCompanies(event.target.value)}
          />
        </Field>
        <Field label="Expires" hint="Leave empty for open-ended.">
          <input
            className="input"
            type="date"
            value={expires}
            onChange={(event) => setExpires(event.target.value)}
          />
        </Field>
        <Field
          label="Owned by"
          hint={owner ? 'Which partner handles this relationship.' : 'Assigned to you.'}
        >
          <select
            className="select"
            value={partnerId}
            disabled={!owner}
            onChange={(event) => setPartnerId(event.target.value)}
          >
            <option value="">Unassigned</option>
            {(owner ? partners : [me]).map((partner) => (
              <option key={partner.id} value={partner.id}>
                {partner.full_name || partner.email}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field label="Notes" hint="Internal. Suspensions append their reason here.">
        <textarea
          className="textarea"
          value={notes}
          maxLength={4000}
          onChange={(event) => setNotes(event.target.value)}
        />
      </Field>

      {error ? <p className="error-text">{error}</p> : null}
    </Modal>
  );
}

/**
 * A second step before switching an account off.
 *
 * The reason is asked for rather than optional-in-practice because it is
 * appended to the notes and read months later, when the same account is
 * suspended again and nobody remembers March.
 */
function ConfirmStatus({ action, account, busy, onCancel, onConfirm }) {
  const [reason, setReason] = useState('');
  const suspending = action === 'suspend';

  return (
    <Modal
      title={suspending ? `Suspend ${account.name}?` : `Reject ${account.name}?`}
      onClose={onCancel}
      footer={
        <>
          <button className="btn" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn btn-danger"
            type="button"
            disabled={busy}
            onClick={() => onConfirm(reason)}
          >
            {busy ? <span className="spinner" /> : null}
            {suspending ? 'Suspend' : 'Reject'}
          </button>
        </>
      }
    >
      <p className="muted" style={{ margin: 0 }}>
        {suspending
          ? 'Everything stays exactly where it is — connectors, companies, snapshots and '
            + 'people. Nobody can read their data until it is reinstated, and reinstating '
            + 'is one decision rather than a fresh onboarding.'
          : 'Use this for a duplicate or a test signup. An account that was a real '
            + 'customer should be suspended instead, so its history is not written off.'}
      </p>
      <Field label="Reason" hint="Appended to the account's notes with today's date.">
        <textarea
          className="textarea"
          autoFocus
          value={reason}
          maxLength={1000}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
      {account.notes ? (
        <div>
          <span className="label">Existing notes</span>
          <div className="notes-log">{account.notes}</div>
        </div>
      ) : null}
      <p className="hint" style={{ margin: 0 }}>
        Last changed {fmtDateTime(account.approved_at) || 'never'}.
      </p>
    </Modal>
  );
}
