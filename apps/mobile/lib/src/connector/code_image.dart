import 'package:flutter/material.dart';

/// The pairing code, drawn to be read off a monitor by a phone held at arm's
/// length.
///
/// Three rules, and all three are about the same failure -- a code that looks
/// perfect on screen and simply does nothing when scanned, which the machine
/// drawing it has no way to detect.
///
/// **Whole *device* pixels**, not whole logical ones. A module is sized to a
/// whole number of the screen's real pixels and the symbol is centred in
/// whatever is left over, so every module edge lands on a pixel boundary and
/// the painter has nothing to antialias between neighbours. Rounding in logical
/// pixels would look right until the first machine at 125% scaling, which is
/// the Windows default on a laptop -- and a half-pixel seam between two dark
/// modules is the difference between a code that scans from a step back and one
/// the owner has to hold their phone against the glass for.
///
/// **Black on white, whatever the window looks like.** Never an inherited
/// colour and never a transparent quiet zone. A code taking its colours from
/// the surrounding surface would be invisible on exactly the machines whose
/// owner runs a dark desktop, and a scanner cannot report that as anything
/// other than "nothing here".
///
/// **The quiet zone is part of the grid**, not padding around it. It arrives in
/// the rows from the connector precisely so that this widget cannot omit it;
/// a symbol butted against its background frequently will not read at all.
class PairingCodeImage extends StatelessWidget {
  const PairingCodeImage({super.key, required this.rows, this.size = 300});

  /// Rows of `0` and `1` from the connector, quiet zone included. Empty while a
  /// code is being fetched or has just been retired.
  final List<String> rows;

  /// The side of the square, in logical pixels. The drawn symbol is at most
  /// this and usually a little less, because the module size is rounded down.
  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox.square(
      dimension: size,
      child: rows.isEmpty
          ? const DecoratedBox(decoration: BoxDecoration(color: Colors.white))
          : CustomPaint(
              painter: _CodePainter(
                rows,
                MediaQuery.devicePixelRatioOf(context),
              ),
            ),
    );
  }
}

class _CodePainter extends CustomPainter {
  const _CodePainter(this.rows, this.pixelRatio);

  final List<String> rows;

  /// Logical pixels per device pixel. The whole point of this painter is to
  /// land module edges on device pixels, which cannot be done without it.
  final double pixelRatio;

  @override
  void paint(Canvas canvas, Size size) {
    // The white ground covers the whole box, not just the symbol. The leftover
    // from rounding the module size down would otherwise show the window's own
    // surface as a ragged frame a millimetre wide, inside the quiet zone the
    // scanner is trying to measure.
    final Paint ground = Paint()
      ..color = Colors.white
      ..isAntiAlias = false;
    canvas.drawRect(Offset.zero & size, ground);

    final int span = rows.length;
    final double ratio = pixelRatio <= 0 ? 1 : pixelRatio;

    // Sized in device pixels, then converted back. At 125% scaling -- the
    // Windows default on a laptop -- a module rounded to a whole logical pixel
    // is 1.25 device pixels, and every second module edge falls mid-pixel.
    final double devicePerModule =
        (size.shortestSide * ratio / span).floorToDouble().clamp(1, double.infinity);
    final double module = devicePerModule / ratio;
    final double drawn = module * span;

    // Centred on device pixels as well: a half-pixel origin would undo the
    // rounding the module size just bought.
    final double left = ((size.width - drawn) / 2 * ratio).floorToDouble() / ratio;
    final double top = ((size.height - drawn) / 2 * ratio).floorToDouble() / ratio;

    final Paint dark = Paint()
      ..color = Colors.black
      ..isAntiAlias = false;

    for (int y = 0; y < span; y++) {
      final String row = rows[y];
      int x = 0;
      while (x < row.length) {
        if (row.codeUnitAt(x) != 0x31) {
          x++;
          continue;
        }
        // Runs rather than modules. A code this size is about 1,400 dark
        // modules; drawing each as its own rect leaves a seam wherever two
        // meet, and gives the raster fifteen times more edges to find.
        final int start = x;
        while (x < row.length && row.codeUnitAt(x) == 0x31) {
          x++;
        }
        canvas.drawRect(
          Rect.fromLTWH(
            left + start * module,
            top + y * module,
            (x - start) * module,
            module,
          ),
          dark,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_CodePainter old) =>
      !identical(old.rows, rows) || old.pixelRatio != pixelRatio;
}
