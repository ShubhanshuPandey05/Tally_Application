import 'package:decimal/decimal.dart';
import 'package:pdf/widgets.dart' as pw;

import '../../features/reports/domain/drilldown.dart';
import '../money/money.dart';
import '../money/money_format.dart';
import 'amount_in_words.dart';
import 'document_style.dart';

/// One voucher, drawn as the document a business actually sends.
///
/// The shape is the one every Indian accounting package prints and every
/// customer therefore recognises: a boxed header with the seller on the left
/// and the document's own numbers on the right, the party underneath, a ruled
/// table of what was supplied, the total written out in words, and a signature
/// block. Departing from it would produce something better looking and less
/// trusted.
///
/// **It reproduces the voucher; it does not invent one.** Every figure on the
/// page came off the voucher or off a master, and anything Tally does not hold
/// -- a GSTIN nobody entered, an HSN code left blank -- is left off the page
/// rather than printed empty. A blank tax box on a document that otherwise
/// looks like a tax invoice is a statement about a supply that nobody made.
class VoucherDocument {
  const VoucherDocument({
    required this.detail,
    required this.companyName,
    this.companyGstin,
  });

  final VoucherDetail detail;

  /// The seller. Taken from the linked company rather than from the voucher:
  /// a voucher names the other side, never its own books.
  final String companyName;

  /// The seller's own registration, printed under the name when it is known.
  ///
  /// Nothing supplies it today: TallyPrime reports a company's GSTIN on the
  /// discovery read, but the backend keeps only the name and the book dates, so
  /// there is no stored value to pass. Left as a parameter rather than removed
  /// because the header has a place for it and the alternative -- printing a
  /// blank GSTIN line -- is the one thing this document must not do.
  final String? companyGstin;

  /// Ledger names that carry tax rather than value. Matched loosely because a
  /// shop names these ledgers freely -- "Output CGST 9%", "CGST Payable",
  /// "IGST @ 18%" are all the same thing to the person reading the bill.
  static final RegExp _taxLedger =
      RegExp(r'\b(c|s|i|ut)?gst\b|\bcess\b|\btax\b', caseSensitive: false);

  /// A document title a recipient can act on. The voucher type as the shop
  /// named it, because "Tax Invoice" and "Retail Sale" are the shop's own words
  /// and replacing them with our own would make two records disagree.
  String get title => detail.voucherType.trim().isEmpty ? 'Voucher' : detail.voucherType;

  String get fileName => DocumentStyle.fileName(<String?>[
        title,
        detail.voucherNumber,
        DocumentStyle.date(detail.date),
      ]);

  /// The stock lines' own total, used to spot which ledger line carries them.
  Money get _itemsTotal => _sum(
        detail.inventoryEntries.map((VoucherStockLine line) => line.amount),
      );

  /// Every ledger line that is not the party's.
  List<VoucherLedgerLine> get _postings =>
      detail.ledgerEntries.where((VoucherLedgerLine e) => !e.isParty).toList();

  /// The line that carries the goods, when the item table already shows it.
  ///
  /// Tally's own invoice print does the same thing: the sales ledger is not a
  /// row on the bill, because the item rows *are* that row broken out. Matched
  /// by value rather than by name -- shops call it "Sales", "Sales @ 18%",
  /// "Local Sales GST" and a dozen other things.
  VoucherLedgerLine? get _goodsLine {
    if (detail.inventoryEntries.isEmpty) return null;
    final Decimal total = _itemsTotal.amount;
    if (total == Decimal.zero) return null;
    for (final VoucherLedgerLine line in _postings) {
      if (line.amount.amount == total) return line;
    }
    return null;
  }

  /// The rows printed under the items: tax, freight, discount, round-off.
  List<VoucherLedgerLine> get _charges {
    final VoucherLedgerLine? goods = _goodsLine;
    return _postings.where((VoucherLedgerLine e) => !identical(e, goods)).toList();
  }

  /// True when the items on this voucher carry an HSN code worth a column.
  bool get _hasHsn => detail.inventoryEntries
      .any((VoucherStockLine line) => (line.hsnCode ?? '').trim().isNotEmpty);

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(
      title: '$title ${detail.voucherNumber ?? ''}'.trim(),
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
          _parties(),
          if (detail.isExcluded) _excludedNotice(),
          if (detail.inventoryEntries.isNotEmpty) _itemTable(),
          if (detail.inventoryEntries.isEmpty) _postingTable(),
          if (detail.inventoryEntries.isNotEmpty && _charges.isNotEmpty) _chargeTable(),
          _totalBlock(),
          if (detail.inventoryEntries.isNotEmpty) _postingTable(dense: true),
          if (detail.inventoryOmitted) _itemsOmittedNotice(),
          if (_notes.isNotEmpty) _notesBlock(),
          _signature(),
        ],
      ),
    );

    return document.save();
  }

  // ------------------------------------------------------------------
  // Header
  // ------------------------------------------------------------------

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
                  pw.Text(title, style: DocumentStyle.title),
                  pw.Text(
                    'Amounts in ${detail.amount.currency}',
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
                    pw.Text(companyName, style: DocumentStyle.heading),
                    if ((companyGstin ?? '').isNotEmpty) ...<pw.Widget>[
                      pw.SizedBox(height: 2),
                      pw.Text('GSTIN/UIN: $companyGstin', style: DocumentStyle.body),
                    ],
                  ],
                ),
              ),
              fields: <pw.Widget>[
                DocumentStyle.field('Voucher No.', detail.voucherNumber),
                DocumentStyle.field('Dated', DocumentStyle.date(detail.date)),
                DocumentStyle.field('Reference', detail.reference),
              ],
            ),
          ],
        ),
      );

  pw.Widget _parties() {
    final PartyDetails? party = detail.partyDetails;
    final String? name = party?.name.isNotEmpty == true ? party!.name : detail.party;
    if (name == null || name.trim().isEmpty) return pw.SizedBox();

    // "Party" rather than "Buyer": the same document shape carries a purchase,
    // a receipt and a payment, and on three of those the other side is not a
    // buyer. Naming it wrongly on a bill somebody forwards is worse than
    // naming it generically.
    return pw.Container(
      margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
      width: double.infinity,
      decoration: DocumentStyle.boxed,
      padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        children: <pw.Widget>[
          pw.Text('Party', style: DocumentStyle.label),
          pw.SizedBox(height: 2),
          pw.Text(name, style: DocumentStyle.heading),
          if (party != null) ...<pw.Widget>[
            for (final String line in party.address)
              pw.Text(line, style: DocumentStyle.body),
            if ((party.state ?? '').isNotEmpty)
              pw.Text('State Name: ${party.state}', style: DocumentStyle.body),
            if ((party.gstin ?? '').isNotEmpty)
              pw.Text('GSTIN/UIN: ${party.gstin}', style: DocumentStyle.bodyBold),
            if ((party.phone ?? '').isNotEmpty)
              pw.Text('Phone: ${party.phone}', style: DocumentStyle.body),
            if ((party.email ?? '').isNotEmpty)
              pw.Text('E-Mail: ${party.email}', style: DocumentStyle.body),
          ],
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Body
  // ------------------------------------------------------------------

  pw.Widget _itemTable() {
    final bool hsn = _hasHsn;
    return pw.Container(
      margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
      child: pw.Table(
        border: DocumentStyle.tableRules,
        columnWidths: <int, pw.TableColumnWidth>{
          0: const pw.FixedColumnWidth(20),
          1: const pw.FlexColumnWidth(4),
          if (hsn) 2: const pw.FixedColumnWidth(52),
          (hsn ? 3 : 2): const pw.FixedColumnWidth(58),
          (hsn ? 4 : 3): const pw.FixedColumnWidth(58),
          (hsn ? 5 : 4): const pw.FixedColumnWidth(74),
        },
        children: <pw.TableRow>[
          pw.TableRow(
            children: <pw.Widget>[
              DocumentStyle.headerCell('Sl'),
              DocumentStyle.headerCell('Description'),
              if (hsn) DocumentStyle.headerCell('HSN/SAC'),
              DocumentStyle.headerCell('Quantity', align: pw.TextAlign.right),
              DocumentStyle.headerCell('Rate', align: pw.TextAlign.right),
              DocumentStyle.headerCell('Amount', align: pw.TextAlign.right),
            ],
          ),
          for (int i = 0; i < detail.inventoryEntries.length; i++)
            _itemRow(i + 1, detail.inventoryEntries[i], hsn: hsn),
        ],
      ),
    );
  }

  pw.TableRow _itemRow(int index, VoucherStockLine line, {required bool hsn}) {
    final List<String> under = <String?>[line.godown, line.batch]
        .whereType<String>()
        .where((String value) => value.trim().isNotEmpty)
        .toList();

    return pw.TableRow(
      children: <pw.Widget>[
        DocumentStyle.cell('$index', align: pw.TextAlign.center),
        pw.Padding(
          padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
          child: pw.Column(
            crossAxisAlignment: pw.CrossAxisAlignment.start,
            children: <pw.Widget>[
              pw.Text(line.item, style: DocumentStyle.bodyBold),
              if (under.isNotEmpty)
                pw.Text(under.join(' | '), style: DocumentStyle.footnote),
            ],
          ),
        ),
        if (hsn) DocumentStyle.cell(line.hsnCode ?? ''),
        DocumentStyle.cell(
          MoneyFormat.quantity(line.quantity, line.unit),
          align: pw.TextAlign.right,
        ),
        DocumentStyle.cell(
          line.rate == null ? '' : DocumentStyle.amount(line.rate!),
          align: pw.TextAlign.right,
        ),
        DocumentStyle.cell(
          DocumentStyle.amount(line.amount),
          align: pw.TextAlign.right,
          bold: true,
        ),
      ],
    );
  }

  /// Tax, freight, discount and round-off, under the items they apply to.
  pw.Widget _chargeTable() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        child: pw.Table(
          border: DocumentStyle.tableRules,
          columnWidths: <int, pw.TableColumnWidth>{
            0: const pw.FlexColumnWidth(4),
            1: const pw.FixedColumnWidth(74),
          },
          children: <pw.TableRow>[
            for (final VoucherLedgerLine line in _charges)
              pw.TableRow(
                children: <pw.Widget>[
                  DocumentStyle.cell(
                    line.ledger,
                    bold: _taxLedger.hasMatch(line.ledger),
                  ),
                  DocumentStyle.cell(
                    DocumentStyle.amount(line.amount),
                    align: pw.TextAlign.right,
                  ),
                ],
              ),
          ],
        ),
      );

  /// Every ledger line with its side, which is what the voucher actually is.
  ///
  /// On an invoice it sits below the total as the supporting detail; on a
  /// receipt, payment or journal there is nothing else to show and it is the
  /// body of the document. Either way it is never dropped: this is the part an
  /// accountant checks against Tally, and a document that omitted it would be a
  /// summary rather than a record.
  pw.Widget _postingTable({bool dense = false}) => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap + 2),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: <pw.Widget>[
            if (dense) ...<pw.Widget>[
              pw.Text('Accounting entries', style: DocumentStyle.label),
              pw.SizedBox(height: 2),
            ],
            pw.Table(
              border: DocumentStyle.tableRules,
              columnWidths: <int, pw.TableColumnWidth>{
                0: const pw.FlexColumnWidth(4),
                1: const pw.FixedColumnWidth(34),
                2: const pw.FixedColumnWidth(80),
              },
              children: <pw.TableRow>[
                pw.TableRow(
                  children: <pw.Widget>[
                    DocumentStyle.headerCell('Particulars'),
                    DocumentStyle.headerCell('Dr/Cr', align: pw.TextAlign.center),
                    DocumentStyle.headerCell('Amount', align: pw.TextAlign.right),
                  ],
                ),
                for (final VoucherLedgerLine line in detail.ledgerEntries)
                  pw.TableRow(
                    children: <pw.Widget>[
                      pw.Padding(
                        padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
                        child: pw.Column(
                          crossAxisAlignment: pw.CrossAxisAlignment.start,
                          children: <pw.Widget>[
                            pw.Text(
                              line.ledger,
                              style: line.isParty
                                  ? DocumentStyle.bodyBold
                                  : DocumentStyle.body,
                            ),
                            if (line.billReferences.isNotEmpty)
                              pw.Text(
                                'Bill: ${line.billReferences.join(', ')}',
                                style: DocumentStyle.footnote,
                              ),
                            if ((line.costCentre ?? '').isNotEmpty)
                              pw.Text(line.costCentre!, style: DocumentStyle.footnote),
                          ],
                        ),
                      ),
                      // Dr and Cr, spelled. An accountant reads the words;
                      // nobody reads a minus sign and concludes "credit".
                      DocumentStyle.cell(
                        line.amount.side == MoneySide.debit ? 'Dr' : 'Cr',
                        align: pw.TextAlign.center,
                      ),
                      DocumentStyle.cell(
                        DocumentStyle.amount(line.amount),
                        align: pw.TextAlign.right,
                      ),
                    ],
                  ),
                pw.TableRow(
                  children: <pw.Widget>[
                    DocumentStyle.cell('Total', bold: true),
                    DocumentStyle.cell('Dr', align: pw.TextAlign.center),
                    DocumentStyle.cell(
                      DocumentStyle.amount(detail.debitTotal),
                      align: pw.TextAlign.right,
                      bold: true,
                    ),
                  ],
                ),
                pw.TableRow(
                  children: <pw.Widget>[
                    DocumentStyle.cell(''),
                    DocumentStyle.cell('Cr', align: pw.TextAlign.center),
                    DocumentStyle.cell(
                      DocumentStyle.amount(detail.creditTotal),
                      align: pw.TextAlign.right,
                      bold: true,
                    ),
                  ],
                ),
              ],
            ),
          ],
        ),
      );

  pw.Widget _totalBlock() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        decoration: DocumentStyle.boxed,
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.stretch,
          children: <pw.Widget>[
            pw.Container(
              padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
              child: pw.Row(
                mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
                children: <pw.Widget>[
                  pw.Text('Total', style: DocumentStyle.heading),
                  pw.Text(
                    DocumentStyle.amount(detail.amount),
                    style: DocumentStyle.title,
                  ),
                ],
              ),
            ),
            pw.Divider(height: 0.6, color: DocumentStyle.rule),
            pw.Container(
              padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
              child: pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.start,
                children: <pw.Widget>[
                  pw.Text('Amount chargeable (in words)', style: DocumentStyle.label),
                  pw.SizedBox(height: 2),
                  pw.Text(
                    AmountInWords.rupees(
                      detail.amount.amount,
                      currency: detail.amount.currency,
                    ),
                    style: DocumentStyle.bodyBold,
                  ),
                ],
              ),
            ),
          ],
        ),
      );

  // ------------------------------------------------------------------
  // Notes and signature
  // ------------------------------------------------------------------

  /// Narration only. Bill references are printed against the lines they belong
  /// to in the accounting table, and repeating them in a box at the bottom
  /// reads as a second, different set of references.
  List<(String, String)> get _notes => <(String, String)>[
        if ((detail.narration ?? '').trim().isNotEmpty) ('Narration', detail.narration!),
      ];

  pw.Widget _notesBlock() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        width: double.infinity,
        decoration: DocumentStyle.boxed,
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 5),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: <pw.Widget>[
            for (final (String label, String value) in _notes) ...<pw.Widget>[
              pw.Text(label, style: DocumentStyle.label),
              pw.Text(value, style: DocumentStyle.body),
              pw.SizedBox(height: 3),
            ],
          ],
        ),
      );

  /// A cancelled or optional voucher counts towards nothing anywhere in the
  /// product. A document of one that did not say so would circulate as a bill.
  pw.Widget _excludedNotice() => _notice(
        'This voucher is marked ${detail.isCancelled ? 'cancelled' : 'optional'} in '
        'TallyPrime. It is not part of any total in these books.',
      );

  pw.Widget _itemsOmittedNotice() => _notice(
        'Stock lines are not kept for a voucher this old, so no item detail is '
        'shown. The accounting entries above are complete.',
      );

  pw.Widget _notice(String text) => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap),
        width: double.infinity,
        decoration: DocumentStyle.boxed,
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: pw.Text(text, style: DocumentStyle.bodyBold),
      );

  pw.Widget _signature() => pw.Container(
        margin: const pw.EdgeInsets.only(top: DocumentStyle.gap + 6),
        alignment: pw.Alignment.centerRight,
        child: pw.SizedBox(
          width: 180,
          child: pw.Column(
            crossAxisAlignment: pw.CrossAxisAlignment.end,
            children: <pw.Widget>[
              pw.Text('for $companyName', style: DocumentStyle.bodyBold),
              pw.SizedBox(height: 26),
              pw.Text('Authorised Signatory', style: DocumentStyle.footnote),
            ],
          ),
        ),
      );

  static Money _sum(Iterable<Money> values) {
    Decimal total = Decimal.zero;
    String currency = 'INR';
    for (final Money value in values) {
      total += value.amount;
      currency = value.currency;
    }
    return Money(
      amount: total,
      side: MoneySide.debit,
      signed: total,
      currency: currency,
    );
  }
}
