/*
 * The mark: one circle cut in two, the halves slipped against each other, on a
 * tile, beside the name.
 *
 * It is double entry drawn as a shape -- a debit and a credit, the same size,
 * making a whole.
 *
 * Two constraints shaped it, and both are worth keeping.
 *
 * The first: an earlier mark -- bars in a squircle -- was the shape half the
 * analytics industry already uses, and a logo that makes a reader think of
 * another product is doing the opposite of its job.
 *
 * The second: the symbol carries no lettering, on purpose. The name shares a
 * word with TallyPrime, and a wordmark in a tile invites setting "Prime" the
 * way theirs is set -- which would read as an official Tally Solutions
 * product, the exact claim the footer on this site disclaims. The name sits
 * beside the tile in a plain sans, heavy then light, nothing like theirs.
 *
 * The path data is the same as `tools/brand/icon.py`, which generates the
 * favicon, the touch icon and the app's launcher icons. Change both or neither.
 */
export default function Logo() {
  return (
    <span className="logo" aria-label="Prime Analyst">
      <svg className="logo-mark" viewBox="0 0 512 512" aria-hidden="true">
        <rect width="512" height="512" rx="136" fill="#0e0f11" />
        <path fill="#fff" d="M250 156A130 130 0 0 0 250 416Z" />
        <path fill="#6887ff" d="M262 96A130 130 0 0 1 262 356Z" />
      </svg>
      <span className="logo-word">
        <b>Prime</b> Analyst
      </span>
    </span>
  );
}
