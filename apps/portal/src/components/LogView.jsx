/*
 * The log reader, shared by both log screens.
 *
 * A monospace list rather than a table, because the thing a person does here is
 * scan a column of times down the left and read across only when something
 * catches their eye. Columns with borders make that harder, not easier.
 *
 * Two behaviours are worth stating because they are the difference between a
 * log viewer that is useful under pressure and one that is not:
 *
 * **Follow is sticky, and it un-sticks itself.** Scrolling up while lines are
 * arriving means "hold still, I am reading this" -- so it does. Scrolling back
 * to the bottom re-arms it. A viewer that yanks the view back down every second
 * cannot be read at all during the incident it exists for.
 *
 * **Nothing here is rendered as HTML.** Log messages contain whatever a stack
 * trace contained. React escapes by default; that default is not worked around
 * anywhere in this file.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { LevelPill } from './ui.jsx';
import { fmtAgo, fmtClock, fmtDate } from '../format.js';
import './logview.css';

/**
 * @param lines      newest-first, as every endpoint returns them
 * @param follow     whether new lines should scroll into view
 * @param onFollow   called when the reader's own scrolling changes that
 * @param meta       optional per-line right-hand column (a connector's name)
 */
export default function LogView({ lines, follow, onFollow, meta, emptyTitle, emptyBody }) {
  const box = useRef(null);
  const [expanded, setExpanded] = useState(() => new Set());

  // Rendered oldest-at-top, which is how a log reads, while the API returns
  // newest-first because that is the half worth fetching. Reversing in the view
  // rather than in the fetch keeps "newest 300" meaning newest 300.
  const ordered = [...lines].reverse();

  useLayoutEffect(() => {
    if (!follow || !box.current) return;
    box.current.scrollTop = box.current.scrollHeight;
  }, [ordered.length, follow]);

  const onScroll = useCallback(() => {
    const element = box.current;
    if (!element || !onFollow) return;
    // A few pixels of slack: some browsers land a pixel short of the bottom
    // after a smooth scroll, and an exact comparison would silently disarm
    // follow the first time somebody used the scrollbar.
    const atBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    onFollow(atBottom);
  }, [onFollow]);

  const toggle = (key) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (!lines.length) {
    return (
      <div className="empty">
        <strong>{emptyTitle || 'Nothing logged'}</strong>
        {emptyBody}
      </div>
    );
  }

  let lastDay = '';

  return (
    <div className="logview" ref={box} onScroll={onScroll}>
      {ordered.map((line, index) => {
        const key = line.seq || `${line.created_at}:${index}`;
        const day = fmtDate(line.created_at);
        const newDay = day !== lastDay;
        lastDay = day;
        const isOpen = expanded.has(key);
        const hasMore = Boolean(line.traceback) || line.message.length > 220;

        return (
          <div key={key}>
            {newDay ? <div className="log-day">{day}</div> : null}

            {/* A gap the connector reported is rendered in place, not as a
                footnote. A missing stretch that reads as a quiet period is how
                a support session goes down the wrong path. */}
            {line.dropped_before > 0 ? (
              <div className="log-gap">
                {line.dropped_before.toLocaleString()} lines were dropped before this
                point — the connector could not reach us fast enough.
              </div>
            ) : null}

            <div className={`log-line lvl-${line.level}${isOpen ? ' is-open' : ''}`}>
              <span className="log-time" title={line.created_at}>
                {fmtClock(line.created_at)}
              </span>
              <LevelPill level={line.level} />
              <span className="log-logger" title={line.logger}>
                {line.logger}
              </span>
              <span className="log-msg">
                {line.message}
                {hasMore ? (
                  <button type="button" className="log-more" onClick={() => toggle(key)}>
                    {isOpen ? 'less' : 'more'}
                  </button>
                ) : null}
              </span>
              {meta ? <span className="log-meta">{meta(line)}</span> : null}
            </div>

            {isOpen ? (
              <div className="log-detail">
                {line.traceback ? <pre>{line.traceback}</pre> : null}
                <dl>
                  {line.request_id ? (
                    <>
                      <dt>Request</dt>
                      <dd className="mono">{line.request_id}</dd>
                    </>
                  ) : null}
                  {line.instance_id ? (
                    <>
                      <dt>Instance</dt>
                      <dd className="mono">{line.instance_id}</dd>
                    </>
                  ) : null}
                  {line.connector_id ? (
                    <>
                      <dt>Connector</dt>
                      <dd className="mono">{line.connector_id}</dd>
                    </>
                  ) : null}
                  {line.session_id ? (
                    <>
                      <dt>Session</dt>
                      <dd className="mono">{line.session_id}</dd>
                    </>
                  ) : null}
                  {/* Both clocks, always, when they disagree by more than a
                      minute. A shop PC whose system time is hours out is a real
                      finding and invisible if only one of them is shown. */}
                  {line.logged_at && clockSkewed(line) ? (
                    <>
                      <dt>PC clock</dt>
                      <dd>
                        {fmtClock(line.logged_at)}{' '}
                        <span className="dim">({fmtAgo(line.logged_at)} by its own clock)</span>
                      </dd>
                    </>
                  ) : null}
                </dl>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

const SKEW_MS = 60_000;

function clockSkewed(line) {
  const received = new Date(line.created_at).getTime();
  const claimed = new Date(line.logged_at).getTime();
  if (Number.isNaN(received) || Number.isNaN(claimed)) return false;
  return Math.abs(received - claimed) > SKEW_MS;
}
