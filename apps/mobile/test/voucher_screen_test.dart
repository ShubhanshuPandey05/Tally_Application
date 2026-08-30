import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/model/freshness.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/reports/application/report_providers.dart';
import 'package:tallyflow/src/features/reports/domain/drilldown.dart';
import 'package:tallyflow/src/features/reports/presentation/voucher_screen.dart';

import 'support/fixtures.dart';

/// The deepest screen in the product.
///
/// It is the one an accountant checks against TallyPrime itself, so these
/// assertions are about what it must never do: hide a posted line, round a
/// figure it is being reconciled against, or show a voucher that is excluded
/// from every total elsewhere without saying so.

const Company _company = Company(
  id: 'company-1',
  name: 'Bhatia Supermarket',
  tallyName: 'Bhatia Supermarket',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

final DateTime _on = DateTime(2026, 3, 15);

Future<void> _pump(
  WidgetTester tester, {
  Map<String, Object?>? override,
}) async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final SharedPreferences preferences = await SharedPreferences.getInstance();

  final Map<String, Object?> data = <String, Object?>{
    ...fixture('voucher').envelopeData,
    ...?override,
  };

  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        sharedPreferencesProvider.overrideWithValue(preferences),
        companiesProvider.overrideWith((Ref ref) async => <Company>[_company]),
        voucherProvider((companyId: _company.id, key: 'k', on: _on)).overrideWith(
          (Ref ref) async => Fresh<VoucherDetail>(
            VoucherDetail.fromJson(data),
            fixture('voucher').envelopeFreshness,
          ),
        ),
      ],
      child: MaterialApp(
        theme: AppTheme.light(),
        home: VoucherScreen(voucherKey: 'k', on: _on),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows every posted line with its side', (WidgetTester tester) async {
    await _pump(tester);

    // Both ledgers named, and Dr/Cr spelled out. An accountant reads Dr and Cr;
    // nobody reads a red number and concludes "credit".
    expect(find.text('Purchases'), findsOneWidget);
    expect(find.text('SARA DISITIBUTOR'), findsWidgets);
    expect(find.text('Dr'), findsOneWidget);
    expect(find.text('Cr'), findsOneWidget);
  });

  testWidgets('prints the amount exactly, to the paisa', (WidgetTester tester) async {
    await _pump(tester);

    // The rounding that is right on a dashboard tile is wrong here: this is the
    // screen somebody holds up against Tally.
    expect(find.text('₹2,12,600.00'), findsWidgets);
    expect(find.text('₹2.13L'), findsNothing);
  });

  testWidgets('says whether the voucher balances', (WidgetTester tester) async {
    await _pump(tester);

    expect(find.text('Balanced'), findsOneWidget);
    expect(find.text('yes'), findsOneWidget);
  });

  testWidgets('a voucher whose sides disagree says so rather than looking fine',
      (WidgetTester tester) async {
    // Sides that do not agree mean the read dropped a line. Silently rendering
    // it would make a broken read indistinguishable from a one-entry voucher.
    await _pump(tester, override: <String, Object?>{
      'credit_total': <String, Object?>{
        'amount': '1.00',
        'side': 'debit',
        'currency': 'INR',
      },
    });

    expect(find.text('no'), findsOneWidget);
  });

  testWidgets('a cancelled voucher is labelled, not quietly shown',
      (WidgetTester tester) async {
    await _pump(tester, override: <String, Object?>{'is_cancelled': true});

    expect(find.textContaining('not counted in any total'), findsOneWidget);
  });

  testWidgets('an ordinary voucher carries no exclusion warning',
      (WidgetTester tester) async {
    await _pump(tester);
    expect(find.textContaining('not counted in any total'), findsNothing);
  });

  testWidgets('a voucher with no stock lines shows no Items card',
      (WidgetTester tester) async {
    // The fixture is an accounting-only purchase. An empty "Items" card would
    // read as a failed read rather than as a voucher without inventory.
    await _pump(tester);
    expect(find.text('Items'), findsNothing);
  });

  testWidgets('stock lines show the arithmetic behind the amount',
      (WidgetTester tester) async {
    await _pump(tester, override: <String, Object?>{
      'inventory_entries': <Object?>[
        <String, Object?>{
          'item': 'Rice',
          'quantity': 25.0,
          'unit': 'KG',
          'rate': <String, Object?>{
            'amount': '400.00',
            'side': 'debit',
            'currency': 'INR',
          },
          'amount': <String, Object?>{
            'amount': '10000.00',
            'side': 'debit',
            'currency': 'INR',
          },
        },
      ],
    });

    expect(find.text('Items'), findsOneWidget);
    expect(find.text('Rice'), findsOneWidget);
    // An owner checking an invoice is checking the rate as often as the total.
    expect(find.textContaining('25 KG × ₹400.00'), findsOneWidget);
  });

  testWidgets('a voucher with nothing written on it shows no Details card',
      (WidgetTester tester) async {
    await _pump(tester);
    expect(find.text('Details'), findsNothing);
  });

  testWidgets('narration and bill references appear when there are any',
      (WidgetTester tester) async {
    await _pump(tester, override: <String, Object?>{
      'narration': 'Monthly stock purchase',
      'ledger_entries': <Object?>[
        <String, Object?>{
          'ledger': 'SARA DISITIBUTOR',
          'amount': <String, Object?>{
            'amount': '212600.00',
            'side': 'credit',
            'currency': 'INR',
          },
          'is_party': true,
          'bill_references': <Object?>['INV-77'],
        },
      ],
    });

    expect(find.text('Details'), findsOneWidget);
    expect(find.text('Monthly stock purchase'), findsOneWidget);
    expect(find.text('INV-77'), findsOneWidget);
  });
}
