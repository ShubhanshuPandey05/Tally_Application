import { useState } from 'react';
import { api } from '../api.js';
import { fmtAgo, fmtCount, fmtDate, initials, platformName } from '../format.js';
import { DailyBars, Spark } from './charts.jsx';
import { Empty, Loading, Pill, Segmented, useLoad } from './ui.jsx';
import './people.css';

const WINDOWS = [
  { value: 7, label: '7d' },
  { value: 30, label: '30d' },
  { value: 90, label: '90d' },
];

/**
 * One account, person by person: who comes back, how often, and on what.
 *
 * The question this answers on a support call is "is it everyone, or just
 * them?", and on a renewal "who actually uses it?". Both are about people
 * rather than the account's total, which is why this lists every member --
 * including the ones who never opened the app, who are the finding.
 */
export default function PeopleUsage({ accountId }) {
  const [days, setDays] = useState(30);
  const usage = useLoad(
    (signal) => api(`/accounts/${accountId}/usage?days=${days}`, { signal }),
    [accountId, days],
  );
  const data = usage.data;

  return (
    <div className="people-usage">
      <div className="row-between">
        <h3 className="label">People and activity</h3>
        <Segmented options={WINDOWS} value={days} onChange={setDays} ariaLabel="Period" />
      </div>

      {usage.loading && !data ? (
        <Loading />
      ) : usage.error ? (
        <Empty title="Could not load activity">{usage.error.message}</Empty>
      ) : (
        <div className={usage.loading ? 'is-stale' : ''}>
          <DailyBars
            rows={data.series}
            field="events"
            unit="actions"
            height={92}
            detail={[
              { field: 'active_users', label: 'people' },
              { field: 'report_views', label: 'reports opened' },
              { field: 'entries_created', label: 'entries made' },
            ]}
          />
          {data.people.length === 0 ? (
            <Empty title="Nobody has joined this account yet" />
          ) : (
            <ul className="people">
              {data.people.map((person) => (
                <Person key={person.id} person={person} days={days} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Person({ person, days }) {
  const [open, setOpen] = useState(false);
  const quiet = person.active_days === 0;
  return (
    <li className={`person${quiet ? ' is-quiet' : ''}`}>
      <div className="person-head">
        <span className="avatar">{initials(person.full_name, person.email)}</span>
        <div className="stack grow">
          <div className="row" style={{ gap: 6 }}>
            <strong className="truncate">{person.full_name || person.email}</strong>
            <Pill tone={person.role === 'admin' ? 'accent' : 'off'} plain>
              {person.role === 'admin' ? 'Admin' : 'Staff'}
            </Pill>
            {person.is_active ? null : <Pill tone="stop" plain>Disabled</Pill>}
          </div>
          <span className="dim truncate person-sub">
            {person.email}
            {' · '}
            {person.last_seen_at ? `seen ${fmtAgo(person.last_seen_at)}` : 'never opened the app'}
          </span>
        </div>
        <Spark values={person.daily} days={days} />
      </div>

      <dl className="person-facts">
        <Fact label="Days used" value={`${person.active_days} of ${days}`} />
        <Fact label="Dashboard" value={fmtCount(person.dashboard_views)} />
        <Fact label="Reports" value={fmtCount(person.report_views)} />
        <Fact label="Entries" value={fmtCount(person.entries_created)} />
        <Fact label="Sign-ins" value={fmtCount(person.logins)} />
        <Fact
          label="App"
          value={
            person.app_version
              ? `${person.app_version}${person.platform ? ` · ${platformName(person.platform)}` : ''}`
              : '—'
          }
        />
      </dl>

      {person.devices.length ? (
        <div className="person-devices">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? '▾' : '▸'} Signed in on {person.devices.length} device
            {person.devices.length === 1 ? '' : 's'}
          </button>
          {open ? (
            <ul className="devices">
              {person.devices.map((device, i) => (
                <li key={i}>
                  <span className="grow truncate">
                    {device.device_name || describeAgent(device.user_agent)}
                  </span>
                  <span className="dim nowrap">
                    since {fmtDate(device.signed_in_at)}
                    {device.last_used_at ? ` · used ${fmtAgo(device.last_used_at)}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <p className="dim person-devices-none">Not signed in anywhere right now.</p>
      )}
    </li>
  );
}

function Fact({ label, value }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** A readable device name from a user agent, for sessions that sent none. */
function describeAgent(agent) {
  if (!agent) return 'Unknown device';
  if (/android/i.test(agent)) return 'Android phone';
  if (/iphone|ipad/i.test(agent)) return 'iPhone or iPad';
  if (/windows/i.test(agent)) return 'Windows PC';
  if (/mac os/i.test(agent)) return 'Mac';
  if (/dart/i.test(agent)) return 'TallyFlow app';
  return 'Browser';
}
