import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, query } from '../api.js';
import LogView from '../components/LogView.jsx';
import { Empty, Loading, Pill, useDebounced, useLoad, useToast } from '../components/ui.jsx';
import { fmtAgo, fmtCount } from '../format.js';
import './connectorlogs.css';

const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR'];
const REFRESH_SECONDS = 15;

/**
 * What one customer's Tally PC has been saying.
 *
 * The screen this whole feature exists for. Someone rings up, you pick their
 * business, you pick the machine, and you read what it said — without asking a
 * shop owner to find a file in %PROGRAMDATA% and email it.
 *
 * Polled rather than streamed, unlike the server log, and that is a deliberate
 * difference rather than an omission. Connector lines arrive through a database
 * write from whichever backend instance happens to hold that connector's
 * socket, so a stream attached to *this* instance would show a fleet that goes
 * quiet whenever routing moves. Fifteen seconds against an indexed query is the
 * honest version.
 */
export default function ConnectorLogs() {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const orgId = params.get('org') || '';
  const connectorId = params.get('pc') || '';

  const [level, setLevel] = useState('');
  const [search, setSearch] = useState('');
  const settled = useDebounced(search);
  const [auto, setAuto] = useState(true);
  const [follow, setFollow] = useState(true);
  const [tick, setTick] = useState(0);

  const setParam = useCallback(
    (name, value) =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) next.set(name, value);
          else next.delete(name);
          // Changing account invalidates the machine chosen inside it.
          if (name === 'org') next.delete('pc');
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  const accounts = useLoad((signal) => api('/accounts?limit=200', { signal }), []);
  const connectors = useLoad(
    (signal) =>
      orgId ? api(`/accounts/${orgId}/connectors`, { signal }) : Promise.resolve([]),
    [orgId],
  );

  const logs = useLoad(
    (signal) =>
      api(
        `/logs/connector${query({
          org_id: orgId,
          connector_id: connectorId,
          level,
          q: settled,
          limit: 500,
        })}`,
        { signal },
      ),
    [orgId, connectorId, level, settled, tick],
  );

  // Only while the tab is visible. A support console left open on a second
  // monitor overnight should not be quietly querying all night.
  useEffect(() => {
    if (!auto) return undefined;
    const timer = setInterval(() => {
      if (!document.hidden) setTick((value) => value + 1);
    }, REFRESH_SECONDS * 1000);
    return () => clearInterval(timer);
  }, [auto]);

  const byId = useMemo(
    () => Object.fromEntries((connectors.data || []).map((row) => [row.id, row])),
    [connectors.data],
  );

  const chosen = accounts.data?.find((account) => account.id === orgId);

  return (
    <>
      <div className="page-head">
        <h1>Connector logs</h1>
        <p className="muted">
          What a customer&rsquo;s Tally PC has been reporting. Pushed over the connection
          it already holds open, so nobody has to go and find a log file.
        </p>
      </div>

      <div className="picker">
        <label className="field">
          <span className="label">Account</span>
          <select
            className="select"
            value={orgId}
            onChange={(event) => setParam('org', event.target.value)}
          >
            <option value="">
              {accounts.loading ? 'Loading…' : 'Every account you can see'}
            </option>
            {(accounts.data || []).map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="label">Tally PC</span>
          <select
            className="select"
            value={connectorId}
            disabled={!orgId}
            onChange={(event) => setParam('pc', event.target.value)}
          >
            <option value="">
              {!orgId ? 'Choose an account first' : 'Every PC on this account'}
            </option>
            {(connectors.data || []).map((connector) => (
              <option key={connector.id} value={connector.id}>
                {connector.label}
                {connector.online ? ' — online' : ''}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="label">Level</span>
          <select
            className="select"
            value={level}
            onChange={(event) => setLevel(event.target.value)}
          >
            {LEVELS.map((value) => (
              <option key={value} value={value}>
                {value ? `${value} and above` : 'All levels'}
              </option>
            ))}
          </select>
        </label>

        <label className="field grow">
          <span className="label">Search</span>
          <input
            className="input"
            type="search"
            placeholder="Message text…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      {orgId && connectors.data?.length ? (
        <div className="pc-strip">
          {connectors.data.map((connector) => (
            <button
              key={connector.id}
              type="button"
              className={`pc-card${connector.id === connectorId ? ' is-active' : ''}`}
              onClick={() =>
                setParam('pc', connector.id === connectorId ? '' : connector.id)
              }
            >
              <div className="row-between">
                <strong className="truncate">{connector.label}</strong>
                {connector.online ? (
                  <Pill tone="ok">Online</Pill>
                ) : (
                  <Pill tone="off">Offline</Pill>
                )}
              </div>
              <span className="dim">
                {connector.hostname || 'unknown host'}
                {connector.connector_version ? ` · v${connector.connector_version}` : ''}
              </span>
              <div className="row wrap" style={{ gap: 6 }}>
                {connector.online ? (
                  <Pill tone={connector.tally_online ? 'ok' : 'wait'}>
                    {connector.tally_online ? 'Tally reachable' : 'Tally not answering'}
                  </Pill>
                ) : null}
                {connector.error_count > 0 ? (
                  <Pill tone="stop">{connector.error_count} errors</Pill>
                ) : null}
              </div>
              <span className="dim">
                {connector.last_log_at
                  ? `last logged ${fmtAgo(connector.last_log_at)}`
                  : 'has never sent a log'}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      <section className="card">
        <div className="log-bar">
          <span className="label" style={{ letterSpacing: '.05em' }}>
            {chosen ? chosen.name : 'All accounts'}
            {connectorId && byId[connectorId] ? ` · ${byId[connectorId].label}` : ''}
          </span>
          <span className="grow" />
          <button
            type="button"
            className={`btn btn-sm${auto ? ' btn-primary' : ''}`}
            onClick={() => setAuto((value) => !value)}
            title={`Re-reads every ${REFRESH_SECONDS} seconds while this tab is visible`}
          >
            {auto ? <span className="live-dot" /> : null}
            {auto ? 'Auto-refresh' : 'Paused'}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setTick((value) => value + 1)}
          >
            Refresh
          </button>
          <button
            type="button"
            className="btn btn-sm"
            disabled={!logs.data?.lines?.length}
            onClick={() => copyLog(logs.data.lines, toast)}
          >
            Copy
          </button>
        </div>

        {logs.loading && !logs.data ? (
          <Loading />
        ) : logs.error ? (
          <Empty title="Could not load the log">{logs.error.message}</Empty>
        ) : (
          <LogView
            lines={logs.data.lines}
            follow={follow}
            onFollow={setFollow}
            meta={(line) =>
              connectorId ? line.org_name : byId[line.connector_id]?.label || line.org_name
            }
            emptyTitle="Nothing logged"
            emptyBody={
              orgId
                ? 'This account has sent no matching lines. A connector on a build older '
                  + 'than 0.2 does not push logs at all.'
                : 'Pick an account, or wait for a connector to report something.'
            }
          />
        )}

        <div className="log-foot">
          <span>{fmtCount(logs.data?.lines?.length || 0)} lines</span>
          <span>· newest first from the server, oldest first on screen</span>
          {!follow ? (
            <>
              <span className="grow" />
              <button type="button" className="btn btn-sm" onClick={() => setFollow(true)}>
                Jump to newest
              </button>
            </>
          ) : null}
        </div>
      </section>
    </>
  );
}

/**
 * Put the visible log on the clipboard, oldest first.
 *
 * Exists because the next step after reading a log is nearly always pasting a
 * chunk of it into a message to somebody, and re-selecting a virtualised list
 * by hand is how people end up screenshotting text.
 */
async function copyLog(lines, toast) {
  const text = [...lines]
    .reverse()
    .map((line) => `${line.created_at} ${line.level.padEnd(7)} ${line.logger} ${line.message}`)
    .join('\n');
  try {
    await navigator.clipboard.writeText(text);
    toast(`${lines.length} lines copied.`);
  } catch {
    toast('The browser would not allow copying.', 'bad');
  }
}
