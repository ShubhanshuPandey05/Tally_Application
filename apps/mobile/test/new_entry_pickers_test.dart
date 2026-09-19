import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/entries/application/entry_providers.dart';
import 'package:tallyflow/src/features/entries/domain/entry_draft.dart';
import 'package:tallyflow/src/features/entries/presentation/new_entry_screen.dart';

/// The entry form's master lists: what each field offers, and what a pick
/// fills in.
void main() {
  const Size phone = Size(400, 860);

  Widget harness(EntryKind kind) {
    return ProviderScope(
      overrides: <Override>[
        activeCompanyIdResolvedProvider.overrideWithValue('c1'),
        partyNamesProvider('c1')
            .overrideWith((Ref ref) async => <String>['Ram Traders']),
        taxLedgerNamesProvider('c1').overrideWith(
          (Ref ref) async => <String>['Output CGST 9%', 'Output SGST 9%'],
        ),
        salesLedgerNamesProvider('c1')
            .overrideWith((Ref ref) async => <String>['Gst Sales']),
        purchaseLedgerNamesProvider('c1')
            .overrideWith((Ref ref) async => <String>['Gst Purchase']),
        cashBankNamesProvider('c1')
            .overrideWith((Ref ref) async => <String>['Cash', 'HDFC Bank']),
        itemOptionsProvider('c1').overrideWith(
          (Ref ref) async => const <ItemOption>[
            ItemOption(name: 'Rice', unit: 'KG', rate: 80),
            ItemOption(name: 'Sugar', unit: 'KG'),
          ],
        ),
      ],
      child: MaterialApp(home: NewEntryScreen(initialKind: kind)),
    );
  }

  Future<void> open(WidgetTester tester, EntryKind kind) async {
    await tester.binding.setSurfaceSize(phone);
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(harness(kind));
    await tester.pumpAndSettle();
  }

  testWidgets('a receipt offers the cash and bank ledgers for its Dr side',
      (WidgetTester tester) async {
    await open(tester, EntryKind.receipt);

    expect(find.text('Cr: Party'), findsOneWidget);
    expect(find.text('Dr: Cash/Bank'), findsOneWidget);

    // Tapped as it opens, still holding the default "Cash": every account is
    // offered, not just the one already typed.
    await tester.tap(find.widgetWithText(TextFormField, 'Dr: Cash/Bank'));
    await tester.pumpAndSettle();

    expect(find.text('HDFC Bank'), findsOneWidget);
    // Not the parties -- those belong to the other field.
    expect(find.text('Ram Traders'), findsNothing);
  });

  testWidgets('picking an item fills its unit and rate',
      (WidgetTester tester) async {
    await open(tester, EntryKind.salesOrder);

    await tester.tap(find.text('Add item'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextFormField, 'Item'));
    await tester.pumpAndSettle();

    expect(find.text('Rice'), findsOneWidget, reason: 'the item master list');
    await tester.tap(find.text('Rice'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(TextField, 'KG'), findsOneWidget);
    expect(find.widgetWithText(TextField, '80'), findsOneWidget);
  });

  testWidgets('a tax picked from the list brings its rate, and adds to the total',
      (WidgetTester tester) async {
    await open(tester, EntryKind.sales);
    await tester.enterText(find.widgetWithText(TextFormField, 'Amount'), '5000');
    await tester.pumpAndSettle();

    await tester.tap(find.text('Add tax'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextFormField, 'Tax ledger'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Output CGST 9%'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(TextField, '9'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Add'));
    await tester.pumpAndSettle();

    expect(find.text('₹450.00'), findsOneWidget);
    expect(find.textContaining('₹5450.00'), findsOneWidget);
  });

  testWidgets("an order offers the company's own ledger, and an order number",
      (WidgetTester tester) async {
    await open(tester, EntryKind.salesOrder);

    // "Sales" does not exist in this company, so its real ledger is used.
    expect(find.widgetWithText(TextFormField, 'Gst Sales'), findsOneWidget);
    expect(find.text('Order no. (optional)'), findsOneWidget);
  });

  testWidgets('an order has no amount of its own', (WidgetTester tester) async {
    await open(tester, EntryKind.salesOrder);
    expect(find.widgetWithText(TextFormField, 'Amount'), findsNothing);
  });

  testWidgets('a sale keeps an amount, for an invoice with no items',
      (WidgetTester tester) async {
    await open(tester, EntryKind.sales);
    expect(find.widgetWithText(TextFormField, 'Amount'), findsOneWidget);
  });
}
