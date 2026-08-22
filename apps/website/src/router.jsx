import { useEffect, useState } from 'react';

/*
 * A two-page router in thirty lines, rather than a dependency.
 *
 * The site has exactly two documents — the pitch and the setup guide — and both
 * are static. Caddy already serves index.html for any unmatched path
 * (`try_files {path} /index.html`), so /docs survives a refresh and a shared
 * link. That, plus the History API, is the whole requirement.
 */

function currentPath() {
  if (typeof window === 'undefined') return '/';
  return window.location.pathname.replace(/\/+$/, '') || '/';
}

export function useRoute() {
  const [path, setPath] = useState(currentPath);

  useEffect(() => {
    const onPop = () => setPath(currentPath());
    window.addEventListener('popstate', onPop);
    window.addEventListener('tf:navigate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('tf:navigate', onPop);
    };
  }, []);

  return path;
}

export function navigate(to) {
  const [pathname, hash = ''] = to.split('#');
  window.history.pushState({}, '', to);
  window.dispatchEvent(new Event('tf:navigate'));

  if (hash) {
    // Let the new page paint before hunting for the anchor.
    requestAnimationFrame(() => {
      document.getElementById(hash)?.scrollIntoView({ behavior: 'smooth' });
    });
  } else if (pathname !== window.location.pathname || !hash) {
    window.scrollTo({ top: 0 });
  }
}

/** An internal link. Falls back to a normal anchor for modified clicks. */
export function Link({ to, children, onClick, ...rest }) {
  const handle = (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    onClick?.(event);
    navigate(to);
  };
  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
