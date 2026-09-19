import 'package:decimal/decimal.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

import '../../features/reports/domain/drilldown.dart';
import '../money/money.dart';
import '../money/money_format.dart';
import 'amount_in_words.dart';
import 'document_style.dart';

/// A sales voucher, drawn as the Tax Invoice TallyPrime itself prints.
///
/// The layout follows Tally's print cell for cell -- seller beside a grid of
/// invoice fields, buyer beside the dispatch grid, a ruled item table with the
/// tax ledgers under the goods, the total in words, an HSN-wise tax summary and
/// the declaration beside the signature. A customer compares this against the
/// other invoices in their chat, and one laid out differently reads as a copy.
///
/// Tally's grid keeps its captions even where the voucher leaves a field
/// empty, so this does too: an empty "Delivery Note" box is what the shop's own
/// print shows. Figures are different -- the tax summary is printed only when
/// it can be reconciled to the tax ledgers actually posted, because a rate
/// table that disagrees with the lines above it is a wrong invoice.
class TaxInvoiceDocument {
  const TaxInvoiceDocument({
    required this.detail,
    required this.companyName,
    this.companyGstin,
  });

  final VoucherDetail detail;
  final String companyName;
  final String? companyGstin;

  static final RegExp _cgst = RegExp(r'\bcgst\b', caseSensitive: false);
  static final RegExp _sgst = RegExp(r'\b(s|ut)gst\b', caseSensitive: false);
  static final RegExp _igst = RegExp(r'\bigst\b', caseSensitive: false);
  static final RegExp _anyTax =
      RegExp(r'\b(c|s|i|ut)?gst\b|\bcess\b|\btax\b', caseSensitive: false);
  static final RegExp _roundOff = RegExp(r'round', caseSensitive: false);

  static const PdfColor _ink = PdfColors.black;
  static const pw.BorderSide _side = pw.BorderSide(color: _ink, width: 0.7);

  static pw.TextStyle get _caption => const pw.TextStyle(fontSize: 7.5, color: _ink);
  static pw.TextStyle get _small => const pw.TextStyle(fontSize: 7, color: DocumentStyle.faint);

  bool get _hasItems => detail.inventoryEntries.isNotEmpty;

  // ------------------------------------------------------------------
  // What goes on the page
  // ------------------------------------------------------------------

  /// A voucher's party line is not always flagged: the backend marks it when
  /// the ledger master says so, and a snapshot that has not landed leaves only
  /// the name to match on.
  bool _isParty(VoucherLedgerLine line) =>
      line.isParty || (detail.party != null && line.ledger == detail.party);

  List<VoucherLedgerLine> get _postings =>
      detail.ledgerEntries.where((VoucherLedgerLine l) => !_isParty(l)).toList();

  bool _isCharge(VoucherLedgerLine line) =>
      _anyTax.hasMatch(line.ledger) || _roundOff.hasMatch(line.ledger);

  /// The side the goods sit on. A line on the other side -- a round-off that
  /// took paise off, a discount ledger -- reduces the bill and is printed so.
  MoneySide get _billSide {
    for (final VoucherLedgerLine line in detail.ledgerEntries) {
      if (_isParty(line)) {
        return line.amount.side == MoneySide.debit ? MoneySide.credit : MoneySide.debit;
      }
    }
    return MoneySide.credit;
  }

  /// Ledger lines printed as the rows under the goods.
  ///
  /// With stock lines, every posting except the one carrying the goods --
  /// found by value, as Tally's own print does, since shops name the sales
  /// ledger freely. When no single posting matches (goods split across two
  /// sales ledgers), only tax and round-off are shown, because printing the
  /// sales ledgers too would list the goods twice.
  List<VoucherLedgerLine> get _charges {
    if (!_hasItems) return _postings.where(_isCharge).toList();
    final Decimal items = _sum(detail.inventoryEntries.map((VoucherStockLine l) => l.amount));
    final List<VoucherLedgerLine> postings = _postings;
    final int goods = postings.indexWhere(
      (VoucherLedgerLine l) => !_isCharge(l) && l.amount.amount == items,
    );
    if (goods < 0) return postings.where(_isCharge).toList();
    return <VoucherLedgerLine>[
      for (int i = 0; i < postings.length; i++)
        if (i != goods) postings[i],
    ];
  }

  Decimal _ledgerTotal(RegExp pattern) => _sum(
        _postings
            .where((VoucherLedgerLine l) => pattern.hasMatch(l.ledger))
            .map((VoucherLedgerLine l) => l.amount),
      );

  Future<List<int>> build() async {
    final pw.Document document = pw.Document(
      title: 'Tax Invoice ${detail.voucherNumber ?? ''}'.trim(),
      author: companyName,
    );

    final _TaxSummary? tax = _taxSummary();

    document.addPage(
      pw.MultiPage(
        pageTheme: DocumentStyle.page(),
        footer: (pw.Context context) => pw.Container(
          margin: const pw.EdgeInsets.only(top: 6),
          alignment: pw.Alignment.center,
          child: pw.Text(
            context.pagesCount > 1
                ? 'This is a computer generated document.  Page ${context.pageNumber} of ${context.pagesCount}'
                : 'This is a computer generated document.',
            style: DocumentStyle.footnote,
          ),
        ),
        build: (pw.Context context) => <pw.Widget>[
          _title(),
          if (detail.isExcluded)
            _notice(
              'This voucher is marked ${detail.isCancelled ? 'cancelled' : 'optional'} in '
              'TallyPrime. It is not part of any total in these books.',
            ),
          _header(),
          _itemTable(),
          _amountInWords(),
          if (tax != null) _taxTable(tax),
          if (detail.inventoryOmitted)
            _notice(
              'Stock lines are not kept for a voucher this old, so no item detail is shown.',
            ),
          _footerBlock(tax),
        ],
      ),
    );

    return document.save();
  }

  // ------------------------------------------------------------------
  // Title and header grid
  // ------------------------------------------------------------------

  pw.Widget _title() => pw.Padding(
        padding: const pw.EdgeInsets.only(bottom: 4),
        child: pw.Stack(
          children: <pw.Widget>[
            pw.Center(
              child: pw.Text(
                'Tax Invoice',
                style: const pw.TextStyle(fontSize: 15, color: _ink, letterSpacing: 0.6),
              ),
            ),
            pw.Positioned(
              right: 0,
              top: 4,
              child: pw.Text(
                '(ORIGINAL FOR RECIPIENT)',
                style: pw.TextStyle(
                  fontSize: 7.5,
                  fontWeight: pw.FontWeight.bold,
                  fontStyle: pw.FontStyle.italic,
                  color: _ink,
                ),
              ),
            ),
          ],
        ),
      );

  pw.Widget _header() => pw.Table(
        border: const pw.TableBorder(
          left: _side,
          right: _side,
          top: _side,
          bottom: _side,
          verticalInside: _side,
          horizontalInside: _side,
        ),
        columnWidths: const <int, pw.TableColumnWidth>{
          0: pw.FlexColumnWidth(1),
          1: pw.FlexColumnWidth(1),
        },
        children: <pw.TableRow>[
          pw.TableRow(
            children: <pw.Widget>[
              _seller(),
              _grid(<(String, String?)>[
                ('Invoice No.', detail.voucherNumber),
                ('Dated', DocumentStyle.date(detail.date)),
                ('Delivery Note', null),
                ('Mode/Terms of Payment', null),
                ('Reference No. & Date', detail.reference),
                ('Other References', null),
              ]),
            ],
          ),
          pw.TableRow(
            children: <pw.Widget>[
              _buyer(),
              _grid(const <(String, String?)>[
                ("Buyer's Order No.", null),
                ('Dated', null),
                ('Dispatch Doc No.', null),
                ('Delivery Note Date', null),
                ('Dispatched through', null),
                ('Destination', null),
                ('Bill of Lading/LR-RR No.', null),
                ('Motor Vehicle No.', null),
              ]),
            ],
          ),
        ],
      );

  pw.Widget _seller() => pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: <pw.Widget>[
            pw.Text(
              companyName,
              style: pw.TextStyle(fontSize: 11, fontWeight: pw.FontWeight.bold, color: _ink),
            ),
            if ((companyGstin ?? '').isNotEmpty) ...<pw.Widget>[
              pw.SizedBox(height: 2),
              pw.Text('GSTIN/UIN: $companyGstin', style: DocumentStyle.bodyBold),
              if (_stateCode(companyGstin) != null)
                pw.Text('State Code: ${_stateCode(companyGstin)}', style: DocumentStyle.body),
            ],
          ],
        ),
      );

  pw.Widget _buyer() {
    final PartyDetails? party = detail.partyDetails;
    final String name = party?.name.isNotEmpty == true ? party!.name : (detail.party ?? '');
    final String? code = _stateCode(party?.gstin);
    return pw.Padding(
      padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        children: <pw.Widget>[
          pw.Text('Buyer (Bill to)', style: _caption),
          if (name.trim().isNotEmpty) pw.Text(name, style: DocumentStyle.bodyBold),
          if (party != null) ...<pw.Widget>[
            for (final String line in party.address) pw.Text(line, style: DocumentStyle.body),
            if ((party.gstin ?? '').isNotEmpty)
              pw.Text('GSTIN/UIN: ${party.gstin}', style: DocumentStyle.body),
            if ((party.state ?? '').isNotEmpty || code != null)
              pw.Text(
                <String>[
                  if ((party.state ?? '').isNotEmpty) 'State Name: ${party.state}',
                  if (code != null) 'Code: $code',
                ].join(', '),
                style: DocumentStyle.body,
              ),
          ],
        ],
      ),
    );
  }

  /// The first two digits of a GSTIN are the state code by construction, so
  /// this is read off the registration rather than looked up or guessed.
  static String? _stateCode(String? gstin) {
    final String value = (gstin ?? '').trim();
    if (value.length < 2 || int.tryParse(value.substring(0, 2)) == null) return null;
    return value.substring(0, 2);
  }

  pw.Widget _grid(List<(String, String?)> fields) => pw.Table(
        border: const pw.TableBorder(verticalInside: _side, horizontalInside: _side),
        columnWidths: const <int, pw.TableColumnWidth>{
          0: pw.FlexColumnWidth(1),
          1: pw.FlexColumnWidth(1),
        },
        children: <pw.TableRow>[
          for (int i = 0; i < fields.length; i += 2)
            pw.TableRow(
              children: <pw.Widget>[
                _field(fields[i]),
                _field(fields[i + 1]),
              ],
            ),
        ],
      );

  pw.Widget _field((String, String?) field) => pw.Container(
        constraints: const pw.BoxConstraints(minHeight: 22),
        padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
        child: pw.Column(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          mainAxisSize: pw.MainAxisSize.min,
          children: <pw.Widget>[
            pw.Text(field.$1, style: _caption),
            if ((field.$2 ?? '').trim().isNotEmpty)
              pw.Text(field.$2!, style: DocumentStyle.bodyBold),
          ],
        ),
      );

  // ------------------------------------------------------------------
  // Items
  // ------------------------------------------------------------------

  pw.Widget _itemTable() {
    final List<VoucherLedgerLine> charges = _charges;
    final List<VoucherLedgerLine> ledgerGoods =
        _hasItems ? const <VoucherLedgerLine>[] : _postings.where((l) => !_isCharge(l)).toList();
    final int rowCount =
        detail.inventoryEntries.length + ledgerGoods.length + charges.length;

    final String totalQuantity = _hasItems
        ? MoneyFormat.quantity(
            detail.inventoryEntries
                .fold<double>(0, (double t, VoucherStockLine l) => t + l.quantity.abs()),
            null,
          )
        : '';

    return pw.Table(
      border: const pw.TableBorder(
        left: _side,
        right: _side,
        top: _side,
        bottom: _side,
        verticalInside: _side,
      ),
      columnWidths: const <int, pw.TableColumnWidth>{
        0: pw.FixedColumnWidth(22),
        1: pw.FlexColumnWidth(1),
        2: pw.FixedColumnWidth(50),
        3: pw.FixedColumnWidth(50),
        4: pw.FixedColumnWidth(56),
        5: pw.FixedColumnWidth(28),
        6: pw.FixedColumnWidth(32),
        7: pw.FixedColumnWidth(72),
      },
      children: <pw.TableRow>[
        pw.TableRow(
          decoration: const pw.BoxDecoration(
            color: DocumentStyle.wash,
            border: pw.Border(bottom: _side),
          ),
          children: <pw.Widget>[
            _head('Sl No.'),
            _head(_hasItems ? 'Description of Goods' : 'Particulars'),
            _head('HSN/SAC'),
            _head('Quantity', align: pw.TextAlign.right),
            _head('Rate', align: pw.TextAlign.right),
            _head('per'),
            _head('Disc. %', align: pw.TextAlign.right),
            _head('Amount', align: pw.TextAlign.right),
          ],
        ),
        for (int i = 0; i < detail.inventoryEntries.length; i++)
          _itemRow(i + 1, detail.inventoryEntries[i]),
        for (int i = 0; i < ledgerGoods.length; i++)
          _ledgerRow(i + 1, ledgerGoods[i], charge: false),
        for (int i = 0; i < charges.length; i++) _ledgerRow(i + 1, charges[i], charge: true),
        // Tally's print keeps the item table tall with its column rules running
        // down to the total; a short invoice without it reads as cut off.
        if (rowCount < 12)
          pw.TableRow(
            children: <pw.Widget>[
              pw.SizedBox(height: (12 - rowCount) * 14.0),
              for (int i = 0; i < 7; i++) pw.SizedBox(),
            ],
          ),
        pw.TableRow(
          decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
          children: <pw.Widget>[
            pw.SizedBox(),
            DocumentStyle.cell('Total', align: pw.TextAlign.right, bold: true),
            pw.SizedBox(),
            DocumentStyle.cell(totalQuantity, align: pw.TextAlign.right, bold: true),
            pw.SizedBox(),
            pw.SizedBox(),
            pw.SizedBox(),
            DocumentStyle.cell(
              DocumentStyle.amount(detail.amount),
              align: pw.TextAlign.right,
              style: pw.TextStyle(fontSize: 9.5, fontWeight: pw.FontWeight.bold, color: _ink),
            ),
          ],
        ),
      ],
    );
  }

  pw.Widget _head(String text, {pw.TextAlign align = pw.TextAlign.left}) => pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 3, vertical: 4),
        child: pw.Text(
          text,
          textAlign: align,
          style: pw.TextStyle(fontSize: 7.5, fontWeight: pw.FontWeight.bold, color: _ink),
        ),
      );

  pw.TableRow _itemRow(int index, VoucherStockLine line) {
    final List<String> under = <String?>[line.godown, line.batch]
        .whereType<String>()
        .where((String value) => value.trim().isNotEmpty)
        .toList();
    return pw.TableRow(
      children: <pw.Widget>[
        DocumentStyle.cell('$index'),
        pw.Padding(
          padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
          child: pw.Column(
            crossAxisAlignment: pw.CrossAxisAlignment.start,
            children: <pw.Widget>[
              pw.Text(line.item, style: DocumentStyle.bodyBold),
              if (under.isNotEmpty) pw.Text(under.join(' | '), style: _small),
            ],
          ),
        ),
        DocumentStyle.cell(line.hsnCode ?? ''),
        DocumentStyle.cell(
          // The unit has its own "per" column, as on Tally's print.
          MoneyFormat.quantity(line.quantity.abs(), null),
          align: pw.TextAlign.right,
          bold: true,
        ),
        DocumentStyle.cell(
          line.rate == null ? '' : DocumentStyle.amount(line.rate!),
          align: pw.TextAlign.right,
        ),
        DocumentStyle.cell(line.unit ?? ''),
        DocumentStyle.cell(_discount(line), align: pw.TextAlign.right),
        DocumentStyle.cell(
          DocumentStyle.amount(line.amount),
          align: pw.TextAlign.right,
          bold: true,
        ),
      ],
    );
  }

  pw.TableRow _ledgerRow(int index, VoucherLedgerLine line, {required bool charge}) {
    final bool reduces = charge && line.amount.side != _billSide;
    return pw.TableRow(
      children: <pw.Widget>[
        // Tax and round-off are not goods, so they take no serial number.
        DocumentStyle.cell(charge ? '' : '$index'),
        DocumentStyle.cell(
          line.ledger,
          align: charge ? pw.TextAlign.right : pw.TextAlign.left,
          bold: true,
        ),
        pw.SizedBox(),
        pw.SizedBox(),
        pw.SizedBox(),
        pw.SizedBox(),
        pw.SizedBox(),
        DocumentStyle.cell(
          '${reduces ? '(-)' : ''}${DocumentStyle.amount(line.amount)}',
          align: pw.TextAlign.right,
          bold: true,
        ),
      ],
    );
  }

  /// Tally stores the rate and the line's net amount, not the discount, so the
  /// percentage is read back from the two. Left blank when that cannot be a
  /// discount at all -- an item billed in an alternate unit makes rate x
  /// quantity meaningless, and a negative or 100%+ figure would say so loudly.
  static String _discount(VoucherStockLine line) {
    if (line.rate == null || line.quantity == 0) return '';
    final double gross = line.rate!.amount.toDouble() * line.quantity.abs();
    if (gross <= 0) return '';
    final double percent = (1 - line.amount.amount.toDouble() / gross) * 100;
    if (percent.abs() < 0.005) return '';
    if (percent < 0 || percent >= 100) return '';
    return percent.toStringAsFixed(2);
  }

  pw.Widget _amountInWords() => pw.Container(
        decoration: const pw.BoxDecoration(
          border: pw.Border(left: _side, right: _side, bottom: _side),
        ),
        padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4),
        child: pw.Row(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: <pw.Widget>[
            pw.Expanded(
              child: pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.start,
                children: <pw.Widget>[
                  pw.Text('Amount Chargeable (in words)', style: _caption),
                  pw.Text(
                    AmountInWords.rupees(detail.amount.amount, currency: detail.amount.currency),
                    style: DocumentStyle.bodyBold,
                  ),
                ],
              ),
            ),
            pw.Text('E. & O.E', style: DocumentStyle.bodyBold),
          ],
        ),
      );

  // ------------------------------------------------------------------
  // HSN-wise tax summary
  // ------------------------------------------------------------------

  _TaxSummary? _taxSummary() {
    final Decimal cgst = _ledgerTotal(_cgst);
    final Decimal sgst = _ledgerTotal(_sgst);
    final Decimal igst = _ledgerTotal(_igst);
    if (cgst == Decimal.zero && sgst == Decimal.zero && igst == Decimal.zero) return null;

    final Map<String, Decimal> taxable = <String, Decimal>{};
    final Map<String, double?> rates = <String, double?>{};
    if (_hasItems) {
      for (final VoucherStockLine line in detail.inventoryEntries) {
        final String hsn = (line.hsnCode ?? '').trim();
        taxable[hsn] = (taxable[hsn] ?? Decimal.zero) + line.amount.amount;
        // One HSN billed at two rates cannot be split from the ledger totals.
        rates[hsn] = rates.containsKey(hsn) && rates[hsn] != line.gstRate ? null : line.gstRate;
      }
    } else {
      taxable[''] = _sum(
        _postings.where((VoucherLedgerLine l) => !_isCharge(l)).map((l) => l.amount),
      );
      rates[''] = null;
    }

    final List<_TaxRow> rows = <_TaxRow>[];
    if (taxable.length == 1) {
      // A single HSN takes the posted ledgers whole, so the table cannot
      // disagree with the lines above it by a paisa.
      final MapEntry<String, Decimal> only = taxable.entries.single;
      if (only.value == Decimal.zero) return null;
      double rate(Decimal tax) => (tax / only.value).toDouble() * 100;
      rows.add((
        hsn: only.key,
        taxable: only.value,
        centralRate: rate(cgst),
        central: cgst,
        stateRate: rate(sgst),
        state: sgst,
        integratedRate: rate(igst),
        integrated: igst,
      ));
    } else {
      for (final MapEntry<String, Decimal> group in taxable.entries) {
        final double? rate = rates[group.key];
        if (rate == null) return null;
        final bool local = igst == Decimal.zero;
        rows.add((
          hsn: group.key,
          taxable: group.value,
          centralRate: local ? rate / 2 : 0,
          central: local ? _percent(group.value, rate / 2) : Decimal.zero,
          stateRate: local ? rate / 2 : 0,
          state: local ? _percent(group.value, rate / 2) : Decimal.zero,
          integratedRate: local ? 0 : rate,
          integrated: local ? Decimal.zero : _percent(group.value, rate),
        ));
      }
      // Computed per HSN from the master's rate, so it must still land on what
      // was posted. A rupee of drift is Tally rounding per line; more than that
      // means the rates on the masters are not the rates this bill was taxed at.
      bool close(Decimal a, Decimal b) => (a - b).abs() <= Decimal.one;
      if (!close(_sumOf(rows, (r) => r.central), cgst) ||
          !close(_sumOf(rows, (r) => r.state), sgst) ||
          !close(_sumOf(rows, (r) => r.integrated), igst)) {
        return null;
      }
    }

    return _TaxSummary(
      rows: rows,
      local: cgst != Decimal.zero || sgst != Decimal.zero,
      interstate: igst != Decimal.zero,
      taxable: _sumOf(rows, (r) => r.taxable),
      central: cgst,
      state: sgst,
      integrated: igst,
    );
  }

  pw.Widget _taxTable(_TaxSummary tax) {
    final List<String> groups = <String>[
      if (tax.local) ...<String>['Central Tax', 'State Tax'],
      if (tax.interstate) 'Integrated Tax',
    ];
    final Map<int, pw.TableColumnWidth> widths = <int, pw.TableColumnWidth>{
      0: const pw.FlexColumnWidth(1),
      1: const pw.FixedColumnWidth(72),
      for (int i = 0; i < groups.length; i++) 2 + i: const pw.FixedColumnWidth(112),
      2 + groups.length: const pw.FixedColumnWidth(72),
    };

    List<(double, Decimal)> parts(_TaxRow row) => <(double, Decimal)>[
          if (tax.local) ...<(double, Decimal)>[
            (row.centralRate, row.central),
            (row.stateRate, row.state),
          ],
          if (tax.interstate) (row.integratedRate, row.integrated),
        ];

    return pw.Table(
      border: const pw.TableBorder(left: _side, right: _side, bottom: _side, verticalInside: _side),
      columnWidths: widths,
      children: <pw.TableRow>[
        pw.TableRow(
          decoration: const pw.BoxDecoration(border: pw.Border(bottom: _side)),
          children: <pw.Widget>[
            _head('HSN/SAC', align: pw.TextAlign.center),
            _head('Taxable Value', align: pw.TextAlign.center),
            for (final String group in groups)
              pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.stretch,
                children: <pw.Widget>[
                  _head(group, align: pw.TextAlign.center),
                  pw.Container(
                    decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
                    child: _split(_head('Rate', align: pw.TextAlign.center),
                        _head('Amount', align: pw.TextAlign.center)),
                  ),
                ],
              ),
            _head('Total Tax Amount', align: pw.TextAlign.center),
          ],
        ),
        for (final _TaxRow row in tax.rows)
          pw.TableRow(
            children: <pw.Widget>[
              DocumentStyle.cell(row.hsn),
              DocumentStyle.cell(_amount(row.taxable), align: pw.TextAlign.right),
              for (final (double rate, Decimal amount) in parts(row))
                _split(
                  DocumentStyle.cell('${rate.toStringAsFixed(2)}%', align: pw.TextAlign.right),
                  DocumentStyle.cell(_amount(amount), align: pw.TextAlign.right),
                ),
              DocumentStyle.cell(
                _amount(row.central + row.state + row.integrated),
                align: pw.TextAlign.right,
              ),
            ],
          ),
        pw.TableRow(
          decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
          children: <pw.Widget>[
            DocumentStyle.cell('Total', align: pw.TextAlign.right, bold: true),
            DocumentStyle.cell(_amount(tax.taxable), align: pw.TextAlign.right, bold: true),
            for (final Decimal amount in <Decimal>[
              if (tax.local) ...<Decimal>[tax.central, tax.state],
              if (tax.interstate) tax.integrated,
            ])
              _split(
                pw.SizedBox(),
                DocumentStyle.cell(_amount(amount), align: pw.TextAlign.right, bold: true),
              ),
            DocumentStyle.cell(
              _amount(tax.central + tax.state + tax.integrated),
              align: pw.TextAlign.right,
              bold: true,
            ),
          ],
        ),
      ],
    );
  }

  pw.Widget _split(pw.Widget rate, pw.Widget amount) => pw.Row(
        children: <pw.Widget>[
          pw.Expanded(flex: 4, child: rate),
          pw.Expanded(
            flex: 5,
            child: pw.Container(
              decoration: const pw.BoxDecoration(border: pw.Border(left: _side)),
              child: amount,
            ),
          ),
        ],
      );

  // ------------------------------------------------------------------
  // Declaration and signature
  // ------------------------------------------------------------------

  pw.Widget _footerBlock(_TaxSummary? tax) => pw.Table(
        border: const pw.TableBorder(
          left: _side,
          right: _side,
          bottom: _side,
          verticalInside: _side,
          horizontalInside: _side,
        ),
        columnWidths: const <int, pw.TableColumnWidth>{
          0: pw.FlexColumnWidth(55),
          1: pw.FlexColumnWidth(45),
        },
        children: <pw.TableRow>[
          if (tax != null)
            pw.TableRow(
              children: <pw.Widget>[
                pw.Padding(
                  padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4),
                  child: pw.Text(
                    'Tax Amount (in words): ${AmountInWords.rupees(
                      tax.central + tax.state + tax.integrated,
                      currency: detail.amount.currency,
                    )}',
                    style: DocumentStyle.bodyBold,
                  ),
                ),
                pw.SizedBox(),
              ],
            ),
          pw.TableRow(
            children: <pw.Widget>[
              pw.Padding(
                padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4),
                child: pw.Column(
                  crossAxisAlignment: pw.CrossAxisAlignment.start,
                  children: <pw.Widget>[
                    pw.Text(
                      'Declaration:',
                      style: const pw.TextStyle(
                        fontSize: 8,
                        color: _ink,
                        decoration: pw.TextDecoration.underline,
                      ),
                    ),
                    pw.Text(
                      'We declare that this invoice shows the actual price of the '
                      '${_hasItems ? 'goods' : 'services'} described and that all '
                      'particulars are true and correct.',
                      style: DocumentStyle.body,
                    ),
                  ],
                ),
              ),
              pw.Padding(
                padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4),
                child: pw.Column(
                  crossAxisAlignment: pw.CrossAxisAlignment.end,
                  children: <pw.Widget>[
                    pw.Text('for $companyName', style: DocumentStyle.bodyBold),
                    pw.SizedBox(height: 26),
                    pw.Text('Authorised Signatory', style: DocumentStyle.body),
                  ],
                ),
              ),
            ],
          ),
        ],
      );

  pw.Widget _notice(String text) => pw.Container(
        margin: const pw.EdgeInsets.only(bottom: 4),
        width: double.infinity,
        decoration: const pw.BoxDecoration(border: pw.Border.fromBorderSide(_side)),
        padding: const pw.EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: pw.Text(text, style: DocumentStyle.bodyBold),
      );

  // ------------------------------------------------------------------
  // Arithmetic
  // ------------------------------------------------------------------

  String _amount(Decimal value) => DocumentStyle.amount(
        Money(
          amount: value.abs(),
          side: MoneySide.debit,
          signed: value,
          currency: detail.amount.currency,
        ),
      );

  static Decimal _percent(Decimal base, double rate) =>
      Decimal.parse((base.toDouble() * rate / 100).toStringAsFixed(2));

  static Decimal _sum(Iterable<Money> values) =>
      values.fold(Decimal.zero, (Decimal t, Money m) => t + m.amount);

  static Decimal _sumOf(List<_TaxRow> rows, Decimal Function(_TaxRow row) pick) =>
      rows.fold(Decimal.zero, (Decimal t, _TaxRow r) => t + pick(r));
}

typedef _TaxRow = ({
  String hsn,
  Decimal taxable,
  double centralRate,
  Decimal central,
  double stateRate,
  Decimal state,
  double integratedRate,
  Decimal integrated,
});

class _TaxSummary {
  const _TaxSummary({
    required this.rows,
    required this.local,
    required this.interstate,
    required this.taxable,
    required this.central,
    required this.state,
    required this.integrated,
  });

  final List<_TaxRow> rows;
  final bool local;
  final bool interstate;
  final Decimal taxable;
  final Decimal central;
  final Decimal state;
  final Decimal integrated;
}
