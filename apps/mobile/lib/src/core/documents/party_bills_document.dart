import 'package:pdf/widgets.dart' as pw;

import '../model/figures.dart';
import '../money/money.dart';
import 'document_style.dart';

/// One party's open bills -- the list an owner sends a customer who asks
/// "which invoices are you chasing me for?", or a supplier the same question
/// the other way round.
///
/// Open bills only, never the ledger: a statement of every voucher answers a
/// different question, and a customer handed one has to work out for themselves
/// which invoices are still unpaid.
class PartyBillsDocument {
  const PartyBillsDocument({
    required this.party,
    required this.heading,
    required this.bills,
    required this.total,
    required this.overdue,
    required this.advances,
    required this.companyName,
    required this.asOf,
  });

  final String party;

  /// "Receivables", "Sundry Debtors" -- what the reader was looking at.
  final String heading;
  final List<OutstandingBill> bills;

  /// What is owed after advances, the figure the owner reads first.
  final Money total;
  final Money overdue;

  /// Printed only when there are any; otherwise net equals billed and saying
  /// so is noise.
  final Money advances;
  final String companyName;
  final DateTime asOf;

  String get fileName => DocumentStyle.fileName(<String?>[
        'Outstanding',
        party,
        DocumentStyle.date(asOf),
      ]);

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(
      title: 'Outstanding - $party',
      author: companyName,
    );

    document.addPage(
      pw.MultiPage(
        pageTheme: DocumentStyle.page(),
        footer: (pw.Context context) => DocumentStyle.footer(
          context,
          note: 'Computer generated from $companyName\'s TallyPrime books.',
        ),
        build: (pw.Context context) => <pw.Widget>[
          _header(),
          _bills(),
          _totals(),
        ],
      ),
    );

    return document.save();
  }

  pw.Widget _header() => pw.Container(
        decoration: DocumentStyle.boxed,
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.stretch,
          children: <pw.Widget>[
            pw.Container(
              padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 4),
              child: pw.Row(
                mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
                crossAxisAlignment: pw.CrossAxisAlignment.end,
                children: <pw.Widget>[
                  pw.Text('Outstanding Bills', style: DocumentStyle.title),
                  pw.Text('Amounts in ${total.currency}', style: DocumentStyle.footnote),
                ],
              ),
            ),
            pw.Divider(height: 0.6, color: DocumentStyle.rule),
            DocumentStyle.headerBlock(
              left: pw.Padding(
                padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
                child: pw.Column(
                  crossAxisAlignment: pw.CrossAxisAlignment.start,
                  children: <pw.Widget>[
                    pw.Text(companyName, style: DocumentStyle.body),
                    pw.SizedBox(height: 3),
                    pw.Text(party, style: DocumentStyle.heading),
                    pw.Text(heading, style: DocumentStyle.footnote),
                  ],
                ),
              ),
              fields: <pw.Widget>[
                DocumentStyle.field('As on', DocumentStyle.date(asOf)),
                DocumentStyle.field('Bills', '${bills.length}'),
              ],
            ),
          ],
        ),
      );

  pw.Widget _bills() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        child: pw.Table(
          border: DocumentStyle.tableRules,
          columnWidths: <int, pw.TableColumnWidth>{
            0: const pw.FlexColumnWidth(3),
            1: const pw.FixedColumnWidth(62),
            2: const pw.FixedColumnWidth(62),
            3: const pw.FixedColumnWidth(48),
            4: const pw.FixedColumnWidth(80),
          },
          children: <pw.TableRow>[
            pw.TableRow(
              repeat: true,
              children: <pw.Widget>[
                DocumentStyle.headerCell('Bill'),
                DocumentStyle.headerCell('Bill date'),
                DocumentStyle.headerCell('Due date'),
                DocumentStyle.headerCell('Overdue', align: pw.TextAlign.right),
                DocumentStyle.headerCell('Pending', align: pw.TextAlign.right),
              ],
            ),
            for (final OutstandingBill bill in bills)
              pw.TableRow(
                children: <pw.Widget>[
                  DocumentStyle.cell(
                    bill.isAdvance
                        ? '${bill.billName ?? 'Advance'} (advance)'
                        : bill.billName ?? 'Bill',
                  ),
                  DocumentStyle.cell(
                    bill.billDate == null ? '' : DocumentStyle.date(bill.billDate!),
                  ),
                  DocumentStyle.cell(
                    bill.dueDate == null ? '' : DocumentStyle.date(bill.dueDate!),
                  ),
                  DocumentStyle.cell(
                    bill.isOverdue ? '${bill.daysOverdue} days' : '',
                    align: pw.TextAlign.right,
                  ),
                  DocumentStyle.cell(
                    DocumentStyle.amount(bill.amount),
                    align: pw.TextAlign.right,
                  ),
                ],
              ),
          ],
        ),
      );

  pw.Widget _totals() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        decoration: DocumentStyle.boxed,
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
        child: pw.Column(
          children: <pw.Widget>[
            _row('Overdue', overdue),
            if (!advances.isZero) _row('Advances held', advances),
            pw.Divider(height: 6, color: DocumentStyle.rule),
            _row('Total outstanding', total, bold: true),
          ],
        ),
      );

  pw.Widget _row(String label, Money value, {bool bold = false}) => pw.Padding(
        padding: const pw.EdgeInsets.symmetric(vertical: 1),
        child: pw.Row(
          mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
          children: <pw.Widget>[
            pw.Text(label, style: bold ? DocumentStyle.heading : DocumentStyle.body),
            pw.Text(
              DocumentStyle.amount(value),
              style: bold ? DocumentStyle.heading : DocumentStyle.body,
            ),
          ],
        ),
      );
}
