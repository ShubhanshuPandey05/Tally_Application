import { useCallback, useEffect, useRef, useState } from 'react';
import { api, query, stream } from '../api.js';
import ClearLogs from '../components/ClearLogs.jsx';
import LogView from '../components/LogView.jsx';
import { Loading, Segmented, useDebounced, useToast } from '../components/ui.jsx';
import { fmtCount } from '../format.js';

const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR'];

const SOURCES = [
  { value: 'live', label: 'Live' },
  { value: 'stored', label: 'Kept' },
];

//: Beyond this the view starts costing more to render than it does to read.
//: Oldest are dropped, because the reason anybody is here is what just happened.
const MAX_LINES = 2000;

/**
 * The backend's own log.
 *
 * Two sources, offered as a switch rather than merged. **Live** is this
 * process's in-memory ring: every level, streamed, gone when the container is
 * replaced. **Kept** is the table: warnings and errors only, every instance,
 * survives a deploy. Merging them would hide which one a search came up empty
 * against, and "I cannot find it" has a completely different answer in each.
 *
 * Owner-only, and enforced on the server. A stack trace from one tenant's
 * request routinely names another's connector, so there is no partner-shaped
 * slice of this to hand out.
 */
export default function BackendLogs() {
  const toast = useToast();
  const [source, setSource] = useState('live');
  const [level, setLevel] = useState('');
  const [loggerName, setLoggerName] = useState('');
  const [search, setSearch] = useState('');
  const settledSearch = useDebounced(search);
  const settledLogger = useDebounced(loggerName);

  const [lines, setLines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [live, setLive] = useState(true);
  const [follow, setFollow] = useState(true);
  const [instance, setInstance] = useState('');

  const filters = query({
    level,
    logger: settledLogger,
    q: settledSearch,
  });

  // The backlog. Fetched on every filter change, and on switching source, so
  // the stream below only ever has to carry what happens next.
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const page = await api(`/logs/backend${filters}${filters ? '&' : '?'}source=${source}&limit=500`);
      setLines(page.lines);
      setInstance(page.instance_id || '');
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, source]);

  useEffect(() => {
    load();
  }, [load]);

  // The live tail. Only ever attached to the `live` source: streaming the
  // stored table would mean polling Postgres for as long as somebody is
  // worried, which is exactly when it can least afford it.
  const seen = useRef(new Set());
  useEffect(() => {
    if (source !== 'live' || !live) return undefined;
    const controller = new AbortController();
    seen.current = new Set();

    stream(`/logs/backend/stream${filters}`, {
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type !== 'line') return;
        if (seen.current.has(event.seq)) return;
        seen.current.add(event.seq);
        setLines((current) => {
          // The backlog fetch and the stream overlap by design -- subscribing
          // first would still race -- so a line already on screen is dropped by
          // sequence number rather than assumed impossible.
          if (current.some((item) => item.seq === event.seq)) return current;
          return [event, ...current].slice(0, MAX_LINES);
        });
      },
    }).catch((failure) => {
      toast(`Live tail stopped: ${failure.message}`, 'bad');
      setLive(false);
    });

    return () => controller.abort();
  }, [source, live, filters, toast]);

  const streaming = source === 'live' && live;

  return (
    <>
      <div className="page-head">
        <h1>Server logs</h1>
        <p className="muted">
          What the API itself is doing. Live reads this process&rsquo;s memory; Kept reads
          the warnings and errors that survive a redeploy.
        </p>
      </div>

      <section className="card">
        <div className="log-bar">
          <Segmented
            ariaLabel="Log source"
            options={SOURCES}
            value={source}
            onChange={setSource}
          />

          <select
            className="select"
            value={level}
            aria-label="Minimum level"
            onChange={(event) => setLevel(event.target.value)}
          >
            {LEVELS.map((value) => (
              <option key={value} value={value}>
                {value ? `${value} and above` : 'All levels'}
              </option>
            ))}
          </select>

          <input
            className="input"
            placeholder="Logger, e.g. hub.link"
            value={loggerName}
            onChange={(event) => setLoggerName(event.target.value)}
          />

          <input
            className="input"
            type="search"
            placeholder="Search messages…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />

          <span className="grow" />

          {source === 'live' ? (
            <button
              type="button"
              className={`btn btn-sm${streaming ? ' btn-primary' : ''}`}
              onClick={() => setLive((value) => !value)}
            >
              {streaming ? <span className="live-dot" /> : null}
              {streaming ? 'Live' : 'Paused'}
            </button>
          ) : null}

          <button type="button" className="btn btn-sm" onClick={load}>
            Refresh
          </button>
          <ClearLogs
            label="Clear"
            title="Clear server logs"
            subject="Kept warnings and errors from every backend instance."
            note={
              'Clearing everything also empties this process\u2019s live ring, so the Live '
              + 'view starts again from the next line logged.'
            }
            onClear={(days) =>
              api(`/logs/backend${query({ older_than_days: days === null ? '' : days })}`, {
                method: 'DELETE',
              })
            }
            onDone={load}
          />
        </div>

        {loading && !lines.length ? (
          <Loading />
        ) : error ? (
          <div className="empty">
            <strong>Could not load the log</strong>
            {error}
          </div>
        ) : (
          <LogView
            lines={lines}
            follow={streaming && follow}
            onFollow={setFollow}
            emptyTitle={source === 'live' ? 'Nothing captured yet' : 'Nothing kept'}
            emptyBody={
              source === 'live'
                ? 'This process has logged nothing matching that filter since it started.'
                : 'No warning or error matching that filter has been written down.'
            }
          />
        )}

        <div className="log-foot">
          <span>{fmtCount(lines.length)} lines</span>
          {instance ? <span>· instance {instance}</span> : null}
          {streaming && !follow ? (
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
