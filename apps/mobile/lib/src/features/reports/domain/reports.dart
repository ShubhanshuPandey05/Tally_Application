import '../../../core/model/figures.dart';
import '../../../core/money/money.dart';

/// How hard a read should try.
///
/// Mirrors the backend's fetch modes. [cached] is the default for list screens
/// because every live read costs a round trip to a desktop PC that is also
/// running the shop's till; [live] is reserved for a deliberate pull-to-refresh.
enum FetchMode {
  auto,
  cached,
  live;

  String get wire => name;
}

class DateRange {
  const DateRange(this.from, this.to);

  final DateTime from;
  final DateTime to;

  static DateRange today() {
    final DateTime now = DateTime.now();
    final DateTime day = DateTime(now.year, now.month, now.day);
    return DateRange(day, day);
  }

  static DateRange thisMonth() {
    final DateTime now = DateTime.now();
    return DateRange(DateTime(now.year, now.month), DateTime(now.year, now.month, now.day));
  }

  static DateRange lastDays(int days) {
    final DateTime now = DateTime.now();
    final DateTime end = DateTime(now.year, now.month, now.day);
    return DateRange(end.subtract(Duration(days: days - 1)), end);
  }

  String get fromWire => _iso(from);
  String get toWire => _iso(to);

  bool get isSingleDay =>
      from.year == to.year && from.month == to.month && from.day == to.day;

  static String _iso(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';

  @override
  bool operator ==(Object other) =>
      other is DateRange && other.fromWire == fromWire && other.toWire == toWire;

  @override
  int get hashCode => Object.hash(fromWire, toWire);
}

class DaybookReport {
  const DaybookReport({
    required this.range,
    required this.voucherCount,
    required this.total,
    required this.vouchers,
  });

  final DateRange range;
  final int voucherCount;
  final Money total;
  final List<TransactionLine> vouchers;

  factory DaybookReport.fromJson(Map<String, Object?> json) => DaybookReport(
        range: DateRange(
          DateTime.tryParse(json['from_date'] as String? ?? '') ?? DateTime.now(),
          DateTime.tryParse(json['to_date'] as String? ?? '') ?? DateTime.now(),
        ),
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
        total: Money.fromJson(json['total']),
        vouchers: _list(json['vouchers'], TransactionLine.fromJson),
      );
}

/// Receivable = money owed to the business. Payable = money it owes.
enum OutstandingKind {
  receivable,
  payable;

  String get wire => name;

  String get title => this == OutstandingKind.receivable ? 'Receivables' : 'Payables';

  String get question =>
      this == OutstandingKind.receivable ? 'Who owes me money?' : 'Who do I owe?';
}

class OutstandingReport {
  const OutstandingReport({
    required this.kind,
    required this.asOf,
    required this.total,
    required this.overdue,
    required this.billCount,
    required this.partyCount,
    required this.ageing,
    required this.bills,
  });

  final OutstandingKind kind;
  final DateTime asOf;
  final Money total;
  final Money overdue;
  final int billCount;
  final int partyCount;
  final Map<String, Money> ageing;
  final List<OutstandingBill> bills;

  /// Bills grouped by party, worst-overdue first -- the way an owner chases
  /// payment. A flat list of two hundred bills is a spreadsheet, not an answer.
  List<PartyBills> get byParty {
    final Map<String, List<OutstandingBill>> grouped = <String, List<OutstandingBill>>{};
    for (final OutstandingBill bill in bills) {
      grouped.putIfAbsent(bill.party, () => <OutstandingBill>[]).add(bill);
    }
    final List<PartyBills> parties = grouped.entries
        .map((MapEntry<String, List<OutstandingBill>> entry) =>
            PartyBills(party: entry.key, bills: entry.value))
        .toList();
    parties.sort((PartyBills a, PartyBills b) {
      final int byOverdue = b.maxDaysOverdue.compareTo(a.maxDaysOverdue);
      return byOverdue != 0 ? byOverdue : b.total.amount.compareTo(a.total.amount);
    });
    return parties;
  }

  factory OutstandingReport.fromJson(Map<String, Object?> json) {
    final Object? summary = json['summary'];
    final Map<String, Object?> totals = summary is Map ? _map(summary) : <String, Object?>{};
    final Object? ageingJson = totals['ageing'];
    final Map<String, Money> ageing = <String, Money>{};
    if (ageingJson is Map) {
      for (final String bucket in ageingOrder) {
        ageing[bucket] = Money.fromJson(ageingJson[bucket]);
      }
    }

    return OutstandingReport(
      kind: (json['kind'] as String? ?? 'receivable').contains('payable')
          ? OutstandingKind.payable
          : OutstandingKind.receivable,
      asOf: DateTime.tryParse(json['as_of'] as String? ?? '') ?? DateTime.now(),
      total: Money.fromJson(totals['total']),
      overdue: Money.fromJson(totals['overdue']),
      billCount: (totals['bill_count'] as num?)?.toInt() ?? 0,
      partyCount: (totals['party_count'] as num?)?.toInt() ?? 0,
      ageing: ageing,
      bills: _list(json['bills'], OutstandingBill.fromJson),
    );
  }
}

class PartyBills {
  PartyBills({required this.party, required this.bills});

  final String party;
  final List<OutstandingBill> bills;

  Money get total => bills.fold(Money.zero, (Money sum, OutstandingBill b) => sum + b.amount);

  int get maxDaysOverdue =>
      bills.fold(0, (int worst, OutstandingBill b) => b.daysOverdue > worst ? b.daysOverdue : worst);
}

class StockReport {
  const StockReport({
    required this.value,
    required this.itemCount,
    required this.negativeStockCount,
    required this.lowStockCount,
    required this.items,
  });

  final Money value;
  final int itemCount;
  final int negativeStockCount;
  final int lowStockCount;
  final List<StockLine> items;

  factory StockReport.fromJson(Map<String, Object?> json) {
    final Object? summary = json['summary'];
    final Map<String, Object?> totals = summary is Map ? _map(summary) : <String, Object?>{};
    return StockReport(
      value: Money.fromJson(totals['value']),
      itemCount: (totals['item_count'] as num?)?.toInt() ?? 0,
      negativeStockCount: (totals['negative_stock_count'] as num?)?.toInt() ?? 0,
      lowStockCount: (totals['low_stock_count'] as num?)?.toInt() ?? 0,
      items: _list(json['items'], StockLine.fromJson),
    );
  }
}

class LedgerReport {
  const LedgerReport({required this.ledgers});

  final List<LedgerLine> ledgers;

  factory LedgerReport.fromJson(Map<String, Object?> json) =>
      LedgerReport(ledgers: _list(json['ledgers'], LedgerLine.fromJson));
}

class SlowMovingReport {
  const SlowMovingReport({required this.windowDays, required this.items});

  final int windowDays;
  final List<StockLine> items;

  factory SlowMovingReport.fromJson(Map<String, Object?> json) => SlowMovingReport(
        windowDays: (json['window_days'] as num?)?.toInt() ?? 90,
        items: _list(json['items'], StockLine.fromJson),
      );
}

Map<String, Object?> _map(Object? value) {
  if (value is Map<String, Object?>) return value;
  if (value is Map) {
    return value.map((Object? k, Object? v) => MapEntry<String, Object?>(k.toString(), v));
  }
  return <String, Object?>{};
}

List<T> _list<T>(Object? raw, T Function(Map<String, Object?> json) decode) {
  if (raw is! List) return const <Never>[];
  return raw
      .whereType<Map<Object?, Object?>>()
      .map((Map<Object?, Object?> item) => decode(_map(item)))
      .toList(growable: false);
}
