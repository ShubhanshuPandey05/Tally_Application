import 'package:decimal/decimal.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

import '../../features/reports/domain/drilldown.dart';
import '../money/money.dart';
import '../money/money_format.dart';
import 'amount_in_words.dart';
import 'document_style.dart';
import 'gst_state.dart';

/// A sales voucher, drawn as the Tax Invoice TallyPrime itself prints.
///
/// The layout follows Tally's print cell for cell -- seller, consignee and
/// buyer stacked beside the grid of invoice and dispatch fields, a ruled item
/// table with the tax ledgers under the goods, the total in words, an HSN-wise
/// tax summary, and the bank details and declaration beside the signature. It
/// was matched against Tally's own print of invoice 2025-26/01 in "D.D
/// Enterprises" (`tmp/Sales Invoice Format.pdf`). A customer compares this
/// against the other invoices in their chat, and one laid out differently
/// reads as a copy.
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

  /// The company as the app knows it. Used when the voucher did not come with
  /// the company's own profile -- an older connector, or one that has not been
  /// read yet -- and then it is the whole of the seller block.
  final String companyName;
  final String? companyGstin;

  CompanyDetails? get _company => detail.companyDetails;

  /// The name the business prints on paper, where Tally holds one.
  String get _sellerName =>
      (_company?.name.trim().isNotEmpty ?? false) ? _company!.name.trim() : companyName;

  String? get _sellerGstin {
    final String own = (_company?.gstin ?? '').trim();
    if (own.isNotEmpty) return own;
    final String given = (companyGstin ?? '').trim();
    return given.isEmpty ? null : given;
  }

  static final RegExp _cgst = RegExp(r'\bcgst\b', caseSensitive: false);
  static final RegExp _sgst = RegExp(r'\b(s|ut)gst\b', caseSensitive: false);
  static final RegExp _igst = RegExp(r'\bigst\b', caseSensitive: false);
  static final RegExp _anyTax =
      RegExp(r'\b(c|s|i|ut)?gst\b|\bcess\b|\btax\b', caseSensitive: false);
  static final RegExp _roundOff = RegExp(r'round', caseSensitive: false);

  static const PdfColor _ink = PdfColors.black;
  static const pw.BorderSide _side = pw.BorderSide(color: _ink, width: 0.7);

  static const pw.EdgeInsets _blockPadding = pw.EdgeInsets.symmetric(horizontal: 5, vertical: 4);

  static pw.TextStyle get _caption => const pw.TextStyle(fontSize: 7.5, color: _ink);
  static pw.TextStyle get _small => const pw.TextStyle(fontSize: 7, color: DocumentStyle.faint);
  static pw.TextStyle get _boldItalic => pw.TextStyle(
        fontSize: 8.5,
        fontWeight: pw.FontWeight.bold,
        fontStyle: pw.FontStyle.italic,
        color: _ink,
      );
  static pw.TextStyle get _italic =>
      pw.TextStyle(fontSize: 8.5, fontStyle: pw.FontStyle.italic, color: _ink);

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
    // By value alone, whatever the ledger is called. "Gst Sales" is an
    // ordinary name for a sales ledger and reads as a tax to any name test --
    // it printed as a charge under its own goods until this stopped asking.
    final int goods = postings.indexWhere(
      (VoucherLedgerLine l) => l.amount.side == _billSide && l.amount.amount == items,
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
      author: _sellerName,
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
                ? 'This is a Computer Generated Invoice.  Page ${context.pageNumber} of ${context.pagesCount}'
                : 'This is a Computer Generated Invoice',
            style: const pw.TextStyle(fontSize: 7.5, color: _ink),
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
        padding: const pw.EdgeInsets.only(bottom: 3),
        child: pw.Center(
          child: pw.Text(
            'Tax Invoice',
            style: pw.TextStyle(fontSize: 12, fontWeight: pw.FontWeight.bold, color: _ink),
          ),
        ),
      );

  /// One box, two columns: who the invoice is from and to on the left, the
  /// invoice and dispatch fields on the right.
  pw.Widget _header() {
    final InvoiceDetails? invoice = detail.invoice;
    final pw.Widget? consignee = _consignee();

    return pw.Table(
      border: const pw.TableBorder(
        left: _side,
        right: _side,
        top: _side,
        bottom: _side,
        verticalInside: _side,
      ),
      columnWidths: const <int, pw.TableColumnWidth>{
        0: pw.FlexColumnWidth(1),
        1: pw.FlexColumnWidth(1),
      },
      children: <pw.TableRow>[
        pw.TableRow(
          children: <pw.Widget>[
            pw.Column(
              crossAxisAlignment: pw.CrossAxisAlignment.stretch,
              mainAxisSize: pw.MainAxisSize.min,
              children: <pw.Widget>[
                _ruledBelow(_seller()),
                if (consignee != null) _ruledBelow(consignee),
                _buyer(),
              ],
            ),
            pw.Column(
              crossAxisAlignment: pw.CrossAxisAlignment.stretch,
              mainAxisSize: pw.MainAxisSize.min,
              children: <pw.Widget>[
                _grid(<(String, String?)>[
                  ('Invoice No.', detail.voucherNumber),
                  ('Dated', DocumentStyle.date(detail.date)),
                  ('Delivery Note', invoice?.deliveryNote),
                  ('Mode/Terms of Payment', invoice?.paymentTerms),
                  ('Reference No. & Date.', _dated(detail.reference, invoice?.referenceDate)),
                  ('Other References', invoice?.otherReferences),
                  ("Buyer's Order No.", invoice?.buyerOrderNo),
                  ('Dated', _dated(null, invoice?.buyerOrderDate)),
                  ('Dispatch Doc No.', invoice?.dispatchDocNo),
                  ('Delivery Note Date', _dated(null, invoice?.deliveryNoteDate)),
                  ('Dispatched through', invoice?.dispatchedThrough),
                  ('Destination', invoice?.destination),
                  (
                    'Bill of Lading/LR-RR No.',
                    _dated(invoice?.billOfLadingNo, invoice?.billOfLadingDate),
                  ),
                  ('Motor Vehicle No.', invoice?.vehicleNo),
                ]),
                pw.Container(
                  decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
                  padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 3),
                  child: pw.Column(
                    crossAxisAlignment: pw.CrossAxisAlignment.start,
                    mainAxisSize: pw.MainAxisSize.min,
                    children: <pw.Widget>[
                      pw.Text('Terms of Delivery', style: _caption),
                      for (final String line in invoice?.termsOfDelivery ?? const <String>[])
                        pw.Text(line, style: DocumentStyle.bodyBold),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ],
    );
  }

  pw.Widget _ruledBelow(pw.Widget child) => pw.Container(
        decoration: const pw.BoxDecoration(border: pw.Border(bottom: _side)),
        child: child,
      );

  /// A number with its date, the way Tally joins them: `771522771058 dt.
  /// 26-Apr-2025`. Either half alone prints alone.
  static String? _dated(String? number, DateTime? date) {
    final String value = (number ?? '').trim();
    if (date == null) return value.isEmpty ? null : value;
    final String day = DocumentStyle.date(date);
    return value.isEmpty ? day : '$value dt. $day';
  }

  /// The seller, in the order Tally's own invoice prints it: name, address,
  /// GSTIN, state and code, then how to reach them. Every line is there only
  /// when Tally holds something for it.
  pw.Widget _seller() {
    final CompanyDetails? company = _company;
    final String? gstin = _sellerGstin;
    final String state = (company?.state ?? '').trim();
    final String? code = GstState.code(gstin: gstin, state: state);
    final List<String> address = company?.address ?? const <String>[];
    // Telephone and mobile on one line, the way Tally prints "Contact".
    final String phone = <String>[
      (company?.phone ?? '').trim(),
      (company?.mobile ?? '').trim(),
    ].where((String part) => part.isNotEmpty).join(', ');
    final String email = (company?.email ?? '').trim();
    final String website = (company?.website ?? '').trim();
    final String pan = (company?.pan ?? '').trim();

    return pw.Padding(
      padding: _blockPadding,
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        mainAxisSize: pw.MainAxisSize.min,
        children: <pw.Widget>[
          pw.Text(
            _sellerName,
            style: pw.TextStyle(fontSize: 10, fontWeight: pw.FontWeight.bold, color: _ink),
          ),
          // The address lines as typed, and no separate PIN line: Tally's own
          // invoice prints the company's Pincode field nowhere -- checked
          // against its print for "D.D Enterprises", which has one.
          for (final String line in address) pw.Text(line, style: DocumentStyle.body),
          if (gstin != null) pw.Text('GSTIN/UIN: $gstin', style: DocumentStyle.body),
          if (state.isNotEmpty || code != null)
            pw.Text(_stateLine(state, code), style: DocumentStyle.body),
          if (pan.isNotEmpty) pw.Text('PAN/IT No: $pan', style: DocumentStyle.body),
          if (phone.isNotEmpty) pw.Text('Contact: $phone', style: DocumentStyle.body),
          if (email.isNotEmpty) pw.Text('E-Mail: $email', style: DocumentStyle.body),
          if (website.isNotEmpty) pw.Text(website, style: DocumentStyle.body),
        ],
      ),
    );
  }

  static String _stateLine(String state, String? code) => <String>[
        if (state.isNotEmpty) 'State Name : $state',
        if (code != null) 'Code : $code',
      ].join(', ');

  /// Where the goods went. Printed only when the invoice itself names a
  /// consignee: a bill stored before that was read has only its buyer, and
  /// repeating the buyer under "Ship to" would be a guess about a delivery.
  pw.Widget? _consignee() {
    final InvoiceParty? consignee = detail.invoice?.consignee;
    if (consignee == null) return null;
    final String name = (consignee.name ?? '').trim();
    if (name.isEmpty && consignee.address.isEmpty) return null;
    return _party(
      caption: 'Consignee (Ship to)',
      name: name,
      address: consignee.address,
      gstin: consignee.gstin,
      state: consignee.state,
    );
  }

  /// The buyer as written on the invoice, or off the party's ledger for a
  /// voucher that came without its own copy.
  pw.Widget _buyer() {
    final InvoiceParty? written = detail.invoice?.buyer;
    final PartyDetails? master = detail.partyDetails;
    final String fallback = master?.name.isNotEmpty == true ? master!.name : (detail.party ?? '');
    // Off the ledger master either way: an invoice keeps its own copy of the
    // buyer's address, but not of a mobile number.
    final String? mobile = master?.mobile ?? master?.phone;

    if (written != null) {
      final String name = (written.name ?? '').trim();
      return _party(
        caption: 'Buyer (Bill to)',
        name: name.isEmpty ? fallback : name,
        address: written.address,
        gstin: written.gstin,
        state: written.state,
        mobile: mobile,
      );
    }
    return _party(
      caption: 'Buyer (Bill to)',
      name: fallback,
      address: master?.address ?? const <String>[],
      gstin: master?.gstin,
      state: master?.state,
      mobile: mobile,
    );
  }

  pw.Widget _party({
    required String caption,
    required String name,
    required List<String> address,
    String? gstin,
    String? state,
    String? mobile,
  }) {
    final String number = (gstin ?? '').trim();
    final String contact = (mobile ?? '').trim();
    final String stateName = (state ?? '').trim();
    final String? code = GstState.code(gstin: number, state: stateName);
    return pw.Padding(
      padding: _blockPadding,
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        mainAxisSize: pw.MainAxisSize.min,
        children: <pw.Widget>[
          pw.Text(caption, style: _caption),
          if (name.trim().isNotEmpty) pw.Text(name, style: DocumentStyle.bodyBold),
          for (final String line in address) pw.Text(line, style: DocumentStyle.body),
          if (number.isNotEmpty) pw.Text('GSTIN/UIN : $number', style: DocumentStyle.body),
          if (stateName.isNotEmpty || code != null)
            pw.Text(_stateLine(stateName, code), style: DocumentStyle.body),
          if (contact.isNotEmpty) pw.Text('Contact : $contact', style: DocumentStyle.body),
        ],
      ),
    );
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
        constraints: const pw.BoxConstraints(minHeight: 23),
        padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 2),
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

  static const int _columns = 9;

  pw.Widget _itemTable() {
    final List<VoucherLedgerLine> charges = _charges;
    final List<VoucherLedgerLine> ledgerGoods =
        _hasItems ? const <VoucherLedgerLine>[] : _postings.where((l) => !_isCharge(l)).toList();
    // Tally rules off the goods before the tax lines and prints what they
    // came to. Only worth the row when something follows it.
    final bool subtotal = _hasItems && charges.isNotEmpty;
    final int rowCount = detail.inventoryEntries.length +
        ledgerGoods.length +
        charges.length +
        (subtotal ? 1 : 0);

    return pw.Table(
      border: const pw.TableBorder(
        left: _side,
        right: _side,
        bottom: _side,
        verticalInside: _side,
      ),
      columnWidths: const <int, pw.TableColumnWidth>{
        0: pw.FixedColumnWidth(20),
        1: pw.FlexColumnWidth(1),
        2: pw.FixedColumnWidth(50),
        3: pw.FixedColumnWidth(30),
        4: pw.FixedColumnWidth(56),
        5: pw.FixedColumnWidth(50),
        6: pw.FixedColumnWidth(26),
        7: pw.FixedColumnWidth(32),
        8: pw.FixedColumnWidth(74),
      },
      children: <pw.TableRow>[
        pw.TableRow(
          decoration: const pw.BoxDecoration(border: pw.Border(bottom: _side)),
          children: <pw.Widget>[
            _head('Sl No.'),
            _head(_hasItems ? 'Description of Goods' : 'Particulars'),
            _head('HSN/SAC'),
            _head('GST Rate'),
            _head('Quantity'),
            _head('Rate'),
            _head('per'),
            _head('Disc. %'),
            _head('Amount'),
          ],
        ),
        for (int i = 0; i < detail.inventoryEntries.length; i++)
          _itemRow(i + 1, detail.inventoryEntries[i]),
        for (int i = 0; i < ledgerGoods.length; i++)
          _ledgerRow(i + 1, ledgerGoods[i], charge: false),
        if (subtotal) _subtotalRow(),
        for (int i = 0; i < charges.length; i++) _ledgerRow(i + 1, charges[i], charge: true),
        // Tally's print keeps the item table tall with its column rules running
        // down to the total; a short invoice without it reads as cut off.
        if (rowCount < 14)
          pw.TableRow(
            children: <pw.Widget>[
              pw.SizedBox(height: (14 - rowCount) * 14.0),
              for (int i = 1; i < _columns; i++) pw.SizedBox(),
            ],
          ),
        pw.TableRow(
          decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
          children: <pw.Widget>[
            pw.SizedBox(),
            DocumentStyle.cell('Total', align: pw.TextAlign.right),
            pw.SizedBox(),
            pw.SizedBox(),
            DocumentStyle.cell(_totalQuantity(), align: pw.TextAlign.right, bold: true),
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

  /// The quantities added up, when adding them means something. Five pieces
  /// and two metres are not seven of anything, and Tally leaves the box empty
  /// for a bill like that too.
  String _totalQuantity() {
    if (!_hasItems) return '';
    final Set<String> units = detail.inventoryEntries
        .map((VoucherStockLine l) => (l.unit ?? '').trim().toLowerCase())
        .toSet();
    if (units.length != 1) return '';
    return MoneyFormat.quantity(
      detail.inventoryEntries
          .fold<double>(0, (double t, VoucherStockLine l) => t + l.quantity.abs()),
      detail.inventoryEntries.first.unit,
    );
  }

  pw.Widget _head(String text, {pw.TextAlign align = pw.TextAlign.center}) => pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 2, vertical: 3),
        child: pw.Text(text, textAlign: align, style: _caption),
      );

  pw.TableRow _itemRow(int index, VoucherStockLine line) {
    final List<String> under = <String?>[line.godown, line.batch]
        .whereType<String>()
        .where((String value) => value.trim().isNotEmpty)
        // Tally's names for "no godown" and "no batch". Printing them under
        // every item tells a customer nothing.
        .where((String value) => value != 'Main Location' && value != 'Primary Batch')
        .toList();
    return pw.TableRow(
      children: <pw.Widget>[
        DocumentStyle.cell('$index', align: pw.TextAlign.right),
        pw.Padding(
          padding: const pw.EdgeInsets.symmetric(horizontal: 4, vertical: 2),
          child: pw.Column(
            crossAxisAlignment: pw.CrossAxisAlignment.start,
            children: <pw.Widget>[
              pw.Text(line.item, style: DocumentStyle.bodyBold),
              if (under.isNotEmpty) pw.Text(under.join(' | '), style: _small),
            ],
          ),
        ),
        _tight(line.hsnCode ?? ''),
        _tight(line.gstRate == null ? '' : '${_rate(line.gstRate!)} %', align: pw.TextAlign.right),
        _tight(
          MoneyFormat.quantity(line.quantity.abs(), line.unit),
          align: pw.TextAlign.right,
          bold: true,
        ),
        _tight(
          line.rate == null ? '' : DocumentStyle.amount(line.rate!),
          align: pw.TextAlign.right,
        ),
        _tight(line.unit ?? ''),
        _tight(_discount(line), align: pw.TextAlign.right),
        _tight(DocumentStyle.amount(line.amount), align: pw.TextAlign.right, bold: true),
      ],
    );
  }

  /// A table cell with the rows closed up, as Tally sets its item lines.
  pw.Widget _tight(
    String text, {
    pw.TextAlign align = pw.TextAlign.left,
    bool bold = false,
    pw.TextStyle? style,
  }) =>
      pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 3, vertical: 2),
        child: pw.Text(
          text,
          textAlign: align,
          style: style ?? (bold ? DocumentStyle.bodyBold : DocumentStyle.body),
        ),
      );

  pw.TableRow _subtotalRow() => pw.TableRow(
        children: <pw.Widget>[
          for (int i = 0; i < _columns - 1; i++) pw.SizedBox(),
          pw.Container(
            margin: const pw.EdgeInsets.only(left: 6, top: 3),
            decoration: const pw.BoxDecoration(
              border: pw.Border(top: pw.BorderSide(color: _ink, width: 0.5)),
            ),
            child: _tight(
              _amount(_sum(detail.inventoryEntries.map((VoucherStockLine l) => l.amount))),
              align: pw.TextAlign.right,
            ),
          ),
        ],
      );

  pw.TableRow _ledgerRow(int index, VoucherLedgerLine line, {required bool charge}) {
    final bool reduces = charge && line.amount.side != _billSide;
    final double? rate = charge ? line.taxRate : null;
    return pw.TableRow(
      children: <pw.Widget>[
        // Tax and round-off are not goods, so they take no serial number.
        _tight(charge ? '' : '$index', align: pw.TextAlign.right),
        _tight(
          line.ledger,
          align: charge ? pw.TextAlign.right : pw.TextAlign.left,
          style: charge ? _boldItalic : DocumentStyle.bodyBold,
        ),
        pw.SizedBox(),
        pw.SizedBox(),
        pw.SizedBox(),
        // The rate a tax line was charged at sits in the Rate column with its
        // percent sign under "per", exactly where Tally puts them.
        _tight(rate == null ? '' : _rate(rate), align: pw.TextAlign.right, style: _italic),
        _tight(rate == null ? '' : '%', style: _italic),
        pw.SizedBox(),
        _tight(
          '${reduces ? '(-)' : ''}${DocumentStyle.amount(line.amount)}',
          align: pw.TextAlign.right,
          bold: true,
        ),
      ],
    );
  }

  /// `12`, `2.50` -- a whole rate without decimals, a fractional one with two.
  static String _rate(double value) =>
      value == value.roundToDouble() ? value.toStringAsFixed(0) : value.toStringAsFixed(2);

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
        padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 3),
        child: pw.Row(
          crossAxisAlignment: pw.CrossAxisAlignment.start,
          children: <pw.Widget>[
            pw.Expanded(
              child: pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.start,
                children: <pw.Widget>[
                  pw.Text('Amount Chargeable (in words)', style: _caption),
                  pw.SizedBox(height: 2),
                  pw.Text(
                    AmountInWords.rupees(detail.amount.amount, currency: detail.amount.currency),
                    style: DocumentStyle.bodyBold,
                  ),
                ],
              ),
            ),
            pw.Text(
              'E. & O.E',
              style: pw.TextStyle(fontSize: 7.5, fontStyle: pw.FontStyle.italic, color: _ink),
            ),
          ],
        ),
      );

  // ------------------------------------------------------------------
  // HSN-wise tax summary
  // ------------------------------------------------------------------

  /// One row per HSN *and rate*, as Tally prints it.
  ///
  /// The same HSN billed at two rates is two rows -- on the invoice this was
  /// matched against, 621142 appears at 12% and again at 5%. Grouping on the
  /// HSN alone left such a bill with no summary at all.
  _TaxSummary? _taxSummary() {
    final Decimal cgst = _ledgerTotal(_cgst);
    final Decimal sgst = _ledgerTotal(_sgst);
    final Decimal igst = _ledgerTotal(_igst);
    if (cgst == Decimal.zero && sgst == Decimal.zero && igst == Decimal.zero) return null;
    final bool local = igst == Decimal.zero;

    final Map<(String, double?), Decimal> taxable = <(String, double?), Decimal>{};
    if (_hasItems) {
      for (final VoucherStockLine line in detail.inventoryEntries) {
        final (String, double?) key = ((line.hsnCode ?? '').trim(), line.gstRate);
        taxable[key] = (taxable[key] ?? Decimal.zero) + line.amount.amount;
      }
    } else {
      taxable[('', null)] = _sum(
        _postings.where((VoucherLedgerLine l) => !_isCharge(l)).map((l) => l.amount),
      );
    }

    final List<_TaxRow> rows = <_TaxRow>[];
    if (taxable.length == 1) {
      // A single row takes the posted ledgers whole, so the table cannot
      // disagree with the lines above it by a paisa.
      final MapEntry<(String, double?), Decimal> only = taxable.entries.single;
      if (only.value == Decimal.zero) return null;
      final double? known = only.key.$2;
      double rate(Decimal tax, double? stated) =>
          stated ?? (tax / only.value).toDouble() * 100;
      rows.add((
        hsn: only.key.$1,
        taxable: only.value,
        centralRate: rate(cgst, known == null ? null : known / 2),
        central: cgst,
        stateRate: rate(sgst, known == null ? null : known / 2),
        state: sgst,
        integratedRate: rate(igst, known),
        integrated: igst,
      ));
    } else {
      for (final MapEntry<(String, double?), Decimal> group in taxable.entries) {
        final double? rate = group.key.$2;
        // A line with no rate cannot be given a share of the tax.
        if (rate == null) return null;
        rows.add((
          hsn: group.key.$1,
          taxable: group.value,
          centralRate: local ? rate / 2 : 0,
          central: local ? _percent(group.value, rate / 2) : Decimal.zero,
          stateRate: local ? rate / 2 : 0,
          state: local ? _percent(group.value, rate / 2) : Decimal.zero,
          integratedRate: local ? 0 : rate,
          integrated: local ? Decimal.zero : _percent(group.value, rate),
        ));
      }
      // Worked out per row from the rate on each line, so it must still land
      // on what was posted. A rupee of drift is Tally rounding per line; more
      // than that means these are not the rates this bill was taxed at.
      bool close(Decimal a, Decimal b) => (a - b).abs() <= Decimal.one;
      if (!close(_sumOf(rows, (r) => r.central), cgst) ||
          !close(_sumOf(rows, (r) => r.state), sgst) ||
          !close(_sumOf(rows, (r) => r.integrated), igst)) {
        return null;
      }
    }

    // Totals are the rows added up, as on Tally's print, so the table foots.
    return _TaxSummary(
      rows: rows,
      local: cgst != Decimal.zero || sgst != Decimal.zero,
      interstate: igst != Decimal.zero,
      taxable: _sumOf(rows, (r) => r.taxable),
      central: _sumOf(rows, (r) => r.central),
      state: _sumOf(rows, (r) => r.state),
      integrated: _sumOf(rows, (r) => r.integrated),
    );
  }

  pw.Widget _taxTable(_TaxSummary tax) {
    final List<String> groups = <String>[
      if (tax.local) ...<String>['CGST', 'SGST/UTGST'],
      if (tax.interstate) 'IGST',
    ];
    final Map<int, pw.TableColumnWidth> widths = <int, pw.TableColumnWidth>{
      0: const pw.FlexColumnWidth(1),
      1: const pw.FixedColumnWidth(74),
      for (int i = 0; i < groups.length; i++) 2 + i: const pw.FixedColumnWidth(104),
      2 + groups.length: const pw.FixedColumnWidth(74),
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
            _head('HSN/SAC'),
            _head('Taxable\nValue'),
            for (final String group in groups)
              pw.Column(
                crossAxisAlignment: pw.CrossAxisAlignment.stretch,
                children: <pw.Widget>[
                  _head(group),
                  pw.Container(
                    decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
                    child: _split(_head('Rate'), _head('Amount')),
                  ),
                ],
              ),
            _head('Total\nTax Amount'),
          ],
        ),
        for (final _TaxRow row in tax.rows)
          pw.TableRow(
            children: <pw.Widget>[
              _line(row.hsn),
              _line(_amount(row.taxable), align: pw.TextAlign.right),
              for (final (double rate, Decimal amount) in parts(row))
                _split(
                  _line('${_rate(rate)}%', align: pw.TextAlign.right),
                  _line(_amount(amount), align: pw.TextAlign.right),
                ),
              _line(
                _amount(row.central + row.state + row.integrated),
                align: pw.TextAlign.right,
              ),
            ],
          ),
        pw.TableRow(
          decoration: const pw.BoxDecoration(border: pw.Border(top: _side)),
          children: <pw.Widget>[
            _line('Total', align: pw.TextAlign.right, bold: true),
            _line(_amount(tax.taxable), align: pw.TextAlign.right, bold: true),
            for (final Decimal amount in <Decimal>[
              if (tax.local) ...<Decimal>[tax.central, tax.state],
              if (tax.interstate) tax.integrated,
            ])
              _split(
                pw.SizedBox(),
                _line(_amount(amount), align: pw.TextAlign.right, bold: true),
              ),
            _line(_amount(tax.total), align: pw.TextAlign.right, bold: true),
          ],
        ),
      ],
    );
  }

  /// A row of the tax summary, which Tally sets tighter than the items.
  pw.Widget _line(String text, {pw.TextAlign align = pw.TextAlign.left, bool bold = false}) =>
      pw.Padding(
        padding: const pw.EdgeInsets.symmetric(horizontal: 3, vertical: 1),
        child: pw.Text(
          text,
          textAlign: align,
          style: pw.TextStyle(
            fontSize: 8,
            fontWeight: bold ? pw.FontWeight.bold : pw.FontWeight.normal,
            color: _ink,
          ),
        ),
      );

  pw.Widget _split(pw.Widget rate, pw.Widget amount) => pw.Row(
        children: <pw.Widget>[
          pw.Expanded(flex: 4, child: rate),
          pw.Expanded(
            flex: 6,
            child: pw.Container(
              decoration: const pw.BoxDecoration(border: pw.Border(left: _side)),
              child: amount,
            ),
          ),
        ],
      );

  // ------------------------------------------------------------------
  // Tax in words, bank details, declaration and signature
  // ------------------------------------------------------------------

  pw.Widget _footerBlock(_TaxSummary? tax) {
    final BankAccount? bank = _company?.bank;
    return pw.Container(
      decoration: const pw.BoxDecoration(
        border: pw.Border(left: _side, right: _side, bottom: _side),
      ),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.stretch,
        mainAxisSize: pw.MainAxisSize.min,
        children: <pw.Widget>[
          if (tax != null)
            pw.Padding(
              padding: const pw.EdgeInsets.fromLTRB(5, 6, 5, 2),
              child: pw.RichText(
                text: pw.TextSpan(
                  children: <pw.InlineSpan>[
                    pw.TextSpan(text: 'Tax Amount (in words) :  ', style: _caption),
                    pw.TextSpan(
                      text: AmountInWords.rupees(tax.total, currency: detail.amount.currency),
                      style: DocumentStyle.bodyBold,
                    ),
                  ],
                ),
              ),
            ),
          pw.Table(
            columnWidths: const <int, pw.TableColumnWidth>{
              0: pw.FlexColumnWidth(1),
              1: pw.FlexColumnWidth(1),
            },
            children: <pw.TableRow>[
              pw.TableRow(
                verticalAlignment: pw.TableCellVerticalAlignment.bottom,
                children: <pw.Widget>[
                  pw.Padding(
                    padding: const pw.EdgeInsets.fromLTRB(5, 4, 5, 4),
                    child: pw.Column(
                      crossAxisAlignment: pw.CrossAxisAlignment.start,
                      mainAxisSize: pw.MainAxisSize.min,
                      children: <pw.Widget>[
                        pw.Text(
                          'Declaration',
                          style: const pw.TextStyle(
                            fontSize: 7.5,
                            color: _ink,
                            decoration: pw.TextDecoration.underline,
                          ),
                        ),
                        pw.Text(
                          'We declare that this invoice shows the actual price of the '
                          '${_hasItems ? 'goods' : 'services'} described and that all '
                          'particulars are true and correct.',
                          style: const pw.TextStyle(fontSize: 7.5, color: _ink),
                        ),
                      ],
                    ),
                  ),
                  pw.Column(
                    crossAxisAlignment: pw.CrossAxisAlignment.stretch,
                    mainAxisSize: pw.MainAxisSize.min,
                    children: <pw.Widget>[
                      if (bank != null) _bankDetails(bank),
                      pw.Container(
                        decoration: const pw.BoxDecoration(
                          border: pw.Border(left: _side, top: _side),
                        ),
                        padding: const pw.EdgeInsets.symmetric(horizontal: 5, vertical: 3),
                        child: pw.Column(
                          crossAxisAlignment: pw.CrossAxisAlignment.end,
                          mainAxisSize: pw.MainAxisSize.min,
                          children: <pw.Widget>[
                            pw.Text('for $_sellerName', style: DocumentStyle.bodyBold),
                            pw.SizedBox(height: 24),
                            pw.Text('Authorised Signatory', style: _caption),
                          ],
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  pw.Widget _bankDetails(BankAccount bank) {
    final String branch = <String>[
      (bank.branch ?? '').trim(),
      (bank.ifsc ?? '').trim(),
    ].where((String part) => part.isNotEmpty).join(' & ');

    pw.TableRow row(String label, String value) => pw.TableRow(
          children: <pw.Widget>[
            pw.Text(label, style: _caption),
            pw.Text(':  $value', style: pw.TextStyle(fontSize: 8, fontWeight: pw.FontWeight.bold)),
          ],
        );

    return pw.Padding(
      padding: const pw.EdgeInsets.fromLTRB(5, 0, 5, 3),
      child: pw.Column(
        crossAxisAlignment: pw.CrossAxisAlignment.start,
        mainAxisSize: pw.MainAxisSize.min,
        children: <pw.Widget>[
          pw.Text("Company's Bank Details", style: _caption),
          pw.Table(
            columnWidths: const <int, pw.TableColumnWidth>{
              0: pw.FixedColumnWidth(84),
              1: pw.FlexColumnWidth(1),
            },
            children: <pw.TableRow>[
              row('Bank Name', bank.bankName),
              row('A/c No.', bank.accountNumber),
              if (branch.isNotEmpty) row('Branch & IFS Code', branch),
            ],
          ),
        ],
      ),
    );
  }

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

  Decimal get total => central + state + integrated;
}
