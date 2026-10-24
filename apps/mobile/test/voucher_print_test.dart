import 'dart:io';

import 'package:decimal/decimal.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/documents/amount_in_words.dart';
import 'package:tallyflow/src/core/documents/document_style.dart';
import 'package:tallyflow/src/core/documents/statement_document.dart';
import 'package:tallyflow/src/core/documents/voucher_document.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/features/reports/domain/drilldown.dart';

import 'support/fixtures.dart';

/// The receipt, payment and ledger prints, built from the shapes of the
/// reference prints the customer asked them to match.
///
/// Set `PRINT_OUT` to a folder to have each one written there as a PDF, which
/// is how the layout was compared against those references by eye.
void main() {
  Map<String, Object?> money(String amount, String side) => <String, Object?>{
        'amount': amount,
        'currency': 'INR',
        'side': side,
        'signed': side == 'debit' ? amount : '-$amount',
      };

  const Map<String, Object?> company = <String, Object?>{
    'name': 'SHREE MEDICAL AGENCY',
    'address': <String>[
      '101, Shree Complex',
      'Udhna Main Road, Udhna',
      'Surat'
    ],
    'state': 'Gujarat',
    'phone': '9925364854',
    'email': 'shreemedicalagency@gmail.com',
  };

  VoucherDetail voucher(String kind) {
    final bool receipt = kind == 'receipt';
    return VoucherDetail.fromJson(<String, Object?>{
      'key': 'k-$kind',
      'date': '2026-04-01',
      'voucher_type': receipt ? 'Receipt' : 'Payment',
      'voucher_number': '1',
      'kind': kind,
      'party': 'BEST SAYAR MEDICAL CENTER PALI',
      'amount': money('2100.00', 'debit'),
      'debit_total': money('2100.00', 'debit'),
      'credit_total': money('2100.00', 'debit'),
      'company_details': company,
      'ledger_entries': <Object?>[
        <String, Object?>{
          'ledger': 'ICICI Bank',
          'amount': money('2100.00', receipt ? 'debit' : 'credit'),
          'bank': <String, Object?>{
            'transaction_type': 'Cheque/DD',
            'instrument_number': '2132001',
            'instrument_date': '2026-04-01',
            if (receipt) 'bank_name': 'State Bank of India (India)',
          },
        },
        <String, Object?>{
          'ledger': 'BEST SAYAR MEDICAL CENTER PALI',
          'amount': money('2100.00', receipt ? 'credit' : 'debit'),
          'is_party': true,
          'bill_references': <String>['SMA26/002'],
          'bills': <Object?>[
            <String, Object?>{
              'name': 'SMA26/002',
              'bill_type': 'Agst Ref',
              'amount': money('2100.00', receipt ? 'credit' : 'debit'),
            },
          ],
        },
      ],
      'inventory_entries': <Object?>[],
    });
  }

  final List<BillAdjustment> settled =
      BillAdjustment.listFromJson(<String, Object?>{
    'bills': <Object?>[
      <String, Object?>{
        'name': 'SMA26/002',
        'bill_type': 'Agst Ref',
        'bill_date': '2026-04-01',
        'credit_days': 15,
        'bill_amount': money('2100.00', 'debit'),
        'note_amount': null,
        'paid_amount': money('2100.00', 'debit'),
        'pending_amount': money('0.00', 'debit'),
      },
    ],
  });

  Future<void> keep(String name, List<int> bytes) async {
    final String? out = Platform.environment['PRINT_OUT'];
    if (out == null || out.isEmpty) return;
    await File('$out/$name.pdf').writeAsBytes(bytes);
  }

  group('receipt and payment prints', () {
    test('a receipt builds with its cheque and its settled bill', () async {
      final List<int> bytes = await VoucherDocument(
        detail: voucher('receipt'),
        companyName: 'Shree Medical Agency',
        adjustments: settled,
      ).build();
      expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
      await keep('receipt', bytes);
    });

    test('a payment builds from the voucher alone when no bill was found',
        () async {
      // The server could not be asked, or found nothing: the table is built
      // from the bills the voucher names, with only the amount it records.
      final List<int> bytes = await VoucherDocument(
        detail: voucher('payment'),
        companyName: 'Shree Medical Agency',
      ).build();
      expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
      await keep('payment', bytes);
    });

    test('a receipt with no bank line and no bills still builds', () async {
      final VoucherDetail cash = VoucherDetail.fromJson(<String, Object?>{
        'key': 'k',
        'date': '2026-04-01',
        'voucher_type': 'Receipt',
        'kind': 'receipt',
        'amount': money('500.00', 'debit'),
        'debit_total': money('500.00', 'debit'),
        'credit_total': money('500.00', 'debit'),
        'ledger_entries': <Object?>[
          <String, Object?>{
            'ledger': 'Cash',
            'amount': money('500.00', 'debit')
          },
          <String, Object?>{
            'ledger': 'Sales',
            'amount': money('500.00', 'credit')
          },
        ],
        'inventory_entries': <Object?>[],
      });
      final List<int> bytes = await VoucherDocument(
              detail: cash, companyName: 'Shree Medical Agency')
          .build();
      expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
    });

    test('only receipts and payments take this print', () {
      expect(VoucherDocument.hasOwnPrint(voucher('receipt')), isTrue);
      expect(VoucherDocument.hasOwnPrint(voucher('payment')), isTrue);
      expect(
          VoucherDocument.hasOwnPrint(
              VoucherDetail.fromJson(fixture('voucher').envelopeData)),
          isFalse);
    });

    test('writes the amount the way the reference print does', () {
      expect(
        AmountInWords.withCode(Decimal.parse('2100.00')),
        'INR Two Thousand One Hundred only',
      );
      expect(
        AmountInWords.withCode(Decimal.parse('10.50')),
        'INR Ten and Fifty Paise only',
      );
    });

    test('dates read day first in both of the forms the prints use', () {
      expect(DocumentStyle.numericDate(DateTime(2026, 4, 1)), '01-04-2026');
      expect(DocumentStyle.shortDate(DateTime(2026, 4, 1)), '1-Apr-26');
    });
  });

  group('ledger print', () {
    Map<String, Object?> row(
      String date,
      String particulars,
      String type,
      String number,
      String amount,
      String side,
      String running,
      String runningSide,
    ) =>
        <String, Object?>{
          'key': '$type-$number',
          'date': date,
          'kind': type == 'Receipt' ? 'receipt' : 'sales',
          'voucher_type': type,
          'voucher_number': number,
          'party': 'AccessGlobal Technology Pvt. Ltd.',
          'particulars': particulars,
          'amount': money(amount, side),
          'movement': money(amount, side),
          'running': money(running, runningSide),
        };

    test('builds a ledger account that closes on nil', () async {
      final LedgerStatement statement =
          LedgerStatement.fromJson(<String, Object?>{
        'ledger': 'AccessGlobal Technology Pvt. Ltd.',
        'voucher_count': 3,
        'debit_total': money('7080.00', 'debit'),
        'credit_total': money('19564.00', 'debit'),
        'net_movement': money('12484.00', 'credit'),
        'opening_balance': money('12484.00', 'debit'),
        'period_closing_balance': money('0.00', 'debit'),
        'closing_balance': money('0.00', 'debit'),
        'company_details': <String, Object?>{
          'name': 'JSR PRIME SOLUTION',
          'address': <String>[
            'A-304, Salasar Icon, Opp. Lake Garden',
            'Dindoli, Surat'
          ],
          'phone': '9998464854',
          'mobile': '9925364854',
          'email': 'jsrprimesolution@gmail.com',
        },
        'party_details': <String, Object?>{
          'name': 'AccessGlobal Technology Pvt. Ltd.',
          'address': <String>[
            '1st Floor, Dr. Jamasji Building',
            'Nanpura, Surat'
          ],
        },
        'entries': <Object?>[
          row('2026-04-04', 'Customization', 'Customization', 'J26-27/004',
              '7080.00', 'debit', '19564.00', 'debit'),
          row('2026-04-16', 'Kotak Mahindra Bank', 'Receipt', '19', '12484.00',
              'credit', '7080.00', 'debit'),
          row('2026-04-16', 'Kotak Mahindra Bank', 'Receipt', '40', '7080.00',
              'credit', '0.00', 'debit'),
        ],
      });

      final List<int> bytes = await StatementDocument(
        statement: statement,
        companyName: 'JSR Prime Solution',
        periodLabel: 'This year',
        from: DateTime(2026, 4, 1),
        to: DateTime(2026, 10, 23),
      ).build();
      expect(String.fromCharCodes(bytes.take(5)), '%PDF-');
      await keep('ledger', bytes);
    });
  });
}
