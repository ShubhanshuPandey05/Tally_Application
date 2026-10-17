import 'package:pdf/widgets.dart' as pw;

import '../../features/reports/domain/drilldown.dart';
import '../money/money.dart';
import 'document_style.dart';

/// One ledger's statement, drawn as the document a business sends a customer.
///
/// This is the thing an owner reaches for when somebody disputes a balance:
/// every voucher that touched the account over a window, oldest first, with a
/// running column beside it. Sent as a document rather than a screenshot
/// because the recipient needs to be able to search it and print it.
///
/// Laid out as Tally's Ledger Vouchers: an opening balance, each voucher with
/// the balance it left behind, and a closing balance for the period.
///
/// **Today's balance is a different figure and the layout says so.** A period
/// that ended last month closes on last month's balance; what the account
/// stands at now is printed apart from it, named, and only when the two
/// differ -- a statement that let them read as one figure is a statement
/// somebody would pay the wrong amount against.
///
/// With no opening balance to run from, the last column is headed "Running"
/// and is movement inside the period only.
class StatementDocument {
  const StatementDocument({
    required this.statement,
    required this.companyName,
    required this.periodLabel,
    required this.from,
    required this.to,
  });

  final LedgerStatement statement;
  final String companyName;

  /// The period as the app named it on screen -- "Last 90 days", "Q2", "This
  /// month". The dates are printed beside it: a document read next week must
  /// not depend on what "last 90 days" meant on the day it was made.
  final String periodLabel;
  final DateTime from;
  final DateTime to;

  String get fileName => DocumentStyle.fileName(<String?>[
        'Statement',
        statement.ledger,
        DocumentStyle.date(from),
        DocumentStyle.date(to),
      ]);

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(
      title: 'Statement - ${statement.ledger}',
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
          _entries(),
          _totals(),
          if (_showBalanceToday) _balanceToday(),
        ],
      ),
    );

    return document.save();
  }

  /// Printed when it is the only balance on the page, or a different one
  /// from the period's closing balance.
  bool get _showBalanceToday {
    final Money? today = statement.closingBalance;
    if (today == null) return false;
    final Money? closing = statement.periodClosingBalance;
    return closing == null || closing.amount != today.amount || closing.side != today.side;
  }

  static String _withSide(Money value) =>
      '${DocumentStyle.amount(value)} ${value.side == MoneySide.debit ? 'Dr' : 'Cr'}';

  /// "Opening Balance" and "Closing Balance", ruled into the table where an
  /// accountant looks for them.
  pw.TableRow _balanceRow(String label, DateTime on, Money balance) => pw.TableRow(
        children: <pw.Widget>[
          DocumentStyle.cell(DocumentStyle.date(on)),
          DocumentStyle.cell(label, bold: true),
          DocumentStyle.cell(''),
          DocumentStyle.cell(''),
          DocumentStyle.cell(_withSide(balance), align: pw.TextAlign.right, bold: true),
        ],
      );

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
                  pw.Text('Ledger Statement', style: DocumentStyle.title),
                  pw.Text(
                    'Amounts in ${statement.netMovement.currency}',
                    style: DocumentStyle.footnote,
                  ),
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
                    pw.Text(statement.ledger, style: DocumentStyle.heading),
                    if ((statement.group ?? '').isNotEmpty)
                      pw.Text(
                        'Under: ${statement.group}',
                        style: DocumentStyle.footnote,
                      ),
                  ],
                ),
              ),
              fields: <pw.Widget>[
                DocumentStyle.field(
                  'Period',
                  '${DocumentStyle.date(from)} to ${DocumentStyle.date(to)}',
                ),
                DocumentStyle.field('Vouchers', '${statement.voucherCount}'),
              ],
            ),
          ],
        ),
      );

  pw.Widget _entries() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        child: pw.Table(
          border: DocumentStyle.tableRules,
          columnWidths: <int, pw.TableColumnWidth>{
            0: const pw.FixedColumnWidth(58),
            1: const pw.FlexColumnWidth(4),
            2: const pw.FixedColumnWidth(70),
            3: const pw.FixedColumnWidth(70),
            4: const pw.FixedColumnWidth(78),
          },
          children: <pw.TableRow>[
            pw.TableRow(
              repeat: true,
              children: <pw.Widget>[
                DocumentStyle.headerCell('Date'),
                DocumentStyle.headerCell('Particulars'),
                DocumentStyle.headerCell('Debit', align: pw.TextAlign.right),
                DocumentStyle.headerCell('Credit', align: pw.TextAlign.right),
                DocumentStyle.headerCell(
                  statement.hasBalances ? 'Balance' : 'Running',
                  align: pw.TextAlign.right,
                ),
              ],
            ),
            if (statement.hasBalances)
              _balanceRow('Opening Balance', from, statement.openingBalance!),
            for (final StatementEntry entry in statement.entries) _row(entry),
            if (statement.hasBalances)
              _balanceRow('Closing Balance', to, statement.periodClosingBalance!),
          ],
        ),
      );

  pw.TableRow _row(StatementEntry entry) {
    final bool isDebit = entry.movement.side == MoneySide.debit;
    final String caption = <String?>[
      entry.line.voucherType,
      entry.line.voucherNumber == null ? null : '#${entry.line.voucherNumber}',
    ].whereType<String>().join('  ');

    return pw.TableRow(
      children: <pw.Widget>[
        DocumentStyle.cell(DocumentStyle.date(entry.line.date)),
        pw.Padding(
          padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
          child: pw.Column(
            crossAxisAlignment: pw.CrossAxisAlignment.start,
            children: <pw.Widget>[
              pw.Text(
                entry.line.party ?? caption,
                style: DocumentStyle.bodyBold,
              ),
              if (entry.line.party != null && caption.isNotEmpty)
                pw.Text(caption, style: DocumentStyle.footnote),
            ],
          ),
        ),
        // One column each, blank on the side the voucher did not move. A single
        // signed column would make the reader work out the direction from a
        // minus sign, which is exactly what a statement exists to spare them.
        DocumentStyle.cell(
          isDebit ? DocumentStyle.amount(entry.movement) : '',
          align: pw.TextAlign.right,
        ),
        DocumentStyle.cell(
          isDebit ? '' : DocumentStyle.amount(entry.movement),
          align: pw.TextAlign.right,
        ),
        DocumentStyle.cell(
          '${DocumentStyle.amount(entry.running)} '
          '${entry.running.side == MoneySide.debit ? 'Dr' : 'Cr'}',
          align: pw.TextAlign.right,
        ),
      ],
    );
  }

  pw.Widget _totals() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        decoration: DocumentStyle.boxed,
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
        child: pw.Column(
          children: <pw.Widget>[
            _totalRow('Debit total', statement.debitTotal),
            _totalRow('Credit total', statement.creditTotal),
            pw.Divider(height: 6, color: DocumentStyle.rule),
            _totalRow(
              'Net movement, $periodLabel',
              statement.netMovement,
              withSide: true,
              bold: true,
            ),
          ],
        ),
      );

  pw.Widget _totalRow(
    String label,
    Money value, {
    bool withSide = false,
    bool bold = false,
  }) =>
      pw.Padding(
        padding: const pw.EdgeInsets.symmetric(vertical: 1),
        child: pw.Row(
          mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
          children: <pw.Widget>[
            pw.Text(label, style: bold ? DocumentStyle.heading : DocumentStyle.body),
            pw.Text(
              withSide
                  ? '${DocumentStyle.amount(value)} '
                      '${value.side == MoneySide.debit ? 'Dr' : 'Cr'}'
                  : DocumentStyle.amount(value),
              style: bold ? DocumentStyle.heading : DocumentStyle.body,
            ),
          ],
        ),
      );

  /// Labelled "today", never "closing". See the class comment: this figure is
  /// not the end of the running column and printing it as though it were is how
  /// somebody pays against the wrong number.
  pw.Widget _balanceToday() {
    final Money balance = statement.closingBalance!;
    return pw.Container(
      margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
      width: double.infinity,
      decoration: DocumentStyle.boxed,
      padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        children: <pw.Widget>[
          pw.Row(
            mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
            children: <pw.Widget>[
              pw.Text('Balance today', style: DocumentStyle.heading),
              pw.Text(
                '${DocumentStyle.amount(balance)} '
                '${balance.side == MoneySide.debit ? 'Dr' : 'Cr'}',
                style: DocumentStyle.heading,
              ),
            ],
          ),
          pw.SizedBox(height: 2),
          pw.Text(
            'The account as it stands now, not at the end of the period above.',
            style: DocumentStyle.footnote,
          ),
        ],
      ),
    );
  }
}
