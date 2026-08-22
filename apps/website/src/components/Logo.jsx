/*
 * The mark: the name in a handwriting face, over a rule, on a tile.
 *
 * Two constraints shaped this, and both are worth keeping.
 *
 * The first: the mark before it -- bars in a squircle -- was the shape half the
 * analytics industry already uses, and a logo that makes a reader think of
 * another product is doing the opposite of its job.
 *
 * The second: it is deliberately *not* a version of TallyPrime's own logo.
 * Theirs is a script "Tally" over a swoosh over a plain second word, and
 * rebuilding that lockup with "Flow" in place of "Prime" would produce a mark
 * readers would reasonably take for an official Tally Solutions product --
 * which is the exact claim the footer on this site disclaims. Hence a straight
 * rule rather than a swoosh, one handwriting face throughout rather than a
 * script word above a typeset one, and letterforms that look nothing like
 * theirs.
 *
 * `light` inverts it to black on white, for surfaces that already carry a dark
 * element and do not need a second black block.
 *
 * There is a second, stacked form -- `Tally` with `Flow` beneath it -- for
 * square icons, where a name across a 32px tile gives each letter about two
 * pixels. It is not a component: it has to render without a webfont, so it
 * lives as outlines in `tools/brand` and is generated into the favicon, the
 * touch icon and the app's launcher icons.
 */
export default function Logo({ light = false }) {
  return (
    <span className={`logo ${light ? 'logo-light' : ''}`} aria-label="TallyFlow">
      <span className="logo-word">TallyFlow</span>
      <span className="logo-rule" />
    </span>
  );
}
