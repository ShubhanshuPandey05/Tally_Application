/*
 * The one place that talks to the backend.
 *
 * Same origin, always. In production Caddy serves this bundle under /portal and
 * proxies /v1 to the API on the same hostname; in development Vite proxies /v1
 * to a local backend. So the token never becomes a cross-origin credential, and
 * the CORS behaviour of the portal is not something that differs between the
 * environment people use and the environment that matters.
 *
 * The token lives in localStorage under the key the single-page portal used, so
 * anybody signed in when this shipped stays signed in. It is a session token
 * with no refresh family -- see `config.portal_token_ttl_seconds` -- which is
 * why 401 has exactly one handling: sign out and show the gate. There is
 * nothing to retry with.
 */

const KEY = 'tallyflow.portal.token';
const BASE = '/v1/portal';

let onSignedOut = () => {};

export function setSignOutHandler(handler) {
  onSignedOut = handler;
}

export const token = {
  get: () => localStorage.getItem(KEY) || '',
  set: (value) => localStorage.setItem(KEY, value),
  clear: () => localStorage.removeItem(KEY),
};

/**
 * An error carrying what the server actually said.
 *
 * `userMessage` is the backend's own wording (see core/errors.py), which is
 * written for the person reading it. Falling back to the raw `message` is a
 * last resort -- it is written for whoever is reading a log.
 */
export class ApiError extends Error {
  constructor(status, body) {
    const detail = body?.error || body || {};
    super(detail.user_message || detail.message || `Request failed (${status})`);
    this.name = 'ApiError';
    this.status = status;
    this.code = detail.code || '';
  }
}

export async function api(path, options = {}) {
  const { method = 'GET', body, signal } = options;
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const current = token.get();
  if (current) headers.Authorization = `Bearer ${current}`;

  const response = await fetch(BASE + path, {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401) {
    // Not thrown as a normal failure. A 401 means this session is over, and
    // every caller would otherwise need its own "and if it was 401..." branch.
    token.clear();
    onSignedOut();
    throw new ApiError(401, { error: { user_message: 'Your session has ended. Please sign in again.' } });
  }

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

/** Query string builder that drops empty values rather than sending `?q=`. */
export function query(params) {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(name, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : '';
}

/**
 * Read a server-sent-event stream, calling `onEvent` per message.
 *
 * Written on `fetch` rather than `EventSource` for one reason: `EventSource`
 * cannot set an Authorization header, so using it would mean putting a portal
 * token in a query string, where it lands in every proxy access log between
 * here and the API. This is the same wire format read by hand.
 *
 * Resolves when the stream ends or the signal aborts. Never rejects on abort --
 * a closed tab is not an error.
 */
export async function stream(path, { signal, onEvent }) {
  const headers = { Accept: 'text/event-stream' };
  const current = token.get();
  if (current) headers.Authorization = `Bearer ${current}`;

  const response = await fetch(BASE + path, { headers, signal });
  if (response.status === 401) {
    token.clear();
    onSignedOut();
    return;
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await response.json().catch(() => null));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffered += decoder.decode(value, { stream: true });

      // Events are separated by a blank line. Anything after the last one is a
      // partial event held back until the rest of it arrives -- parsing it now
      // would mean silently dropping every message that happened to straddle a
      // chunk boundary, which is most of the large ones.
      const chunks = buffered.split('\n\n');
      buffered = chunks.pop() ?? '';

      for (const chunk of chunks) {
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue; // `: keepalive` comments
          try {
            onEvent(JSON.parse(line.slice(5).trim()));
          } catch {
            // A truncated frame is not worth ending a live tail over.
          }
        }
      }
    }
  } catch (error) {
    if (signal?.aborted || error?.name === 'AbortError') return;
    throw error;
  } finally {
    reader.cancel().catch(() => {});
  }
}
