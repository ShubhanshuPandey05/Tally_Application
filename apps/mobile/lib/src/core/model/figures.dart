import '../money/money.dart';

/// Small value objects that appear in more than one feature.
///
/// The dashboard's "top customers" tile and the day book's transaction list are
/// the same shapes the reports use, so they live here rather than being decoded
/// twice with two subtly different sets of fallbacks.

class TrendPoint {
  const TrendPoint({required this.date, required this.value});

  final DateTime date;
  final double value;

  /// The backend emits a zero for days with no trade rather than omitting them,
  /// so a chart cannot draw a straight line across a week the shop was shut and
  /// imply steady business.
  factory TrendPoint.fromJson(Map<String, Object?> json) => TrendPoint(
        date: DateTime.tryParse(json['date'] as String? ?? '') ?? DateTime.now(),
        value: double.tryParse(json['value']?.toString() ?? '0') ?? 0,
      );
}

class PartyTotal {
  const PartyTotal({
    required this.name,
    required this.amount,
    this.voucherCount = 0,
    this.daysOverdue = 0,
  });

  final String name;
  final Money amount;
  final int voucherCount;
  final int daysOverdue;

  factory PartyTotal.fromJson(Map<String, Object?> json) => PartyTotal(
        name: json['name'] as String? ?? '',
        amount: Money.fromJson(json['amount']),
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
        daysOverdue: (json['days_overdue'] as num?)?.toInt() ?? 0,
      );
}

class ProductTotal {
  const ProductTotal({
    required this.name,
    required this.amount,
    required this.quantity,
    this.unit,
  });

  final String name;
  final Money amount;
  final double quantity;
  final String? unit;

  factory ProductTotal.fromJson(Map<String, Object?> json) => ProductTotal(
        name: json['name'] as String? ?? '',
        amount: Money.fromJson(json['amount']),
        quantity: (json['quantity'] as num?)?.toDouble() ?? 0,
        unit: json['unit'] as String?,
      );
}

class BalanceLine {
  const BalanceLine({required this.name, required this.balance});

  final String name;
  final Money balance;

  factory BalanceLine.fromJson(Map<String, Object?> json) => BalanceLine(
        name: json['name'] as String? ?? '',
        balance: Money.fromJson(json['balance']),
      );
}

class StockLine {
  const StockLine({
    required this.name,
    required this.quantity,
    required this.value,
    this.unit,
    this.group,
    this.rate,
    this.reorderLevel,
    this.isNegative = false,
    this.isBelowReorder = false,
  });

  final String name;
  final double quantity;
  final Money value;
  final String? unit;
  final String? group;
  final Money? rate;
  final double? reorderLevel;
  final bool isNegative;
  final bool isBelowReorder;

  factory StockLine.fromJson(Map<String, Object?> json) => StockLine(
        name: json['name'] as String? ?? '',
        quantity: (json['quantity'] as num?)?.toDouble() ?? 0,
        value: Money.fromJson(json['value']),
        unit: json['unit'] as String?,
        group: json['group'] as String?,
        rate: json['rate'] == null ? null : Money.fromJson(json['rate']),
        reorderLevel: (json['reorder_level'] as num?)?.toDouble(),
        isNegative: json['is_negative'] as bool? ?? false,
        isBelowReorder: json['is_below_reorder'] as bool? ?? false,
      );
}

class TransactionLine {
  const TransactionLine({
    required this.date,
    required this.amount,
    required this.kind,
    this.key,
    this.voucherNumber,
    this.voucherType,
    this.party,
    this.narration,
  });

  /// The voucher's identity, for opening its detail.
  ///
  /// Nullable because an older backend does not send it. A row without one is
  /// still a perfectly good row -- it simply does not open, and the app must
  /// not offer a tap that leads nowhere.
  final String? key;

  final DateTime date;
  final Money amount;

  /// Normalised voucher family: `sales`, `purchase`, `receipt`, `payment`...
  /// Tally lets a shop name its voucher types anything at all, so the app keys
  /// icons and colours off this rather than off [voucherType].
  final String kind;

  final String? voucherNumber;
  final String? voucherType;
  final String? party;
  final String? narration;

  factory TransactionLine.fromJson(Map<String, Object?> json) => TransactionLine(
        key: json['key'] as String?,
        date: DateTime.tryParse(json['date'] as String? ?? '') ?? DateTime.now(),
        amount: Money.fromJson(json['amount']),
        kind: json['kind'] as String? ?? 'other',
        voucherNumber: json['voucher_number'] as String?,
        voucherType: json['voucher_type'] as String?,
        party: json['party'] as String?,
        narration: json['narration'] as String?,
      );
}

/// One outstanding bill, with the ageing already worked out by the backend.
class OutstandingBill {
  const OutstandingBill({
    required this.party,
    required this.amount,
    required this.daysOverdue,
    required this.ageingBucket,
    this.billName,
    this.billDate,
    this.dueDate,
    this.isAdvance = false,
  });

  final String party;
  final Money amount;
  final int daysOverdue;
  final String ageingBucket;
  final String? billName;
  final DateTime? billDate;
  final DateTime? dueDate;

  /// Money received before the invoice exists. Not a debt, and showing it in a
  /// receivables total would overstate what the business is owed.
  final bool isAdvance;

  bool get isOverdue => daysOverdue > 0;

  factory OutstandingBill.fromJson(Map<String, Object?> json) => OutstandingBill(
        party: json['party'] as String? ?? '',
        amount: Money.fromJson(json['amount']),
        daysOverdue: (json['days_overdue'] as num?)?.toInt() ?? 0,
        ageingBucket: json['ageing_bucket'] as String? ?? 'not_due',
        billName: json['bill_name'] as String?,
        billDate: DateTime.tryParse(json['bill_date'] as String? ?? ''),
        dueDate: DateTime.tryParse(json['due_date'] as String? ?? ''),
        isAdvance: json['is_advance'] as bool? ?? false,
      );
}

/// Ageing buckets, in the order an accountant reads them.
const List<String> ageingOrder = <String>[
  'not_due',
  '1_30',
  '31_60',
  '61_90',
  '91_180',
  '180_plus',
];

String ageingLabel(String bucket) => switch (bucket) {
      'not_due' => 'Not due',
      '1_30' => '1-30 days',
      '31_60' => '31-60 days',
      '61_90' => '61-90 days',
      '91_180' => '91-180 days',
      '180_plus' => '180+ days',
      _ => bucket,
    };

class LedgerLine {
  const LedgerLine({
    required this.name,
    required this.opening,
    required this.closing,
    this.group,
    this.gstin,
    this.phone,
  });

  final String name;
  final Money opening;
  final Money closing;
  final String? group;
  final String? gstin;
  final String? phone;

  factory LedgerLine.fromJson(Map<String, Object?> json) => LedgerLine(
        name: json['name'] as String? ?? '',
        opening: Money.fromJson(json['opening']),
        closing: Money.fromJson(json['closing']),
        group: json['group'] as String?,
        gstin: json['gstin'] as String?,
        phone: json['phone'] as String?,
      );
}

/// "1 voucher", "2 vouchers".
///
/// Small, but these counts sit in card subtitles all over the reports, and
/// "1 vouchers" on the screen an accountant opens to check a figure is the kind
/// of detail that makes a reader trust the figures less.
String countOf(int count, String singular, [String? plural]) =>
    '$count ${count == 1 ? singular : plural ?? '${singular}s'}';
