import { useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api.js';
import { DailyBars } from '../components/charts.jsx';
import { Empty, Loading, StatusPill, useLoad } from '../components/ui.jsx';
import { fmtAgo, fmtCount, fmtDate } from '../format.js';
import './overview.css';

/** The window the home screen's usage figures cover. The Usage page can change
    it; here it is fixed, so the same glance means the same thing every day. */
const USAGE_DAYS = 30;

/**
 * The landing screen: is there work, and is the fleet up.
 *
 * The pending queue is the whole point of the page, so it is a list of real
 * accounts you can act on rather than a number you then have to go and find.
 * Everything else on this screen is context for that decision.
 */
export default function Overview({ me, onCounts }) {
  const navigate = useNavigate();
  const stats = useLoad((signal) => api('/stats', { signal }), []);
  const queue = useLoad(
    (signal) => api('/accounts?status=pending&limit=8', { signal }),
    [],
  );

  // Its own request, and its own failure: the queue above it is what the page
  // is for, and a slow usage read must not hold a signup back from being seen.
  const usage = useLoad((signal) => api(`/usage?days=${USAGE_DAYS}`, { signal }), []);

  const data = stats.data;
  useEffect(() => {
    if (data) onCounts(data.pending || 0);
  }, [data, onCounts]);

  return (
    <>
      <div className="page-head">
        <h1>Overview</h1>
        <p className="muted">
          {me.role === 'owner'
            ? 'Every account on the platform.'
            : 'The accounts you are responsible for.'}
        </p>
      </div>

      {stats.loading && !data ? (
        <div className="card"><Loading /></div>
      ) : stats.error ? (
        <div className="card"><Empty title="Could not load the counters">{stats.error.message}</Empty></div>
      ) : (
        <div className="stat-grid">
          <Stat
            tone="wait"
            value={data.pending}
            label="Awaiting approval"
            note={data.pending ? 'Needs a decision' : 'Nothing waiting'}
            to="/accounts?status=pending"
          />
          <Stat tone="ok" value={data.active} label="Active" to="/accounts?status=active" />
          <Stat
            tone={data.expired ? 'stop' : 'plain'}
            value={data.expired}
            label="Expired"
            note={data.expired ? 'Term ran out' : null}
            to="/accounts?status=active"
          />
          <Stat
            tone={data.suspended ? 'stop' : 'plain'}
            value={data.suspended}
            label="Suspended"
            to="/accounts?status=suspended"
          />
          <Stat tone="plain" value={data.companies} label="Companies linked" />
          <Stat
            tone="plain"
            value={data.connectors_online}
            label="Connectors online"
            note="Right now"
          />
        </div>
      )}

      <section className="card">
        <div className="card-pad row-between">
          <div className="stack">
            <h2>Waiting on a decision</h2>
            <span className="muted" style={{ fontSize: 13 }}>
              A business that has signed up and can do nothing until somebody says what
              it covers.
            </span>
          </div>
          <Link className="btn btn-sm" to="/accounts?status=pending">
            See all
          </Link>
        </div>
        <hr className="divider" />

        {queue.loading ? (
          <Loading />
        ) : queue.error ? (
          <Empty title="Could not load the queue">{queue.error.message}</Empty>
        ) : !queue.data?.length ? (
          <Empty title="Nothing waiting">
            Every signup has been decided. New ones will appear here.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Business</th>
                  <th>Admin</th>
                  <th>Signed up</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {queue.data.map((account) => (
                  <tr
                    key={account.id}
                    className="clickable"
                    onClick={() => navigate(`/accounts?open=${account.id}`)}
                  >
                    <td>
                      <div className="stack">
                        <strong>{account.name}</strong>
                        <StatusPill account={account} />
                      </div>
                    </td>
                    <td className="muted">
                      {account.admins[0]
                        ? account.admins[0].full_name || account.admins[0].email
                        : '—'}
                    </td>
                    <td className="muted nowrap">
                      {fmtDate(account.created_at)}
                      <span className="dim"> · {fmtAgo(account.created_at)}</span>
                    </td>
                    <td className="right">
                      <span className="btn btn-sm btn-primary">Review</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <UsageGlance usage={usage} />
    </>
  );
}

/**
 * Is the product being used -- the second thing worth knowing on arrival.
 *
 * A glance, not the Usage page: one row of counts, one chart, and the accounts
 * doing the most. Everything that needs a filter or a second look lives behind
 * the link. Counts of what people did, never what they saw.
 */
function UsageGlance({ usage }) {
  const data = usage.data;
  return (
    <section className="card">
      <div className="card-pad row-between">
        <div className="stack">
          <h2>Usage</h2>
          <span className="muted" style={{ fontSize: 13 }}>
            Who has been opening the app over the last {USAGE_DAYS} days.
          </span>
        </div>
        <Link className="btn btn-sm" to="/usage">
          Open usage
        </Link>
      </div>
      <hr className="divider" />

      {usage.loading && !data ? (
        <Loading />
      ) : usage.error ? (
        <Empty title="Could not load usage">{usage.error.message}</Empty>
      ) : (
        <div className="card-pad glance">
          <div className="glance-figures">
            <Figure value={data.headline.active_today} label="Active today" />
            <Figure value={data.headline.active_7d} label="Active in 7 days" />
            <Figure
              value={data.headline.active_30d}
              label="Active in 30 days"
              note={
                data.headline.people
                  ? `of ${fmtCount(data.headline.people)} people`
                  : null
              }
            />
            <Figure value={data.headline.report_views} label="Reports opened" />
            <Figure value={data.headline.entries_created} label="Entries made" />
            <Figure value={data.headline.logins} label="Sign-ins" />
          </div>

          <div className="glance-split">
            <div className="stack" style={{ gap: 12 }}>
              <h3>People active each day</h3>
              <DailyBars
                rows={data.series}
                field="active_users"
                unit="people active"
                height={140}
                detail={[
                  { field: 'active_accounts', label: 'accounts' },
                  { field: 'report_views', label: 'reports opened' },
                  { field: 'entries_created', label: 'entries made' },
                ]}
              />
            </div>

            <div className="stack" style={{ gap: 8 }}>
              <h3>Most active accounts</h3>
              {data.top_accounts.length === 0 ? (
                <span className="muted" style={{ fontSize: 13 }}>
                  Nobody has used the app in this period.
                </span>
              ) : (
                <ul className="glance-list">
                  {data.top_accounts.slice(0, 5).map((account) => (
                    <li key={account.id}>
                      <Link className="glance-name" to={`/accounts?open=${account.id}`}>
                        {account.name}
                      </Link>
                      <span className="muted nowrap">
                        {fmtCount(account.active_days)}
                        <span className="dim"> of {USAGE_DAYS} days</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function Figure({ value, label, note }) {
  return (
    <div className="glance-figure">
      <span className="glance-value">{fmtCount(value)}</span>
      <span className="stat-label">{label}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </div>
  );
}

function Stat({ value, label, note, tone = 'plain', to }) {
  const body = (
    <>
      <span className="stat-value">{fmtCount(value)}</span>
      <span className="stat-label">{label}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </>
  );
  const className = `stat card stat-${tone}`;
  return to ? (
    <Link className={className} to={to}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}
