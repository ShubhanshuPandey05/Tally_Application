import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/entries/domain/entry_draft.dart';
import 'package:tallyflow/src/features/entries/presentation/create_entry_button.dart';

/// Where the entry menu actually lands on a phone.
///
/// These assertions are about geometry rather than behaviour, because the first
/// version of this menu analysed clean and passed every behavioural test while
/// rendering completely wrong: the labels overflowed off the left edge of the
/// screen and the icon buttons drifted diagonally instead of stacking. The
/// cause was putting a five-item column inside the Scaffold's
/// `floatingActionButton` slot, which sizes itself to its child.
///
/// A widget test catches that where `flutter analyze` cannot: Flutter reports a
/// RenderFlex overflow as a test failure, and the positions below are checked
/// against the real screen rectangle.
void main() {
  const Size phone = Size(400, 860);

  /// The real button, in a Scaffold shaped like the app shell.
  ///
  /// Not a stand-in opener: the menu anchors itself to wherever the Scaffold
  /// puts the button, and the bug that made the close button jump 82 pixels
  /// was only visible with the real navigation bar in place.
  Widget harness() {
    return ProviderScope(
      overrides: <Override>[
        activeCompanyIdResolvedProvider.overrideWithValue('company-1'),
      ],
      child: const MaterialApp(
        home: Scaffold(
          extendBody: true,
          body: SizedBox.expand(),
          floatingActionButton: CreateEntryButton(),
          // Stands in for the shell's floating navigation bar: 54 high with 12
          // of padding under it.
          bottomNavigationBar: SizedBox(height: 66),
        ),
      ),
    );
  }

  Future<Rect> openMenu(WidgetTester tester) async {
    await tester.binding.setSurfaceSize(phone);
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(harness());
    await tester.pumpAndSettle();

    final Rect opener = tester.getRect(find.byIcon(Icons.add));
    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();
    return opener;
  }

  group('the entry button', () {
    testWidgets('sits above the navigation bar, not over it',
        (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(phone);
      addTearDown(() => tester.binding.setSurfaceSize(null));

      await tester.pumpWidget(harness());
      await tester.pumpAndSettle();

      final Rect button = tester.getRect(find.byIcon(Icons.add));

      // Clear of the 66px bar...
      expect(button.bottom, lessThan(phone.height - 66));
      // ...but not adrift in the middle of the content.
      expect(button.bottom, greaterThan(phone.height - 200));
      expect(button.right, greaterThan(phone.width - 90));
    });

    testWidgets('is hidden until a company is chosen',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: <Override>[
            activeCompanyIdResolvedProvider.overrideWithValue(null),
          ],
          child: const MaterialApp(
            home: Scaffold(
              body: SizedBox.expand(),
              floatingActionButton: CreateEntryButton(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.add), findsNothing);
    });
  });

  group('the entry menu', () {
    testWidgets('offers every kind, each with a readable label',
        (WidgetTester tester) async {
      await openMenu(tester);

      for (final EntryKind kind in EntryKind.values) {
        expect(
          find.text(kind.label),
          findsOneWidget,
          reason: '${kind.label} should be offered',
        );
      }
    });

    testWidgets('lays every option out inside the screen',
        (WidgetTester tester) async {
      // The bug this file exists for. Labels ran off the left edge, which is
      // invisible to a test that only asks whether the text exists.
      await openMenu(tester);

      for (final EntryKind kind in EntryKind.values) {
        final Rect label = tester.getRect(find.text(kind.label));
        expect(label.left, greaterThanOrEqualTo(0), reason: kind.label);
        expect(label.right, lessThanOrEqualTo(phone.width), reason: kind.label);
        expect(label.top, greaterThanOrEqualTo(0), reason: kind.label);
        expect(label.bottom, lessThanOrEqualTo(phone.height), reason: kind.label);
      }
    });

    testWidgets('stacks the option buttons in one column',
        (WidgetTester tester) async {
      // They drifted diagonally before, because each row resized during the
      // animation. Same right edge for all five is what "stacked" means here.
      await openMenu(tester);

      final List<double> rights = <double>[
        for (final EntryKind kind in EntryKind.values)
          tester.getRect(find.byKey(ValueKey<String>('entry-${kind.wire}'))).right,
      ];

      for (final double right in rights) {
        expect((right - rights.first).abs(), lessThan(0.5));
      }
    });

    testWidgets('puts each label to the left of its own button',
        (WidgetTester tester) async {
      await openMenu(tester);

      for (final EntryKind kind in EntryKind.values) {
        final Rect label = tester.getRect(find.text(kind.label));
        final Rect button =
            tester.getRect(find.byKey(ValueKey<String>('entry-${kind.wire}')));

        expect(label.right, lessThanOrEqualTo(button.left), reason: kind.label);
        // And beside it, not above or below: the two should line up, which is
        // what makes the pair read as one control.
        expect(label.center.dy, closeTo(button.center.dy, 8), reason: kind.label);
      }
    });

    testWidgets('replaces the button in place rather than moving it',
        (WidgetTester tester) async {
      // Off by exactly the height of the navigation bar once, because the menu
      // computed its own offset instead of measuring the button.
      final Rect opener = await openMenu(tester);

      final Rect close = tester.getRect(find.byIcon(Icons.close));

      expect(close.center.dx, closeTo(opener.center.dx, 1));
      expect(close.center.dy, closeTo(opener.center.dy, 1));
    });

    testWidgets('opens upward, clear of the button', (WidgetTester tester) async {
      await openMenu(tester);

      final Rect close = tester.getRect(find.byIcon(Icons.close));
      final Rect lowest = tester.getRect(
        find.byKey(ValueKey<String>('entry-${EntryKind.purchaseOrder.wire}')),
      );

      expect(lowest.bottom, lessThan(close.top));
    });

    testWidgets('hands back the kind that was tapped',
        (WidgetTester tester) async {
      // The route is pushed directly here rather than through the button, so
      // what is under test is the menu's answer and not the navigation that
      // happens afterwards.
      await tester.binding.setSurfaceSize(phone);
      addTearDown(() => tester.binding.setSurfaceSize(null));

      EntryKind? chosen;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            floatingActionButton: Builder(
              builder: (BuildContext context) => FloatingActionButton(
                onPressed: () async {
                  chosen = await Navigator.of(context).push<EntryKind>(
                    entryMenuRoute(
                      anchor: const Rect.fromLTWH(328, 652, 56, 56),
                    ),
                  );
                },
                child: const Icon(Icons.add),
              ),
            ),
          ),
        ),
      );

      await tester.tap(find.byIcon(Icons.add));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Payment'));
      await tester.pumpAndSettle();

      expect(chosen, EntryKind.payment);
      // And the menu is gone rather than left open behind the form.
      expect(find.text('Receipt'), findsNothing);
    });

    testWidgets('closes on the barrier without choosing anything',
        (WidgetTester tester) async {
      await openMenu(tester);

      // Top-left, well clear of the menu in the bottom-right corner.
      await tester.tapAt(const Offset(40, 40));
      await tester.pumpAndSettle();

      expect(find.text('Receipt'), findsNothing);
    });
  });
}
