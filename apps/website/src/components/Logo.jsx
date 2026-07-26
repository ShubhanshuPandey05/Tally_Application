export default function Logo({ size = 34 }) {
  return (
    <span className="logo" style={{ '--logo-size': `${size}px` }}>
      <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
        <defs>
          <linearGradient id="lg-mark" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#6D8BFF" />
            <stop offset="55%" stopColor="#3A5AF0" />
            <stop offset="100%" stopColor="#8B3AF0" />
          </linearGradient>
        </defs>
        <rect width="64" height="64" rx="17" fill="url(#lg-mark)" />
        <path
          d="M16 41 L26 28 L35 34.5 L47 18"
          fill="none"
          stroke="#fff"
          strokeWidth="5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="47" cy="18" r="5.2" fill="#5AF0C8" />
      </svg>
      <span className="logo-word">
        Tally<span className="grad-text">Flow</span>
      </span>
    </span>
  );
}
