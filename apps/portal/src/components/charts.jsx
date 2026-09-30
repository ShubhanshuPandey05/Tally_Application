/*
 * The portal's charts. Three shapes, all single-series, all counts.
 *
 * Drawn as HTML boxes rather than an SVG stretched to fit: a viewBox scaled to
 * the card's width scales its text with it, and an axis label that changes size
 * when the window does reads as broken. No chart library either -- three
 * shapes do not earn a dependency in a tool that must keep building.
 *
 * Colour: one hue, `--chart`, because every chart here plots one thing. It is
 * the app's blue on the light skin; on Dim and Dark it is #6887ff rather than
 * the app's #8aa4ff, which is too light to hold a mark's shape on those
 * surfaces (run through the dataviz palette validator: #8aa4ff fails the
 * lightness band on both, #6887ff passes on both). Text never wears it.
 */

import { useId, useState } from 'react';
import './charts.css';

const DAY_FMT = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' });
const LONG_FMT = new Intl.DateTimeFormat('en-IN', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
});
const NUM = new Intl.NumberFormat('en-IN');

// `day` arrives as 2026-09-30. Parsed as a local date, not UTC midnight, or a
// browser west of Greenwich labels every bar with the day before.
function asDate(day) {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d);
}

/**
 * A clean top for the axis, and always an even one.
 *
 * The axis is labelled at the top and halfway, and everything plotted here is
 * a count of people or actions -- so a top of 5 would put "2.5 people" on the
 * axis. Even steps keep the midpoint whole.
 */
function niceMax(value) {
  const floor = Math.max(value, 4);
  const power = 10 ** Math.floor(Math.log10(floor));
  for (const step of [2, 4, 6, 8, 10]) {
    if (step * power >= floor) return step * power;
  }
  return 20 * power;
}

/**
 * Columns, one per day.
 *
 * `rows` is the series from the API, oldest first, every day present. `detail`
 * lists the other fields to show in the tooltip and table, so a reader hovering
 * "12 people active" can also see what those twelve did.
 */
export function DailyBars({ rows, field, unit, detail = [], height = 168 }) {
  const [hover, setHover] = useState(null);
  const tableId = useId();
  if (!rows?.length) return null;

  const top = niceMax(Math.max(...rows.map((r) => r[field] || 0)));
  const ticks = [top, top / 2, 0];
  const last = rows.length - 1;
  // Three dates under the axis: the ends and the middle. More collide on a
  // 90-day window; fewer leave the reader counting bars.
  const labelled = new Set([0, Math.floor(last / 2), last]);

  return (
    <figure className="chart" aria-describedby={tableId}>
      <div className="chart-plot" style={{ height }}>
        <div className="chart-grid" aria-hidden="true">
          {ticks.map((t) => (
            <div key={t} className="chart-gridline">
              <span className="chart-tick">{NUM.format(t)}</span>
            </div>
          ))}
        </div>
        <div className="chart-bars" onPointerLeave={() => setHover(null)}>
          {rows.map((row, i) => {
            const value = row[field] || 0;
            return (
              <button
                key={row.day}
                type="button"
                className={`chart-slot${hover === i ? ' is-hot' : ''}`}
                onPointerEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                aria-label={`${LONG_FMT.format(asDate(row.day))}: ${NUM.format(value)} ${unit}`}
              >
                <span
                  className="chart-bar"
                  style={{ height: value ? `${Math.max((value / top) * 100, 1.5)}%` : 0 }}
                />
              </button>
            );
          })}
        </div>
        {hover !== null ? (
          <Tooltip
            row={rows[hover]}
            index={hover}
            count={rows.length}
            field={field}
            unit={unit}
            detail={detail}
          />
        ) : null}
      </div>
      <div className="chart-dates" aria-hidden="true">
        {rows.map((row, i) => (
          <span key={row.day}>{labelled.has(i) ? DAY_FMT.format(asDate(row.day)) : ''}</span>
        ))}
      </div>

      {/* The table view: every value a tooltip shows, reachable without one. */}
      <details className="chart-table" id={tableId}>
        <summary>Show as table</summary>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Day</th>
                <th className="right">{unit}</th>
                {detail.map((d) => (
                  <th key={d.field} className="right">{d.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...rows].reverse().map((row) => (
                <tr key={row.day}>
                  <td>{LONG_FMT.format(asDate(row.day))}</td>
                  <td className="right">{NUM.format(row[field] || 0)}</td>
                  {detail.map((d) => (
                    <td key={d.field} className="right">{NUM.format(row[d.field] || 0)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

function Tooltip({ row, index, count, field, unit, detail }) {
  // Anchored over its bar, but flipped inward near either edge so it never
  // hangs off the card.
  const at = ((index + 0.5) / count) * 100;
  const side = at < 20 ? 'start' : at > 80 ? 'end' : 'mid';
  return (
    <div className={`chart-tip chart-tip-${side}`} style={{ left: `${at}%` }} role="status">
      <div className="chart-tip-head">{LONG_FMT.format(asDate(row.day))}</div>
      <div className="chart-tip-row">
        <span className="chart-key" aria-hidden="true" />
        <strong>{NUM.format(row[field] || 0)}</strong>
        <span className="muted">{unit}</span>
      </div>
      {detail.map((d) => (
        <div key={d.field} className="chart-tip-row chart-tip-minor">
          <strong>{NUM.format(row[d.field] || 0)}</strong>
          <span className="muted">{d.label}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * A person's month at a glance: one thin column per day.
 *
 * Small enough to sit in a table row, so no axis -- the numbers beside it in
 * the row carry the totals, and each column names its own day and count.
 */
export function Spark({ values, days }) {
  const top = Math.max(...values, 1);
  const start = new Date();
  start.setDate(start.getDate() - (values.length - 1));
  const active = values.filter(Boolean).length;
  return (
    <span
      className="spark"
      role="img"
      aria-label={`Active on ${active} of the last ${days} days`}
    >
      {values.map((v, i) => {
        const day = new Date(start);
        day.setDate(start.getDate() + i);
        return (
          <span
            key={i}
            className={`spark-bar${v ? '' : ' is-empty'}`}
            style={{ height: v ? `${Math.max((v / top) * 100, 12)}%` : undefined }}
            title={`${LONG_FMT.format(day)}: ${v} action${v === 1 ? '' : 's'}`}
          />
        );
      })}
    </span>
  );
}

/**
 * Parts of a whole, largest first, each with its count at the tip.
 * Used for "which build are people on".
 */
export function ShareBars({ rows }) {
  const total = rows.reduce((sum, r) => sum + r.value, 0) || 1;
  const top = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="shares">
      {rows.map((row) => (
        <li key={row.key} className="share">
          <span className="share-label">{row.label}</span>
          <span className="share-track">
            <span className="share-fill" style={{ width: `${(row.value / top) * 100}%` }} />
          </span>
          <span className="share-value">
            {NUM.format(row.value)}
            <span className="dim"> · {Math.round((row.value / total) * 100)}%</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
