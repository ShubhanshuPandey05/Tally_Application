"""Turn the TallyFlow wordmark into outlines.

The logo is set in Caveat, and neither a favicon nor a launcher icon can rely on
a font being present -- an icon that falls back to a system script is not the
logo. So the two words are converted to paths once, here, and the result is
baked into the files that ship. Re-run `generate.py` after changing either.
"""

from __future__ import annotations

from pathlib import Path

import uharfbuzz as hb
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# The same file the app bundles, so the icon and the wordmark drawn inside the
# app are one drawing rather than two things that merely resemble each other.
FONT = Path(__file__).resolve().parents[2] / "apps/mobile/assets/fonts/Caveat-Bold.ttf"


def _shape(text: str):
    """Positioned glyph ids, via HarfBuzz.

    Not naive advance widths: Caveat is a connected script and leans on GPOS to
    close the gaps between letters. Laying it out without kerning produces a
    wordmark that is recognisably not the one on the website.
    """
    blob = hb.Blob.from_file_path(str(FONT))
    font = hb.Font(hb.Face(blob))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)

    out, x = [], 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        out.append((info.codepoint, x + pos.x_offset, pos.y_offset))
        x += pos.x_advance
    return out


def word(text: str) -> tuple[str, tuple[float, float, float, float]]:
    """SVG path data for `text`, y-down, together with its ink bounding box.

    The bounds are of the ink, not of the font's metric box. What should sit in
    the middle of a tile is the shape somebody can see; centring on metrics
    leaves a script face looking dropped a few pixels low.
    """
    tt = TTFont(FONT)
    glyphs = tt.getGlyphSet()
    order = tt.getGlyphOrder()

    # Integer font units. At 1000 per em that is finer than any size these icons
    # are drawn at, and it keeps the favicon a few kilobytes smaller.
    svg = SVGPathPen(glyphs, ntos=lambda v: str(round(v)))
    bounds = BoundsPen(glyphs)
    for gid, dx, dy in _shape(text):
        name = order[gid]
        # Font units run y-up and SVG runs y-down, so the glyph is flipped and
        # offset in a single transform.
        flip = Transform(1, 0, 0, -1, dx, -dy)
        glyphs[name].draw(TransformPen(svg, flip))
        glyphs[name].draw(TransformPen(bounds, flip))

    return svg.getCommands(), bounds.bounds
