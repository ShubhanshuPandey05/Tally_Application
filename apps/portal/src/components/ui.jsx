/*
 * The small pieces used on more than one screen.
 *
 * Kept in one file rather than one file each: these are twenty-line components
 * with no state, and a directory of twenty-line files costs more to navigate
 * than it saves. Anything that grows a hook of its own moves out.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { LEVEL_TONE, STATUS_LABEL, STATUS_TONE, shownStatus } from '../format.js';

/* ---- Toasts ------------------------------------------------------ */

const ToastContext = createContext(() => {});

export function ToastHost({ children }) {
  const [items, setItems] = useState([]);
  const nextId = useRef(0);

  const push = useCallback((message, tone = 'ok') => {
    const id = ++nextId.current;
    setItems((current) => [...current, { id, message, tone }]);
    setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 4200);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`toast${item.tone === 'bad' ? ' toast-bad' : ''}`}>
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

/* ---- Pills ------------------------------------------------------- */

export function Pill({ tone = 'off', children, plain = false }) {
  return <span className={`pill pill-${tone}${plain ? ' pill-plain' : ''}`}>{children}</span>;
}

export function StatusPill({ account }) {
  const status = shownStatus(account);
  return <Pill tone={STATUS_TONE[status] || 'off'}>{STATUS_LABEL[status] || status}</Pill>;
}

export function LevelPill({ level }) {
  const tone = LEVEL_TONE[level] || 'off';
  // INFO gets no colour at all. If every level is coloured, none of them is a
  // signal, and the only levels worth spotting in a scroll are the top two.
  if (tone === 'plain') return <span className="dim mono">{level}</span>;
  return (
    <span className={`pill pill-${tone} pill-plain mono`} style={{ fontSize: 11 }}>
      {level}
    </span>
  );
}

/* ---- Layout helpers ---------------------------------------------- */

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span className="label">{label}</span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function Loading({ label = 'Loading…' }) {
  return (
    <div className="empty row" style={{ justifyContent: 'center' }}>
      <span className="spinner" />
      <span>{label}</span>
    </div>
  );
}

/**
 * A segmented control.
 *
 * `options` is `[{ value, label, badge }]`. The badge exists for the one case
 * that motivates the whole component: the count of accounts waiting on a
 * decision, on the tab that shows them.
 */
export function Segmented({ options, value, onChange, ariaLabel }) {
  return (
    <div className="seg" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.badge ? <span className="seg-badge">{option.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}

/* ---- Modal ------------------------------------------------------- */

export function Modal({ title, subtitle, onClose, children, footer, wide = false }) {
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="scrim"
      onMouseDown={(event) => {
        // mousedown on the scrim itself, not a click: a drag that starts inside
        // the dialog and ends outside it is a text selection, and closing the
        // form somebody was reading is a real way to lose typed input.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={wide ? { width: 'min(760px, 100%)' } : undefined}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          {subtitle ? <p className="muted" style={{ margin: '4px 0 0' }}>{subtitle}</p> : null}
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

/* ---- Data fetching ----------------------------------------------- */

/**
 * Load something once and expose `{ data, error, loading, reload }`.
 *
 * Aborts in flight when its dependencies change. Without that, switching
 * filters quickly lets a slow first response land after a fast second one and
 * overwrite it -- which reads as a filter that sometimes does not work.
 */
export function useLoad(loader, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  const fn = useRef(loader);
  fn.current = loader;

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((current) => ({ ...current, loading: true }));

    fn.current(controller.signal)
      .then((data) => live && setState({ data, error: null, loading: false }))
      .catch((error) => {
        if (!live || controller.signal.aborted || error?.name === 'AbortError') return;
        setState({ data: null, error, loading: false });
      });

    return () => {
      live = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return useMemo(() => ({ ...state, reload }), [state, reload]);
}

/** Delay a fast-changing value, so a search box does not fetch per keystroke. */
export function useDebounced(value, delay = 300) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
