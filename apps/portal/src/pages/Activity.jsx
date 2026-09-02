import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, query } from '../api.js';
import ClearLogs from '../components/ClearLogs.jsx';
import { Empty, Loading, Pill, useDebounced, useLoad } from '../components/ui.jsx';
import { fmtAgo, fmtDateTime } from '../format.js';

/**
 * The audit trail, filtered to what the caller may see.
 *
 * The same table the product writes every read into — auditing reads and not
 * just writes, because in a read-only accounting product the sensitive act *is*
 * the read. This screen is the other half of the support view: the connector
 * log says what a customer's machine did, and this says what was asked of it.
 */
export default function Activity({ me }) {
  const [params, setParams] = useSearchParams();
  const orgId = params.get('org') || '';
  const [action, setAction] = useState('');
  const settled = useDebounced(action);
  const [detail, setDetail] = useState(null);

  const accounts = useLoad((signal) => api('/accounts?limit=200', { signal }), []);
  const entries = useLoad(
    (signal) => api(`/audit${query({ org_id: orgId, action: settled, limit: 200 })}`, { signal }),
    [orgId, settled],
  );

  return (
    <>
      <div className="page-head">
        <h1>Activity</h1>
        <p className="muted">
          Who did what, newest first. Portal decisions and customers&rsquo; reads alike.
        </p>
      </div>

      <div className="row wrap" style={{ gap: 10 }}>
        <select
          className="select"
          style={{ width: 'auto', minWidth: 200 }}
          value={orgId}
          aria-label="Account"
          onChange={(event) =>
            setParams(
              (current) => {
                const next = new URLSearchParams(current);
                if (event.target.value) next.set('org', event.target.value);
                else next.delete('org');
                return next;
              },
              { replace: true },
            )
          }
        >
          <option value="">Every account you can see</option>
          {(accounts.data || []).map((account) => (
            <option key={account.id} value={account.id}>
              {account.name}
            </option>
          ))}
        </select>

        <input
          className="input"
          style={{ maxWidth: 240 }}
          type="search"
          placeholder="Action, e.g. portal.approve"
          value={action}
          onChange={(event) => setAction(event.target.value)}
        />

        {/* Owner-only, and the server says so too. Erasing the record of who
            looked at whose books is not something a partner should be able to
            do to the accounts they manage. */}
        {me?.role === 'owner' ? (
          <>
            <span className="grow" />
            <ClearLogs
              label="Clear activity"
              title="Clear the activity trail"
              subject={
                orgId
                  ? 'Recorded actions for this account.'
                  : 'Recorded actions for every account.'
              }
              note={
                'The clearing itself is recorded, so the gap it leaves has a name and a '
                + 'time on it.'
              }
              onClear={(days) =>
                api(
                  `/audit${query({ org_id: orgId, older_than_days: days === null ? '' : days })}`,
                  { method: 'DELETE' },
                )
              }
              onDone={entries.reload}
            />
          </>
        ) : null}
      </div>

      <section className="card">
        {entries.loading && !entries.data ? (
          <Loading />
        ) : entries.error ? (
          <Empty title="Could not load activity">{entries.error.message}</Empty>
        ) : !entries.data.length ? (
          <Empty title="Nothing recorded">
            {orgId
              ? 'Nothing has happened on this account yet.'
              : 'No activity matches that filter.'}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Action</th>
                  <th>Account</th>
                  <th>Who</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {entries.data.map((entry) => (
                  <tr
                    key={entry.id}
                    className={entry.detail ? 'clickable' : undefined}
                    onClick={() => entry.detail && setDetail(entry)}
                  >
                    <td className="muted nowrap" title={fmtDateTime(entry.created_at)}>
                      {fmtAgo(entry.created_at)}
                    </td>
                    <td className="mono">{entry.action}</td>
                    <td className="muted">{entry.org_name || '—'}</td>
                    <td className="muted">{entry.actor || '—'}</td>
                    <td>
                      {entry.outcome === 'ok' ? (
                        <span className="dim">ok</span>
                      ) : (
                        <Pill tone="stop">{entry.outcome}</Pill>
                      )}
                      {entry.duration_ms ? (
                        <span className="dim"> · {entry.duration_ms}ms</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {detail ? (
        <div className="scrim" onMouseDown={() => setDetail(null)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={detail.action}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="modal-head">
              <h2 className="mono">{detail.action}</h2>
              <p className="muted" style={{ margin: '4px 0 0' }}>
                {fmtDateTime(detail.created_at)}
                {detail.ip_address ? ` · from ${detail.ip_address}` : ''}
              </p>
            </div>
            <div className="modal-body">
              <pre
                style={{
                  margin: 0,
                  padding: 12,
                  background: 'var(--surface-2)',
                  borderRadius: 'var(--r-sm)',
                  overflowX: 'auto',
                  fontSize: 12,
                }}
              >
                {JSON.stringify(detail.detail, null, 2)}
              </pre>
            </div>
            <div className="modal-foot">
              <button className="btn" type="button" onClick={() => setDetail(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
