import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/widgets/primitives.dart';

/// The mark has two forms and picks between them on size alone.
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
  testWidgets('the large form sets the name on one line over a rule',
      (WidgetTester tester) async {
    await _pump(tester, 76);

    expect(find.text('TallyFlow'), findsOneWidget);
    expect(find.text('Tally'), findsNothing);
  });

  testWidgets('the small form stacks Flow beneath Tally',
      (WidgetTester tester) async {
    // 38 is the size the wide-window rail draws, and the one a launcher icon
    // is closest to.
    await _pump(tester, 38);

    expect(find.text('Tally'), findsOneWidget);
    expect(find.text('Flow'), findsOneWidget);
    expect(find.text('TallyFlow'), findsNothing);

    // Stacked, not side by side.
    final Offset tally = tester.getCenter(find.text('Tally'));
    final Offset flow = tester.getCenter(find.text('Flow'));
    expect(flow.dy, greaterThan(tally.dy));
  });

  testWidgets('both forms fit inside their tile', (WidgetTester tester) async {
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
