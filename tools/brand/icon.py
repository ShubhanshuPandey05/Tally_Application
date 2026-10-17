"""The Prime Analyst icon, in every shape the products need.

The mark is the name: heavy "Prime" over light "Analyst" in Sora, stretched to
one width, with the app's blue bar beneath (decided 2026-10-16; it was a circle
cut in two). The letters are outlines from `wordmark.py`, never live text --
a favicon cannot load a font.

One drawing, five jobs:

  * the website favicon    -- ink and blue on white, with a hairline, because
                              a white tile on a white tab strip is not visible
  * the app launcher icon  -- white and blue on black, the colourway the app
                              uses for its own tile everywhere else
  * an iOS square          -- iOS rounds the corners itself and rejects an alpha
                              channel, so that one is full-bleed
  * a maskable web icon    -- pulled in so a circular crop clips nothing
  * an Android foreground  -- the name on nothing, pulled in to the area the
                              adaptive-icon mask is guaranteed to leave
"""

from __future__ import annotations

import math

import wordmark

BOX = 512.0

# Radius carried over from the marks before this one, so the silhouette on a
# home screen does not change under somebody who already has the app installed.
RADIUS = BOX * 0.2656

# Android reserves the outer ring of an adaptive icon for the launcher's mask.
# Only the middle 66 of 108 units is guaranteed to survive every OEM shape.
SAFE = 66 / 108

# A maskable web icon's guaranteed area is a circle 80% of the icon across.
MASKABLE_SAFE = 0.8

INK = "#0e0f11"

# Two blues, because the bar has to hold against the tile behind it: the app's
# primary on white, and the lighter chart blue on black, where the primary is
# too dark to read.
BLUE_ON_WHITE = "#2D5BFF"
BLUE_ON_BLACK = "#6887ff"


def _layout(fill: str, accent: str) -> str:
    """The name and the bar, placed in the BOX square."""
    prime = wordmark.svg_path(wordmark.line(0))
    analyst = wordmark.svg_path(wordmark.line(1))
    x, y, w, h, r = wordmark.BAR
    return (
        f'<path fill="{fill}" d="{prime}{analyst}"/>'
        f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{r:g}" fill="{accent}"/>'
    )


def _fit_circle(diameter_share: float) -> str:
    """A transform that centres the name inside a circle of that share of BOX.

    For the two icons a launcher crops: the name is a wide block, so it is the
    corners of that block, not its width, that a circular mask takes first.
    """
    left, top, right, bottom = wordmark.bounds()
    cx, cy = (left + right) / 2, (top + bottom) / 2
    half_diagonal = math.hypot((right - left) / 2, (bottom - top) / 2)
    scale = (BOX * diameter_share / 2) / half_diagonal
    tx = BOX / 2 - cx * scale
    ty = BOX / 2 - cy * scale
    return f"translate({tx:.2f} {ty:.2f}) scale({scale:.5f})"


def svg(
    *,
    fill: str,
    accent: str,
    tile: str | None,
    rounded: bool,
    hairline: str | None,
    safe: float | None,
) -> str:
    body = _layout(fill, accent)
    if safe is not None:
        body = f'<g transform="{_fit_circle(safe)}">{body}</g>'

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
    "favicon": dict(fill=INK, accent=BLUE_ON_WHITE, tile="#ffffff", rounded=True, hairline="#dcdde3", safe=None),
    "app": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=INK, rounded=True, hairline=None, safe=None),
    "app-square": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=INK, rounded=False, hairline=None, safe=None),
    "app-maskable": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=INK, rounded=False, hairline=None, safe=MASKABLE_SAFE),
    "adaptive-fg": dict(fill="#ffffff", accent=BLUE_ON_BLACK, tile=None, rounded=False, hairline=None, safe=SAFE),
}


def build(name: str) -> str:
    return svg(**VARIANTS[name])
