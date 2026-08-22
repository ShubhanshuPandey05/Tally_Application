import { useState } from 'react';

import Logo from './Logo.jsx';
import { Link, useRoute } from '../router.jsx';
import { useScrolled } from '../hooks.js';
import './nav.css';

const LINKS = [
  { to: '/#product', label: 'Product' },
  { to: '/#how', label: 'How it works' },
  { to: '/docs', label: 'Setup guide' },
];

export default function Nav() {
  const lined = useScrolled(4);
  const route = useRoute();
  const [open, setOpen] = useState(false);

  const close = () => setOpen(false);

  return (
    <header className={`nav ${lined ? 'is-lined' : ''}`}>
      <div className="shell nav-inner">
        <Link to="/" onClick={close} aria-label="TallyFlow home">
          <Logo light />
        </Link>

        <nav className="nav-links" aria-label="Primary">
          {LINKS.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              aria-current={route === link.to ? 'page' : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <Link className="btn btn-primary nav-cta" to="/#download">
          Download
        </Link>

        <button
          className="nav-burger"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
        >
          <span />
          <span />
          <span />
        </button>
      </div>

      <div className={`shell nav-sheet ${open ? 'is-open' : ''}`}>
        {LINKS.map((link) => (
          <Link key={link.to} to={link.to} onClick={close}>
            {link.label}
          </Link>
        ))}
        <Link className="btn btn-primary" to="/#download" onClick={close}>
          Download
        </Link>
      </div>
    </header>
  );
}
