import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, query } from '../api.js';
import {
  Empty,
  Loading,
  Segmented,
  StatusPill,
  useDebounced,
  useLoad,
  useToast,
} from '../components/ui.jsx';
import { fmtAgo, fmtDate } from '../format.js';
import AccountDrawer from './AccountDrawer.jsx';
import './accounts.css';

const TABS = [
  { value: 'pending', label: 'Pending' },
  { value: 'active', label: 'Active' },
  { value: 'suspended', label: 'Suspended' },
  { value: 'rejected', label: 'Rejected' },
  { value: '', label: 'All' },
];

/**
 * The account list, and the drawer that acts on one.
 *
 * Filter and open-account both live in the URL. That is not tidiness: this
 * screen is used while on the phone to somebody, and "send me the link to that
 * account" has to produce a link that opens that account.
 */
export default function Accounts({ me, onCounts }) {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') ?? 'pending';
  const openId = params.get('open') || '';
  const [search, setSearch] = useState(params.get('q') || '');
  const settled = useDebounced(search);

  const accounts = useLoad(
    (signal) => api(`/accounts${query({ status, search: settled, limit: 200 })}`, { signal }),
    [status, settled],
  );

  // Partners populate the "owned by" dropdown in the drawer. Loaded once here
  // rather than per open: it is the same short list every time, and refetching
  // it makes opening an account feel slower than it is.
  const partners = useLoad((signal) => api('/partners', { signal }), []);

  const [selected, setSelected] = useState(null);

  const setParam = useCallback(
    (name, value) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) next.set(name, value);
          else next.delete(name);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // A deep link names an account this list may not contain -- an `?open=` for a
  // suspended account while the Pending tab is showing, say. Fetching it by id
  // rather than searching the loaded rows is what makes such a link work at all.
  useEffect(() => {
    if (!openId) {
      setSelected(null);
      return;
    }
    let live = true;
    api(`/accounts/${openId}`)
      .then((account) => live && setSelected(account))
      .catch((error) => {
        if (!live) return;
        toast(error.message, 'bad');
        setParam('open', '');
      });
    return () => {
      live = false;
    };
  }, [openId, setParam, toast]);

  const afterChange = useCallback(
    (updated) => {
      setSelected(updated);
      accounts.reload();
      onCounts();
    },
    [accounts, onCounts],
  );

  return (
    <>
      <div className="page-head">
        <h1>Accounts</h1>
        <p className="muted">
          Every business is its own onboarding request. Approving one is where its
          limits are set.
        </p>
      </div>

      <div className="row wrap" style={{ gap: 10 }}>
        <Segmented
          ariaLabel="Filter by status"
          options={TABS}
          value={status}
          onChange={(value) => setParam('status', value)}
        />
        <span className="grow" />
        <input
          className="input"
          style={{ maxWidth: 260 }}
          type="search"
          placeholder="Search business name…"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setParam('q', event.target.value);
          }}
        />
      </div>

      <section className="card">
        {accounts.loading && !accounts.data ? (
          <Loading />
        ) : accounts.error ? (
          <Empty title="Could not load accounts">{accounts.error.message}</Empty>
        ) : !accounts.data.length ? (
          <Empty title="Nothing here">
            {status === 'pending'
              ? 'Every signup has been decided.'
              : 'No account matches that filter.'}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Business</th>
                  <th>Status</th>
                  <th>Users</th>
                  <th>Companies</th>
                  <th>Tally PCs</th>
                  <th>Term</th>
                  <th>Signed up</th>
                </tr>
              </thead>
              <tbody>
                {accounts.data.map((account) => (
                  <tr
                    key={account.id}
                    className="clickable"
                    onClick={() => setParam('open', account.id)}
                  >
                    <td>
                      <div className="stack">
                        <strong>{account.name}</strong>
                        <span className="dim" style={{ fontSize: 12 }}>
                          {account.admins[0]
                            ? account.admins[0].email
                            : 'no admin has signed in yet'}
                        </span>
                      </div>
                    </td>
                    <td><StatusPill account={account} /></td>
                    <td><Meter used={account.users_used} cap={account.max_users} /></td>
                    <td><Meter used={account.companies_used} cap={account.max_companies} /></td>
                    <td>
                      <span className="nowrap">
                        {account.connectors_online > 0 ? (
                          <span className="dot dot-ok" title="A connector is online" />
                        ) : (
                          <span className="dot dot-off" title="No connector online" />
                        )}
                        {account.connectors}
                      </span>
                    </td>
                    <td className="muted nowrap">
                      {account.expires_at ? fmtDate(account.expires_at) : 'Open-ended'}
                    </td>
                    <td className="muted nowrap">{fmtAgo(account.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected ? (
        <AccountDrawer
          account={selected}
          me={me}
          partners={partners.data || []}
          onClose={() => setParam('open', '')}
          onChanged={afterChange}
        />
      ) : null}
    </>
  );
}

/**
 * Usage against a ceiling.
 *
 * Both numbers together, always. A request to raise a limit cannot be judged
 * from the limit alone, and "go and look it up" is how limits get raised by
 * reflex.
 */
function Meter({ used, cap }) {
  const full = cap > 0 && used >= cap;
  const share = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  return (
    <div className="meter" title={`${used} of ${cap}`}>
      <span className={`meter-text${full ? ' is-full' : ''}`}>
        {used}<span className="dim"> / {cap}</span>
      </span>
      <span className="meter-track">
        <span
          className={`meter-fill${full ? ' is-full' : ''}`}
          style={{ width: `${share}%` }}
        />
      </span>
    </div>
  );
}
