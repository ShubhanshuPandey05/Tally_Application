"""The Prime Analyst icon, in every shape the products need.

The mark is one circle cut in two with the halves slipped against each other:
double entry drawn as a shape. A debit and a credit, the same size, making a
whole.

One drawing, four jobs:

  * the website favicon    -- ink and blue on white, with a hairline, because
                              a white tile on a white tab strip is not visible
  * the app launcher icon  -- white and blue on black, the colourway the app
                              uses for its own tile everywhere else
  * an iOS square          -- iOS rounds the corners itself and rejects an alpha
                              channel, so that one is full-bleed
  * an Android foreground  -- the two halves on nothing, pulled in to the area
                              the adaptive-icon mask is guaranteed to leave

No lettering, and so no font: the name was once set in the tile, and thirteen
letters across a 32px favicon gave each one barely more than a pixel. Two arcs
read at 16px.
"""

from __future__ import annotations

BOX = 512.0

# Radius carried over from the mark this replaces, so the silhouette on a home
# screen does not change under somebody who already has the app installed.
RADIUS = BOX * 0.2656

# Android reserves the outer ring of an adaptive icon for the launcher's mask.
# Only the middle 66 of 108 units is guaranteed to survive every OEM shape.
SAFE = 66 / 108

INK = "#0e0f11"

# Two blues, because the half has to hold against the tile behind it: the app's
# primary on white, and the lighter chart blue on black, where the primary is
# too dark to read as a second half.
BLUE_ON_WHITE = "#2d5bff"
BLUE_ON_BLACK = "#6887ff"

# One circle of this radius, cut down the middle. The left half sits low and
# the right half high; the gap between their straight edges is what makes them
# read as two pieces of one circle rather than as a circle with a step in it.
# `BrandMark` in the app draws the same numbers -- change both or neither.
HALF = 130
LOWER = (250, 286)
UPPER = (262, 226)


def _layout(fill: str, accent: str) -> str:
    """Both halves, placed in the BOX square."""
    lx, ly = LOWER
    ux, uy = UPPER
    return (
        f'<path fill="{fill}" d="M{lx} {ly - HALF}A{HALF} {HALF} 0 0 0 {lx} {ly + HALF}Z"/>'
        f'<path fill="{accent}" d="M{ux} {uy - HALF}A{HALF} {HALF} 0 0 1 {ux} {uy + HALF}Z"/>'
    )


def svg(
    *,
    fill: str,
    accent: str,
    tile: str | None,
    rounded: bool,
    hairline: str | None,
    scale: float,
) -> str:
    body = _layout(fill, accent)
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
    parts.append(f"{body}</svg>")
    return "".join(parts)


VARIANTS: dict[str, dict] = {
    "favicon": dict(fill=INK, accent=BLUE_ON_WHITE, tile="#ffffff", rounded=True, hairline="#e2e3e8", scale=1.0),
    "app": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=INK, rounded=True, hairline=None, scale=1.0),
    "app-square": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=INK, rounded=False, hairline=None, scale=1.0),
    # A maskable web icon is cropped to a circle by some launchers, so the mark
    # is pulled in far enough that neither half is clipped.
    "app-maskable": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=INK, rounded=False, hairline=None, scale=0.72),
    "adaptive-fg": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=None, rounded=False, hairline=None, scale=SAFE),
}


def build(name: str) -> str:
    return svg(**VARIANTS[name])
