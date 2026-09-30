import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { DailyBars, ShareBars } from '../components/charts.jsx';
import { Empty, Loading, Segmented, StatusPill, useLoad } from '../components/ui.jsx';
import { fmtAgo, fmtCount, fmtDate, platformName } from '../format.js';
import './usage.css';

const WINDOWS = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
];

/**
 * Who is actually using the product.
 *
 * Built for two conversations: a partner deciding who to ring this week (the
 * quiet list), and a renewal ("your team opened the app on 22 of the last 30
 * days"). Counts of what people did, never what they saw -- the API behind
 * this cannot return a figure from anybody's books.
 */
export default function Usage({ me }) {
  const [days, setDays] = useState(30);
  const usage = useLoad((signal) => api(`/usage?days=${days}`, { signal }), [days]);
  const data = usage.data;

  return (
    <>
      <div className="page-head">
        <h1>Usage</h1>
        <p className="muted">
          {me.role === 'owner'
            ? 'How much every account on the platform uses TallyFlow.'
            : 'How much the accounts you look after use TallyFlow.'}
        </p>
      </div>

      {/* One filter, above everything it scopes. */}
      <div className="row">
        <Segmented options={WINDOWS} value={days} onChange={setDays} ariaLabel="Period" />
        {usage.loading && data ? <span className="spinner" aria-label="Refreshing" /> : null}
      </div>

      {usage.loading && !data ? (
        <div className="card"><Loading /></div>
      ) : usage.error ? (
        <div className="card">
          <Empty title="Could not load usage">{usage.error.message}</Empty>
        </div>
      ) : (
        // Held at reduced opacity while a new window loads, rather than
        // replaced by a skeleton: the frame stays, the numbers change.
        <div className={`usage-body${usage.loading ? ' is-stale' : ''}`}>
          <Headline headline={data.headline} />
          <TrackingNote since={data.headline.tracking_since} days={days} />

          <section className="card card-pad">
            <div className="row-between usage-card-head">
              <div className="stack">
                <h2>People active each day</h2>
                <span className="muted usage-sub">
                  Anyone who opened a screen, a report or made an entry that day.
                </span>
              </div>
            </div>
            <DailyBars
              rows={data.series}
              field="active_users"
              unit="people active"
              detail={[
                { field: 'active_accounts', label: 'accounts' },
                { field: 'dashboard_views', label: 'dashboard opens' },
                { field: 'report_views', label: 'reports opened' },
                { field: 'entries_created', label: 'entries made' },
              ]}
            />
          </section>

          <div className="usage-split">
            <AccountTable
              title="Most active accounts"
              subtitle={`By actions in the last ${days} days.`}
              rows={data.top_accounts}
              empty="Nobody has used the app in this period."
              days={days}
            />
            <Versions versions={data.versions} />
          </div>

          <AccountTable
            title="Gone quiet"
            subtitle="Live accounts nobody has opened for a week or more. Worth a call before renewal, not after."
            rows={data.quiet_accounts}
            empty="Every live account has been used in the last week."
            days={days}
            quiet
          />
        </div>
      )}
    </>
  );
}

function Headline({ headline }) {
  const share = headline.people
    ? Math.round((headline.active_30d / headline.people) * 100)
    : 0;
  return (
    <div className="stat-grid">
      <Tile value={headline.active_today} label="Active today" />
      <Tile value={headline.active_7d} label="Active in 7 days" />
      <Tile
        value={headline.active_30d}
        label="Active in 30 days"
        note={headline.people ? `${share}% of ${fmtCount(headline.people)} people` : null}
      />
      <Tile value={headline.report_views} label="Reports opened" note="In this period" />
      <Tile value={headline.entries_created} label="Entries made" note="In this period" />
      <Tile value={headline.logins} label="Sign-ins" note="In this period" />
    </div>
  );
}

function Tile({ value, label, note }) {
  return (
    <div className="stat card">
      <span className="stat-value">{fmtCount(value)}</span>
      <span className="stat-label">{label}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </div>
  );
}

/**
 * Counting started when this build was deployed. Without saying so, a chart
 * that is flat until last Tuesday reads as a collapse in usage.
 */
function TrackingNote({ since, days }) {
  if (!since) {
    return (
      <p className="usage-note">
        Usage is counted from now on. Nothing has been recorded yet.
      </p>
    );
  }
  const start = new Date();
  start.setDate(start.getDate() - (days - 1));
  if (new Date(since) <= start) return null;
  return (
    <p className="usage-note">
      Usage has been counted since {fmtDate(since)}. Days before that show as zero.
    </p>
  );
}

function AccountTable({ title, subtitle, rows, empty, days, quiet = false }) {
  return (
    <section className="card">
      <div className="card-pad stack">
        <h2>{title}</h2>
        <span className="muted usage-sub">{subtitle}</span>
      </div>
      {rows.length === 0 ? (
        <Empty title={empty} />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Account</th>
                <th className="right">{quiet ? 'People' : 'People active'}</th>
                {quiet ? null : <th className="right">Days used</th>}
                {quiet ? null : <th className="right">Reports</th>}
                {quiet ? null : <th className="right">Entries</th>}
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((account) => (
                <tr key={account.id}>
                  <td>
                    <div className="row">
                      <Link className="usage-account" to={`/accounts?open=${account.id}`}>
                        {account.name}
                      </Link>
                      {quiet ? null : <StatusPill account={account} />}
                    </div>
                  </td>
                  <td className="right">
                    {/* On the quiet list "active" would count days before the
                        silence and contradict the heading, so it is headcount. */}
                    {quiet ? (
                      fmtCount(account.people)
                    ) : (
                      <>
                        {fmtCount(account.active_users)}
                        <span className="dim"> of {fmtCount(account.people)}</span>
                      </>
                    )}
                  </td>
                  {quiet ? null : (
                    <td className="right">
                      {fmtCount(account.active_days)}
                      <span className="dim"> of {days}</span>
                    </td>
                  )}
                  {quiet ? null : <td className="right">{fmtCount(account.report_views)}</td>}
                  {quiet ? null : <td className="right">{fmtCount(account.entries_created)}</td>}
                  <td className="nowrap muted">
                    {account.last_seen_at ? fmtAgo(account.last_seen_at) : 'Never opened'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Versions({ versions }) {
  return (
    <section className="card card-pad usage-versions">
      <div className="stack usage-card-head">
        <h2>App versions</h2>
        <span className="muted usage-sub">
          The build each person used last, over the past week.
        </span>
      </div>
      {versions.length === 0 ? (
        <Empty title="No app has reported its version yet" />
      ) : (
        <ShareBars
          rows={versions.map((v) => ({
            key: `${v.version}-${v.platform || ''}`,
            label: `${v.version}${v.platform ? ` · ${platformName(v.platform)}` : ''}`,
            value: v.users,
          }))}
        />
      )}
    </section>
  );
}
