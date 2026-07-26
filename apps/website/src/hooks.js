import { useEffect, useRef, useState } from 'react';

const REDUCED =
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Reveals every `.reveal` in the document as it scrolls into view.
 *
 * One observer for the whole page rather than a hook per component: the set of
 * revealed nodes is large and mostly static, and a shared observer means adding
 * a section is a class name, not wiring.
 */
export function useScrollReveal() {
  useEffect(() => {
    const nodes = document.querySelectorAll('.reveal:not(.in)');
    if (REDUCED) {
      nodes.forEach((n) => n.classList.add('in'));
      return undefined;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('in');
          io.unobserve(entry.target);
        });
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.08 },
    );

    nodes.forEach((n) => io.observe(n));
    return () => io.disconnect();
  }, []);
}

/** Counts from 0 to `to` once the element is on screen. Returns [ref, value]. */
export function useCountUp(to, { duration = 1400, decimals = 0 } = {}) {
  const ref = useRef(null);
  const [value, setValue] = useState(REDUCED ? to : 0);

  useEffect(() => {
    if (REDUCED) {
      setValue(to);
      return undefined;
    }
    const el = ref.current;
    if (!el) return undefined;

    let raf = 0;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        const start = performance.now();
        const tick = (now) => {
          const t = Math.min(1, (now - start) / duration);
          // easeOutExpo: fast enough to feel responsive, settles rather than stops.
          const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
          const next = to * eased;
          setValue(decimals ? Number(next.toFixed(decimals)) : Math.round(next));
          if (t < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );

    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [to, duration, decimals]);

  return [ref, value];
}

/** Moves the pointer-follow highlight on `.card-glow` elements. */
export function usePointerGlow() {
  useEffect(() => {
    if (REDUCED) return undefined;
    const onMove = (event) => {
      const card = event.target.closest?.('.card-glow');
      if (!card) return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${event.clientX - rect.left}px`);
      card.style.setProperty('--my', `${event.clientY - rect.top}px`);
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => window.removeEventListener('pointermove', onMove);
  }, []);
}

/** True once the page has scrolled past `offset` — used to condense the nav. */
export function useScrolled(offset = 24) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > offset);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [offset]);
  return scrolled;
}

/** Indian digit grouping (1,23,45,678) — the shape these users read. */
export function inr(value, { compact = false } = {}) {
  if (compact && value >= 10000000) return `${(value / 10000000).toFixed(2)} Cr`;
  if (compact && value >= 100000) return `${(value / 100000).toFixed(2)} L`;
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(value);
}
