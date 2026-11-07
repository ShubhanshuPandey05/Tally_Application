import 'package:pdf/widgets.dart' as pw;

import '../money/money.dart';
import 'document_style.dart';

/// One column of a shared report.
class ReportColumn {
  const ReportColumn(this.label, {this.numeric = false, this.flex = 2});

  final String label;

  /// Right-aligned, the way every figure in an accounting print is.
  final bool numeric;

  /// Its share of the page width against the other columns.
  final double flex;
}

/// Any report, as the rows on screen, ready to be printed.
///
/// Each report screen hands over what it is already showing -- the same
/// filter, the same period, the same rows -- rather than the document asking
/// the server again. What is shared must be what the person was looking at,
/// and a second read could come back with a row that was not on their screen.
class ReportTable {
  const ReportTable({
    required this.title,
    required this.columns,
    required this.rows,
    this.subtitle,
    this.total,
    this.notes = const <String>[],
  });

  final String title;

  /// The period, or "Balances as on ..." for a report that has none.
  final String? subtitle;
  final List<ReportColumn> columns;
  final List<List<String>> rows;

  /// A bold last row, when the report has one on screen.
  final List<String>? total;
  final List<String> notes;
}

/// Prints a [ReportTable] in the same hairline style as every other document
/// the app shares.
class ReportTableDocument {
  const ReportTableDocument({
    required this.table,
    required this.companyName,
    required this.generatedAt,
  });

  final ReportTable table;
  final String companyName;
  final DateTime generatedAt;

  String get fileName => DocumentStyle.fileName(<String?>[
        table.title,
        DocumentStyle.date(generatedAt),
      ]);

  /// The PDF faces have no rupee glyph and print a box in its place (see
  /// [DocumentStyle]), so a cell formatted for the screen loses its symbol.
  static String clean(String text) => text.replaceAll('₹', '').trim();

  /// An amount without a symbol, to the paisa.
  static String amount(Money value) => DocumentStyle.amount(value);

  /// An amount with Dr or Cr after it, as Tally prints a balance.
  static String balance(Money value) =>
      '${DocumentStyle.amount(value)} ${value.side == MoneySide.debit ? 'Dr' : 'Cr'}';

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(title: table.title, author: companyName);
    document.addPage(
      pw.MultiPage(
        pageTheme: DocumentStyle.page(),
        footer: (pw.Context context) => DocumentStyle.footer(
          context,
          note: 'Computer generated from $companyName\'s TallyPrime books.',
        ),
        build: (pw.Context context) => <pw.Widget>[
          _header(),
          _rows(),
          for (final String note in table.notes)
            pw.Padding(
              padding: const pw.EdgeInsets.only(top: 4),
              child: pw.Text(clean(note), style: DocumentStyle.footnote),
            ),
        ],
      ),
    );
    return document.save();
  }

  pw.Widget _header() => pw.Container(
        decoration: DocumentStyle.boxed,
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
        child: pw.Row(
          mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
          crossAxisAlignment: pw.CrossAxisAlignment.end,
          children: <pw.Widget>[
            pw.Expanded(
              child: pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.start,
                children: <pw.Widget>[
                  pw.Text(companyName, style: DocumentStyle.body),
                  pw.SizedBox(height: 3),
                  pw.Text(clean(table.title), style: DocumentStyle.title),
                  if (table.subtitle != null) ...<pw.Widget>[
                    pw.SizedBox(height: 2),
                    pw.Text(clean(table.subtitle!), style: DocumentStyle.body),
                  ],
                ],
              ),
            ),
            pw.Text(
              'Printed ${DocumentStyle.date(generatedAt)}',
              style: DocumentStyle.footnote,
            ),
          ],
        ),
      );

  pw.Widget _rows() {
    final List<ReportColumn> columns = table.columns;
    pw.TextAlign align(int i) =>
        columns[i].numeric ? pw.TextAlign.right : pw.TextAlign.left;
    List<pw.Widget> cells(List<String> row, {bool bold = false}) => <pw.Widget>[
          for (int i = 0; i < columns.length; i++)
            DocumentStyle.cell(
              i < row.length ? clean(row[i]) : '',
              align: align(i),
              bold: bold,
            ),
        ];

    return pw.Container(
      margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
      child: table.rows.isEmpty
          ? pw.Padding(
              padding: const pw.EdgeInsets.all(8),
              child: pw.Text('Nothing in this report.', style: DocumentStyle.body),
            )
          : pw.Table(
              border: DocumentStyle.tableRules,
              columnWidths: <int, pw.TableColumnWidth>{
                for (int i = 0; i < columns.length; i++)
                  i: pw.FlexColumnWidth(columns[i].flex),
              },
              children: <pw.TableRow>[
                pw.TableRow(
                  repeat: true,
                  children: <pw.Widget>[
                    for (int i = 0; i < columns.length; i++)
                      DocumentStyle.headerCell(columns[i].label, align: align(i)),
                  ],
                ),
                for (final List<String> row in table.rows) pw.TableRow(children: cells(row)),
                if (table.total != null) pw.TableRow(children: cells(table.total!, bold: true)),
              ],
            ),
    );
  }
}
