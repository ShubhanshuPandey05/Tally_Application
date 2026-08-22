"""The TallyFlow icon, in every shape the products need.

One drawing, four jobs:

  * the website favicon    -- black wordmark on white, with a hairline, because
                              a white tile on a white tab strip is not visible
  * the app launcher icon  -- white wordmark on black, the colourway the app
                              uses for its own tile everywhere else
  * an iOS square          -- iOS rounds the corners itself and rejects an alpha
                              channel, so that one is full-bleed
  * an Android foreground  -- white wordmark on nothing, pulled in to the area
                              the adaptive-icon mask is guaranteed to leave

Stacked rather than set on one line: `TallyFlow` across a 32px favicon gives
each letter about two pixels. Two lines roughly double the height per letter,
which is the difference between a mark you can read and a smudge.
"""

from __future__ import annotations

from mark import word

BOX = 512.0

# Radius carried over from the mark this replaces, so the silhouette on a home
# screen does not change under somebody who already has the app installed.
RADIUS = BOX * 0.2656

# How much of the tile the wordmark may fill. Two lines of a script face need
# the width, and the corners of a rounded square are dead space anyway.
INSET = BOX * 0.135

# Baseline to baseline, in em. Tighter than text leading on purpose: this is a
# signature, not a paragraph, and the second word should read as tucked under
# the first rather than as the next line of something.
LEADING = 0.80

# Android reserves the outer ring of an adaptive icon for the launcher's mask.
# Only the middle 66 of 108 units is guaranteed to survive every OEM shape.
SAFE = 66 / 108

INK = "#0e0f11"


def _layout() -> str:
    """Both words, placed in the BOX square."""
    top_d, top_b = word("Tally")
    bottom_d, bottom_b = word("Flow")
    drop = LEADING * 1000

    left = min(top_b[0], bottom_b[0])
    right = max(top_b[2], bottom_b[2])
    top = top_b[1]
    bottom = drop + bottom_b[3]

    width, height = right - left, bottom - top
    room = BOX - 2 * INSET
    scale = min(room / width, room / height)

    x = (BOX - width * scale) / 2 - left * scale
    y = (BOX - height * scale) / 2 - top * scale

    def place(d: str, dy: float) -> str:
        return (
            f'<path transform="translate({x:.2f} {y + dy * scale:.2f}) '
            f'scale({scale:.5f})" d="{d}"/>'
        )

    return place(top_d, 0) + place(bottom_d, drop)


def svg(*, fill: str, tile: str | None, rounded: bool, hairline: str | None, scale: float) -> str:
    body = _layout()
    if scale != 1.0:
        pad = BOX * (1 - scale) / 2
        body = f'<g transform="translate({pad:.2f} {pad:.2f}) scale({scale:.5f})">{body}</g>'

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX:.0f} {BOX:.0f}">']
    if tile:
        radius = f' rx="{RADIUS:.1f}"' if rounded else ""
        parts.append(f'<rect width="{BOX:.0f}" height="{BOX:.0f}"{radius} fill="{tile}"/>')
        if hairline:
            inset = 5.0
            parts.append(
                f'<rect x="{inset:.0f}" y="{inset:.0f}" width="{BOX - 2 * inset:.0f}"'
                f' height="{BOX - 2 * inset:.0f}" rx="{RADIUS - inset:.1f}" fill="none"'
                f' stroke="{hairline}" stroke-width="10"/>'
            )
    parts.append(f'<g fill="{fill}">{body}</g></svg>')
    return "".join(parts)


VARIANTS: dict[str, dict] = {
    "favicon": dict(fill=INK, tile="#ffffff", rounded=True, hairline="#e2e3e8", scale=1.0),
    "app": dict(fill="#ffffff", tile=INK, rounded=True, hairline=None, scale=1.0),
    "app-square": dict(fill="#ffffff", tile=INK, rounded=False, hairline=None, scale=1.0),
    # A maskable web icon is cropped to a circle by some launchers, so the mark
    # is pulled in far enough that the corners of the wordmark are not clipped.
    "app-maskable": dict(fill="#ffffff", tile=INK, rounded=False, hairline=None, scale=0.72),
    "adaptive-fg": dict(fill="#ffffff", tile=None, rounded=False, hairline=None, scale=SAFE),
}


def build(name: str) -> str:
    return svg(**VARIANTS[name])
