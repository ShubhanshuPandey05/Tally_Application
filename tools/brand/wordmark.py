"""The name, as outlines.

The mark is the name set in Sora -- heavy "Prime" over light "Analyst", both
stretched to the same width, with the app's blue bar under them (option E in
the logo sheet, decided 2026-10-16). It is drawn from outlines rather than set
in a font, for the same two reasons the old mark was drawn from arcs: a favicon
cannot load a font, and the app has to draw its own logo on the days the shop's
connection is down.

So the font is read here, once, at build time, and every consumer gets the
shapes: `icon.py` as SVG path data, and the app as a generated Dart file
(`apps/mobile/lib/src/core/widgets/brand_outlines.dart`). Sora is under the
SIL Open Font License; the licence sits beside the font in `fonts/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONT = Path(__file__).with_name("fonts") / "Sora[wght].ttf"

#: Where each line sits in the 512 square, copied from the approved sheet:
#: (text, weight, font size, baseline). Both lines are stretched to WIDTH, the
#: way SVG's ``textLength`` with ``spacingAndGlyphs`` does it.
LINES = (
    ("Prime", 800, 124.0, 238.0),
    ("Analyst", 300, 96.0, 346.0),
)
LEFT = 76.0
WIDTH = 360.0

#: The blue bar under the name: x, y, width, height, corner radius.
BAR = (76.0, 388.0, 110.0, 16.0, 3.0)


@dataclass(frozen=True)
class Segment:
    """One drawing step, in 512-unit coordinates. ``op`` is M, L, Q, C or Z."""

    op: str
    points: tuple[tuple[float, float], ...]


class _Collect(BasePen):
    def __init__(self, glyphs, transform) -> None:
        super().__init__(glyphs)
        self.out: list[Segment] = []
        self._t = transform

    def _pt(self, p):
        return self._t(p)

    def _moveTo(self, p):
        self.out.append(Segment("M", (self._pt(p),)))

    def _lineTo(self, p):
        self.out.append(Segment("L", (self._pt(p),)))

    def _qCurveToOne(self, p1, p2):
        self.out.append(Segment("Q", (self._pt(p1), self._pt(p2))))

    def _curveToOne(self, p1, p2, p3):
        self.out.append(Segment("C", (self._pt(p1), self._pt(p2), self._pt(p3))))

    def _closePath(self):
        self.out.append(Segment("Z", ()))


@cache
def _font(weight: int) -> TTFont:
    return instantiateVariableFont(TTFont(FONT), {"wght": weight})


def line(index: int) -> list[Segment]:
    """One line of the name, placed and stretched exactly as on the sheet."""
    text, weight, size, baseline = LINES[index]
    font = _font(weight)
    upm = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    names = [cmap[ord(ch)] for ch in text]
    advance = sum(font["hmtx"][name][0] for name in names)

    sy = size / upm
    sx = WIDTH / advance  # textLength stretches glyphs and spacing together
    out: list[Segment] = []
    cursor = 0.0
    for name in names:
        def place(p, x0=cursor):
            return (round(LEFT + (x0 + p[0]) * sx, 2), round(baseline - p[1] * sy, 2))

        pen = _Collect(glyphs, place)
        glyphs[name].draw(pen)
        out.extend(pen.out)
        cursor += font["hmtx"][name][0]
    return out


def svg_path(segments: list[Segment]) -> str:
    parts = []
    for seg in segments:
        coords = " ".join(f"{x:g} {y:g}" for x, y in seg.points)
        parts.append(f"{seg.op}{coords}")
    return "".join(parts)


def bounds() -> tuple[float, float, float, float]:
    """The box the name and bar occupy: left, top, right, bottom."""
    xs, ys = [], []
    for i in range(len(LINES)):
        for seg in line(i):
            for x, y in seg.points:
                xs.append(x)
                ys.append(y)
    bx, by, bw, bh, _ = BAR
    xs += [bx, bx + bw]
    ys += [by, by + bh]
    return min(xs), min(ys), max(xs), max(ys)
