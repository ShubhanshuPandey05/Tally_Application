import { useEffect, useState } from 'react';

import { PUBLISHED } from './data/downloads.js';

/** True once the page has scrolled past `offset` — used to line the header. */
export function useScrolled(offset = 8) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > offset);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [offset]);
  return scrolled;
}

/**
 * The published release manifest, or the built-in fallback until it arrives.
 *
 * Failing quietly to the fallback is the point: a missing or malformed manifest
 * must never leave a visitor with no way to download the thing, and it must
 * never make the page claim a version that is not there. Same rule the backend
 * follows — an unreadable manifest means "no opinion", not "you are stuck".
 */
export function useManifest() {
  const [manifest, setManifest] = useState(PUBLISHED);

  useEffect(() => {
    let live = true;
    fetch('/downloads/manifest.json', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!live || !data) return;
        // Only take a section that has the three fields a download row needs.
        const next = { ...PUBLISHED };
        for (const key of ['connector', 'android']) {
          const entry = data[key];
          if (entry?.version && entry?.url && entry?.size_bytes) next[key] = entry;
        }
        setManifest(next);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  return manifest;
}

/** Binary megabytes, the unit Windows and Android both report. */
export function mb(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** First and last eight characters of a checksum — enough to compare by eye. */
export function shortHash(sha256) {
  if (!sha256 || sha256.length < 24) return null;
  return `${sha256.slice(0, 8)}…${sha256.slice(-8)}`;
}

/**
 * Live counts from the backend, or `null` while unknown.
 *
 * `null` and `0` are different answers and the caller must be able to tell them
 * apart: zero businesses is a fact worth printing, and "we could not ask" is
 * not. So a failed, disabled or unreachable endpoint leaves this null and the
 * hero simply does not draw the row — it never falls back to zeros, which would
 * be inventing the most damaging number on the page.
 */
export function useStats() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let live = true;
    fetch('/v1/public/stats', { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!live || !data) return;
        // Guard on the shape rather than on the status alone: a static host with
        // no API answers /v1/public/stats with its own index.html and a 200.
        if (typeof data.businesses !== 'number') return;
        setStats(data);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  return stats;
}
