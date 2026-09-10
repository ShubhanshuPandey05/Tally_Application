"""Turning the pairing code into something a phone camera can read.

Only the module grid is produced here. The window paints it, because the thing
this ends up on is a **monitor** and the drawing has to be pixel-exact: modules
land on whole-number rectangles on a whole-number grid so there is nothing to
antialias between neighbours, and a half-pixel seam between two dark modules is
the difference between a code that scans from a step back and one the owner has
to hold their phone against the glass for.

The grid is built here rather than in the window for one reason: the two
decisions below are load-bearing and were both paid for once already. Keeping
them beside the encoder means a second front end cannot quietly get them wrong.
"""

from __future__ import annotations

import segno

#: Error correction level. ``M`` recovers about 15% and keeps the symbol small.
#: Higher would let the code survive a dirtier screen at the cost of more
#: modules, which is the wrong trade: the failure here is a camera too far away,
#: and more modules makes that worse rather than better.
ERROR_LEVEL = "m"

#: Blank modules around the symbol. Four is the specification's number and it is
#: not decoration -- a scanner needs the margin to find the symbol's edges, and
#: a code butted against its background frequently will not read at all. It is
#: included in the grid returned here so that a front end cannot omit it by
#: drawing the matrix edge to edge.
QUIET_ZONE = 4


def matrix_rows(payload: str) -> list[str]:
    """``payload`` as rows of ``0`` and ``1``, quiet zone included.

    A string per row rather than a list of booleans: this crosses a JSON
    boundary on every poll while a code is on screen, and ``"0101..."`` is a
    twelfth the size of ``[false, true, false, true, ...]`` for the same grid.
    """
    # ``micro=False`` is not a preference. Left to choose, the encoder drops to
    # a Micro QR for a short payload, and Micro QR is a different symbology that
    # a good many phone cameras -- including ones that read every other code
    # they are shown -- simply do not decode. The failure is a code that looks
    # perfect on screen and does nothing when scanned, which is unfalsifiable
    # from the machine drawing it.
    matrix = segno.make(payload, error=ERROR_LEVEL, micro=False).matrix
    span = len(matrix) + QUIET_ZONE * 2

    blank = "0" * span
    rows = [blank] * QUIET_ZONE
    pad = "0" * QUIET_ZONE
    rows.extend(pad + "".join("1" if dark else "0" for dark in row) + pad for row in matrix)
    rows.extend([blank] * QUIET_ZONE)
    return rows
