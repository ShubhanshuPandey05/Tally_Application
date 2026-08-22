import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/core/widgets/cards.dart';
import 'package:tallyflow/src/core/widgets/primitives.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/auth/application/auth_controller.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/dashboard/application/dashboard_providers.dart';
import 'package:tallyflow/src/features/dashboard/data/dashboard_repository.dart';
import 'package:tallyflow/src/features/dashboard/domain/dashboard.dart';
import 'package:tallyflow/src/features/dashboard/presentation/dashboard_screen.dart';
import 'package:tallyflow/src/core/widgets/period_picker.dart';
import 'package:tallyflow/src/features/reports/domain/reports.dart';
import 'package:tallyflow/src/features/subscription/domain/subscription.dart';

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

  _FakeDashboardRepository.periodAware(this._dashboard, this._periodDashboard)
      : failure = null;

  final Dashboard? _dashboard;
  final ApiException? failure;

  /// Served instead of [_dashboard] once a period is asked for, so a test can
  /// assert on what the period view renders rather than only on what it sent.
  Dashboard? _periodDashboard;

  int loads = 0;
  FetchMode? lastMode;
  DateRange? lastPeriod;

  @override
  Future<Dashboard> load(
    String companyId, {
    FetchMode mode = FetchMode.auto,
    DateRange? period,
  }) async {
    loads++;
    lastMode = mode;
    lastPeriod = period;
    if (failure != null) throw failure!;
    if (period != null && _periodDashboard != null) return _periodDashboard!;
    return _dashboard!;
  }
}

/// A signed-in admin of an approved business.
///
/// Pinned rather than left to the real controller: the dashboard now asks who
/// is looking and what their subscription allows before it decides what a
/// company-less account should be told, and the router never reaches this
/// screen without a settled session anyway.
AppUser _admin({bool approved = true}) => AppUser(
      id: 'user-1',
      email: 'owner@bhatiastores.in',
      orgId: 'org-1',
      orgName: 'Bhatia Supermarket',
      role: UserRole.admin,
      subscription: OrgSubscription.fromJson(<String, Object?>{
        'status': approved ? 'active' : 'pending',
        'allows_changes': approved,
        'allows_data': true,
        'max_companies': approved ? 3 : 0,
        'max_users': approved ? 5 : 0,
        'message': approved
            ? ''
            : 'Your business is waiting to be approved. You can sign in now.',
      }),
    );

Future<void> _pump(
  WidgetTester tester, {
  required DashboardRepository repository,
  List<Company> companies = const <Company>[_company],
  AppUser? user,
}) async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final SharedPreferences preferences = await SharedPreferences.getInstance();

  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        sharedPreferencesProvider.overrideWithValue(preferences),
        companiesProvider.overrideWith((Ref ref) async => companies),
        dashboardRepositoryProvider.overrideWithValue(repository),
        authControllerProvider.overrideWith(() => _SignedIn(user ?? _admin())),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: const DashboardScreen()),
    ),
  );
  await tester.pumpAndSettle();
}

class _SignedIn extends AuthController {
  _SignedIn(this._user);

  final AppUser _user;

  @override
  AuthState build() => AuthState(status: AuthStatus.signedIn, user: _user);
}

/// Rebuilds a dashboard fixture with a different freshness block.
Map<String, Object?> _withFreshness(Map<String, Object?> freshness) {
  final Map<String, Object?> body =
      Map<String, Object?>.from(fixture('dashboard'));
  body['freshness'] = freshness;
  return body;
}

/// The amount shown under one named label.
///
/// Scoped to the card carrying that label rather than searched for globally:
/// several figures legitimately share a value in this sample data, and a bare
/// text finder would pass while the number sat under the wrong label.
///
/// Two card types, because the headline figures live on the dark [HeroCard] and
/// the rest on [KpiCard] tiles. The guarantee the helper exists for -- that the
/// amount is under *this* label -- is the same either way.
Finder _kpiAmount(String label, String amount) {
  for (final Type card in <Type>[KpiCard, HeroCard]) {
    final Finder holder = find.widgetWithText(card, label);
    if (holder.evaluate().isNotEmpty) {
      return find.descendant(of: holder, matching: find.text(amount));
    }
  }
  // Nothing carries the label; return a finder that fails with the KpiCard
  // message, which is the more common case and the more useful hint.
  return find.descendant(
    of: find.widgetWithText(KpiCard, label),
    matching: find.text(amount),
  );
}

/// Drives the date selection through the real provider.
///
/// The date picker itself is Flutter's, so tapping through its calendar would
/// be testing the framework; what matters here is that a chosen date reaches
/// the repository and changes what the screen claims.
void _selectPeriod(WidgetTester tester, DateRange? range, {String label = 'Custom'}) {
  ProviderScope.containerOf(tester.element(find.byType(DashboardScreen)))
      .read(dashboardPeriodProvider(_company.id).notifier)
      .select(range == null ? null : PeriodSelection(range, label));
}

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
  testWidgets('picking a period sends both bounds and relabels the figures',
      (WidgetTester tester) async {
    final _FakeDashboardRepository repository =
        _FakeDashboardRepository.periodAware(
      Dashboard.fromJson(fixture('dashboard')),
      Dashboard.fromJson(fixture('dashboard_period')),
    );
    await _pump(tester, repository: repository);

    // Today is the default and sends no dates at all, so the request still
    // matches the snapshot the background refresher warms.
    expect(repository.lastPeriod, isNull);
    expect(find.text("Today's sales"), findsOneWidget);

    final DateRange range = DateRange(
      DateTime(2026, 2, 14),
      DateTime(2026, 3, 15),
    );
    _selectPeriod(tester, range, label: 'This month');
    await tester.pumpAndSettle();

    expect(repository.lastPeriod?.fromWire, '2026-02-14');
    expect(repository.lastPeriod?.toWire, '2026-03-15');

    // "Today's sales" over a figure covering thirty days is the single most
    // misleading label this screen could carry.
    expect(find.text("Today's sales"), findsNothing);
    expect(find.text('Sales · This month'), findsOneWidget);
    // The period total from the fixture, not today's 11,800.
    expect(_kpiAmount('Sales · This month', '₹16,800'), findsOneWidget);
    // And the way back is always on screen, not buried in a menu.
    expect(find.text('Back to today'), findsOneWidget);
  });

  testWidgets('a period view declares which figures could not be scoped to it',
      (WidgetTester tester) async {
    // Tally evaluates closing balances and stock value against the current
    // date and takes no date to evaluate against, so cash and inventory are
    // still "now" on a period dashboard. Showing them unmarked beside scoped
    // sales is the one outcome that silently mixes two windows.
    final _FakeDashboardRepository repository =
        _FakeDashboardRepository.periodAware(
      Dashboard.fromJson(fixture('dashboard')),
      Dashboard.fromJson(fixture('dashboard_period')),
    );
    await _pump(tester, repository: repository);

    // Nothing to scroll to: on today's dashboard the caveat is not in the
    // tree at all, because there are not two windows to tell apart.
    expect(find.textContaining('as they stand now'), findsNothing);

    _selectPeriod(
      tester,
      DateRange(DateTime(2026, 2, 14), DateTime(2026, 3, 15)),
    );
    await tester.pumpAndSettle();

    await _scrollTo(tester, find.textContaining('as they stand now'));
    expect(find.textContaining('as they stand now'), findsWidgets);
  });

  testWidgets('a missing baseline renders as "--", never as a collapse',
      (WidgetTester tester) async {
    // The sample books hold nothing in the span before the period, so there is
    // no honest comparison to draw. A 100% drop would be a claim about trade
    // that never happened.
    final _FakeDashboardRepository repository =
        _FakeDashboardRepository.periodAware(
      Dashboard.fromJson(fixture('dashboard')),
      Dashboard.fromJson(fixture('dashboard_period')),
    );
    await _pump(tester, repository: repository);

    _selectPeriod(
      tester,
      DateRange(DateTime(2026, 2, 14), DateTime(2026, 3, 15)),
      label: 'This month',
    );
    await tester.pumpAndSettle();

    await _scrollTo(tester, find.text('This period'));
    expect(find.text('This period'), findsOneWidget);
    expect(find.text('Previous'), findsOneWidget);
    expect(find.text('--'), findsWidgets);
  });

  testWidgets('returning to today clears both bounds from the request',
      (WidgetTester tester) async {
    final _FakeDashboardRepository repository =
        _FakeDashboardRepository.periodAware(
      Dashboard.fromJson(fixture('dashboard')),
      Dashboard.fromJson(fixture('dashboard_period')),
    );
    await _pump(tester, repository: repository);

    _selectPeriod(
      tester,
      DateRange(DateTime(2026, 2, 14), DateTime(2026, 3, 15)),
    );
    await tester.pumpAndSettle();
    expect(repository.lastPeriod, isNotNull);

    await tester.tap(find.text('Back to today'));
    await tester.pumpAndSettle();

    expect(repository.lastPeriod, isNull);
    expect(find.text("Today's sales"), findsOneWidget);
  });

  testWidgets('a single day that is today is not treated as a period',
      (WidgetTester tester) async {
    // Otherwise every launch would send a spelled-out date and miss the
    // warmed snapshot, making the ordinary dashboard slower for no gain.
    final _FakeDashboardRepository repository =
        _FakeDashboardRepository.periodAware(
      Dashboard.fromJson(fixture('dashboard')),
      Dashboard.fromJson(fixture('dashboard_period')),
    );
    await _pump(tester, repository: repository);

    _selectPeriod(tester, DateRange.today(), label: 'Today');
    await tester.pumpAndSettle();

    expect(repository.lastPeriod, isNull);
    expect(find.text("Today's sales"), findsOneWidget);
    expect(find.text('Back to today'), findsNothing);
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

  testWidgets('an account nobody has approved is told to wait, not to set up',
      (WidgetTester tester) async {
    // The first screen a brand new customer sees. Pointing them at a pairing
    // wizard the backend would refuse is worse than useless -- they would
    // conclude the product is broken rather than that they are in a queue.
    await _pump(
      tester,
      repository: _FakeDashboardRepository(Dashboard.fromJson(fixture('dashboard'))),
      companies: const <Company>[],
      user: _admin(approved: false),
    );

    expect(find.text('Almost there'), findsOneWidget);
    expect(find.textContaining('waiting to be approved'), findsOneWidget);
    expect(find.text('Set up a connection'), findsNothing);
  });
}
