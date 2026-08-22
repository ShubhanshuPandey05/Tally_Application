import '../../../core/model/date_range.dart';
import '../../../core/model/figures.dart';
import '../../../core/money/money.dart';

// Re-exported so the many screens that import this file for [DaybookReport] and
// friends keep seeing [DateRange] alongside them, now that it is shared with
// the dashboard and lives in core.
export '../../../core/model/date_range.dart';

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

  /// The stock Indian group these parties are filed under. Only a label for the
  /// report index -- the backend resolves the real group and echoes it back, so
  /// a company that renamed its groups still gets the right heading.
  String get defaultGroup =>
      this == OutstandingKind.receivable ? 'Sundry Debtors' : 'Sundry Creditors';

  String get groupQuestion => this == OutstandingKind.receivable
      ? 'Every debtor, netted against advances'
      : 'Every creditor, netted against advances';
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

/// Outstanding for the parties filed under one ledger group.
///
/// A different question from [OutstandingReport], not a filtered version of it.
/// That report splits bills by the side each one closes on, so a customer's
/// advance counts as a payable. This one takes every party whose ledger sits
/// under the group -- Sundry Debtors, say -- and reports their advances
/// separately from what they owe. The two totals will not agree, and both are
/// right.
class GroupOutstandingReport {
  const GroupOutstandingReport({
    required this.group,
    required this.kind,
    required this.asOf,
    required this.total,
    required this.overdue,
    required this.advances,
    required this.net,
    required this.billCount,
    required this.partyCount,
    required this.ageing,
    required this.parties,
    required this.ungroupedPartyCount,
  });

  final String group;
  final OutstandingKind kind;
  final DateTime asOf;

  /// What the group owes on its expected side. Advances are not in here.
  final Money total;
  final Money overdue;

  /// Bills pointing the other way: prepayments, contra entries.
  final Money advances;

  /// [total] less [advances], carrying its own side.
  final Money net;

  final int billCount;
  final int partyCount;
  final Map<String, Money> ageing;
  final List<GroupParty> parties;

  /// Parties with bills whose ledger was not in this read. Surfaced rather than
  /// hidden: the difference between "no other debtors" and "we could not tell"
  /// is the difference between a total an owner can trust and one they cannot.
  final int ungroupedPartyCount;

  bool get hasAdvances => !advances.isZero;

  factory GroupOutstandingReport.fromJson(Map<String, Object?> json) {
    final Map<String, Object?> summary = _map(json['summary']);
    final Map<String, Object?> ageingJson = _map(summary['ageing']);
    final Map<String, Money> ageing = <String, Money>{
      for (final String bucket in ageingOrder) bucket: Money.fromJson(ageingJson[bucket]),
    };

    return GroupOutstandingReport(
      group: json['group'] as String? ?? '',
      kind: (json['kind'] as String? ?? 'receivable').contains('payable')
          ? OutstandingKind.payable
          : OutstandingKind.receivable,
      asOf: DateTime.tryParse(json['as_of'] as String? ?? '') ?? DateTime.now(),
      total: Money.fromJson(summary['total']),
      overdue: Money.fromJson(summary['overdue']),
      advances: Money.fromJson(summary['advances']),
      net: Money.fromJson(summary['net']),
      billCount: (summary['bill_count'] as num?)?.toInt() ?? 0,
      partyCount: (summary['party_count'] as num?)?.toInt() ?? 0,
      ageing: ageing,
      parties: _list(json['parties'], GroupParty.fromJson),
      ungroupedPartyCount: (json['ungrouped_party_count'] as num?)?.toInt() ?? 0,
    );
  }
}

/// One party's position within a group. Grouped by the backend, which is the
/// only side that knows which ledger group a party belongs to.
class GroupParty {
  const GroupParty({
    required this.party,
    required this.total,
    required this.advances,
    required this.net,
    required this.billCount,
    required this.daysOverdue,
    required this.bills,
  });

  final String party;
  final Money total;
  final Money advances;
  final Money net;
  final int billCount;
  final int daysOverdue;
  final List<OutstandingBill> bills;

  bool get isOverdue => daysOverdue > 0;
  bool get hasAdvances => !advances.isZero;

  factory GroupParty.fromJson(Map<String, Object?> json) => GroupParty(
        party: json['party'] as String? ?? '',
        total: Money.fromJson(json['total']),
        advances: Money.fromJson(json['advances']),
        net: Money.fromJson(json['net']),
        billCount: (json['bill_count'] as num?)?.toInt() ?? 0,
        daysOverdue: (json['days_overdue'] as num?)?.toInt() ?? 0,
        bills: _list(json['bills'], OutstandingBill.fromJson),
      );
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
