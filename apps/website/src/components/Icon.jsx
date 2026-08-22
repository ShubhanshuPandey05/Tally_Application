/*
 * A small, closed set of glyphs.
 *
 * Deliberately not an icon library: every one of these sits inside a coloured
 * tile at around 19px, and a pack drawn for 24px outlines turns to mush at that
 * size. Each is drawn on the same 24px grid at the same stroke weight, so no
 * one of them reads as heavier or more important than the rest.
 *
 * The report glyphs mirror the ones the app draws for the same report. That
 * pairing is the only reason the index is on the site at all: somebody should
 * be able to recognise here what they will later tap there.
 */
const PATHS = {
  chart: 'M4 19V10M10 19V5M16 19v-6M4 19h16',
  wallet: 'M3 8.5A2.5 2.5 0 0 1 5.5 6H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5.5A2.5 2.5 0 0 1 3 16.5v-8ZM16 12.5h2',
  clock: 'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  lock: 'M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z',
  branch: 'M12 4v6m0 0H6v4m6-4h6v4M6 20v-2m12 2v-2M12 20v-6',
  alert: 'M12 8v5m0 3h.01M10.3 4.3 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0Z',
  windows: 'M4 6.2 10.5 5.3v6.2H4V6.2ZM4 17.8l6.5.9v-6.2H4v5.3ZM11.8 5.1 20 4v7.5h-8.2V5.1ZM11.8 12.5H20V20l-8.2-1.1v-6.4Z',
  android: 'M6 10h12v7a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-7ZM8 10a4 4 0 0 1 8 0M9 5 8 3M15 5l1-2',
  apple: 'M16.4 12.7c0-2.4 2-3.6 2.1-3.6-1.1-1.7-2.9-1.9-3.5-1.9-1.5-.2-2.9.9-3.6.9-.8 0-1.9-.9-3.1-.8-1.6 0-3 .9-3.8 2.4-1.7 2.9-.4 7.1 1.2 9.4.8 1.1 1.7 2.4 3 2.3 1.2 0 1.6-.7 3.1-.7s1.9.7 3.1.7c1.3 0 2.1-1.1 2.9-2.3.9-1.3 1.3-2.6 1.3-2.6s-2.5-1-2.7-3.8Z',
  check: 'M5 13l4 4L19 7',
  download: 'M12 4v12m0 0 5-5m-5 5-5-5M4 20h16',
  phone: 'M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1ZM10 18h4',
  cloud: 'M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1.3A3.7 3.7 0 0 1 18 18H7Z',
  box: 'M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8ZM3 4h18v4H3zM10 12h4',
  receipt: 'M6 3h12v18l-2-1.5L14 21l-2-1.5L10 21l-2-1.5L6 21V3ZM9 8h6M9 12h6',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM2.5 20a6.5 6.5 0 0 1 13 0M16 11a3 3 0 0 0 0-6M18 20a5.5 5.5 0 0 0-2-4.3',
  bank: 'M3 10h18M5 10v8m4-8v8m6-8v8m4-8v8M3 20h18M12 3 3 8h18l-9-5Z',
  // Money in and money out: one diagonal mirrored, so receivables and payables
  // read as a pair rather than as two unrelated arrows.
  'arrow-in': 'M19 5 9 15M15 15H9V9',
  'arrow-out': 'M5 19 15 9M9 9h6v6',
  truck:
    'M3 6h11v9H3zM14 10h3.4l2.6 3v2H14zM9.5 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM19.5 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  'warn-circle': 'M12 8v4.5m0 3.5h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  hourglass: 'M6 3h12M6 21h12M8 3v3.8l4 5.2 4-5.2V3M8 21v-3.8l4-5.2 4 5.2V21',
};

export default function Icon({ name, className = '' }) {
  const d = PATHS[name] ?? PATHS.chart;
  const filled = name === 'apple' || name === 'windows';
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      aria-hidden="true"
      fill={filled ? 'currentColor' : 'none'}
      stroke={filled ? 'none' : 'currentColor'}
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  );
}
