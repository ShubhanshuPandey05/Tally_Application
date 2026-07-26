import { useEffect, useState } from 'react';
import Logo from './Logo.jsx';
import { useScrolled } from '../hooks.js';
import './nav.css';

const LINKS = [
  { href: '#product', label: 'Product' },
  { href: '#how', label: 'How it works' },
  { href: '#security', label: 'Security' },
  { href: '#download', label: 'Download' },
  { href: '#pricing', label: 'Pricing' },
];

export default function Nav() {
  const scrolled = useScrolled(20);
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const onScroll = () => {
      const max = document.body.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? (window.scrollY / max) * 100 : 0);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  return (
    <header className={`nav ${scrolled ? 'nav-solid' : ''}`}>
      <div className="nav-progress" style={{ transform: `scaleX(${progress / 100})` }} />
      <div className="shell nav-inner">
        <a href="#top" className="nav-brand" onClick={() => setOpen(false)}>
          <Logo size={32} />
        </a>

        <nav className="nav-links" aria-label="Primary">
          {LINKS.map((link) => (
            <a key={link.href} href={link.href}>
              {link.label}
            </a>
          ))}
        </nav>

        <div className="nav-actions">
          <a className="btn btn-ghost nav-signin" href="#download">
            Sign in
          </a>
          <a className="btn btn-primary" href="#download">
            Get TallyFlow
          </a>
        </div>

        <button
          className={`nav-burger ${open ? 'is-open' : ''}`}
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
        >
          <span />
          <span />
        </button>
      </div>

      <div className={`nav-sheet ${open ? 'is-open' : ''}`}>
        {LINKS.map((link, i) => (
          <a
            key={link.href}
            href={link.href}
            style={{ '--i': i }}
            onClick={() => setOpen(false)}
          >
            {link.label}
          </a>
        ))}
        <a
          className="btn btn-primary btn-lg"
          href="#download"
          onClick={() => setOpen(false)}
        >
          Get TallyFlow
        </a>
      </div>
    </header>
  );
}
