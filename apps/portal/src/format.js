/* Formatting the portal does in more than one place. */

const DATE = new Intl.DateTimeFormat(undefined, {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

const TIME = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

/** A date, or an em dash. Never "Invalid Date", and never today's date. */
export function fmtDate(iso) {
  if (!iso) return '—';
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? '—' : DATE.format(value);
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return '—';
  return `${DATE.format(value)} ${TIME.format(value)}`;
}

/** Wall-clock time only. What a log reader actually scans by. */
export function fmtClock(iso) {
  if (!iso) return '';
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return '';
  const ms = String(value.getMilliseconds()).padStart(3, '0');
  return `${TIME.format(value)}.${ms}`;
}

/**
 * "3 days ago", roughly.
 *
 * Deliberately coarse. A support view that says "2 minutes ago" invites the
 * reader to believe the number is exact, and it is computed against a clock
 * that may not be -- see the two timestamps on a connector log line.
 */
export function fmtAgo(iso) {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return 'just now';
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86400);
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/** For an avatar bubble. Splits on the separators a name or an email uses. */
export function initials(name, email) {
  const parts = String(name || email || '?')
    .trim()
    .split(/[\s.@_-]+/)
    .filter(Boolean);
  if (!parts.length) return '?';
  return (parts[0][0] + (parts[1]?.[0] ?? '')).toUpperCase();
}

/** `1234` -> `1,234`. */
export function fmtCount(value) {
  return new Intl.NumberFormat().format(value ?? 0);
}

/**
 * An org's effective status.
 *
 * `expired` is not a stored status -- it is `active` plus a date that has
 * passed. Rendering it as active would hide the one thing wrong with the
 * account, so the portal treats it as its own state everywhere it is shown.
 */
export function shownStatus(account) {
  return account?.is_expired && account.status === 'active' ? 'expired' : account?.status;
}

export const STATUS_LABEL = {
  pending: 'Awaiting approval',
  active: 'Active',
  suspended: 'Suspended',
  rejected: 'Rejected',
  expired: 'Expired',
};

export const STATUS_TONE = {
  pending: 'wait',
  active: 'ok',
  suspended: 'stop',
  rejected: 'off',
  expired: 'stop',
};

/** Level -> pill tone. Anything below WARNING is deliberately colourless. */
export const LEVEL_TONE = {
  DEBUG: 'off',
  INFO: 'plain',
  WARNING: 'wait',
  ERROR: 'stop',
};

/** `<input type="date">` wants `yyyy-mm-dd`; the API speaks ISO instants. */
export function toDateInput(iso) {
  if (!iso) return '';
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? '' : value.toISOString().slice(0, 10);
}

/**
 * A date typed into a form, as an instant.
 *
 * End of day, not midnight. "Expires 31 March" means the customer has the 31st,
 * and a naive conversion would cut them off a day early -- which is a support
 * call and a refund, not a rounding error.
 */
export function fromDateInput(value) {
  if (!value) return null;
  return new Date(`${value}T23:59:59Z`).toISOString();
}
