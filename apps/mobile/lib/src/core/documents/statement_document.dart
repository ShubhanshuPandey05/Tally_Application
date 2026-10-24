import 'package:decimal/decimal.dart';
import 'package:pdf/widgets.dart' as pw;

import '../../features/reports/domain/drilldown.dart';
import '../money/money.dart';
import 'document_style.dart';

/// One ledger's account over a period, printed as Tally prints a Ledger
/// Account.
///
/// Laid out to the reference print the customer sent: the company's
/// letterhead, then the account holder's name and address under "Ledger
/// Account", the period, and a ruled-only table -- Date, Dr/Cr, Particulars,
/// Vch Type, Vch No., Debit, Credit, Balance. Each row names the account on
/// the *other* side of the voucher, marked with that side, which is how an
/// accountant reads "Cr Sales" against a customer's invoice.
///
/// **The totals balance, the way a ledger account does.** The opening balance
/// sits in its own column at the top and is in its column's total; when the
/// period closes on anything but nil, a Closing Balance row on the other side
/// brings the two totals level. That is the shape every Indian accountant has
/// signed off for years, and the figure they look for is the one that
/// balances.
///
/// **Today's balance is a different figure and the print says so.** A period
/// that ended last month closes on last month's balance; what the account
/// stands at now is printed under the table, named, and only when the two
/// differ.
///
/// With no opening balance to run from -- the history around the period is not
/// all held yet -- there is no Opening or Closing row, the totals are the
/// period's own, and the last column is headed "Running": movement inside the
/// period only, never an account that started from nil.
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

  /// The period as the app named it on screen. The print carries the dates
  /// instead: a page read next week must not depend on what "last 90 days"
  /// meant on the day it was made. Kept for the file's subject line.
  final String periodLabel;
  final DateTime from;
  final DateTime to;

  String get fileName => DocumentStyle.fileName(<String?>[
        'Ledger',
        statement.ledger,
        DocumentStyle.date(from),
        DocumentStyle.date(to),
      ]);

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(
      title: 'Ledger Account - ${statement.ledger}',
      author: companyName,
    );

    document.addPage(
      pw.MultiPage(
        pageTheme: DocumentStyle.page(),
        // "Page n" over the table on every page after the first. The first
        // page carries it inline, directly over the column headings, where the
        // reference print has it.
        header: (pw.Context context) => context.pageNumber == 1
            ? pw.SizedBox()
            : _pageLabel(context.pageNumber),
        build: (pw.Context context) => <pw.Widget>[
          DocumentStyle.letterhead(
            name: companyName,
            details: statement.companyDetails,
          ),
          pw.SizedBox(height: 8),
          _accountHolder(),
          pw.SizedBox(height: 8),
          pw.Center(
            child: pw.Text(
              '${DocumentStyle.shortDate(from)} to ${DocumentStyle.shortDate(to)}',
              style: DocumentStyle.body,
            ),
          ),
          pw.SizedBox(height: 8),
          // Literally page one: the build runs before pages are numbered, and
          // this line is always on the first of them.
          _pageLabel(1),
          _entries(),
          if (_showBalanceToday) _balanceToday(),
        ],
      ),
    );

    return document.save();
  }

  static pw.Widget _pageLabel(int page) => pw.Align(
        alignment: pw.Alignment.centerRight,
        child: pw.Text('Page $page', style: DocumentStyle.body),
      );

  pw.Widget _accountHolder() {
    final PartyDetails? party = statement.partyDetails;
    return pw.Container(
      width: double.infinity,
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.center,
        children: <pw.Widget>[
          pw.Text(
            statement.ledger,
            style: pw.TextStyle(
              fontSize: 11,
              fontWeight: pw.FontWeight.bold,
              color: DocumentStyle.ink,
            ),
            textAlign: pw.TextAlign.center,
          ),
          pw.Text('Ledger Account', style: DocumentStyle.body),
          if (party != null && party.address.isNotEmpty) ...<pw.Widget>[
            pw.SizedBox(height: 4),
            for (final String line in party.address)
              pw.Text(line,
                  style: DocumentStyle.body, textAlign: pw.TextAlign.center),
          ],
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // The table
  // ------------------------------------------------------------------

  static const Map<int, pw.TableColumnWidth> _columns =
      <int, pw.TableColumnWidth>{
    0: pw.FixedColumnWidth(50), // Date
    1: pw.FixedColumnWidth(18), // Dr / Cr
    2: pw.FlexColumnWidth(4), // Particulars
    3: pw.FixedColumnWidth(66), // Vch Type
    4: pw.FixedColumnWidth(62), // Vch No.
    5: pw.FixedColumnWidth(64), // Debit
    6: pw.FixedColumnWidth(64), // Credit
    7: pw.FixedColumnWidth(72), // Balance
  };

  pw.Widget _entries() {
    final bool balances = statement.hasBalances;
    final Money? opening = statement.openingBalance;
    final Money? closing = statement.periodClosingBalance;

    Decimal debit = statement.debitTotal.amount;
    Decimal credit = statement.creditTotal.amount;
    if (balances && !_isNil(opening!)) {
      if (opening.side == MoneySide.debit) {
        debit += opening.amount;
      } else {
        credit += opening.amount;
      }
    }
    final bool closingRow = balances && !_isNil(closing!);
    if (closingRow) {
      // On the side that brings the account level: a debit balance is
      // carried down as a credit.
      if (closing.side == MoneySide.debit) {
        credit += closing.amount;
      } else {
        debit += closing.amount;
      }
    }
    final String currency = statement.netMovement.currency;

    final List<pw.TableRow> rows = <pw.TableRow>[
      pw.TableRow(
        repeat: true,
        decoration: const pw.BoxDecoration(
          border:
              pw.Border(top: DocumentStyle.ink06, bottom: DocumentStyle.ink06),
        ),
        children: <pw.Widget>[
          _cell('Date', align: pw.TextAlign.center),
          _cell(''),
          _cell('Particulars', bold: true, align: pw.TextAlign.center),
          _cell('Vch Type'),
          _cell('Vch No.', align: pw.TextAlign.right),
          _cell('Debit', bold: true, align: pw.TextAlign.right),
          _cell('Credit', bold: true, align: pw.TextAlign.right),
          _cell(balances ? 'Balance' : 'Running', align: pw.TextAlign.right),
        ],
      ),
      if (balances) _openingRow(opening!),
    ];

    DateTime? previous = balances ? from : null;
    for (final StatementEntry entry in statement.entries) {
      rows.add(_row(entry, showDate: !_sameDay(previous, entry.line.date)));
      previous = entry.line.date;
    }

    if (closingRow) rows.add(_closingRow(closing));

    rows.add(
      pw.TableRow(
        children: <pw.Widget>[
          for (int i = 0; i < 5; i++) _cell(''),
          _totalCell(_amount(_money(debit, currency))),
          _totalCell(_amount(_money(credit, currency))),
          _cell(''),
        ],
      ),
    );

    return pw.Table(columnWidths: _columns, children: rows);
  }

  pw.TableRow _openingRow(Money opening) => pw.TableRow(
        children: <pw.Widget>[
          _cell(DocumentStyle.shortDate(from), align: pw.TextAlign.right),
          _cell(''),
          _cell('Opening Balance', bold: true),
          _cell(''),
          _cell(''),
          _cell(
            opening.side == MoneySide.debit ? _amount(opening) : '',
            bold: true,
            align: pw.TextAlign.right,
          ),
          _cell(
            opening.side == MoneySide.credit ? _amount(opening) : '',
            bold: true,
            align: pw.TextAlign.right,
          ),
          _cell(''),
        ],
      );

  pw.TableRow _closingRow(Money closing) => pw.TableRow(
        children: <pw.Widget>[
          _cell(DocumentStyle.shortDate(to), align: pw.TextAlign.right),
          _cell(''),
          _cell('Closing Balance', bold: true),
          _cell(''),
          _cell(''),
          _cell(
            closing.side == MoneySide.credit ? _amount(closing) : '',
            bold: true,
            align: pw.TextAlign.right,
          ),
          _cell(
            closing.side == MoneySide.debit ? _amount(closing) : '',
            bold: true,
            align: pw.TextAlign.right,
          ),
          _cell(''),
        ],
      );

  pw.TableRow _row(StatementEntry entry, {required bool showDate}) {
    final bool isDebit = entry.movement.side == MoneySide.debit;
    final String particulars = <String?>[
      entry.particulars,
      entry.line.party,
      entry.line.voucherType,
    ]
        .whereType<String>()
        .firstWhere((String s) => s.trim().isNotEmpty, orElse: () => '');

    return pw.TableRow(
      children: <pw.Widget>[
        _cell(showDate ? DocumentStyle.shortDate(entry.line.date) : '',
            align: pw.TextAlign.right),
        // The side of the account named beside it, which is the opposite of
        // the side this ledger moved: a customer debited by a sale reads
        // "Cr Sales". An older backend sends no account to name, and the
        // marker then goes with it rather than standing beside the wrong name.
        _cell(entry.particulars == null ? '' : (isDebit ? 'Cr' : 'Dr')),
        _cell(particulars, bold: true),
        _cell(entry.line.voucherType ?? ''),
        _cell(entry.line.voucherNumber ?? '', align: pw.TextAlign.right),
        // One column each, blank on the side the voucher did not move. A
        // single signed column would make the reader work out the direction
        // from a minus sign, which is exactly what a ledger exists to spare
        // them.
        _cell(isDebit ? _amount(entry.movement) : '',
            align: pw.TextAlign.right),
        _cell(isDebit ? '' : _amount(entry.movement),
            align: pw.TextAlign.right),
        _cell(_withSide(entry.running), align: pw.TextAlign.right),
      ],
    );
  }

  // ------------------------------------------------------------------
  // Balance today
  // ------------------------------------------------------------------

  /// Printed when it is the only balance on the page, or a different one from
  /// the period's closing balance.
  bool get _showBalanceToday {
    final Money? today = statement.closingBalance;
    if (today == null) return false;
    final Money? closing = statement.periodClosingBalance;
    return closing == null ||
        closing.amount != today.amount ||
        closing.side != today.side;
  }

  /// Labelled "today", never "closing". See the class comment: this figure is
  /// not where the table ends, and printing it as though it were is how
  /// somebody pays against the wrong number.
  pw.Widget _balanceToday() {
    final Money balance = statement.closingBalance!;
    final String figure = _isNil(balance) ? 'Nil' : _withSide(balance);
    return pw.Padding(
      padding: const pw.EdgeInsets.only(top: 10),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.end,
        children: <pw.Widget>[
          pw.Text('Balance today: $figure', style: DocumentStyle.bodyBold),
          pw.Text(
            'The account as it stands now, not at the end of the period above.',
            style: DocumentStyle.footnote,
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Pieces
  // ------------------------------------------------------------------

  static bool _sameDay(DateTime? a, DateTime b) =>
      a != null && a.year == b.year && a.month == b.month && a.day == b.day;

  static bool _isNil(Money value) => value.amount == Decimal.zero;

  static String _amount(Money value) => DocumentStyle.amount(value);

  /// Blank when the account stands at nil, as Tally leaves it on a row that
  /// settled the account; otherwise the figure with its side.
  static String _withSide(Money value) => _isNil(value)
      ? ''
      : '${DocumentStyle.amount(value)} ${value.side == MoneySide.debit ? 'Dr' : 'Cr'}';

  static Money _money(Decimal amount, String currency) => Money(
        amount: amount,
        side: MoneySide.debit,
        signed: amount,
        currency: currency,
      );

  static pw.Widget _cell(
    String text, {
    bool bold = false,
    pw.TextAlign align = pw.TextAlign.left,
  }) =>
      pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 2.5, vertical: 1.5),
        child: pw.Text(
          text,
          textAlign: align,
          style: bold ? DocumentStyle.bodyBold : DocumentStyle.body,
        ),
      );

  /// A total under its column: a rule over it and a double one under, as a
  /// ledger account closes.
  static pw.Widget _totalCell(String text) => pw.Container(
        margin: const pw.EdgeInsets.only(left: 4, top: 2),
        padding: const pw.EdgeInsets.only(top: 2, bottom: 1, right: 2.5),
        decoration: const pw.BoxDecoration(
          border:
              pw.Border(top: DocumentStyle.ink06, bottom: DocumentStyle.ink06),
        ),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.stretch,
          children: <pw.Widget>[
            pw.Text(text,
                textAlign: pw.TextAlign.right, style: DocumentStyle.bodyBold),
            pw.SizedBox(height: 1.2),
            pw.Container(
              height: 0,
              decoration: const pw.BoxDecoration(
                border: pw.Border(top: DocumentStyle.ink06),
              ),
            ),
          ],
        ),
      );
}
