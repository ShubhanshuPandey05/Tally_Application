import 'package:decimal/decimal.dart';
import 'package:pdf/widgets.dart' as pw;

import '../../features/reports/domain/drilldown.dart';
import '../money/money.dart';
import 'amount_in_words.dart';
import 'document_style.dart';

/// A receipt or a payment, printed the way the shop's own Tally prints it.
///
/// Laid out to the reference prints the customer sent (a Receipt Voucher and a
/// Payment Voucher from the same business): the letterhead centred over the
/// page, the voucher number and date on one line, a Debit/Credit table of the
/// ledgers, then how the money moved -- mode, cheque, bank -- the amount in
/// words and the narration, and under all of it the bills this voucher
/// settled. No boxes, only rules: this is the page an accountant files, and it
/// should look like every other one in the file.
///
/// Every field is printed only when Tally holds it. A cash receipt has no
/// cheque number and gets no "Cheque/Ref. No." line; a receipt against no
/// bill gets no adjustment table. A blank caption on a filed receipt reads as
/// a detail somebody forgot to write in.
class ReceiptPaymentDocument {
  const ReceiptPaymentDocument({
    required this.detail,
    required this.companyName,
    this.adjustments = const <BillAdjustment>[],
  });

  final VoucherDetail detail;
  final String companyName;

  /// The bills this voucher settled, each with its own figures, from the
  /// backend's search of the history. Empty when it found none or could not
  /// be asked -- the table is then built from the voucher's own bill names,
  /// with only the amounts this voucher itself records.
  final List<BillAdjustment> adjustments;

  bool get _isReceipt => detail.kind == 'receipt';

  /// "Receipt Voucher", "Payment Voucher" -- or the shop's own type name with
  /// "Voucher" after it, because "Bank Receipt" is their word and replacing it
  /// with ours would make the print disagree with the Day Book.
  String get title {
    final String type = detail.voucherType.trim();
    if (type.isEmpty) return _isReceipt ? 'Receipt Voucher' : 'Payment Voucher';
    return type.toLowerCase().contains('voucher') ? type : '$type Voucher';
  }

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(
      title: '$title ${detail.voucherNumber ?? ''}'.trim(),
      author: companyName,
    );

    document.addPage(
      pw.MultiPage(
        pageTheme: DocumentStyle.page(),
        // Page numbers only where there is more than one page. The reference
        // print carries no footer at all, and "Page 1 of 1" under a one-page
        // receipt is noise.
        footer: (pw.Context context) => context.pagesCount > 1
            ? pw.Align(
                alignment: pw.Alignment.centerRight,
                child: pw.Text(
                  'Page ${context.pageNumber} of ${context.pagesCount}',
                  style: DocumentStyle.footnote,
                ),
              )
            : pw.SizedBox(),
        build: (pw.Context context) => <pw.Widget>[
          DocumentStyle.letterhead(
              name: companyName, details: detail.companyDetails),
          pw.SizedBox(height: 6),
          pw.Center(child: pw.Text(title, style: DocumentStyle.heading)),
          if (detail.isExcluded) _excludedNotice(),
          pw.SizedBox(height: 4),
          _numberAndDate(),
          DocumentStyle.hairline(top: 2, bottom: 0),
          _ledgerTable(),
          _modeOfPayment(),
          _wordsAndNarration(),
          if (_bills.isNotEmpty) _adjustmentTable(),
        ],
      ),
    );

    return document.save();
  }

  // ------------------------------------------------------------------
  // Number and date
  // ------------------------------------------------------------------

  pw.Widget _numberAndDate() => pw.Row(
        mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
        children: <pw.Widget>[
          _pair('Voucher No.', detail.voucherNumber ?? '', labelWidth: 56),
          _pair('Date', DocumentStyle.numericDate(detail.date), labelWidth: 30),
        ],
      );

  /// `Caption  :  value`, the shape of every captioned field on these prints.
  static pw.Widget _pair(
    String caption,
    String value, {
    double labelWidth = 76,
    bool boldValue = true,
    bool italicValue = false,
    bool boldLabel = false,
  }) =>
      pw.Row(
        mainAxisSize: pw.MainAxisSize.min,
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        children: <pw.Widget>[
          pw.SizedBox(
            width: labelWidth,
            child: pw.Text(
              caption,
              style: boldLabel ? DocumentStyle.bodyBold : DocumentStyle.body,
            ),
          ),
          pw.Text(':  ', style: DocumentStyle.body),
          pw.Text(
            value,
            style: (boldValue ? DocumentStyle.bodyBold : DocumentStyle.body)
                .copyWith(
              fontStyle:
                  italicValue ? pw.FontStyle.italic : pw.FontStyle.normal,
            ),
          ),
        ],
      );

  // ------------------------------------------------------------------
  // Particulars, Debit, Credit
  // ------------------------------------------------------------------

  /// The party first, then everything else in the order Tally holds it --
  /// both reference prints put the customer or supplier on the top line, and
  /// it is the line a reader looks for.
  List<VoucherLedgerLine> get _orderedLines => <VoucherLedgerLine>[
        ...detail.ledgerEntries.where((VoucherLedgerLine e) => e.isParty),
        ...detail.ledgerEntries.where((VoucherLedgerLine e) => !e.isParty),
      ];

  static const Map<int, pw.TableColumnWidth> _ledgerColumns =
      <int, pw.TableColumnWidth>{
    0: pw.FlexColumnWidth(5),
    1: pw.FixedColumnWidth(84),
    2: pw.FixedColumnWidth(84),
  };

  pw.Widget _ledgerTable() => pw.Table(
        columnWidths: _ledgerColumns,
        children: <pw.TableRow>[
          pw.TableRow(
            repeat: true,
            decoration: const pw.BoxDecoration(
              border: pw.Border(bottom: DocumentStyle.ink06),
            ),
            children: <pw.Widget>[
              _cell('Particulars', bold: true),
              _cell('Debit', bold: true, align: pw.TextAlign.right),
              _cell('Credit', bold: true, align: pw.TextAlign.right),
            ],
          ),
          for (final VoucherLedgerLine line in _orderedLines)
            pw.TableRow(
              children: <pw.Widget>[
                _cell(line.ledger),
                // Dr and Cr each in a column of their own, blank on the side
                // the line did not touch -- so the side is read from where the
                // figure sits, never from a sign.
                _cell(
                  line.amount.side == MoneySide.debit
                      ? DocumentStyle.amount(line.amount)
                      : '',
                  align: pw.TextAlign.right,
                ),
                _cell(
                  line.amount.side == MoneySide.credit
                      ? DocumentStyle.amount(line.amount)
                      : '',
                  align: pw.TextAlign.right,
                ),
              ],
            ),
          pw.TableRow(
            decoration: const pw.BoxDecoration(
              border: pw.Border(
                  top: DocumentStyle.ink06, bottom: DocumentStyle.ink06),
            ),
            children: <pw.Widget>[
              pw.Padding(
                padding: const pw.EdgeInsets.only(left: 110, top: 3, bottom: 3),
                child: pw.Text('Total', style: DocumentStyle.bodyBold),
              ),
              _cell(DocumentStyle.amount(detail.debitTotal),
                  bold: true, align: pw.TextAlign.right),
              _cell(DocumentStyle.amount(detail.creditTotal),
                  bold: true, align: pw.TextAlign.right),
            ],
          ),
        ],
      );

  // ------------------------------------------------------------------
  // Mode of payment
  // ------------------------------------------------------------------

  /// The bank line, when the voucher went through one with a cheque or
  /// transfer recorded against it.
  VoucherLedgerLine? get _bankLine {
    for (final VoucherLedgerLine line in detail.ledgerEntries) {
      if (line.bank != null) return line;
    }
    return null;
  }

  String? get _partyName {
    final String fromMaster = (detail.partyDetails?.name ?? '').trim();
    if (fromMaster.isNotEmpty) return fromMaster;
    final String named = (detail.party ?? '').trim();
    if (named.isNotEmpty) return named;
    for (final VoucherLedgerLine line in detail.ledgerEntries) {
      if (line.isParty) return line.ledger;
    }
    return null;
  }

  pw.Widget _modeOfPayment() {
    final VoucherLedgerLine? bankLine = _bankLine;
    final VoucherBankDetails? bank = bankLine?.bank;
    final String? party = _partyName;
    final String? mode = _blankToNull(bank?.transactionType);
    final String? cheque = _blankToNull(bank?.instrumentNumber);
    // A receipt names the customer's bank, which Tally keeps on the
    // allocation; a payment's is usually blank there and the bank is our own
    // ledger, which is what the reference payment prints.
    final String? bankName = _blankToNull(bank?.bankName) ?? bankLine?.ledger;

    final List<pw.Widget> first = <pw.Widget>[
      if (mode != null) _pair('Mode of Payment', mode),
      if (party != null)
        _pair(_isReceipt ? 'Paid By' : 'Paid To', party, labelWidth: 40),
    ];
    final List<pw.Widget> second = <pw.Widget>[
      if (cheque != null) _pair('Cheque/Ref. No.', cheque, boldValue: false),
      if (bank?.instrumentDate != null)
        _pair(
          'Date',
          DocumentStyle.shortDate(bank!.instrumentDate!),
          labelWidth: 40,
          boldValue: false,
        ),
      if (bank != null && bankName != null)
        _pair('Bank', bankName, labelWidth: 34, boldValue: false),
    ];
    if (first.isEmpty && second.isEmpty) return pw.SizedBox(height: 2);

    return pw.Container(
      width: double.infinity,
      padding: const pw.EdgeInsets.symmetric(vertical: 3),
      decoration: const pw.BoxDecoration(
          border: pw.Border(bottom: DocumentStyle.ink06)),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        children: <pw.Widget>[
          if (first.isNotEmpty) _fieldRow(first),
          if (second.isNotEmpty) ...<pw.Widget>[
            pw.SizedBox(height: 3),
            _fieldRow(second),
          ],
        ],
      ),
    );
  }

  /// Captioned fields across the page in fixed slots, so "Paid By" and the
  /// cheque's "Date" start at the same place on both lines.
  static pw.Widget _fieldRow(List<pw.Widget> fields) => pw.Row(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        children: <pw.Widget>[
          for (int i = 0; i < fields.length; i++)
            if (i == fields.length - 1)
              pw.Expanded(child: fields[i])
            else
              pw.SizedBox(width: i == 0 ? 210 : 120, child: fields[i]),
        ],
      );

  // ------------------------------------------------------------------
  // Amount in words and narration
  // ------------------------------------------------------------------

  pw.Widget _wordsAndNarration() => pw.Container(
        width: double.infinity,
        padding: const pw.EdgeInsets.symmetric(vertical: 3),
        decoration: const pw.BoxDecoration(
            border: pw.Border(bottom: DocumentStyle.ink06)),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: <pw.Widget>[
            _pair(
              'Amount in Words',
              AmountInWords.withCode(detail.amount.amount,
                  currency: detail.amount.currency),
              boldLabel: true,
              boldValue: false,
              italicValue: true,
            ),
            pw.SizedBox(height: 3),
            // Kept with nothing after it when the voucher has no narration --
            // the reference prints do the same, and a filed receipt with a
            // narration line is one where "nothing was written" is visible.
            _pair('Narration', detail.narration?.trim() ?? '',
                boldLabel: true, boldValue: false),
          ],
        ),
      );

  // ------------------------------------------------------------------
  // Adjustment details
  // ------------------------------------------------------------------

  /// What the table prints: the backend's rows when it has them, otherwise
  /// one row per bill named on the party's line with what this voucher itself
  /// says about it.
  List<BillAdjustment> get _bills {
    if (adjustments.isNotEmpty) return adjustments;
    final List<BillAdjustment> own = <BillAdjustment>[];
    for (final VoucherLedgerLine line
        in detail.ledgerEntries.where((VoucherLedgerLine e) => e.isParty)) {
      if (line.bills.isNotEmpty) {
        for (final VoucherBill bill in line.bills) {
          own.add(BillAdjustment(
            name: bill.name,
            billType: bill.billType,
            billDate: bill.billDate,
            creditDays: bill.creditDays,
            paidAmount:
                bill.amount ?? (line.bills.length == 1 ? line.amount : null),
          ));
        }
      } else {
        for (final String name in line.billReferences) {
          own.add(BillAdjustment(
            name: name,
            paidAmount: line.billReferences.length == 1 ? line.amount : null,
          ));
        }
      }
    }
    return own;
  }

  static const Map<int, pw.TableColumnWidth> _billColumns =
      <int, pw.TableColumnWidth>{
    0: pw.FlexColumnWidth(3),
    1: pw.FixedColumnWidth(62),
    2: pw.FixedColumnWidth(58),
    3: pw.FixedColumnWidth(66),
    4: pw.FixedColumnWidth(66),
    5: pw.FixedColumnWidth(66),
    6: pw.FixedColumnWidth(70),
  };

  pw.Widget _adjustmentTable() {
    final List<BillAdjustment> bills = _bills;
    final String noteCaption = _isReceipt ? 'C/Note Amt.' : 'D/Note Amt.';

    return pw.Padding(
      padding: const pw.EdgeInsets.only(top: 2),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.stretch,
        children: <pw.Widget>[
          pw.Center(
              child:
                  pw.Text('Adjustment Details', style: DocumentStyle.heading)),
          DocumentStyle.hairline(top: 2, bottom: 0),
          pw.Table(
            columnWidths: _billColumns,
            children: <pw.TableRow>[
              pw.TableRow(
                repeat: true,
                decoration: const pw.BoxDecoration(
                  border: pw.Border(bottom: DocumentStyle.ink06),
                ),
                children: <pw.Widget>[
                  _cell('Bill No.', bold: true, align: pw.TextAlign.center),
                  _cell('Bill Date', bold: true, align: pw.TextAlign.right),
                  _cell('Credit Days', bold: true, align: pw.TextAlign.right),
                  _cell('Bill Amt.', bold: true, align: pw.TextAlign.right),
                  _cell(noteCaption, bold: true, align: pw.TextAlign.right),
                  _cell('Paid Amt.', bold: true, align: pw.TextAlign.right),
                  _cell('Pending Amt.', bold: true, align: pw.TextAlign.right),
                ],
              ),
              for (final BillAdjustment bill in bills)
                pw.TableRow(
                  children: <pw.Widget>[
                    _cell(bill.name, align: pw.TextAlign.center),
                    _cell(
                      bill.billDate == null
                          ? ''
                          : DocumentStyle.numericDate(bill.billDate!),
                      align: pw.TextAlign.right,
                    ),
                    _cell(
                      bill.creditDays == null ? '' : '${bill.creditDays} Days',
                      align: pw.TextAlign.right,
                    ),
                    _cell(_amount(bill.billAmount), align: pw.TextAlign.right),
                    _cell(_amount(bill.noteAmount), align: pw.TextAlign.right),
                    _cell(_amount(bill.paidAmount), align: pw.TextAlign.right),
                    _cell(_amount(bill.pendingAmount),
                        align: pw.TextAlign.right),
                  ],
                ),
              pw.TableRow(
                decoration: const pw.BoxDecoration(
                  border: pw.Border(
                      top: DocumentStyle.ink06, bottom: DocumentStyle.ink06),
                ),
                children: <pw.Widget>[
                  pw.Padding(
                    padding:
                        const pw.EdgeInsets.only(left: 30, top: 3, bottom: 3),
                    child: pw.Text('Total', style: DocumentStyle.bodyBold),
                  ),
                  _cell(''),
                  _cell(''),
                  _cell(_total(bills, (BillAdjustment b) => b.billAmount),
                      bold: true, align: pw.TextAlign.right),
                  _cell(
                      _total(bills, (BillAdjustment b) => b.noteAmount,
                          nilWhenFound: true),
                      bold: true,
                      align: pw.TextAlign.right),
                  _cell(_total(bills, (BillAdjustment b) => b.paidAmount),
                      bold: true, align: pw.TextAlign.right),
                  _cell(_total(bills, (BillAdjustment b) => b.pendingAmount),
                      bold: true, align: pw.TextAlign.right),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// Blank for a figure nobody could find and for a nil one, as the reference
  /// prints leave a settled bill's pending amount. A bill settled for more than
  /// it was raised keeps its minus sign rather than reading as nil.
  static String _amount(Money? value) {
    if (value == null || value.amount == Decimal.zero) return '';
    final String text = DocumentStyle.amount(value);
    return value.signed < Decimal.zero ? '-$text' : text;
  }

  /// A column's total, or blank when any row in it is unknown: a total over
  /// the bills that could be found would be a smaller number printed as though
  /// it were the whole.
  ///
  /// A note amount is unknown only on a bill that was not found. On one that
  /// was, no note means none was raised against it, which is nil.
  static String _total(
    List<BillAdjustment> bills,
    Money? Function(BillAdjustment) pick, {
    bool nilWhenFound = false,
  }) {
    Decimal sum = Decimal.zero;
    String currency = 'INR';
    for (final BillAdjustment bill in bills) {
      final Money? value = pick(bill);
      if (value == null) {
        if (nilWhenFound && bill.billAmount != null) continue;
        return '';
      }
      sum += value.signed < Decimal.zero ? -value.amount : value.amount;
      currency = value.currency;
    }
    return _amount(Money(
      amount: sum.abs(),
      side: sum < Decimal.zero ? MoneySide.credit : MoneySide.debit,
      signed: sum,
      currency: currency,
    ));
  }

  // ------------------------------------------------------------------
  // Pieces
  // ------------------------------------------------------------------

  static pw.Widget _cell(
    String text, {
    bool bold = false,
    pw.TextAlign align = pw.TextAlign.left,
  }) =>
      pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 3, vertical: 2.5),
        child: pw.Text(
          text,
          textAlign: align,
          style: bold ? DocumentStyle.bodyBold : DocumentStyle.body,
        ),
      );

  static String? _blankToNull(String? value) {
    final String trimmed = (value ?? '').trim();
    return trimmed.isEmpty ? null : trimmed;
  }

  /// An optional or cancelled entry counts towards nothing in these books. A
  /// print of one that did not say so would circulate as a receipt.
  pw.Widget _excludedNotice() => pw.Container(
        margin: const pw.EdgeInsets.only(top: 4),
        width: double.infinity,
        decoration: DocumentStyle.boxed,
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: pw.Text(
          'This voucher is marked ${detail.isCancelled ? 'cancelled' : 'optional'} in '
          'TallyPrime. It is not part of any total in these books.',
          style: DocumentStyle.bodyBold,
        ),
      );
}
