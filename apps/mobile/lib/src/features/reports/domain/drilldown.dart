import '../../../core/model/figures.dart';
import '../../../core/money/money.dart';

/// The shapes behind "show me this one, in full".
///
/// Where the list reports summarise, these carry everything the backend read:
/// every ledger line of a voucher with its side intact, every movement of a
/// stock item, every voucher that touched a ledger. They are the screens an
/// accountant checks a figure against, so nothing here rounds, nets or hides a
/// row on the app's own initiative.

/// One bill a voucher line raised or settled, as that voucher recorded it.
class VoucherBill {
  const VoucherBill({
    required this.name,
    this.billType,
    this.billDate,
    this.amount,
    this.creditDays,
  });

  final String name;

  /// Tally's own word: `New Ref`, `Agst Ref`, `Advance`, `On Account`.
  final String? billType;
  final DateTime? billDate;
  final Money? amount;
  final int? creditDays;

  factory VoucherBill.fromJson(Map<String, Object?> json) => VoucherBill(
        name: json['name'] as String? ?? '',
        billType: json['bill_type'] as String?,
        billDate: _day(json['bill_date']),
        amount: _moneyOrNull(json['amount']),
        creditDays: (json['credit_days'] as num?)?.toInt(),
      );
}

/// The cheque or transfer behind a bank line. Every field is optional, and a
/// blank one is left off the print rather than printed as an empty caption.
class VoucherBankDetails {
  const VoucherBankDetails({
    this.transactionType,
    this.instrumentNumber,
    this.instrumentDate,
    this.bankName,
    this.favouring,
  });

  /// "Cheque/DD", "e-Fund Transfer", "Cash" -- Tally's words.
  final String? transactionType;
  final String? instrumentNumber;
  final DateTime? instrumentDate;

  /// On a receipt, the bank the customer's cheque was drawn on.
  final String? bankName;
  final String? favouring;

  factory VoucherBankDetails.fromJson(Map<String, Object?> json) =>
      VoucherBankDetails(
        transactionType: json['transaction_type'] as String?,
        instrumentNumber: json['instrument_number'] as String?,
        instrumentDate: _day(json['instrument_date']),
        bankName: json['bank_name'] as String?,
        favouring: json['favouring'] as String?,
      );
}

/// One row of a receipt's or payment's "Adjustment Details": a bill, and what
/// this voucher did to it.
///
/// Everything but the name and the paid amount is null when the bill could not
/// be found in the history the backend holds -- an opening bill, or one older
/// than it searched. Null prints as a blank cell, never as a zero: "nothing
/// pending" and "we cannot say" are different statements on a receipt.
class BillAdjustment {
  const BillAdjustment({
    required this.name,
    this.billType,
    this.billDate,
    this.creditDays,
    this.billAmount,
    this.noteAmount,
    this.paidAmount,
    this.pendingAmount,
  });

  final String name;
  final String? billType;
  final DateTime? billDate;
  final int? creditDays;
  final Money? billAmount;

  /// Settled by credit note (on a receipt) or debit note (on a payment).
  final Money? noteAmount;
  final Money? paidAmount;
  final Money? pendingAmount;

  factory BillAdjustment.fromJson(Map<String, Object?> json) => BillAdjustment(
        name: json['name'] as String? ?? '',
        billType: json['bill_type'] as String?,
        billDate: _day(json['bill_date']),
        creditDays: (json['credit_days'] as num?)?.toInt(),
        billAmount: _moneyOrNull(json['bill_amount']),
        noteAmount: _moneyOrNull(json['note_amount']),
        paidAmount: _moneyOrNull(json['paid_amount']),
        pendingAmount: _moneyOrNull(json['pending_amount']),
      );

  static List<BillAdjustment> listFromJson(Map<String, Object?> json) =>
      _list(json['bills'], BillAdjustment.fromJson);
}

/// One posted line of a voucher.
class VoucherLedgerLine {
  const VoucherLedgerLine({
    required this.ledger,
    required this.amount,
    this.isParty = false,
    this.costCentre,
    this.billReferences = const <String>[],
    this.bills = const <VoucherBill>[],
    this.bank,
    this.taxRate,
  });

  final String ledger;

  /// Keeps its side. Which line is Dr and which is Cr *is* the content of the
  /// row, so this is never reduced to a magnitude on the way to the screen.
  final Money amount;

  /// The party ledger, where the voucher has one. Rendered first and named,
  /// because "who was this with" is the first thing anybody reads.
  final bool isParty;

  final String? costCentre;
  final List<String> billReferences;

  /// The same bills with their type, date and amount. Empty from an older
  /// backend, and on a voucher read before these were fetched.
  final List<VoucherBill> bills;

  /// Set on a bank line that carried a cheque or transfer. Null otherwise.
  final VoucherBankDetails? bank;

  /// The percentage a tax line was charged at, as the invoice recorded it.
  /// Null on every line that is not a tax.
  final double? taxRate;

  factory VoucherLedgerLine.fromJson(Map<String, Object?> json) => VoucherLedgerLine(
        ledger: json['ledger'] as String? ?? '',
        amount: Money.fromJson(json['amount']),
        isParty: json['is_party'] as bool? ?? false,
        costCentre: json['cost_centre'] as String?,
        billReferences: <String>[
          for (final Object? ref in (json['bill_references'] as List<Object?>?) ??
              const <Object?>[])
            if (ref != null) ref.toString(),
        ],
        bills: _list(json['bills'], VoucherBill.fromJson),
        bank: json['bank'] == null
            ? null
            : VoucherBankDetails.fromJson(_map(json['bank'])),
        taxRate: (json['tax_rate'] as num?)?.toDouble(),
      );
}

/// One stock line of a voucher.
class VoucherStockLine {
  const VoucherStockLine({
    required this.item,
    required this.quantity,
    required this.amount,
    this.unit,
    this.rate,
    this.godown,
    this.batch,
    this.hsnCode,
    this.gstRate,
  });

  final String item;
  final double quantity;
  final Money amount;
  final String? unit;
  final Money? rate;
  final String? godown;
  final String? batch;

  /// As the invoice itself recorded them, or off the stock master for a voucher
  /// stored before the line's own were read. Null whenever Tally holds neither;
  /// [gstRate] is the full rate, so half of it is the CGST on a local sale.
  final String? hsnCode;
  final double? gstRate;

  factory VoucherStockLine.fromJson(Map<String, Object?> json) => VoucherStockLine(
        item: json['item'] as String? ?? '',
        quantity: (json['quantity'] as num?)?.toDouble() ?? 0,
        amount: Money.fromJson(json['amount']),
        unit: json['unit'] as String?,
        rate: json['rate'] == null ? null : Money.fromJson(json['rate']),
        godown: json['godown'] as String?,
        batch: json['batch'] as String?,
        hsnCode: json['hsn_code'] as String?,
        gstRate: (json['gst_rate'] as num?)?.toDouble(),
      );
}

/// A party's own contact details, as Tally holds them on the ledger master.
///
/// Contact only. A receipt an owner forwards to a customer must not carry that
/// customer's balance or credit limit, so those never reach this shape.
class PartyDetails {
  const PartyDetails({
    required this.name,
    this.gstin,
    this.address = const <String>[],
    this.state,
    this.phone,
    this.mobile,
    this.email,
  });

  final String name;
  final String? gstin;
  final List<String> address;
  final String? state;
  final String? phone;
  final String? mobile;
  final String? email;

  bool get hasContact =>
      (gstin?.isNotEmpty ?? false) ||
      address.isNotEmpty ||
      (state?.isNotEmpty ?? false) ||
      (phone?.isNotEmpty ?? false) ||
      (mobile?.isNotEmpty ?? false) ||
      (email?.isNotEmpty ?? false);

  factory PartyDetails.fromJson(Map<String, Object?> json) => PartyDetails(
        name: json['name'] as String? ?? '',
        gstin: json['gstin'] as String?,
        address: <String>[
          for (final Object? line in (json['address'] as List<Object?>?) ?? const <Object?>[])
            if (line != null && line.toString().trim().isNotEmpty) line.toString().trim(),
        ],
        state: json['state'] as String?,
        phone: json['phone'] as String?,
        mobile: json['mobile'] as String?,
        email: json['email'] as String?,
      );
}

/// A buyer or consignee as written on one invoice, which keeps its own copy of
/// the name and address it was raised to.
class InvoiceParty {
  const InvoiceParty({
    this.name,
    this.address = const <String>[],
    this.gstin,
    this.state,
  });

  final String? name;
  final List<String> address;
  final String? gstin;
  final String? state;

  factory InvoiceParty.fromJson(Map<String, Object?> json) => InvoiceParty(
        name: json['name'] as String?,
        address: _lines(json['address']),
        gstin: json['gstin'] as String?,
        state: json['state'] as String?,
      );
}

/// The header of a printed invoice: who it went to and how it travelled.
///
/// Most of it is empty on most invoices. The document prints the caption
/// anyway, because that is what the shop's own print from Tally does.
class InvoiceDetails {
  const InvoiceDetails({
    this.buyer,
    this.consignee,
    this.placeOfSupply,
    this.deliveryNote,
    this.deliveryNoteDate,
    this.paymentTerms,
    this.referenceDate,
    this.otherReferences,
    this.buyerOrderNo,
    this.buyerOrderDate,
    this.dispatchDocNo,
    this.dispatchedThrough,
    this.destination,
    this.billOfLadingNo,
    this.billOfLadingDate,
    this.vehicleNo,
    this.termsOfDelivery = const <String>[],
  });

  final InvoiceParty? buyer;
  final InvoiceParty? consignee;
  final String? placeOfSupply;
  final String? deliveryNote;
  final DateTime? deliveryNoteDate;
  final String? paymentTerms;
  final DateTime? referenceDate;
  final String? otherReferences;
  final String? buyerOrderNo;
  final DateTime? buyerOrderDate;
  final String? dispatchDocNo;
  final String? dispatchedThrough;
  final String? destination;
  final String? billOfLadingNo;
  final DateTime? billOfLadingDate;
  final String? vehicleNo;
  final List<String> termsOfDelivery;

  factory InvoiceDetails.fromJson(Map<String, Object?> json) => InvoiceDetails(
        buyer: json['buyer'] == null ? null : InvoiceParty.fromJson(_map(json['buyer'])),
        consignee:
            json['consignee'] == null ? null : InvoiceParty.fromJson(_map(json['consignee'])),
        placeOfSupply: json['place_of_supply'] as String?,
        deliveryNote: json['delivery_note'] as String?,
        deliveryNoteDate: _day(json['delivery_note_date']),
        paymentTerms: json['payment_terms'] as String?,
        referenceDate: _day(json['reference_date']),
        otherReferences: json['other_references'] as String?,
        buyerOrderNo: json['buyer_order_no'] as String?,
        buyerOrderDate: _day(json['buyer_order_date']),
        dispatchDocNo: json['dispatch_doc_no'] as String?,
        dispatchedThrough: json['dispatched_through'] as String?,
        destination: json['destination'] as String?,
        billOfLadingNo: json['bill_of_lading_no'] as String?,
        billOfLadingDate: _day(json['bill_of_lading_date']),
        vehicleNo: json['vehicle_no'] as String?,
        termsOfDelivery: _lines(json['terms_of_delivery']),
      );
}

/// The account a business asks to be paid into, as printed on its invoices.
class BankAccount {
  const BankAccount({
    required this.bankName,
    required this.accountNumber,
    this.ifsc,
    this.branch,
  });

  final String bankName;
  final String accountNumber;
  final String? ifsc;
  final String? branch;

  factory BankAccount.fromJson(Map<String, Object?> json) => BankAccount(
        bankName: json['bank_name'] as String? ?? '',
        accountNumber: json['account_number'] as String? ?? '',
        ifsc: json['ifsc'] as String?,
        branch: json['branch'] as String?,
      );
}

List<String> _lines(Object? value) => <String>[
      for (final Object? line in (value as List<Object?>?) ?? const <Object?>[])
        if (line != null && line.toString().trim().isNotEmpty) line.toString().trim(),
    ];

DateTime? _day(Object? value) => value == null ? null : DateTime.tryParse(value.toString());

/// The company's own details, as Tally holds them: the seller block of an
/// invoice.
///
/// A voucher names the other side and never its own books, so this comes off
/// the company master -- and the GSTIN off the company's GST registration,
/// which is where TallyPrime keeps it.
class CompanyDetails {
  const CompanyDetails({
    required this.name,
    this.gstin,
    this.pan,
    this.address = const <String>[],
    this.state,
    this.pincode,
    this.phone,
    this.mobile,
    this.email,
    this.website,
    this.bank,
  });

  final String name;
  final String? gstin;
  final String? pan;
  final List<String> address;
  final String? state;
  final String? pincode;
  final String? phone;
  final String? email;

  /// The mobile numbers as Tally shows them, ISD code first.
  final String? mobile;
  final String? website;

  /// Null unless the books leave no doubt which account an invoice should
  /// name. A wrong bank on a bill is worse than none.
  final BankAccount? bank;

  factory CompanyDetails.fromJson(Map<String, Object?> json) => CompanyDetails(
        name: json['name'] as String? ?? '',
        gstin: json['gstin'] as String?,
        pan: json['pan'] as String?,
        address: <String>[
          for (final Object? line in (json['address'] as List<Object?>?) ?? const <Object?>[])
            if (line != null && line.toString().trim().isNotEmpty) line.toString().trim(),
        ],
        state: json['state'] as String?,
        pincode: json['pincode'] as String?,
        phone: json['phone'] as String?,
        email: json['email'] as String?,
        mobile: json['mobile'] as String?,
        website: json['website'] as String?,
        bank: json['bank'] == null ? null : BankAccount.fromJson(_map(json['bank'])),
      );
}

/// Everything posted on one voucher.
class VoucherDetail {
  const VoucherDetail({
    required this.key,
    required this.date,
    required this.voucherType,
    required this.kind,
    required this.amount,
    required this.debitTotal,
    required this.creditTotal,
    required this.ledgerEntries,
    required this.inventoryEntries,
    this.voucherNumber,
    this.party,
    this.narration,
    this.reference,
    this.partyDetails,
    this.companyDetails,
    this.invoice,
    this.isCancelled = false,
    this.isOptional = false,
    this.inventoryOmitted = false,
  });

  final String key;
  final DateTime date;
  final String voucherType;
  final String kind;
  final Money amount;

  /// Both sides, so the screen can show that the voucher balances. Sides that
  /// disagree mean a line was dropped on the way in, and without both totals
  /// that is indistinguishable from a voucher with one entry.
  final Money debitTotal;
  final Money creditTotal;

  final List<VoucherLedgerLine> ledgerEntries;
  final List<VoucherStockLine> inventoryEntries;

  final String? voucherNumber;
  final String? party;
  final String? narration;
  final String? reference;

  /// The party ledger's own address and GSTIN, when Tally holds them. Null on a
  /// voucher with no party, and on one whose master snapshot has not landed yet.
  final PartyDetails? partyDetails;

  /// The company's own address and registrations. Null until its profile has
  /// been read once, and always on a connector older than that read -- the
  /// document then prints the company's name alone.
  final CompanyDetails? companyDetails;

  /// Buyer, consignee and dispatch details as the voucher recorded them. Null
  /// on anything that is not an invoice, and on one stored before these were
  /// read -- the document then takes the buyer from [partyDetails].
  final InvoiceDetails? invoice;

  /// Excluded from every total in the product. The detail screen says so out
  /// loud rather than showing a figure that is in none of the ones around it.
  final bool isCancelled;
  final bool isOptional;

  /// True when this voucher is old enough that the history sync did not keep
  /// its stock lines. An empty item list then means "not kept", not "sold
  /// nothing", and the screen has to say which -- the same rule that stops the
  /// dashboard rendering an unread figure as ₹0.
  final bool inventoryOmitted;

  bool get isExcluded => isCancelled || isOptional;

  /// True when debits and credits agree. False is worth saying on screen.
  bool get balances => debitTotal.amount == creditTotal.amount;

  factory VoucherDetail.fromJson(Map<String, Object?> json) => VoucherDetail(
        key: json['key'] as String? ?? '',
        date: DateTime.tryParse(json['date'] as String? ?? '') ?? DateTime.now(),
        voucherType: json['voucher_type'] as String? ?? 'Voucher',
        kind: json['kind'] as String? ?? 'other',
        amount: Money.fromJson(json['amount']),
        debitTotal: Money.fromJson(json['debit_total']),
        creditTotal: Money.fromJson(json['credit_total']),
        ledgerEntries: _list(json['ledger_entries'], VoucherLedgerLine.fromJson),
        inventoryEntries: _list(json['inventory_entries'], VoucherStockLine.fromJson),
        voucherNumber: json['voucher_number'] as String?,
        party: json['party'] as String?,
        narration: json['narration'] as String?,
        reference: json['reference'] as String?,
        partyDetails: json['party_details'] == null
            ? null
            : PartyDetails.fromJson(_map(json['party_details'])),
        companyDetails: json['company_details'] == null
            ? null
            : CompanyDetails.fromJson(_map(json['company_details'])),
        invoice:
            json['invoice'] == null ? null : InvoiceDetails.fromJson(_map(json['invoice'])),
        isCancelled: json['is_cancelled'] as bool? ?? false,
        isOptional: json['is_optional'] as bool? ?? false,
        inventoryOmitted: json['inventory_omitted'] as bool? ?? false,
      );
}

/// One row of a ledger statement: a voucher, what it moved, and the total so far.
class StatementEntry {
  const StatementEntry({
    required this.line,
    required this.movement,
    required this.running,
    this.particulars,
  });

  final TransactionLine line;

  /// The account on the other side of the voucher -- "Sales" against an
  /// invoice, the bank against a receipt -- which is what Tally's Ledger
  /// Vouchers prints. Null from an older backend.
  final String? particulars;

  /// What this voucher did to the ledger, side intact.
  final Money movement;

  /// The account's balance after this voucher, when the statement has an
  /// opening balance to run from. Without one it is only the movement so far
  /// inside the window -- see [LedgerStatement.openingBalance].
  final Money running;

  factory StatementEntry.fromJson(Map<String, Object?> json) => StatementEntry(
        line: TransactionLine.fromJson(json),
        movement: Money.fromJson(json['movement']),
        running: Money.fromJson(json['running']),
        particulars: json['particulars'] as String?,
      );
}

/// Every voucher that touched one ledger over a window.
class LedgerStatement {
  const LedgerStatement({
    required this.ledger,
    required this.voucherCount,
    required this.debitTotal,
    required this.creditTotal,
    required this.netMovement,
    required this.entries,
    this.group,
    this.closingBalance,
    this.openingBalance,
    this.periodClosingBalance,
    this.partyDetails,
    this.companyDetails,
  });

  final String ledger;
  final int voucherCount;
  final Money debitTotal;
  final Money creditTotal;

  /// Movement over the window. Not a balance -- see [closingBalance].
  final Money netMovement;

  final List<StatementEntry> entries;
  final String? group;

  /// The ledger's balance **today**, whatever window is on screen. Tally
  /// evaluates a closing balance against the current date and takes no date to
  /// evaluate against, so this is never the end of the running column and the
  /// screen must not let it read as though it were.
  ///
  /// Null when the master read was unavailable -- which is a different
  /// statement from a nil balance, and rendered differently.
  final Money? closingBalance;

  /// What the account stood at on the morning the window begins.
  ///
  /// Null when it could not be worked out -- the history before or after the
  /// window is not all held yet. That is not a nil balance: the running column
  /// is then movement only, and the screen says so instead of drawing an
  /// account that started from zero.
  final Money? openingBalance;

  /// [openingBalance] plus everything that moved: the account at the end of
  /// the window. Null exactly when the opening balance is.
  final Money? periodClosingBalance;

  /// The account holder's own address, printed under its name on a ledger
  /// account. Null when the master read is not there.
  final PartyDetails? partyDetails;

  /// The company's own address, for the top of the print. Null until its
  /// profile has been read once.
  final CompanyDetails? companyDetails;

  bool get hasBalances => openingBalance != null && periodClosingBalance != null;

  factory LedgerStatement.fromJson(Map<String, Object?> json) => LedgerStatement(
        ledger: json['ledger'] as String? ?? '',
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
        debitTotal: Money.fromJson(json['debit_total']),
        creditTotal: Money.fromJson(json['credit_total']),
        netMovement: Money.fromJson(json['net_movement']),
        entries: _list(json['entries'], StatementEntry.fromJson),
        group: json['group'] as String?,
        closingBalance:
            json['closing_balance'] == null ? null : Money.fromJson(json['closing_balance']),
        openingBalance: _moneyOrNull(json['opening_balance']),
        periodClosingBalance: _moneyOrNull(json['period_closing_balance']),
        partyDetails: json['party_details'] == null
            ? null
            : PartyDetails.fromJson(_map(json['party_details'])),
        companyDetails: json['company_details'] == null
            ? null
            : CompanyDetails.fromJson(_map(json['company_details'])),
      );
}

Money? _moneyOrNull(Object? json) => json == null ? null : Money.fromJson(json);

/// One month's column of a register.
class MonthTotal {
  const MonthTotal({
    required this.month,
    required this.label,
    required this.total,
    required this.voucherCount,
    this.cancelledCount = 0,
    this.quantity,
    this.itemValue,
    this.igst,
    this.cgst,
    this.sgst,
  });

  final String month;
  final String label;
  final Money total;
  final int voucherCount;

  /// Cancelled vouchers of this kind in the month. Counted, and in no total.
  final int cancelledCount;

  /// Units sold or bought, and what the goods alone came to before tax. Both
  /// null when the month's vouchers were stored without their stock lines --
  /// which is "not known", not nothing sold.
  final double? quantity;
  final Money? itemValue;

  /// Null from a backend that predates them.
  final Money? igst;
  final Money? cgst;
  final Money? sgst;

  factory MonthTotal.fromJson(Map<String, Object?> json) => MonthTotal(
        month: json['month'] as String? ?? '',
        label: json['label'] as String? ?? '',
        total: Money.fromJson(json['total']),
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
        cancelledCount: (json['cancelled_count'] as num?)?.toInt() ?? 0,
        quantity: (json['quantity'] as num?)?.toDouble(),
        itemValue: _moneyOrNull(json['item_value']),
        igst: _moneyOrNull(json['igst']),
        cgst: _moneyOrNull(json['cgst']),
        sgst: _moneyOrNull(json['sgst']),
      );
}

/// A sales or purchase register.
class RegisterReport {
  const RegisterReport({
    required this.kind,
    required this.voucherCount,
    required this.total,
    required this.months,
    required this.byParty,
    required this.byProduct,
    required this.vouchers,
    this.truncated = false,
  });

  final String kind;

  /// How many vouchers the window really holds. [vouchers] may be shorter --
  /// the list is capped, the count is not, so a capped list never reads as a
  /// shorter register than the one that exists.
  final int voucherCount;

  final Money total;
  final List<MonthTotal> months;
  final List<PartyTotal> byParty;
  final List<ProductTotal> byProduct;
  final List<TransactionLine> vouchers;
  final bool truncated;

  factory RegisterReport.fromJson(Map<String, Object?> json) => RegisterReport(
        kind: json['kind'] as String? ?? 'sales',
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
        total: Money.fromJson(json['total']),
        months: _list(json['months'], MonthTotal.fromJson),
        byParty: _list(json['by_party'], PartyTotal.fromJson),
        byProduct: _list(json['by_product'], ProductTotal.fromJson),
        vouchers: _list(json['vouchers'], TransactionLine.fromJson),
        truncated: json['truncated'] as bool? ?? false,
      );
}

/// Which way stock moved on one voucher.
enum MovementDirection {
  inward,
  outward,

  /// A stock journal, a physical verification, or a voucher type this company
  /// named something we cannot classify. Shown, and counted in neither total --
  /// guessing it into one would move a figure somebody is reconciling against.
  other;

  static MovementDirection parse(String? raw) => switch (raw) {
        'in' => MovementDirection.inward,
        'out' => MovementDirection.outward,
        _ => MovementDirection.other,
      };
}

class ItemMovementLine {
  const ItemMovementLine({
    required this.line,
    required this.direction,
    required this.quantity,
    required this.value,
    this.unit,
  });

  final TransactionLine line;
  final MovementDirection direction;
  final double quantity;
  final Money value;
  final String? unit;

  factory ItemMovementLine.fromJson(Map<String, Object?> json) => ItemMovementLine(
        line: TransactionLine.fromJson(json),
        direction: MovementDirection.parse(json['direction'] as String?),
        quantity: (json['quantity'] as num?)?.toDouble() ?? 0,
        value: Money.fromJson(json['value']),
        unit: json['unit'] as String?,
      );
}

/// What came in and what went out for one stock item.
class ItemMovementReport {
  const ItemMovementReport({
    required this.item,
    required this.voucherCount,
    required this.quantityIn,
    required this.quantityOut,
    required this.netQuantity,
    required this.valueIn,
    required this.valueOut,
    required this.movements,
    this.unit,
  });

  final String item;
  final int voucherCount;
  final double quantityIn;
  final double quantityOut;
  final double netQuantity;
  final Money valueIn;
  final Money valueOut;
  final List<ItemMovementLine> movements;
  final String? unit;

  factory ItemMovementReport.fromJson(Map<String, Object?> json) => ItemMovementReport(
        item: json['item'] as String? ?? '',
        voucherCount: (json['voucher_count'] as num?)?.toInt() ?? 0,
        quantityIn: (json['quantity_in'] as num?)?.toDouble() ?? 0,
        quantityOut: (json['quantity_out'] as num?)?.toDouble() ?? 0,
        netQuantity: (json['net_quantity'] as num?)?.toDouble() ?? 0,
        valueIn: Money.fromJson(json['value_in']),
        valueOut: Money.fromJson(json['value_out']),
        movements: _list(json['movements'], ItemMovementLine.fromJson),
        unit: json['unit'] as String?,
      );
}

Map<String, Object?> _map(Object? value) {
  if (value is Map<String, Object?>) return value;
  if (value is Map) {
    return value.map((Object? k, Object? v) => MapEntry<String, Object?>(k.toString(), v));
  }
  return <String, Object?>{};
}

List<T> _list<T>(Object? raw, T Function(Map<String, Object?> json) decode) {
  if (raw is! List) return const <Never>[];
  return raw
      .whereType<Map<Object?, Object?>>()
      .map((Map<Object?, Object?> item) => decode(_map(item)))
      .toList(growable: false);
}
