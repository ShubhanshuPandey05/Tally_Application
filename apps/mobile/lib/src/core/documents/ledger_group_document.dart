import 'package:pdf/widgets.dart' as pw;

import '../model/figures.dart';
import '../money/money.dart';
import 'document_style.dart';

/// Every account under one Tally group, with its balance -- the list an owner
/// sends their accountant when the question is "who are all my debtors?".
///
/// Balances only, and labelled as standing today: Tally evaluates a closing
/// balance against the current date whatever is asked of it, so a group list
/// carries no period and must not look as though it does.
class LedgerGroupDocument {
  const LedgerGroupDocument({
    required this.group,
    required this.lines,
    required this.total,
    required this.companyName,
    required this.asOf,
  });

  final String group;
  final List<LedgerLine> lines;
  final Money total;
  final String companyName;

  /// When the figures were read, printed so a document opened next week says
  /// which day's balances it holds.
  final DateTime asOf;

  String get fileName => DocumentStyle.fileName(<String?>[
        group,
        DocumentStyle.date(asOf),
      ]);

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(title: group, author: companyName);

    document.addPage(
      pw.MultiPage(
        pageTheme: DocumentStyle.page(),
        footer: (pw.Context context) => DocumentStyle.footer(
          context,
          note: 'Computer generated from $companyName\'s TallyPrime books.',
        ),
        build: (pw.Context context) => <pw.Widget>[
          _header(),
          _ledgers(),
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
                  pw.Text('Group Summary', style: DocumentStyle.title),
                  pw.Text(
                    'Amounts in ${total.currency}',
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
                    pw.Text(group, style: DocumentStyle.heading),
                  ],
                ),
              ),
              fields: <pw.Widget>[
                DocumentStyle.field('Balances as on', DocumentStyle.date(asOf)),
                DocumentStyle.field('Ledgers', '${lines.length}'),
              ],
            ),
          ],
        ),
      );

  pw.Widget _ledgers() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        child: pw.Table(
          border: DocumentStyle.tableRules,
          columnWidths: <int, pw.TableColumnWidth>{
            0: const pw.FlexColumnWidth(4),
            1: const pw.FixedColumnWidth(100),
            2: const pw.FixedColumnWidth(90),
          },
          children: <pw.TableRow>[
            pw.TableRow(
              repeat: true,
              children: <pw.Widget>[
                DocumentStyle.headerCell('Ledger'),
                DocumentStyle.headerCell('GSTIN'),
                DocumentStyle.headerCell('Balance', align: pw.TextAlign.right),
              ],
            ),
            for (final LedgerLine line in lines)
              pw.TableRow(
                children: <pw.Widget>[
                  DocumentStyle.cell(line.name),
                  DocumentStyle.cell(line.gstin ?? ''),
                  DocumentStyle.cell(_withSide(line.closing), align: pw.TextAlign.right),
                ],
              ),
            pw.TableRow(
              children: <pw.Widget>[
                DocumentStyle.cell('Total', bold: true),
                DocumentStyle.cell(''),
                DocumentStyle.cell(
                  _withSide(total),
                  align: pw.TextAlign.right,
                  bold: true,
                ),
              ],
            ),
          ],
        ),
      );

  static String _withSide(Money value) =>
      '${DocumentStyle.amount(value)} ${value.side == MoneySide.debit ? 'Dr' : 'Cr'}';
}
