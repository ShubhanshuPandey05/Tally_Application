import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import PeopleUsage from '../components/PeopleUsage.jsx';
import { Field, Modal, Pill, StatusPill, useLoad, useToast } from '../components/ui.jsx';
import {
  fmtAgo,
  fmtDate,
  fmtDateTime,
  fmtRupees,
  fromDateInput,
  initials,
  toDateInput,
} from '../format.js';
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
  // A trial is live, but nobody has chosen its plan yet -- approving it is
  // the next step, exactly as for a signup still waiting.
  const pending = account.status !== 'active' || account.is_trial;

  const plans = useLoad((signal) => api('/plans', { signal }), []);
  // No plan is preselected: which plan was sold is the one thing the person
  // approving must say out loud, and a default would get approved by reflex.
  const [plan, setPlan] = useState('');
  const [renewing, setRenewing] = useState(false);

  const [maxUsers, setMaxUsers] = useState(account.max_users || 5);
  const [maxCompanies, setMaxCompanies] = useState(account.max_companies || 3);
  // A trial's end date is the trial's, not the plan's: carried into the form
  // it would end the paid plan on the day the trial would have.
  const [expires, setExpires] = useState(
    account.is_trial ? '' : toDateInput(account.expires_at),
  );
  const [partnerId, setPartnerId] = useState(account.partner_id || (owner ? '' : me.id));
  const [notes, setNotes] = useState(account.notes || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState('');

  function choosePlan(chosen) {
    setPlan(chosen.code);
    // Suggested, not imposed: the plan sets the price, the person approving
    // still decides the ceilings.
    setMaxUsers(chosen.suggested_users);
    setMaxCompanies(chosen.suggested_companies);
  }

  async function save(approving) {
    if (approving && !plan) {
      setError('Choose the plan that was sold.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const body = {
        max_users: Number(maxUsers),
        max_companies: Number(maxCompanies),
        partner_id: partnerId || null,
        notes,
      };
      // Moving the account between partners is its own audited action, so a
      // transfer -- including back to unassigned -- is never a side effect
      // of saving the limits.
      if (owner && !approving && (partnerId || null) !== (account.partner_id || null)) {
        await api(`/accounts/${account.id}/transfer`, {
          method: 'POST',
          body: { partner_id: partnerId || null },
        });
      }
      const updated = approving
        ? await api(`/accounts/${account.id}/approve`, {
            method: 'POST',
            body: { ...body, plan },
          })
        : await api(`/accounts/${account.id}`, {
            method: 'PATCH',
            // A nullable field cannot say "set this back to nothing" and "leave
            // it alone" at once, so clearing the date needs its own flag.
            body: {
              ...body,
              partner_id: null,
              expires_at: fromDateInput(expires),
              clear_expiry: !expires,
            },
          });
      onChanged(updated);
      toast(
        approving
          ? account.is_trial
            ? `${updated.name} is now on a plan.`
            : `${updated.name} is now active.`
          : 'Saved.',
      );
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

  async function renew(chosen) {
    setBusy(true);
    try {
      const updated = await api(`/accounts/${account.id}/renew`, {
        method: 'POST',
        body: { plan: chosen },
      });
      onChanged(updated);
      toast(`Renewed until ${fmtDate(updated.expires_at)}.`);
      setRenewing(false);
    } catch (failure) {
      toast(failure.message, 'bad');
    } finally {
      setBusy(false);
    }
  }

  if (renewing) {
    return (
      <RenewPlan
        account={account}
        plans={plans.data || []}
        busy={busy}
        onCancel={() => setRenewing(false)}
        onConfirm={renew}
      />
    );
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
          {account.status === 'active' && !account.is_trial ? (
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() => setRenewing(true)}
            >
              Renew
            </button>
          ) : null}
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
            {account.is_trial
              ? 'Approve plan'
              : pending
                ? 'Approve and activate'
                : 'Save changes'}
          </button>
        </>
      }
    >
      <div className="row wrap" style={{ gap: 8 }}>
        <StatusPill account={account} />
        {account.plan_name ? <Pill tone="ok">{account.plan_name} plan</Pill> : null}
        {account.partner_name ? (
          <Pill tone="accent">{account.partner_name}</Pill>
        ) : (
          <Pill tone="wait">No partner</Pill>
        )}
        <span className="grow" />
        <Link className="btn btn-sm" to={`/logs/connector?org=${account.id}`}>
          Connector logs
        </Link>
        <Link className="btn btn-sm" to={`/activity?org=${account.id}`}>
          Activity
        </Link>
      </div>

      {account.is_trial ? (
        <p className="hint" style={{ margin: 0 }}>
          {account.trial_started_at
            ? `On the free trial since they linked their first company on ${fmtDate(
                account.trial_started_at,
              )}. ${account.is_expired ? 'It ended' : 'It ends'} on ${fmtDate(
                account.expires_at,
              )}.`
            : 'On the free trial. Its days start counting when they link their first company.'}{' '}
          Approving a plan ends the trial.
        </p>
      ) : pending ? (
        <p className="hint" style={{ margin: 0 }}>
          Until this is approved the business can add no Tally PC, company or
          colleague — both ceilings are zero. Meanwhile its people can sign in and
          explore the demo company.
        </p>
      ) : null}

      {pending ? (
        <div>
          <h3 className="label" style={{ marginBottom: 6 }}>Plan sold</h3>
          <PlanPicker plans={plans.data || []} value={plan} onChange={choosePlan} />
          <p className="hint" style={{ margin: '6px 0 0' }}>
            Runs for one year from today. Choosing a plan suggests the limits at the bottom.
          </p>
        </div>
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
                <span className="dim" style={{ fontSize: 12 }}>
                  {admin.email}
                  {admin.phone ? (
                    <>
                      {' · '}
                      <a href={`tel:${admin.phone}`}>{admin.phone}</a>
                    </>
                  ) : null}
                </span>
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

      <PeopleUsage accountId={account.id} />

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
        {pending ? null : (
          <Field label="Expires" hint="Set by the plan. Change only to correct a mistake.">
            <input
              className="input"
              type="date"
              value={expires}
              onChange={(event) => setExpires(event.target.value)}
            />
          </Field>
        )}
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

/** The two plans, as cards: name, price, GST and what is paid in all. */
export function PlanPicker({ plans, value, onChange }) {
  if (!plans.length) return <p className="muted" style={{ margin: 0 }}>Loading plans…</p>;
  return (
    <div className="plan-grid" role="radiogroup" aria-label="Plan">
      {plans.map((plan) => (
        <button
          key={plan.code}
          type="button"
          role="radio"
          aria-checked={value === plan.code}
          className={`plan-card${value === plan.code ? ' is-selected' : ''}`}
          onClick={() => onChange(plan)}
        >
          <strong>{plan.name}</strong>
          <span className="plan-price">
            {fmtRupees(plan.price_paise)} <span className="dim">+ 18% GST</span>
          </span>
          <span className="dim" style={{ fontSize: 12 }}>
            {fmtRupees(plan.total_paise)} a year in all
          </span>
        </button>
      ))}
    </div>
  );
}

function RenewPlan({ account, plans, busy, onCancel, onConfirm }) {
  const [plan, setPlan] = useState(account.plan || '');
  const ends = account.expires_at ? new Date(account.expires_at) : null;
  const running = ends && ends > new Date();
  return (
    <Modal
      title={`Renew ${account.name}`}
      subtitle={
        running
          ? `The new year starts when the current one ends, on ${fmtDate(account.expires_at)}.`
          : 'The plan has run out, so the new year starts today.'
      }
      onClose={onCancel}
      footer={
        <>
          <button className="btn" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            type="button"
            disabled={busy || !plan}
            onClick={() => onConfirm(plan)}
          >
            {busy ? <span className="spinner" /> : null}
            Renew for a year
          </button>
        </>
      }
    >
      <PlanPicker plans={plans} value={plan} onChange={(chosen) => setPlan(chosen.code)} />
      <p className="hint" style={{ margin: 0 }}>
        Counted as a renewal in the sales report, credited to{' '}
        {account.partner_name || 'nobody (direct)'}.
      </p>
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
