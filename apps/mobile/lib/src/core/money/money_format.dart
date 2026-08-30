import 'package:decimal/decimal.dart';

import 'money.dart';

/// Indian-format currency rendering.
///
/// Deliberately not `NumberFormat.currency`: the users are Indian businesses,
/// and 1,23,45,678 is the grouping they read fluently while 12,345,678 makes
/// them count digits. The compact forms (L, Cr) are the units they actually
/// speak in, and a KPI tile is the one place a shop owner wants the shape of
/// the number rather than the paise.
class MoneyFormat {
  const MoneyFormat._();

  static const Map<String, String> _symbols = <String, String>{
    'INR': '₹',
    'USD': '\$',
    'EUR': '€',
    'GBP': '£',
    'AED': 'AED ',
  };

  static String symbol(String currency) =>
      _symbols[currency.toUpperCase()] ?? '$currency ';

  static final Decimal _thousand = Decimal.fromInt(1000);
  static final Decimal _lakh = Decimal.fromInt(100000);
  static final Decimal _crore = Decimal.fromInt(10000000);

  /// Full precision, grouped Indian-style. Used wherever the exact figure
  /// matters: statements, bill lists, ledger balances.
  static String full(Money money, {bool withSymbol = true, bool decimals = true}) {
    final String body = _grouped(money.amount, decimals: decimals);
    return withSymbol ? '${symbol(money.currency)}$body' : body;
  }

  /// Short form for tiles and chart labels: `₹12.4L`, `₹1.2Cr`.
  ///
  /// Always paired with the full value somewhere reachable -- rounding a
  /// receivable to "12.4L" is fine on a tile and unacceptable on a bill.
  static String compact(Money money, {bool withSymbol = true}) {
    final Decimal value = money.amount;
    final String prefix = withSymbol ? symbol(money.currency) : '';

    if (value >= _crore) {
      return '$prefix${_short(value.toDouble() / 10000000)}Cr';
    }
    if (value >= _lakh) {
      return '$prefix${_short(value.toDouble() / 100000)}L';
    }
    if (value >= _thousand) {
      return '$prefix${_grouped(value, decimals: false)}';
    }
    return '$prefix${_grouped(value, decimals: value != value.truncate())}';
  }

  /// Compact form for a bare chart value.
  ///
  /// A plotting library deals in doubles, so by the time an axis label or a
  /// tooltip is being built the [Money] wrapper is gone. This puts it back
  /// rather than letting every chart invent its own rupee formatting -- the
  /// axis and the figure beside the chart must round the same way, or they
  /// look like two different readings of the same number.
  static String compactValue(double value, String currency) => compact(
        Money.fromJson(<String, Object?>{
          'amount': value.abs().toStringAsFixed(2),
          'side': value < 0 ? 'credit' : 'debit',
          'currency': currency,
        }),
      );

  /// Signed presentation for balances, where direction is the point.
  static String signed(Money money) {
    final String body = _grouped(money.signed.abs(), decimals: true);
    final String sign = money.signed < Decimal.zero ? '-' : '';
    return '$sign${symbol(money.currency)}$body';
  }

  /// `Dr` / `Cr` suffix, as an accountant expects to see next to a balance.
  static String withSide(Money money) =>
      '${full(money)} ${money.side == MoneySide.credit ? 'Cr' : 'Dr'}';

  /// Compact scaling is approximate by definition, so a double is honest here.
  /// Exact rendering never goes through this path.
  static String _short(double value) {
    final String text = value.toStringAsFixed(value >= 10 ? 1 : 2);
    // Trailing zeroes read as false precision on a tile: "12.0L" implies a
    // measurement, "12L" states a size.
    return text.contains('.') ? text.replaceFirst(RegExp(r'\.?0+$'), '') : text;
  }

  static String _grouped(Decimal value, {required bool decimals}) {
    final String fixed = value.abs().toStringAsFixed(decimals ? 2 : 0);
    final List<String> parts = fixed.split('.');
    final String fraction = parts.length > 1 ? '.${parts[1]}' : '';
    final String sign = value < Decimal.zero ? '-' : '';
    return '$sign${_indianGroups(parts.first)}$fraction';
  }

  /// Last three digits together, then pairs: 1,23,45,678.
  static String _indianGroups(String digits) {
    if (digits.length <= 3) return digits;
    final String last3 = digits.substring(digits.length - 3);
    String rest = digits.substring(0, digits.length - 3);

    final List<String> groups = <String>[];
    while (rest.length > 2) {
      groups.insert(0, rest.substring(rest.length - 2));
      rest = rest.substring(0, rest.length - 2);
    }
    if (rest.isNotEmpty) groups.insert(0, rest);

    return '${groups.join(',')},$last3';
  }

  /// Quantities, which are not money: up to three decimals, zeroes dropped.
  static String quantity(double value, String? unit) {
    String text = value.toStringAsFixed(3);
    if (text.contains('.')) {
      text = text.replaceFirst(RegExp(r'\.?0+$'), '');
    }
    final List<String> parts = text.split('.');
    final String grouped = _indianGroups(parts.first.replaceFirst('-', '')) +
        (parts.length > 1 ? '.${parts[1]}' : '');
    final String signed = value < 0 ? '-$grouped' : grouped;
    return unit == null || unit.isEmpty ? signed : '$signed $unit';
  }
}
