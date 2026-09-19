import 'package:flutter/foundation.dart';

/// What somebody can create from their phone.
///
/// This list is short on purpose and matches `WRITEABLE_KINDS` on the backend.
/// A kind the server does not accept is refused there whatever this file says,
/// so the two only need to agree about what to *offer*, never about what is
/// allowed.
enum EntryKind {
  receipt(
    wire: 'receipt',
    label: 'Receipt',
    blurb: 'Money received from a customer',
    partyLabel: 'Cr: Party',
    accountLabel: 'Dr: Cash/Bank',
    defaultAccount: 'Cash',
  ),
  payment(
    wire: 'payment',
    label: 'Payment',
    blurb: 'Money paid to a supplier',
    partyLabel: 'Dr: Party',
    accountLabel: 'Cr: Cash/Bank',
    defaultAccount: 'Cash',
  ),
  sales(
    wire: 'sales',
    label: 'Sale',
    blurb: 'An invoice raised on a customer',
    partyLabel: 'Customer',
    accountLabel: 'Sales ledger',
    defaultAccount: 'Sales',
    takesLines: true,
  ),
  salesOrder(
    wire: 'sales_order',
    label: 'Sales order',
    blurb: 'An order a customer has placed',
    partyLabel: 'Customer',
    accountLabel: 'Sales ledger',
    defaultAccount: 'Sales',
    takesLines: true,
    needsLines: true,
  ),
  purchaseOrder(
    wire: 'purchase_order',
    label: 'Purchase order',
    blurb: 'An order placed on a supplier',
    partyLabel: 'Supplier',
    accountLabel: 'Purchase ledger',
    defaultAccount: 'Purchase',
    takesLines: true,
    needsLines: true,
  );

  const EntryKind({
    required this.wire,
    required this.label,
    required this.blurb,
    required this.partyLabel,
    required this.accountLabel,
    required this.defaultAccount,
    this.takesLines = false,
    this.needsLines = false,
  });

  /// What the API is sent. Never shown to anybody.
  final String wire;
  final String label;
  final String blurb;

  /// The two sides of the entry. On a receipt and a payment they carry Dr and
  /// Cr, because the person filling them in keeps books in TallyPrime and reads
  /// an entry that way -- and the prefix is what tells a receipt's party field
  /// from a payment's, which are the same field to the API.
  final String partyLabel;
  final String accountLabel;
  final String defaultAccount;

  /// Whether stock lines may be attached.
  final bool takesLines;

  /// Whether the entry is meaningless without them. An order is a list of
  /// goods; an invoice can be a single figure.
  final bool needsLines;

  /// Every kind has a second ledger: cash or bank on a receipt or payment,
  /// the sales or purchase ledger that an invoice's or order's goods post to.
  bool get hasAccount => accountLabel.isNotEmpty;

  /// An order: its reference is its order number, written on every line.
  bool get isOrder => this == salesOrder || this == purchaseOrder;

}

/// Resolve a kind from a URL, falling back rather than throwing.
///
/// A link with a kind this build does not know -- an older app opening a
/// notification from a newer one -- should land on the form, not on a crash.
EntryKind entryKindFromWire(String? wire) {
  for (final EntryKind kind in EntryKind.values) {
    if (kind.wire == wire) {
      return kind;
    }
  }
  return EntryKind.receipt;
}

@immutable
class EntryLine {
  const EntryLine({
    required this.item,
    required this.quantity,
    required this.rate,
    this.unit,
  });

  final String item;
  final double quantity;
  final double rate;
  final String? unit;

  /// Derived, never entered. A line where the total disagrees with quantity ×
  /// rate is one the backend refuses, and letting somebody type all three is
  /// inviting exactly that.
  double get amount => quantity * rate;

  Map<String, Object?> toJson() => <String, Object?>{
        'item': item,
        'quantity': quantity.toString(),
        'rate': rate.toString(),
        'amount': amount.toStringAsFixed(2),
        if (unit != null && unit!.isNotEmpty) 'unit': unit,
      };
}

/// A duty or tax ledger on a sale or an order, at a rate.
///
/// The rate is kept rather than an amount, so the tax follows the items: add
/// a line after choosing CGST and the tax on it is already counted.
@immutable
class EntryTax {
  const EntryTax({required this.ledger, required this.rate});

  final String ledger;

  /// Percent, as somebody reads it off the ledger: 9 for "CGST 9%".
  final double rate;

  /// The tax on [taxable], to the paisa. Rounded here, once, so the figure on
  /// the send button is exactly the figure TallyPrime receives.
  double amountOn(double taxable) => (taxable * rate).round() / 100;
}

/// The rate written into a tax ledger's name -- "Output CGST 9%" gives 9 --
/// or null when the name carries none. Only ever a starting value: the field
/// it fills stays editable.
double? rateInName(String name) {
  final RegExpMatch? match =
      RegExp(r'(\d+(?:\.\d+)?)\s*%').firstMatch(name);
  return match == null ? null : double.tryParse(match.group(1)!);
}

/// A stock item as the line sheet offers it.
///
/// [rate] is the item's current stock rate in TallyPrime, offered as a
/// starting point only -- the price actually charged is agreed at the counter.
@immutable
class ItemOption {
  const ItemOption({required this.name, this.unit, this.rate});

  factory ItemOption.fromJson(Map<String, Object?> json) => ItemOption(
        name: json['name']! as String,
        unit: json['unit'] as String?,
        rate: double.tryParse(json['rate'] as String? ?? ''),
      );

  final String name;
  final String? unit;
  final double? rate;
}

@immutable
class EntryDraft {
  const EntryDraft({
    required this.kind,
    required this.date,
    required this.party,
    required this.amount,
    this.account,
    this.narration,
    this.reference,
    this.lines = const <EntryLine>[],
    this.taxes = const <EntryTax>[],
  });

  final EntryKind kind;
  final DateTime date;
  final String party;
  final double amount;
  final String? account;
  final String? narration;
  final String? reference;
  final List<EntryLine> lines;
  final List<EntryTax> taxes;

  /// What the party is charged: the taxable [amount] plus every tax on it.
  double get grandTotal => taxes.fold<double>(
        amount,
        (double sum, EntryTax t) => sum + t.amountOn(amount),
      );

  Map<String, Object?> toJson() => <String, Object?>{
        'kind': kind.wire,
        // Date only. A timestamp would carry the phone's timezone into a
        // financial year boundary, and 31 March at 23:00 in one zone is 1
        // April in another.
        'date': date.toIso8601String().split('T').first,
        'party': party,
        'amount': amount.toStringAsFixed(2),
        if (account != null && account!.isNotEmpty) 'account': account,
        if (narration != null && narration!.isNotEmpty) 'narration': narration,
        if (reference != null && reference!.isNotEmpty) 'reference': reference,
        if (lines.isNotEmpty)
          'lines': <Map<String, Object?>>[
            for (final EntryLine line in lines) line.toJson(),
          ],
        // Amounts, not rates: the server posts exactly what was on screen.
        if (taxes.isNotEmpty)
          'taxes': <Map<String, Object?>>[
            for (final EntryTax tax in taxes)
              <String, Object?>{
                'ledger': tax.ledger,
                'amount': tax.amountOn(amount).toStringAsFixed(2),
              },
          ],
      };
}

/// What came back. Deliberately not a bool.
@immutable
class EntryResult {
  const EntryResult({
    required this.ok,
    required this.awaitingApproval,
    this.voucherId,
    this.message,
    this.canRetry = false,
    this.queued = false,
    this.pendingId,
    this.missingKind,
    this.missingName,
  });

  factory EntryResult.fromJson(Map<String, Object?> json) => EntryResult(
        ok: json['ok'] == true,
        awaitingApproval: json['awaiting_approval'] == true,
        voucherId: json['voucher_id'] is int ? json['voucher_id'] as int : null,
        message: json['message'] as String?,
        // Defaulted false, so a backend that does not send it yet gets the
        // cautious behaviour: no retry offered rather than one that might
        // duplicate an entry.
        canRetry: json['can_retry'] == true,
        queued: json['queued'] == true,
        pendingId: json['pending_id'] as String?,
        missingKind: json['missing_kind'] as String?,
        missingName: json['missing_name'] as String?,
      );

  final bool ok;

  /// Saved, but counting towards nothing until somebody approves it in
  /// TallyPrime. The screen says which of the two happened, because telling
  /// somebody who just took ₹5,000 that it is "saved" when no balance moved
  /// would be true and misleading at the same time.
  final bool awaitingApproval;
  final int? voucherId;
  final String? message;

  /// Whether the app may offer to send this again.
  ///
  /// True only when something could prove nothing was written -- the PC was
  /// offline, TallyPrime was closed, the company was not open. After a timeout
  /// it stays false and the message sends the person to the Day Book instead,
  /// because a "Try again" button in that moment is how one receipt becomes
  /// two.
  final bool canRetry;

  /// Held on the server because the PC was not reachable, and will go when it
  /// comes back.
  ///
  /// Deliberately separate from [ok]: nothing is in the books yet. Treating a
  /// queued entry as saved is how somebody walks away from a counter believing
  /// a receipt is recorded when it is sitting in a queue.
  final bool queued;

  /// The queue row, so the screen can point at it or cancel it.
  final String? pendingId;

  /// Something the entry named is not in TallyPrime yet: `ledger` or
  /// `stock item`, and its name.
  ///
  /// Decided on the server from Tally's own complaint, so the screen offers to
  /// create exactly the missing thing rather than matching on message text.
  /// Absent for anything the app may not create -- a stock group or a unit is
  /// a decision about how a business classifies things.
  final String? missingKind;
  final String? missingName;

  /// Whether to offer creating what is missing.
  bool get canCreateMissing => missingKind != null && missingName != null;

  /// What to call it on a button, in the words a shopkeeper uses.
  String get missingLabel => missingKind == 'ledger' ? 'customer' : 'item';
}

/// One entry on its way, as the pending list shows it.
@immutable
class PendingEntry {
  const PendingEntry({
    required this.id,
    required this.kind,
    required this.state,
    required this.attempts,
    required this.isOpen,
    required this.createdAt,
    this.party,
    this.lastError,
  });

  factory PendingEntry.fromJson(Map<String, Object?> json) => PendingEntry(
        id: json['id'] as String? ?? '',
        kind: json['kind'] as String? ?? '',
        state: json['state'] as String? ?? 'waiting',
        attempts: json['attempts'] is int ? json['attempts']! as int : 0,
        isOpen: json['is_open'] == true,
        createdAt:
            DateTime.tryParse(json['created_at'] as String? ?? '')?.toLocal() ??
                DateTime.now(),
        party: json['party'] as String?,
        lastError: json['last_error'] as String?,
      );

  final String id;
  final String kind;

  /// `waiting`, `sending`, `sent`, `failed` or `cancelled`.
  final String state;
  final int attempts;

  /// Still on its way. What the badge counts.
  final bool isOpen;
  final DateTime createdAt;
  final String? party;

  /// TallyPrime's own words from the last attempt.
  final String? lastError;

  /// Only a waiting entry can be withdrawn. One already claimed by a delivery
  /// may be on the wire this second, and saying it was cancelled while it
  /// lands in somebody's books would be worse than refusing.
  bool get canCancel => state == 'waiting';

  String get label {
    const Map<String, String> names = <String, String>{
      'receipt': 'Receipt',
      'payment': 'Payment',
      'sales': 'Sale',
      'sales_order': 'Sales order',
      'purchase_order': 'Purchase order',
    };
    return names[kind] ?? kind;
  }
}
