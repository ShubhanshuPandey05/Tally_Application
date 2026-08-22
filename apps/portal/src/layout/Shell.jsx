/*
 * The sidebar shell every signed-in screen renders inside.
 *
 * A sidebar rather than the top nav this replaced, and the reason is the
 * support view. Top nav works while there are three destinations; it does not
 * survive a section that is itself a workspace -- pick an account, pick a
 * machine, filter, tail -- because those controls then have to compete with
 * navigation for the same horizontal strip. Vertical navigation gets out of the
 * way, and gives the count of accounts waiting on a decision somewhere
 * permanent to live.
 */

import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { initials } from '../format.js';
import './shell.css';

const ICONS = {
  overview: 'M3 12l9-8 9 8v8a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  accounts: 'M4 20v-1a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v1M12.5 7.5a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0M17 11a3 3 0 1 0 0-6M21 20v-1a4 4 0 0 0-3-3.87',
  partners: 'M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7z',
  server: 'M4 5h16v5H4zM4 14h16v5H4zM7.5 7.5h.01M7.5 16.5h.01',
  connector: 'M9 3v5M15 3v5M6 8h12v5a6 6 0 0 1-12 0zM12 19v3',
  activity: 'M3 12h4l3 8 4-16 3 8h4',
  settings:
    'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6h.08A1.7 1.7 0 0 0 10.1 3.04V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9v.08a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1.03',
};

function Icon({ name }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="nav-icon">
      <path
        d={ICONS[name]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Item({ to, icon, label, badge, end = false }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
    >
      <Icon name={icon} />
      <span className="grow">{label}</span>
      {badge ? <span className="nav-badge">{badge}</span> : null}
    </NavLink>
  );
}

/**
 * Light / dark / system, as an explicit three-state choice.
 *
 * Three and not two because the default is "follow the machine", and a toggle
 * with two positions cannot express that -- it either forces a choice on first
 * paint or silently makes one of the two mean "unset", which then drifts out of
 * sync with the OS.
 */
function ThemeToggle() {
  const [theme, setTheme] = useState(() => localStorage.getItem('tallyflow.portal.theme') || 'system');

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    localStorage.setItem('tallyflow.portal.theme', theme);
  }, [theme]);

  const next = { system: 'light', light: 'dark', dark: 'system' }[theme];
  const glyph = { system: '◐', light: '☀', dark: '☾' }[theme];

  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm"
      title={`Theme: ${theme}. Click for ${next}.`}
      aria-label={`Theme: ${theme}`}
      onClick={() => setTheme(next)}
    >
      <span aria-hidden="true" style={{ fontSize: 15 }}>{glyph}</span>
    </button>
  );
}

export default function Shell({ me, pending, onSignOut, children }) {
  const owner = me.role === 'owner';
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Navigating closes the drawer. On a narrow screen the sidebar covers the
  // page, so without this a tap on a link leaves the reader looking at the menu
  // they just used rather than at the thing they asked for.
  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <div className={`shell${open ? ' is-open' : ''}`}>
      <aside className="sidebar">
        <div className="brand">
          <svg viewBox="0 0 32 32" className="brand-mark" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="currentColor" />
            <path
              d="M8 20.5 13 14l4 4.5L24 9"
              fill="none"
              stroke="var(--surface)"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <div className="stack">
            <strong>TallyFlow</strong>
            <span className="dim" style={{ fontSize: 11.5 }}>Partner Desk</span>
          </div>
        </div>

        <nav className="nav">
          <span className="nav-group">Manage</span>
          <Item to="/" icon="overview" label="Overview" end />
          <Item to="/accounts" icon="accounts" label="Accounts" badge={pending || null} />
          {owner ? <Item to="/partners" icon="partners" label="Partners" /> : null}

          <span className="nav-group">Support</span>
          {owner ? <Item to="/logs/backend" icon="server" label="Server logs" /> : null}
          <Item to="/logs/connector" icon="connector" label="Connector logs" />
          <Item to="/activity" icon="activity" label="Activity" />

          <span className="nav-group">You</span>
          <Item to="/settings" icon="settings" label="Settings" />
        </nav>

        <div className="side-foot">
          <div className="avatar" aria-hidden="true">{initials(me.full_name, me.email)}</div>
          <div className="stack grow">
            <strong className="truncate">{me.full_name || me.email}</strong>
            <span className="dim truncate" style={{ fontSize: 11.5 }}>
              {owner ? 'Owner' : 'Partner'}
            </span>
          </div>
          <ThemeToggle />
          <button type="button" className="btn btn-ghost btn-sm" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </aside>

      {/* Only reachable below the sidebar's breakpoint; hidden otherwise. */}
      <button
        type="button"
        className="drawer-scrim"
        aria-label="Close menu"
        onClick={() => setOpen(false)}
      />

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="btn btn-ghost btn-sm menu-button"
            aria-label="Menu"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            ☰
          </button>
          <span className="grow" />
          {pending > 0 ? (
            <NavLink to="/accounts?status=pending" className="topbar-alert">
              {pending} account{pending === 1 ? '' : 's'} waiting on you
            </NavLink>
          ) : null}
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
