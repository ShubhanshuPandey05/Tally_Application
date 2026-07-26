import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/model/figures.dart';
import 'package:tallyflow/src/core/money/money.dart';
import 'package:tallyflow/src/core/money/money_format.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/connectors/domain/connector.dart';
import 'package:tallyflow/src/features/dashboard/domain/dashboard.dart';
import 'package:tallyflow/src/features/reports/domain/reports.dart';

import 'support/fixtures.dart';

/// Decodes real backend responses.
///
/// The fixtures are produced by the backend's own test suite from the real ASGI
/// app, so a field rename on the server fails here rather than showing up as a
/// blank tile in production.
void main() {
  group('dashboard', () {
    test('decodes every section of a healthy response', () {
      final Dashboard dashboard = Dashboard.fromJson(fixture('dashboard'));

      expect(dashboard.isEmpty, isFalse);
      expect(dashboard.currency, 'INR');
      expect(dashboard.freshness.available, isTrue);
      expect(dashboard.freshness.connectorOnline, isTrue);

      expect(dashboard.sales.hasData, isTrue);
      expect(dashboard.purchases.hasData, isTrue);
      expect(dashboard.funds.hasData, isTrue);
      expect(dashboard.receivables.hasData, isTrue);
      expect(dashboard.payables.hasData, isTrue);
      expect(dashboard.inventory.hasData, isTrue);
      expect(dashboard.activity.hasData, isTrue);
    });

    test("today's sales survive the trip as an exact figure", () {
      final Dashboard dashboard = Dashboard.fromJson(fixture('dashboard'));
      final TradeSummary sales = dashboard.sales.data!;

      // One 11,800 invoice today; the cancelled 9,99,999 voucher must not be
      // in here. The backend drops it, and this asserts the app is reading the
      // total that already excludes it.
      expect(MoneyFormat.full(sales.today), '₹11,800.00');
      expect(sales.trend, isNotEmpty);
      expect(sales.trend.length, 30, reason: 'one point per day including zeroes');
      expect(sales.topParties.first.name, 'Reliance Retail');
    });

    test('a null change_pct stays null instead of becoming zero', () {
      final Dashboard dashboard = Dashboard.fromJson(fixture('dashboard'));
      // Nothing was sold last month in the sample data, so there is no
      // baseline. Rendering that as 0% or 100% would both be false claims.
      expect(dashboard.sales.data!.changePct, isNull);
    });

    test('cash and bank are split and totalled', () {
      final FundsSummary funds = Dashboard.fromJson(fixture('dashboard')).funds.data!;
      expect(MoneyFormat.full(funds.cash), '₹3,44,220.00');
      expect(MoneyFormat.full(funds.bank), '₹1,25,000.00');
      expect(funds.cashAccounts.map((BalanceLine b) => b.name), contains('Cash'));
      expect(funds.bankAccounts.map((BalanceLine b) => b.name), contains('HDFC Bank'));
    });

    test('ageing buckets arrive in a renderable order', () {
      final OutstandingSummary payables =
          Dashboard.fromJson(fixture('dashboard')).payables.data!;

      expect(payables.ageing.keys, containsAll(ageingOrder));
      // The sample payable is 113 days old.
      expect(payables.ageing['91_180']!.isZero, isFalse);
      expect(payables.overdueShare, greaterThan(0));
    });

    test('negative stock is surfaced with examples, not just a count', () {
      final InventorySummary inventory =
          Dashboard.fromJson(fixture('dashboard')).inventory.data!;

      expect(inventory.negativeStockCount, 1);
      expect(inventory.negativeStock.single.name, 'Oil');
      expect(inventory.lowStockCount, 1);
      expect(inventory.lowStock.single.name, 'Sugar');
    });

    test('a failed section degrades on its own', () {
      final Dashboard dashboard = Dashboard.fromJson(fixture('dashboard_degraded'));

      // The promise the whole dashboard layout depends on: three good sections
      // render, the fourth says why it could not.
      expect(dashboard.sales.hasData, isTrue);
      expect(dashboard.funds.hasData, isTrue);
      expect(dashboard.inventory.hasData, isTrue);

      expect(dashboard.receivables.ok, isFalse);
      expect(dashboard.receivables.data, isNull);
      expect(dashboard.receivables.error, isNotNull);
      expect(dashboard.isEmpty, isFalse);
    });
  });

  group('reports', () {
    test('day book groups real vouchers with their kinds', () {
      final Map<String, Object?> body = fixture('daybook');
      final DaybookReport report =
          DaybookReport.fromJson(body['data']! as Map<String, Object?>);

      expect(report.voucherCount, 3, reason: 'the cancelled voucher is excluded');
      expect(
        report.vouchers.map((TransactionLine v) => v.kind).toSet(),
        <String>{'sales', 'purchase'},
      );
      expect(report.vouchers.first.party, isNotNull);
      expect(report.total.isZero, isFalse);
    });

    test('outstanding groups bills by party, worst overdue first', () {
      final Map<String, Object?> body = fixture('outstanding_payable');
      final OutstandingReport report =
          OutstandingReport.fromJson(body['data']! as Map<String, Object?>);

      expect(report.kind, OutstandingKind.payable);
      expect(report.bills, isNotEmpty);
      expect(report.byParty.first.party, 'SARA DISITIBUTOR');
      expect(report.byParty.first.maxDaysOverdue, greaterThan(90));
      expect(MoneyFormat.full(report.byParty.first.total), '₹2,12,600.00');
    });

    test('receivables that are not yet due report zero overdue', () {
      final Map<String, Object?> body = fixture('outstanding_receivable');
      final OutstandingReport report =
          OutstandingReport.fromJson(body['data']! as Map<String, Object?>);

      expect(report.kind, OutstandingKind.receivable);
      expect(report.overdue.isZero, isTrue);
      expect(report.total.isZero, isFalse);
    });

    test('stock carries the flags the list colours itself with', () {
      final Map<String, Object?> body = fixture('stock');
      final StockReport report =
          StockReport.fromJson(body['data']! as Map<String, Object?>);

      expect(report.itemCount, 3);
      final StockLine oil =
          report.items.firstWhere((StockLine item) => item.name == 'Oil');
      expect(oil.isNegative, isTrue);
      expect(oil.quantity, lessThan(0));

      final StockLine sugar =
          report.items.firstWhere((StockLine item) => item.name == 'Sugar');
      expect(sugar.isBelowReorder, isTrue);
      expect(sugar.reorderLevel, 50);
    });

    test('ledger balances keep their side', () {
      final Map<String, Object?> body = fixture('ledgers');
      final LedgerReport report =
          LedgerReport.fromJson(body['data']! as Map<String, Object?>);

      final LedgerLine creditor = report.ledgers
          .firstWhere((LedgerLine line) => line.group == 'Sundry Creditors');
      expect(creditor.closing.side, MoneySide.credit);
      expect(MoneyFormat.withSide(creditor.closing), endsWith('Cr'));

      final LedgerLine cash =
          report.ledgers.firstWhere((LedgerLine line) => line.name == 'Cash');
      expect(cash.closing.side, MoneySide.debit);
    });

    test('slow movers state the window they were measured over', () {
      final Map<String, Object?> body = fixture('slow_moving');
      final SlowMovingReport report =
          SlowMovingReport.fromJson(body['data']! as Map<String, Object?>);

      expect(report.windowDays, 90);
      expect(report.items, isNotEmpty);
      // Negative-stock items are not "slow moving", they are a data error.
      expect(
        report.items.every((StockLine item) => item.quantity > 0),
        isTrue,
      );
    });

    test('every report envelope carries its own freshness', () {
      for (final String name in <String>[
        'daybook',
        'outstanding_receivable',
        'stock',
        'ledgers',
        'slow_moving',
      ]) {
        final Map<String, Object?> body = fixture(name);
        expect(body['meta'], isA<Map<String, Object?>>(), reason: '$name has no meta');
        final Map<String, Object?> meta = body['meta']! as Map<String, Object?>;
        expect(meta.containsKey('refreshed_at'), isTrue, reason: name);
        expect(meta.containsKey('connector_online'), isTrue, reason: name);
      }
    });
  });

  group('account', () {
    test('decodes the signed-in user and role', () {
      final AppUser user = AppUser.fromJson(fixture('me'));
      expect(user.email, 'owner@bhatiastores.in');
      expect(user.role, UserRole.owner);
      expect(user.role.canManageConnectors, isTrue);
      expect(user.initials, 'SO');
    });

    test('decodes companies and connectors', () {
      final List<Company> companies =
          fixtureList('companies').map(Company.fromJson).toList();
      expect(companies, hasLength(1));
      expect(companies.single.baseCurrency, 'INR');
      expect(companies.single.isActive, isTrue);

      final List<Connector> connectors =
          fixtureList('connectors').map(Connector.fromJson).toList();
      expect(connectors, hasLength(1));
      expect(connectors.single.companyCount, 1);

      // The distinction the whole connectors screen turns on: the PC is
      // reachable but has not confirmed TallyPrime is answering. Collapsing
      // these two into one "offline" state would send a shopkeeper to check
      // their internet when the real fix is to open Tally.
      expect(connectors.single.online, isTrue);
      expect(connectors.single.tallyOnline, isFalse);
      expect(connectors.single.health, ConnectorHealth.tallyClosed);
      expect(connectors.single.health.advice, contains('TallyPrime'));
    });
  });
}
