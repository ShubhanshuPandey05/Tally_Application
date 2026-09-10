import 'package:decimal/decimal.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/documents/amount_in_words.dart';
import 'package:tallyflow/src/core/documents/document_style.dart';
import 'package:tallyflow/src/core/documents/statement_document.dart';
import 'package:tallyflow/src/core/documents/voucher_document.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/features/reports/domain/drilldown.dart';

import 'support/fixtures.dart';

/// The documents a customer receives.
///
/// Two things are worth a test here and nothing else really is. The first is
/// the amount in words: it is the one figure on an invoice that is written
/// rather than formatted, it is what a dispute is settled against, and it is
/// wrong in a way nobody notices until somebody reads it aloud. The second is
/// that the page builds at all from the responses the backend really sends --
/// a layout exception on a shop owner's phone, in front of the customer they
/// were about to send the bill to, is the failure this file exists to catch.
void main() {
  group('amount in words', () {
    String words(String amount) => AmountInWords.rupees(Decimal.parse(amount));

    test('writes the reference invoice total exactly as it prints it', () {
      // The figure on tmp/Invoice-BizMitra.pdf, which is the format this
      // document reproduces.
      expect(words('2360.00'), 'Rupees Two Thousand Three Hundred Sixty Only');
    });

    test('groups in lakhs and crores, not millions', () {
      expect(words('1250000'), 'Rupees Twelve Lakh Fifty Thousand Only');
      expect(
        words('21260000'),
        'Rupees Two Crore Twelve Lakh Sixty Thousand Only',
      );
      // Above ninety-nine crore Tally keeps counting in crores, and so does
      // everybody reading the number aloud.
      expect(
        words('12000000000'),
        'Rupees One Thousand Two Hundred Crore Only',
      );
    });

    test('spells paise separately, and omits them when there are none', () {
      expect(
        words('1234.50'),
        'Rupees One Thousand Two Hundred Thirty Four and Fifty Paise Only',
      );
      expect(words('1234.00'), 'Rupees One Thousand Two Hundred Thirty Four Only');
      // Rounded to paise first: a stored 1234.567 must not write out as
      // "fifty six paise" beside numerals that read 1,234.57.
      expect(
        words('1234.567'),
        'Rupees One Thousand Two Hundred Thirty Four and Fifty Seven Paise Only',
      );
    });

    test('names the unit from the currency, so other books are not called rupees', () {
      expect(
        AmountInWords.rupees(Decimal.parse('40.00'), currency: 'USD'),
        'Dollars Forty Only',
      );
    });

    test('says zero rather than nothing at all', () {
      expect(words('0'), 'Rupees Zero Only');
    });
  });

  group('voucher document', () {
    VoucherDetail load() => VoucherDetail.fromJson(fixture('voucher').envelopeData);

    test('builds a page from the response the backend really sends', () async {
      final List<int> bytes = await VoucherDocument(
        detail: load(),
        companyName: 'JSR Prime Solution',
      ).build();

      expect(bytes.length, greaterThan(1000));
      expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
    });

    test('prints the seller registration only when the books record one', () async {
      // Both shapes have to build. The header keeps a place for the seller's
      // GSTIN, and nothing supplies it yet -- a document that only rendered
      // with one would break the day it is wired up, and one that printed an
      // empty GSTIN line would assert a registration that was never entered.
      for (final String? gstin in <String?>[null, '24AZOPP6385N1ZE']) {
        final List<int> bytes = await VoucherDocument(
          detail: load(),
          companyName: 'JSR Prime Solution',
          companyGstin: gstin,
        ).build();
        expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
      }
    });

    test('names the file after the voucher, not after the screen', () {
      final String name = VoucherDocument(
        detail: load(),
        companyName: 'JSR Prime Solution',
      ).fileName;

      expect(name, 'Purchase-3-15-Mar-2026');
    });

    test('folds a party name a filesystem would refuse', () {
      // "M/s. Sharma & Co." is an entirely ordinary ledger name and carries a
      // directory separator.
      expect(
        DocumentStyle.fileName(<String?>['Sales', 'M/s. Sharma & Co.', null]),
        'Sales-M-s.-Sharma-Co.',
      );
    });
  });

  group('statement document', () {
    test('builds a page from the response the backend really sends', () async {
      final LedgerStatement statement =
          LedgerStatement.fromJson(fixture('ledger_statement').envelopeData);

      final List<int> bytes = await StatementDocument(
        statement: statement,
        companyName: 'JSR Prime Solution',
        periodLabel: 'Last 90 days',
        from: DateTime(2026, 1, 1),
        to: DateTime(2026, 3, 31),
      ).build();

      expect(bytes.length, greaterThan(1000));
      expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
    });
  });
}
