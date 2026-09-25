import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/entries/application/entry_providers.dart';
import 'package:tallyflow/src/features/entries/domain/entry_draft.dart';
import 'package:tallyflow/src/features/entries/presentation/new_entry_screen.dart';

/// The "Optional" switch on the entry form, which follows the Tally PC's own
/// setting: locked on when the PC sends every entry as optional, and the
/// person's choice when the PC allows regular entries.
void main() {
  Widget harness({required bool canPostRegular}) {
    return ProviderScope(
      overrides: <Override>[
        activeCompanyIdResolvedProvider.overrideWithValue('c1'),
        canPostRegularProvider('c1')
            .overrideWith((Ref ref) async => canPostRegular),
        partyNamesProvider('c1').overrideWith((Ref ref) async => <String>[]),
        cashBankNamesProvider('c1')
            .overrideWith((Ref ref) async => <String>['Cash']),
      ],
      child: const MaterialApp(
        home: NewEntryScreen(initialKind: EntryKind.receipt),
      ),
    );
  }

  Switch optionalSwitch(WidgetTester tester) => tester
      .widget<Switch>(find.byKey(const ValueKey<String>('entry-optional')));

  testWidgets('is locked on when the PC sends every entry as optional',
      (WidgetTester tester) async {
    await tester.pumpWidget(harness(canPostRegular: false));
    await tester.pumpAndSettle();

    expect(optionalSwitch(tester).value, isTrue);
    expect(optionalSwitch(tester).onChanged, isNull);
  });

  testWidgets('is the person\'s choice when the PC allows regular entries',
      (WidgetTester tester) async {
    await tester.pumpWidget(harness(canPostRegular: true));
    await tester.pumpAndSettle();

    // Follows the PC's own setting until somebody moves it.
    expect(optionalSwitch(tester).value, isFalse);
    expect(optionalSwitch(tester).onChanged, isNotNull);

    await tester.tap(find.byKey(const ValueKey<String>('entry-optional')));
    await tester.pumpAndSettle();
    expect(optionalSwitch(tester).value, isTrue);
  });

  test('the choice travels with the entry', () {
    EntryDraft draft({required bool optional}) => EntryDraft(
          kind: EntryKind.receipt,
          date: DateTime(2026, 9, 1),
          party: 'Ram Traders',
          amount: 100,
          optional: optional,
        );
    expect(draft(optional: false).toJson()['optional'], isFalse);
    expect(draft(optional: true).toJson()['optional'], isTrue);
  });
}
