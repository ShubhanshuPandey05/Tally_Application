import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

import '../money/money.dart';
import '../money/money_format.dart';

/// The look every shared document shares.
///
/// Deliberately the look of an Indian accounting print-out -- hairline rules,
/// boxed header, small serif-free type -- and not the look of the app. A
/// customer receiving a bill on WhatsApp is going to compare it against the
/// other bills in that chat, and one that arrives styled as a dashboard reads
/// as marketing rather than as a record.
///
/// **No currency symbol anywhere.** The PDF package's built-in faces have no
/// glyph for U+20B9, and a missing glyph prints as a blank box beside somebody's
/// money. Bundling a font that has one would add a few hundred kilobytes to
/// every build for a symbol the numerals do not need: the document names its
/// currency once, in the header, and the columns stay plain.
class DocumentStyle {
  const DocumentStyle._();

  static const PdfColor ink = PdfColors.black;
  static const PdfColor rule = PdfColor.fromInt(0xFF9E9E9E);
  static const PdfColor faint = PdfColor.fromInt(0xFF616161);
  static const PdfColor wash = PdfColor.fromInt(0xFFF2F2F2);

  static const double gap = 4;

  static pw.TextStyle get title =>
      pw.TextStyle(fontSize: 13, fontWeight: pw.FontWeight.bold, color: ink);

  static pw.TextStyle get heading =>
      pw.TextStyle(fontSize: 9.5, fontWeight: pw.FontWeight.bold, color: ink);

  static pw.TextStyle get body => const pw.TextStyle(fontSize: 8.5, color: ink);

  static pw.TextStyle get bodyBold =>
      pw.TextStyle(fontSize: 8.5, fontWeight: pw.FontWeight.bold, color: ink);

  /// For the labels that sit above a value in a boxed header cell.
  static pw.TextStyle get label => const pw.TextStyle(fontSize: 6.5, color: faint);

  static pw.TextStyle get footnote => const pw.TextStyle(fontSize: 7, color: faint);

  static pw.BoxDecoration get boxed => pw.BoxDecoration(
        border: pw.Border.all(color: rule, width: 0.6),
      );

  static pw.BoxDecoration get topRule => const pw.BoxDecoration(
        border: pw.Border(top: pw.BorderSide(color: rule, width: 0.6)),
      );

  static pw.TableBorder get tableRules => pw.TableBorder.all(color: rule, width: 0.4);

  /// A boxed header cell: a small grey caption over the value it names.
  ///
  /// Rendered even when the value is missing, because these cells sit in a grid
  /// and a collapsed one shifts every cell beside it. An empty cell says "not
  /// recorded"; a shifted grid says the document was built wrong.
  ///
  /// The placeholder is a hyphen rather than a dash. Every glyph on these pages
  /// is ASCII on purpose -- the built-in PDF faces carry no Unicode, and a
  /// character they cannot draw either throws while building the document or
  /// prints as a blank box next to somebody's money.
  static pw.Widget field(String caption, String? value) => pw.Container(
        padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 3),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          mainAxisSize: pw.MainAxisSize.min,
          children: <pw.Widget>[
            pw.Text(caption, style: label),
            pw.SizedBox(height: 1),
            pw.Text(
              value == null || value.trim().isEmpty ? '-' : value,
              style: bodyBold,
            ),
          ],
        ),
      );

  /// A two-column header block: a wide cell beside a stack of small fields.
  ///
  /// A table rather than a row of `Expanded`s. `MultiPage` lays its children
  /// out one at a time to decide where the page breaks, and a row that stretches
  /// to its tallest child has no height to offer until it has one -- which ends
  /// as a document that never fits on any number of pages.
  static pw.Widget headerBlock({
    required pw.Widget left,
    required List<pw.Widget> fields,
  }) =>
      pw.Table(
        border: const pw.TableBorder(
          verticalInside: pw.BorderSide(color: rule, width: 0.6),
        ),
        columnWidths: <int, pw.TableColumnWidth>{
          0: const pw.FlexColumnWidth(3),
          1: const pw.FlexColumnWidth(2),
        },
        children: <pw.TableRow>[
          pw.TableRow(
            children: <pw.Widget>[
              left,
              pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.stretch,
                mainAxisSize: pw.MainAxisSize.min,
                children: <pw.Widget>[
                  for (int i = 0; i < fields.length; i++) ...<pw.Widget>[
                    if (i > 0) pw.Divider(height: 0.6, color: rule),
                    fields[i],
                  ],
                ],
              ),
            ],
          ),
        ],
      );

  /// One cell of a ruled table.
  static pw.Widget cell(
    String text, {
    pw.TextAlign align = pw.TextAlign.left,
    bool bold = false,
    pw.TextStyle? style,
  }) =>
      pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
        child: pw.Text(
          text,
          textAlign: align,
          style: style ?? (bold ? bodyBold : body),
        ),
      );

  static pw.Widget headerCell(String text, {pw.TextAlign align = pw.TextAlign.left}) =>
      pw.Container(
        color: wash,
        padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 4),
        child: pw.Text(
          text,
          textAlign: align,
          style: pw.TextStyle(fontSize: 7.5, fontWeight: pw.FontWeight.bold, color: ink),
        ),
      );

  /// Amounts without a symbol, grouped Indian-style, always to the paisa.
  ///
  /// A document is the artefact somebody reconciles against Tally, so the
  /// rounding that is right on a dashboard tile is wrong here.
  static String amount(Money money) => MoneyFormat.full(money, withSymbol: false);

  /// `16-Apr-2024` -- the form every Tally print-out uses, and unambiguous in a
  /// way that `04-16-2024` and `16-04-2024` are not to the same reader.
  static String date(DateTime value) =>
      '${value.day.toString().padLeft(2, '0')}-${_months[value.month - 1]}-${value.year}';

  static const List<String> _months = <String>[
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];

  /// A file name a person can find again in their downloads folder.
  ///
  /// Everything a filesystem or a chat app might choke on is folded to a
  /// hyphen: a party called "M/s. Sharma & Co." is entirely ordinary and would
  /// otherwise produce a path with a directory separator in it.
  static String fileName(List<String?> parts) {
    final String joined = parts
        .whereType<String>()
        .map((String part) => part.trim())
        .where((String part) => part.isNotEmpty)
        .join('-');
    final String safe = joined
        .replaceAll(RegExp(r'[^A-Za-z0-9\-_. ]'), ' ')
        .replaceAll(RegExp(r'\s+'), '-')
        .replaceAll(RegExp(r'-{2,}'), '-')
        .replaceAll(RegExp(r'^-|-$'), '');
    return safe.isEmpty ? 'tallyflow' : safe;
  }

  /// The page frame: A4, narrow margins, and a footer on every page.
  ///
  /// The footer carries the page number because these documents are emailed and
  /// printed, and a two-page statement that arrives without "1 of 2" is one
  /// somebody has to count.
  static pw.PageTheme page() => pw.PageTheme(
        pageFormat: PdfPageFormat.a4.copyWith(
          marginLeft: 24,
          marginRight: 24,
          marginTop: 24,
          marginBottom: 28,
        ),
        theme: pw.ThemeData.withFont().copyWith(defaultTextStyle: body),
      );

  static pw.Widget footer(pw.Context context, {required String note}) => pw.Container(
        alignment: pw.Alignment.centerLeft,
        margin: const pw.EdgeInsets.only(top: 8),
        child: pw.Row(
          mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
          children: <pw.Widget>[
            pw.Text(note, style: footnote),
            pw.Text(
              'Page ${context.pageNumber} of ${context.pagesCount}',
              style: footnote,
            ),
          ],
        ),
      );
}
