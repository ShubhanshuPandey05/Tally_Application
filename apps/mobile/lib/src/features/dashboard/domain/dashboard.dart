import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';

/// One tile's worth of data, which may have failed on its own.
///
/// The backend degrades per section: if reading outstanding bills times out but
/// the ledgers came back, the app still shows cash and sales and marks only
/// receivables unavailable. Modelling that as `ok + error` per section, rather
/// than one nullable dashboard, is what makes a partial outage look like a
/// partial outage instead of a broken app.
class Section<T> {
  const Section({required this.ok, this.data, this.error, this.freshness = Freshness.unavailable});

  final bool ok;
  final T? data;
  final String? error;
  final Freshness freshness;

  bool get hasData => ok && data != null;

  static Section<T> parse<T>(
    Map<String, Object?>? json,
    T Function(Map<String, Object?> data) decode,
  ) {
    if (json == null) {
      return Section<T>(ok: false, error: 'Not available.');
    }
    final Object? payload = json['data'];
    final bool ok = (json['ok'] as bool? ?? false) && payload is Map;
    return Section<T>(
      ok: ok,
      data: ok ? decode(_map(payload)) : null,
      error: json['error'] as String?,
      freshness: Freshness.fromJson(json['meta'] is Map ? _map(json['meta']) : null),
    );
  }

  static Map<String, Object?> _map(Object? value) {
    if (value is Map<String, Object?>) return value;
    if (value is Map) {
      return value.map((Object? k, Object? v) => MapEntry<String, Object?>(k.toString(), v));
    }
    return <String, Object?>{};
  }
}

class TradeSummary {
  const TradeSummary({
    required this.today,
    required this.yesterday,
    required this.thisMonth,
    required this.lastMonth,
    required this.trend,
    required this.topParties,
    required this.topProducts,
    this.changePct,
  });

  final Money today;
  final Money yesterday;
  final Money thisMonth;
  final Money lastMonth;

  /// Null when last month was zero. Rendered as "--", never as 0% or 100% --
  /// both would be a claim about a movement that did not happen.
  final double? changePct;

  final List<TrendPoint> trend;
  final List<PartyTotal> topParties;
  final List<ProductTotal> topProducts;

  factory TradeSummary.fromJson(Map<String, Object?> json) => TradeSummary(
        today: Money.fromJson(json['today']),
        yesterday: Money.fromJson(json['yesterday']),
        thisMonth: Money.fromJson(json['this_month']),
        lastMonth: Money.fromJson(json['last_month']),
        changePct: (json['change_pct'] as num?)?.toDouble(),
        trend: _list(json['trend'], TrendPoint.fromJson),
        topParties: _list(json['top_parties'], PartyTotal.fromJson),
        topProducts: _list(json['top_products'], ProductTotal.fromJson),
      );
}

class FundsSummary {
  const FundsSummary({
    required this.cash,
    required this.bank,
    required this.total,
    required this.cashAccounts,
    required this.bankAccounts,
  });

  final Money cash;
  final Money bank;
  final Money total;
  final List<BalanceLine> cashAccounts;
  final List<BalanceLine> bankAccounts;

  factory FundsSummary.fromJson(Map<String, Object?> json) => FundsSummary(
        cash: Money.fromJson(json['cash']),
        bank: Money.fromJson(json['bank']),
        total: Money.fromJson(json['total']),
        cashAccounts: _list(json['cash_accounts'], BalanceLine.fromJson),
        bankAccounts: _list(json['bank_accounts'], BalanceLine.fromJson),
      );
}

class OutstandingSummary {
  const OutstandingSummary({
    required this.total,
    required this.overdue,
    required this.billCount,
    required this.partyCount,
    required this.ageing,
    required this.topParties,
  });

  final Money total;
  final Money overdue;
  final int billCount;
  final int partyCount;
  final Map<String, Money> ageing;
  final List<PartyTotal> topParties;

  double get overdueShare =>
      total.isZero ? 0 : overdue.amount.toDouble() / total.amount.toDouble();

  factory OutstandingSummary.fromJson(Map<String, Object?> json) {
    final Object? ageingJson = json['ageing'];
    final Map<String, Money> ageing = <String, Money>{};
    if (ageingJson is Map) {
      for (final String bucket in ageingOrder) {
        ageing[bucket] = Money.fromJson(ageingJson[bucket]);
      }
    }
    return OutstandingSummary(
      total: Money.fromJson(json['total']),
      overdue: Money.fromJson(json['overdue']),
      billCount: (json['bill_count'] as num?)?.toInt() ?? 0,
      partyCount: (json['party_count'] as num?)?.toInt() ?? 0,
      ageing: ageing,
      topParties: _list(json['top_parties'], PartyTotal.fromJson),
    );
  }
}

class InventorySummary {
  const InventorySummary({
    required this.value,
    required this.itemCount,
    required this.negativeStockCount,
    required this.lowStockCount,
    required this.negativeStock,
    required this.lowStock,
  });

  final Money value;
  final int itemCount;

  /// Goods billed out that the books say were never received. A data-entry
  /// error worth surfacing, not a rounding detail.
  final int negativeStockCount;

  final int lowStockCount;
  final List<StockLine> negativeStock;
  final List<StockLine> lowStock;

  factory InventorySummary.fromJson(Map<String, Object?> json) => InventorySummary(
        value: Money.fromJson(json['value']),
        itemCount: (json['item_count'] as num?)?.toInt() ?? 0,
        negativeStockCount: (json['negative_stock_count'] as num?)?.toInt() ?? 0,
        lowStockCount: (json['low_stock_count'] as num?)?.toInt() ?? 0,
        negativeStock: _list(json['negative_stock'], StockLine.fromJson),
        lowStock: _list(json['low_stock'], StockLine.fromJson),
      );
}

class ActivitySummary {
  const ActivitySummary({required this.recent, required this.voucherCount});

  final List<TransactionLine> recent;
  final int voucherCount;

  factory ActivitySummary.fromJson(Map<String, Object?> json) => ActivitySummary(
        recent: _list(json['recent'], TransactionLine.fromJson),
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
      );
}

/// Everything the home screen needs, from a single round trip.
class Dashboard {
  const Dashboard({
    required this.companyId,
    required this.companyName,
    required this.currency,
    required this.asOf,
    required this.freshness,
    required this.sales,
    required this.purchases,
    required this.funds,
    required this.receivables,
    required this.payables,
    required this.inventory,
    required this.activity,
  });

  final String companyId;
  final String companyName;
  final String currency;
  final DateTime asOf;
  final Freshness freshness;

  final Section<TradeSummary> sales;
  final Section<TradeSummary> purchases;
  final Section<FundsSummary> funds;
  final Section<OutstandingSummary> receivables;
  final Section<OutstandingSummary> payables;
  final Section<InventorySummary> inventory;
  final Section<ActivitySummary> activity;

  /// True when not one section came back. The app must show a first-run or
  /// offline state here -- rendering zeroes would read as "you sold nothing",
  /// which is a completely different claim from "we could not reach Tally".
  bool get isEmpty =>
      !sales.ok && !purchases.ok && !funds.ok && !receivables.ok &&
      !payables.ok && !inventory.ok && !activity.ok;

  factory Dashboard.fromJson(Map<String, Object?> json) {
    final Map<String, Object?> company = Section._map(json['company']);
    final Map<String, Object?> sections = Section._map(json['sections']);

    Map<String, Object?>? section(String name) {
      final Object? value = sections[name];
      return value is Map ? Section._map(value) : null;
    }

    return Dashboard(
      companyId: company['id'] as String? ?? '',
      companyName: company['name'] as String? ?? '',
      currency: company['currency'] as String? ?? 'INR',
      asOf: DateTime.tryParse(json['as_of'] as String? ?? '') ?? DateTime.now(),
      freshness: Freshness.fromJson(
        json['freshness'] is Map ? Section._map(json['freshness']) : null,
      ),
      sales: Section.parse(section('sales'), TradeSummary.fromJson),
      purchases: Section.parse(section('purchases'), TradeSummary.fromJson),
      funds: Section.parse(section('cash_and_bank'), FundsSummary.fromJson),
      receivables: Section.parse(section('receivables'), OutstandingSummary.fromJson),
      payables: Section.parse(section('payables'), OutstandingSummary.fromJson),
      inventory: Section.parse(section('inventory'), InventorySummary.fromJson),
      activity: Section.parse(section('activity'), ActivitySummary.fromJson),
    );
  }
}

List<T> _list<T>(Object? raw, T Function(Map<String, Object?> json) decode) {
  if (raw is! List) return const <Never>[];
  return raw
      .whereType<Map<Object?, Object?>>()
      .map((Map<Object?, Object?> item) => decode(Section._map(item)))
      .toList(growable: false);
}
