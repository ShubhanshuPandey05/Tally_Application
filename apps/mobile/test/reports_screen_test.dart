import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/model/freshness.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/reports/application/report_providers.dart';
import 'package:tallyflow/src/features/reports/domain/reports.dart';
import 'package:tallyflow/src/features/reports/presentation/outstanding_screen.dart';
import 'package:tallyflow/src/features/reports/presentation/stock_screen.dart';

import 'support/fixtures.dart';

const Company _company = Company(
  id: 'company-1',
  name: 'Bhatia Supermarket',
  tallyName: 'Bhatia Supermarket',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

Fresh<T> _fresh<T>(T data) => Fresh<T>(
      data,
      Freshness.fromJson(<String, Object?>{
        'available': true,
        'refreshed_at': DateTime.now().toUtc().toIso8601String(),
        'is_stale': false,
        'connector_online': true,
      }),
    );

Future<void> _pump(
  WidgetTester tester,
  Widget screen,
  List<Override> overrides,
) async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final SharedPreferences preferences = await SharedPreferences.getInstance();

  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        sharedPreferencesProvider.overrideWithValue(preferences),
        companiesProvider.overrideWith((Ref ref) async => <Company>[_company]),
        ...overrides,
      ],
      child: MaterialApp(theme: AppTheme.light(), home: screen),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('outstanding groups bills under the party you would ring',
      (WidgetTester tester) async {
    final OutstandingReport report = OutstandingReport.fromJson(
      fixture('outstanding_payable')['data']! as Map<String, Object?>,
    );

    await _pump(
      tester,
      const OutstandingScreen(kind: OutstandingKind.payable),
      <Override>[
        outstandingProvider(
          (companyId: 'company-1', kind: OutstandingKind.payable),
        ).overrideWith((Ref ref) async => _fresh(report)),
      ],
    );

    expect(find.text('Payables'), findsOneWidget);
    expect(find.text('Who do I owe?'), findsOneWidget);
    expect(find.text('SARA DISITIBUTOR'), findsOneWidget);
    expect(find.text('₹2,12,600.00'), findsWidgets);
    // Ageing is the part that makes the number actionable.
    expect(find.textContaining('days overdue'), findsWidgets);
  });

  testWidgets('a settled ledger reads as good news, not as an empty table',
      (WidgetTester tester) async {
    await _pump(
      tester,
      const OutstandingScreen(kind: OutstandingKind.receivable),
      <Override>[
        outstandingProvider(
          (companyId: 'company-1', kind: OutstandingKind.receivable),
        ).overrideWith(
          (Ref ref) async => _fresh(
            OutstandingReport.fromJson(<String, Object?>{
              'kind': 'receivable',
              'as_of': '2026-03-15',
              'summary': <String, Object?>{},
              'bills': <Object?>[],
            }),
          ),
        ),
      ],
    );

    expect(find.text('Nothing outstanding'), findsOneWidget);
    expect(find.text('Every bill has been settled.'), findsOneWidget);
  });

  testWidgets('stock flags negative and low items distinctly',
      (WidgetTester tester) async {
    final StockReport report = StockReport.fromJson(
      fixture('stock')['data']! as Map<String, Object?>,
    );

    await _pump(
      tester,
      const StockScreen(),
      <Override>[
        stockProvider((companyId: 'company-1', only: null))
            .overrideWith((Ref ref) async => _fresh(report)),
      ],
    );

    expect(find.text('Rice'), findsOneWidget);
    // Negative stock is a data-entry error, not a small number -- it gets its
    // own flag rather than sharing "Reorder".
    expect(find.text('Negative'), findsWidgets);
    expect(find.text('Reorder'), findsOneWidget);
    expect(find.text('-3 LTR'), findsOneWidget);
  });

  testWidgets('searching stock filters on the phone without another read',
      (WidgetTester tester) async {
    final StockReport report = StockReport.fromJson(
      fixture('stock')['data']! as Map<String, Object?>,
    );
    int reads = 0;

    await _pump(
      tester,
      const StockScreen(),
      <Override>[
        stockProvider((companyId: 'company-1', only: null)).overrideWith((Ref ref) async {
          reads++;
          return _fresh(report);
        }),
      ],
    );

    await tester.enterText(find.byType(TextField), 'sug');
    await tester.pumpAndSettle();

    expect(find.text('Sugar'), findsOneWidget);
    expect(find.text('Rice'), findsNothing);
    // Typing must not round-trip to a shop's PC on every keystroke.
    expect(reads, 1);
  });
}
