import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/widgets/cards.dart';
import 'package:tallyflow/src/core/widgets/charts.dart';

void main() {
  testWidgets('a stat carrying only a chip lays out instead of asserting',
      (WidgetTester tester) async {
    // The change column on the dashboard's trend card puts its whole answer in
    // the chip and leaves the value empty. An empty string measures zero wide,
    // and the `FittedBox` around the figure asserts on a zero-width child --
    // which failed the entire card and every sliver below it, so a dashboard
    // that had a comparable previous period rendered blank.
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: StatStrip(
            stats: <Stat>[
              Stat(label: 'This month', value: '\u20b91.2L'),
              Stat(label: 'Change', value: '', trailing: ChangeChip(changePct: 12.5)),
            ],
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    expect(find.text('Change'), findsOneWidget);
  });
}
