import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/widgets/primitives.dart';

/// The mark is one drawing at every size.
///
/// Worth a test because the small form is what a launcher icon shows, and the
/// failure mode is silent: a wordmark that no longer fits its tile overflows by
/// a few pixels, which in release is a clipped logo rather than an error.
Future<void> _pump(WidgetTester tester, double size, {bool inverted = false}) {
  return tester.pumpWidget(
    MaterialApp(
      theme: AppTheme.forMode(AppThemeMode.light),
      home: Scaffold(
        body: Center(child: BrandMark(size: size, inverted: inverted)),
      ),
    ),
  );
}

void main() {
  testWidgets('the mark is a drawing, with no lettering in it',
      (WidgetTester tester) async {
    // 38 is the size the wide-window rail draws, and the one a launcher icon
    // is closest to; 76 is the sign-in screen.
    for (final double size in <double>[38, 76]) {
      await _pump(tester, size);

      expect(
        find.descendant(
          of: find.byType(BrandMark),
          matching: find.byType(CustomPaint),
        ),
        findsOneWidget,
      );
      // No font to fall back from on a day the connection is down, and no
      // "Prime" for anybody to mistake for TallyPrime's.
      expect(
        find.descendant(of: find.byType(BrandMark), matching: find.byType(Text)),
        findsNothing,
      );
    }
  });

  testWidgets('the tile is square at every size it is drawn', (WidgetTester tester) async {
    for (final double size in <double>[28, 38, 55, 56, 76, 120]) {
      await _pump(tester, size);
      final Size tile = tester.getSize(find.byType(BrandMark));
      expect(tile, Size(size, size), reason: 'tile is square at $size');
      // pumpWidget rethrows a layout overflow, so reaching here means the
      // contents fit; this asserts the tile itself did not grow to hold them.
    }
  });

  testWidgets('inverted swaps the tile and the ink', (WidgetTester tester) async {
    await _pump(tester, 76, inverted: true);

    final Container tile = tester.widget<Container>(
      find.descendant(
        of: find.byType(BrandMark),
        matching: find.byType(Container),
      ).first,
    );
    final BoxDecoration decoration = tile.decoration! as BoxDecoration;
    expect(decoration.color, Colors.white);
    // A white tile needs an edge, or it disappears into a white page.
    expect(decoration.border, isNotNull);
  });
}
