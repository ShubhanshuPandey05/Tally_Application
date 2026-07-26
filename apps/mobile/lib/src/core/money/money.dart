import 'package:decimal/decimal.dart';

/// Which side of the ledger a balance sits on.
///
/// Tally's own convention is inverted from the usual one -- in its XML a
/// negative amount is a *debit* -- but that translation happens in the backend.
/// By the time a figure reaches this app the side is explicit, and no screen
/// should ever infer direction from a minus sign.
enum MoneySide {
  debit,
  credit;

  static MoneySide parse(String? raw) =>
      (raw ?? '').toLowerCase() == 'credit' ? MoneySide.credit : MoneySide.debit;

  String get wire => name;
}

/// An amount of money, exactly.
///
/// Backed by [Decimal] rather than `double` on purpose: this app shows people
/// their bank balance and their customers' dues, and binary floating point
/// cannot represent 0.1. The API sends amounts as strings for the same reason,
/// so parsing them into a double here would throw that guarantee away at the
/// last possible moment.
class Money {
  const Money({
    required this.amount,
    required this.side,
    required this.signed,
    required this.currency,
  });

  /// The magnitude. Always non-negative in practice; direction lives in [side].
  final Decimal amount;

  final MoneySide side;

  /// Debit-positive signed value, as sent by the backend. This is what charts
  /// plot -- an overdrawn bank account must dip below the axis.
  final Decimal signed;

  final String currency;

  static final Money zero = Money(
    amount: Decimal.zero,
    side: MoneySide.debit,
    signed: Decimal.zero,
    currency: 'INR',
  );

  /// Reads the backend's `money_out` shape.
  ///
  /// Tolerant of a missing or malformed node rather than throwing: one bad
  /// figure in a list of two hundred stock items should blank that one cell,
  /// not take down the screen the owner opened the app for.
  factory Money.fromJson(Object? json) {
    if (json is! Map) return zero;
    final Decimal amount = _decimal(json['amount']);
    final MoneySide side = MoneySide.parse(json['side'] as String?);
    return Money(
      amount: amount,
      side: side,
      signed: json['signed'] == null
          ? (side == MoneySide.debit ? amount : -amount)
          : _decimal(json['signed']),
      currency: (json['currency'] as String?) ?? 'INR',
    );
  }

  static Decimal _decimal(Object? raw) {
    if (raw == null) return Decimal.zero;
    if (raw is num) return Decimal.parse(raw.toString());
    return Decimal.tryParse(raw.toString().replaceAll(',', '')) ?? Decimal.zero;
  }

  bool get isZero => amount == Decimal.zero;

  /// True when the signed value points the wrong way for an asset -- an
  /// overdrawn bank account, a supplier in credit.
  bool get isNegative => signed < Decimal.zero;

  double get asDouble => signed.toDouble();

  Money operator +(Money other) => Money(
        amount: amount + other.amount,
        side: side,
        signed: signed + other.signed,
        currency: currency,
      );

  @override
  bool operator ==(Object other) =>
      other is Money &&
      other.amount == amount &&
      other.side == side &&
      other.currency == currency;

  @override
  int get hashCode => Object.hash(amount, side, currency);

  @override
  String toString() => '$currency $amount ${side.name}';
}
