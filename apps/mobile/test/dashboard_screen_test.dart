import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/core/widgets/cards.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/dashboard/application/dashboard_providers.dart';
import 'package:tallyflow/src/features/dashboard/data/dashboard_repository.dart';
import 'package:tallyflow/src/features/dashboard/domain/dashboard.dart';
import 'package:tallyflow/src/features/dashboard/presentation/dashboard_screen.dart';
import 'package:tallyflow/src/features/reports/domain/reports.dart';

import 'support/fixtures.dart';

const Company _company = Company(
  id: 'company-1',
  name: 'Bhatia Supermarket',
  tallyName: 'Bhatia Supermarket',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

/// Serves a canned dashboard. Implemented rather than extended so the test
/// fails to compile if the repository grows a method the screen depends on.
class _FakeDashboardRepository implements DashboardRepository {
  _FakeDashboardRepository(this._dashboard, {this.failure});

  final Dashboard? _dashboard;
  final ApiException? failure;
  int loads = 0;
  FetchMode? lastMode;

  @override
  Future<Dashboard> load(String companyId, {FetchMode mode = FetchMode.auto}) async {
    loads++;
    lastMode = mode;
    if (failure != null) throw failure!;
    return _dashboard!;
  }
}

Future<void> _pump(
  WidgetTester tester, {
  required DashboardRepository repository,
  List<Company> companies = const <Company>[_company],
}) async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final SharedPreferences preferences = await SharedPreferences.getInstance();

  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        sharedPreferencesProvider.overrideWithValue(preferences),
        companiesProvider.overrideWith((Ref ref) async => companies),
        dashboardRepositoryProvider.overrideWithValue(repository),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: const DashboardScreen()),
    ),
  );
  await tester.pumpAndSettle();
}

/// Rebuilds a dashboard fixture with a different freshness block.
Map<String, Object?> _withFreshness(Map<String, Object?> freshness) {
  final Map<String, Object?> body =
      Map<String, Object?>.from(fixture('dashboard'));
  body['freshness'] = freshness;
  return body;
}

/// The amount shown on one named KPI tile.
///
/// Scoped to the tile rather than searched for globally: several tiles
/// legitimately carry the same figure in this sample data, and a bare text
/// finder would pass while the number sat under the wrong label.
Finder _kpiAmount(String label, String amount) => find.descendant(
      of: find.widgetWithText(KpiCard, label),
      matching: find.text(amount),
    );

/// Scrolls the dashboard until [target] is built.
///
/// Sections below the fold are not in the widget tree at all, so asserting on
/// them without scrolling would test the viewport height rather than the app.
Future<void> _scrollTo(WidgetTester tester, Finder target) async {
  await tester.dragUntilVisible(
    target,
    find.byType(ListView).first,
    const Offset(0, -320),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('answers the four questions the product exists for',
      (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeDashboardRepository(
        Dashboard.fromJson(fixture('dashboard')),
      ),
    );

    // "How much did I sell today?"
    expect(_kpiAmount("Today's sales", '₹11,800'), findsOneWidget);

    // "What is my cash position?" -- cash 3,44,220 + bank 1,25,000.
    expect(find.text('Cash & bank'), findsWidgets);
    expect(_kpiAmount('Cash & bank', '₹4.69L'), findsOneWidget);

    // "Who owes me money?" -- one open receivable of 11,800.
    expect(_kpiAmount('You are owed', '₹11,800'), findsOneWidget);
    // ...and what the business owes, which is a much larger number here.
    expect(_kpiAmount('You owe', '₹2.13L'), findsOneWidget);

    // "What is running out?"
    await _scrollTo(tester, find.text('Inventory'));
    expect(find.text('Inventory'), findsOneWidget);
    expect(find.text('Running low'), findsOneWidget);
  });

  testWidgets('shows an offline banner over last-known figures',
      (WidgetTester tester) async {
    // The product rule: stale-but-real beats an error page, as long as it is
    // labelled. An unlabelled stale figure is the one outcome that can make an
    // owner act on numbers they believe are live.
    await _pump(
      tester,
      repository: _FakeDashboardRepository(
        Dashboard.fromJson(_withFreshness(<String, Object?>{
          'available': true,
          'refreshed_at': DateTime.now()
              .subtract(const Duration(hours: 6))
              .toUtc()
              .toIso8601String(),
          'age_seconds': 21600,
          'is_stale': true,
          'connector_online': false,
        })),
      ),
    );

    expect(find.text('Your Tally PC is offline'), findsOneWidget);
    expect(find.textContaining('6 hours ago'), findsOneWidget);
    // The figures are still on screen behind the banner.
    expect(_kpiAmount("Today's sales", '₹11,800'), findsOneWidget);
  });

  testWidgets('a stale-but-online read says so without crying offline',
      (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeDashboardRepository(
        Dashboard.fromJson(_withFreshness(<String, Object?>{
          'available': true,
          'refreshed_at':
              DateTime.now().subtract(const Duration(minutes: 40)).toUtc().toIso8601String(),
          'age_seconds': 2400,
          'is_stale': true,
          'connector_online': true,
        })),
      ),
    );

    expect(find.text('These figures are not current'), findsOneWidget);
    expect(find.text('Your Tally PC is offline'), findsNothing);
  });

  testWidgets('a failed section degrades without taking the screen with it',
      (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeDashboardRepository(
        Dashboard.fromJson(fixture('dashboard_degraded')),
      ),
    );

    // Sales still rendered...
    expect(_kpiAmount("Today's sales", '₹11,800'), findsOneWidget);
    // ...and receivables says why it is missing, rather than the whole screen
    // becoming an error page. The reason shown is the connector's own -- a
    // generic "could not read" would send the owner after the wrong problem
    // when the fix is to open Tally.
    await _scrollTo(tester, find.text('Receivables'));
    expect(find.text('Receivables'), findsOneWidget);
    expect(find.textContaining("TallyPrime isn't responding"), findsWidgets);
  });

  testWidgets('never renders zeroes when nothing could be read',
      (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeDashboardRepository(
        Dashboard.fromJson(<String, Object?>{
          'company': <String, Object?>{
            'id': 'company-1',
            'name': 'Bhatia Supermarket',
            'currency': 'INR',
          },
          'as_of': '2026-03-15',
          'sections': <String, Object?>{
            for (final String name in <String>[
              'sales',
              'purchases',
              'cash_and_bank',
              'receivables',
              'payables',
              'inventory',
              'activity',
            ])
              name: <String, Object?>{'ok': false, 'error': 'Could not read.'},
          },
          'freshness': <String, Object?>{
            'available': false,
            'connector_online': false,
          },
        }),
      ),
    );

    // "You sold nothing today" and "we could not reach your Tally" are
    // completely different statements. A grid of ₹0.00 tiles asserts the first.
    expect(find.text('No figures to show yet'), findsOneWidget);
    expect(find.text('₹0.00'), findsNothing);
    expect(find.text('₹0'), findsNothing);
  });

  testWidgets('pull-to-refresh asks for live data, not the cache',
      (WidgetTester tester) async {
    final _FakeDashboardRepository repository = _FakeDashboardRepository(
      Dashboard.fromJson(fixture('dashboard')),
    );
    await _pump(tester, repository: repository);

    expect(repository.lastMode, FetchMode.auto);

    await tester.fling(find.byType(ListView).first, const Offset(0, 400), 1000);
    await tester.pumpAndSettle();

    expect(repository.loads, 2);
    expect(repository.lastMode, FetchMode.live);
  });

  testWidgets('an unreachable backend explains itself in plain language',
      (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeDashboardRepository(
        null,
        failure: const ApiException(
          code: 'connector_offline',
          message: 'Your Tally PC is offline. Data shown was last updated earlier.',
          retryable: true,
          statusCode: 503,
        ),
      ),
    );

    expect(find.text('Cannot reach your data'), findsOneWidget);
    expect(find.textContaining('Tally PC is offline'), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
  });

  testWidgets('an account with no company is sent to set one up',
      (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeDashboardRepository(Dashboard.fromJson(fixture('dashboard'))),
      companies: const <Company>[],
    );

    expect(find.text('Connect your Tally PC'), findsOneWidget);
    expect(find.text('Set up a connection'), findsOneWidget);
  });
}
