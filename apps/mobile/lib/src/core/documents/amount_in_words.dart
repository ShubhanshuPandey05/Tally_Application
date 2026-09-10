import 'package:decimal/decimal.dart';

/// An amount written out the way an Indian invoice writes it.
///
/// Every printed bill in the country carries the figure twice -- once in
/// numerals and once in words -- because the words are what cannot be altered
/// with a pen after the fact. The grouping is the Indian one (crore, lakh,
/// thousand), not the international one: "Twelve Lakh" and "1.2 Million" are
/// the same quantity and only one of them is readable to the person being
/// handed the bill.
///
/// Paise are spelled separately rather than as a fraction, and are omitted when
/// there are none -- "and Zero Paise" reads as a rounding note rather than as an
/// exact amount.
class AmountInWords {
  const AmountInWords._();

  static const List<String> _ones = <String>[
    '',
    'One',
    'Two',
    'Three',
    'Four',
    'Five',
    'Six',
    'Seven',
    'Eight',
    'Nine',
    'Ten',
    'Eleven',
    'Twelve',
    'Thirteen',
    'Fourteen',
    'Fifteen',
    'Sixteen',
    'Seventeen',
    'Eighteen',
    'Nineteen',
  ];

  static const List<String> _tens = <String>[
    '',
    '',
    'Twenty',
    'Thirty',
    'Forty',
    'Fifty',
    'Sixty',
    'Seventy',
    'Eighty',
    'Ninety',
  ];

  /// `Rupees Two Thousand Three Hundred Sixty Only`.
  ///
  /// [currency] names the unit, so a company keeping books in something other
  /// than rupees does not get a document that says otherwise.
  static String rupees(Decimal amount, {String currency = 'INR'}) {
    final Decimal absolute = amount.abs();
    final BigInt whole = absolute.truncate().toBigInt();
    // Round to paise first. Without this a stored 1234.567 writes out as
    // "…and Fifty Six Paise" while the numerals beside it read 1,234.57.
    final int fraction =
        ((absolute - absolute.truncate()) * Decimal.fromInt(100)).round().toBigInt().toInt();

    final (String major, String minor) = _units(currency);
    final StringBuffer buffer = StringBuffer(major);

    if (whole == BigInt.zero && fraction == 0) {
      buffer.write(' Zero Only');
      return buffer.toString();
    }

    if (whole > BigInt.zero) {
      buffer.write(' ${_indian(whole)}');
    }
    if (fraction > 0) {
      if (whole > BigInt.zero) buffer.write(' and');
      buffer.write(' ${_below(BigInt.from(fraction))} $minor');
    }
    buffer.write(' Only');
    return buffer.toString();
  }

  /// Rupees and paise, or the neutral wording for any other currency. A book
  /// kept in dollars must not have "Paise" printed on its documents.
  static (String, String) _units(String currency) => switch (currency.toUpperCase()) {
        'INR' => ('Rupees', 'Paise'),
        'USD' => ('Dollars', 'Cents'),
        'EUR' => ('Euros', 'Cents'),
        'GBP' => ('Pounds', 'Pence'),
        final String other => (other, 'Cents'),
      };

  /// Crore, lakh, thousand, hundred -- the Indian place names, in order.
  static String _indian(BigInt value) {
    final List<String> parts = <String>[];
    final BigInt crore = BigInt.from(10000000);
    final BigInt lakh = BigInt.from(100000);
    final BigInt thousand = BigInt.from(1000);

    BigInt rest = value;
    for (final (BigInt unit, String name) in <(BigInt, String)>[
      (crore, 'Crore'),
      (lakh, 'Lakh'),
      (thousand, 'Thousand'),
    ]) {
      final BigInt count = rest ~/ unit;
      if (count > BigInt.zero) {
        // Recursive on crore alone: above 99 crore Tally keeps counting in
        // crores ("One Thousand Two Hundred Crore"), and so does everybody who
        // reads the number aloud.
        parts.add('${unit == crore ? _indian(count) : _below(count)} $name');
        rest = rest.remainder(unit);
      }
    }
    if (rest > BigInt.zero) parts.add(_below(rest));
    return parts.join(' ');
  }

  /// Anything under a thousand.
  static String _below(BigInt value) {
    final int number = value.toInt();
    final List<String> parts = <String>[];
    if (number >= 100) {
      parts.add('${_ones[number ~/ 100]} Hundred');
    }
    final int remainder = number % 100;
    if (remainder > 0) {
      if (remainder < 20) {
        parts.add(_ones[remainder]);
      } else {
        parts.add(_tens[remainder ~/ 10]);
        if (remainder % 10 > 0) parts.add(_ones[remainder % 10]);
      }
    }
    return parts.join(' ');
  }
}
