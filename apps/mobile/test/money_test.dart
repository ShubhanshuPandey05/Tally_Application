import 'package:decimal/decimal.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/money/money.dart';
import 'package:tallyflow/src/core/money/money_format.dart';

void main() {
  group('Money', () {
    test('keeps full precision instead of going through a double', () {
      final Money money = Money.fromJson(<String, Object?>{
        'amount': '12345678.91',
        'side': 'debit',
        'signed': '12345678.91',
        'currency': 'INR',
      });

      // The exact reason this class exists: 12345678.91 is not representable
      // in binary floating point, and an accounting app that quietly loses a
      // paisa on every balance is not usable.
      expect(money.amount, Decimal.parse('12345678.91'));
      expect(money.amount.toString(), '12345678.91');
    });

    test('reads the side rather than inferring it from a sign', () {
      final Money credit = Money.fromJson(<String, Object?>{
        'amount': '212600.00',
        'side': 'credit',
        'signed': '-212600.00',
        'currency': 'INR',
      });

      expect(credit.side, MoneySide.credit);
      expect(credit.isNegative, isTrue, reason: 'signed is debit-positive');
      expect(credit.amount, Decimal.parse('212600.00'),
          reason: 'the magnitude stays positive');
    });

    test('derives a signed value when the server omits one', () {
      final Money credit = Money.fromJson(<String, Object?>{
        'amount': '500.00',
        'side': 'credit',
        'currency': 'INR',
      });
      expect(credit.signed, Decimal.parse('-500.00'));
    });

    test('a malformed node blanks one figure instead of throwing', () {
      // One bad amount in a list of two hundred stock items must not take down
      // the screen the owner opened the app for.
      expect(Money.fromJson(null), Money.zero);
      expect(Money.fromJson('not money'), Money.zero);
      expect(Money.fromJson(<String, Object?>{'amount': 'oops'}).amount, Decimal.zero);
    });
  });

  group('Indian formatting', () {
    Money rupees(String amount) => Money(
          amount: Decimal.parse(amount),
          side: MoneySide.debit,
          signed: Decimal.parse(amount),
          currency: 'INR',
        );

    test('groups in lakhs and crores, not thousands', () {
      expect(MoneyFormat.full(rupees('12345678.90')), '₹1,23,45,678.90');
      expect(MoneyFormat.full(rupees('123456.00')), '₹1,23,456.00');
      expect(MoneyFormat.full(rupees('1234.50')), '₹1,234.50');
      expect(MoneyFormat.full(rupees('999.00')), '₹999.00');
      expect(MoneyFormat.full(rupees('0.00')), '₹0.00');
    });

    test('compacts to the units a shop owner actually speaks in', () {
      expect(MoneyFormat.compact(rupees('12345678.90')), '₹1.23Cr');
      expect(MoneyFormat.compact(rupees('1240000.00')), '₹12.4L');
      expect(MoneyFormat.compact(rupees('100000.00')), '₹1L');
      expect(MoneyFormat.compact(rupees('45000.00')), '₹45,000');
      expect(MoneyFormat.compact(rupees('999.50')), '₹999.50');
    });

    test('drops trailing zeroes so a tile does not imply false precision', () {
      expect(MoneyFormat.compact(rupees('1000000.00')), '₹10L');
      expect(MoneyFormat.compact(rupees('20000000.00')), '₹2Cr');
    });

    test('marks the side the way an accountant reads it', () {
      final Money credit = Money(
        amount: Decimal.parse('212600.00'),
        side: MoneySide.credit,
        signed: Decimal.parse('-212600.00'),
        currency: 'INR',
      );
      expect(MoneyFormat.withSide(credit), '₹2,12,600.00 Cr');
      expect(MoneyFormat.signed(credit), '-₹2,12,600.00');
    });

    test('falls back to the currency code for anything unmapped', () {
      final Money dinar = Money(
        amount: Decimal.parse('100.00'),
        side: MoneySide.debit,
        signed: Decimal.parse('100.00'),
        currency: 'KWD',
      );
      expect(MoneyFormat.full(dinar), 'KWD 100.00');
    });

    test('quantities are not money: no currency, no forced decimals', () {
      expect(MoneyFormat.quantity(500, 'KG'), '500 KG');
      expect(MoneyFormat.quantity(12.5, 'LTR'), '12.5 LTR');
      expect(MoneyFormat.quantity(-3, 'LTR'), '-3 LTR');
      expect(MoneyFormat.quantity(100000, 'PCS'), '1,00,000 PCS');
      expect(MoneyFormat.quantity(7, null), '7');
    });
  });
}
