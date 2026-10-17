import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/model/date_range.dart';
import 'package:tallyflow/src/core/model/freshness.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/reports/domain/reports.dart' show FetchMode;
import 'package:tallyflow/src/features/top_reports/application/top_report_providers.dart';
import 'package:tallyflow/src/features/top_reports/domain/top_report.dart';
import 'package:tallyflow/src/features/top_reports/presentation/top_report_screen.dart';
import 'package:tallyflow/src/features/top_reports/presentation/top_reports_screen.dart';

import 'support/fixtures.dart';

Map<String, Object?> _money(String amount) => <String, Object?>{
      'amount': amount,
      'side': 'debit',
      'signed': amount,
      'currency': 'INR',
      'display': '$amount Dr',
    };

/// The backend's shape for a top-customers report, two rows of three.
Map<String, Object?> _customers({String sort = 'amount'}) => <String, Object?>{
      'key': 'customers',
      'title': 'Top Prime Customers',
      'from_date': '2026-04-01',
      'to_date': '2026-10-17',
      'sort': sort,
      'sorts': <Object?>[
        <String, Object?>{'key': 'amount', 'label': 'Bill amount', 'column': 'amount'},
        <String, Object?>{'key': 'quantity', 'label': 'Quantity', 'column': 'quantity'},
      ],
      'limit': 2,
      'row_count': 3,
      'columns': <Object?>[
        <String, Object?>{'key': 'last_date', 'label': 'Last sale date', 'type': 'date', 'total': false},
        <String, Object?>{'key': 'bills', 'label': 'No. of bills', 'type': 'count', 'total': true},
        <String, Object?>{'key': 'quantity', 'label': 'Quantity', 'type': 'quantity', 'total': true},
        <String, Object?>{'key': 'amount', 'label': 'Bill amount', 'type': 'money', 'total': true},
      ],
      'rows': <Object?>[
        <String, Object?>{
          'rank': 1,
          'name': 'Raj Enterprises',
          'drill': <String, Object?>{'type': 'ledger', 'name': 'Raj Enterprises'},
          'cells': <String, Object?>{
            'last_date': '2026-10-16',
            'bills': 79,
            'quantity': 2171.0,
            'amount': _money('730369.60'),
          },
        },
        <String, Object?>{
          'rank': 2,
          'name': 'Not Available',
          'drill': null,
          'cells': <String, Object?>{
            'last_date': null,
            'bills': 1,
            'quantity': 0.0,
            'amount': _money('100.00'),
          },
        },
      ],
      'total': <String, Object?>{
        'last_date': null,
        'bills': 80,
        'quantity': 2171.0,
        'amount': _money('730469.60'),
      },
      'notes': <Object?>['History is synced back to 01 Apr 2025.'],
    };

class _FakeRepository implements TopReportsRepository {
  final List<String?> sorts = <String?>[];

  @override
  Future<Fresh<TopReport>> report(
    String companyId, {
    required String key,
    required DateRange range,
    String? sort,
    int limit = 10,
    FetchMode mode = FetchMode.auto,
    CachePolicy cache = CachePolicy.keep,
  }) async {
    sorts.add(sort);
    return Fresh<TopReport>(
      TopReport.fromJson(_customers(sort: sort ?? 'amount')),
      Freshness.fromJson(<String, Object?>{
        'available': true,
        'refreshed_at': DateTime.now().toUtc().toIso8601String(),
        'is_stale': false,
        'connector_online': true,
      }),
    );
  }
}

const Company _company = Company(
  id: 'company-1',
  name: 'Bhatia Supermarket',
  tallyName: 'Bhatia Supermarket',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

Future<void> _pump(WidgetTester tester, Widget screen, List<Override> overrides) async {
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
  test('a report decodes its columns, rows and the TOTAL over the rows shown', () {
    final TopReport report = TopReport.fromJson(_customers());

    expect(report.rows.map((TopRow r) => r.name), <String>['Raj Enterprises', 'Not Available']);
    expect(report.rowCount, 3);
    expect(report.activeSort?.column, 'amount');
    expect(report.rows.last.drill, isNull);

    final TopColumn amount = report.columns.last;
    expect(amount.format(report.total['amount']), '₹7,30,469.60');
    // No value is "--", never a zero or a blank.
    expect(report.columns.first.format(null), '--');
  });

  test('the backend fixtures decode, every row carrying every column', () {
    for (final String name in <String>['top_customers', 'top_selling_products']) {
      final TopReport report = TopReport.fromJson(fixture(name).envelopeData);
      expect(report.rows, isNotEmpty, reason: name);
      for (final TopRow row in report.rows) {
        for (final TopColumn column in report.columns) {
          expect(row.cells.containsKey(column.key), isTrue, reason: '$name ${column.key}');
          expect(column.format(row.cells[column.key]), isNotEmpty);
        }
      }
      expect(report.activeSort, isNotNull, reason: name);
    }
  });

  test('a bill not yet due reads as due, not as negative days overdue', () {
    const TopColumn overdue = TopColumn(
      key: 'overdue_days',
      label: 'Overdue days',
      type: TopCellType.days,
      totalled: false,
    );
    expect(overdue.format(36), '36 d');
    expect(overdue.format(-4), 'due in 4 d');
  });

  test('every catalogue key is unique, and only the order reports are held back', () {
    final List<TopReportEntry> entries = <TopReportEntry>[
      for (final TopReportGroup group in topReportCatalog) ...group.entries,
    ];
    expect(entries, hasLength(23));
    expect(entries.map((TopReportEntry e) => e.key).toSet(), hasLength(23));
    expect(
      entries.where((TopReportEntry e) => e.unavailable != null).map((TopReportEntry e) => e.key),
      <String>['so_customers', 'po_suppliers', 'so_items', 'po_items'],
    );
  });

  testWidgets('the list names every report and says why the order ones wait',
      (WidgetTester tester) async {
    await _pump(tester, const TopReportsScreen(), const <Override>[]);

    expect(find.text('Top Prime Customers'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Top Purchase Order Item Outstanding'), 300);
    expect(find.textContaining('Needs order tracking'), findsWidgets);
  });

  testWidgets('a report ranks its rows, leads with the sorted figure and totals them',
      (WidgetTester tester) async {
    final _FakeRepository repository = _FakeRepository();
    await _pump(
      tester,
      const TopReportScreen(reportKey: 'customers'),
      <Override>[topReportsRepositoryProvider.overrideWithValue(repository)],
    );

    expect(find.text('Top Prime Customers'), findsOneWidget);
    expect(find.text('Raj Enterprises'), findsOneWidget);
    expect(find.text('₹7,30,369.60'), findsOneWidget);
    expect(find.text('Top 2 of 3'), findsOneWidget);
    expect(find.text('₹7,30,469.60'), findsOneWidget);
    expect(find.text('History is synced back to 01 Apr 2025.'), findsOneWidget);

    await tester.tap(find.text('By bill amount'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('By quantity').last);
    await tester.pumpAndSettle();
    expect(repository.sorts.last, 'quantity');
  });
}
