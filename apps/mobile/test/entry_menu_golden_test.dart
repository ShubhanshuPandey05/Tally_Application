import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/entries/presentation/create_entry_button.dart';

/// Renders the real button, and the menu it opens, to PNGs that can be looked
/// at.
///
/// Regenerate with `flutter test --update-goldens`. The assertions in
/// `create_entry_button_test.dart` guard the geometry; these exist so the
/// result can be *seen*. The first version of this menu analysed clean and
/// passed its behavioural tests while rendering completely wrong, and no
/// amount of coordinate-checking would have been as quick as one look.
void main() {
  Widget harness() {
    return ProviderScope(
      overrides: <Override>[
        // A company is chosen, which is all the button asks about.
        activeCompanyIdResolvedProvider.overrideWithValue('company-1'),
      ],
      child: MaterialApp(
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF3B5BDB),
            brightness: Brightness.dark,
          ),
        ),
        home: const Scaffold(
          backgroundColor: Color(0xFF15171C),
          extendBody: true,
          body: SizedBox.expand(),
          floatingActionButton: CreateEntryButton(),
          // Stands in for the shell's floating navigation bar, so the gap
          // above it is the real one.
          bottomNavigationBar: SizedBox(height: 66),
        ),
      ),
    );
  }

  testWidgets('closed, above the navigation bar', (WidgetTester tester) async {
    await tester.binding.setSurfaceSize(const Size(400, 860));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(harness());
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MaterialApp),
      matchesGoldenFile('goldens/entry_button_closed.png'),
    );
  });

  testWidgets('open, with the close button exactly where the button was',
      (WidgetTester tester) async {
    await tester.binding.setSurfaceSize(const Size(400, 860));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(harness());
    await tester.pumpAndSettle();

    final Rect opener = tester.getRect(find.byIcon(Icons.add));
    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();

    // The close button replaces the opener in place. A menu whose close button
    // lands somewhere else makes the button appear to jump when it opens.
    final Rect close = tester.getRect(find.byIcon(Icons.close));
    expect(close.center.dx, closeTo(opener.center.dx, 1));
    expect(close.center.dy, closeTo(opener.center.dy, 1));

    await expectLater(
      find.byType(MaterialApp),
      matchesGoldenFile('goldens/entry_menu_open.png'),
    );
  });
}
