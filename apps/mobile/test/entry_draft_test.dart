import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/entries/domain/entry_draft.dart';

/// What the phone puts on the wire.
///
/// Small, but it is the boundary where a rupee figure and a date turn into
/// strings the backend parses, and both have a way of going wrong that no
/// screen would show.
void main() {
  group('entry draft', () {
    test('sends the amount as a fixed string, never a float', () {
      final EntryDraft draft = EntryDraft(
        kind: EntryKind.receipt,
        date: DateTime(2026, 9, 17),
        party: 'Ram & Sons',
        amount: 5000,
      );

      // A JSON number here would hand the backend a binary float for somebody's
      // money. Decimal is the point of the whole money design.
      expect(draft.toJson()['amount'], '5000.00');
      expect(draft.toJson()['amount'], isA<String>());
    });

    test('sends a date, not a timestamp', () {
      final EntryDraft draft = EntryDraft(
        kind: EntryKind.receipt,
        // Late on 31 March: with a timezone attached this is 1 April somewhere,
        // which is a different financial year and a different set of books.
        date: DateTime(2026, 3, 31, 23, 30),
        party: 'Ram & Sons',
        amount: 100,
      );

      expect(draft.toJson()['date'], '2026-03-31');
    });

    test('leaves optional fields out rather than sending blanks', () {
      final EntryDraft draft = EntryDraft(
        kind: EntryKind.receipt,
        date: DateTime(2026, 9, 17),
        party: 'Ram & Sons',
        amount: 100,
        narration: '',
        reference: '',
      );

      final Map<String, Object?> json = draft.toJson();
      expect(json.containsKey('narration'), isFalse);
      expect(json.containsKey('reference'), isFalse);
    });

    test('a line amount is derived, so it cannot disagree with the maths', () {
      const EntryLine line = EntryLine(
        item: 'Sugar 1kg',
        quantity: 10,
        rate: 118,
        unit: 'Nos',
      );

      expect(line.amount, 1180);
      expect(line.toJson()['amount'], '1180.00');
    });

    test('an order carries its lines', () {
      final EntryDraft draft = EntryDraft(
        kind: EntryKind.purchaseOrder,
        date: DateTime(2026, 9, 17),
        party: 'Wholesaler',
        amount: 1180,
        lines: const <EntryLine>[
          EntryLine(item: 'Sugar 1kg', quantity: 10, rate: 118),
        ],
      );

      expect((draft.toJson()['lines']! as List<Object?>).length, 1);
    });
  });

  group('entry kinds', () {
    test('an order names the ledger its goods post to', () {
      // TallyPrime's own orders carry it on every line, like an invoice.
      expect(EntryKind.salesOrder.accountLabel, 'Sales ledger');
      expect(EntryKind.purchaseOrder.accountLabel, 'Purchase ledger');
      expect(EntryKind.salesOrder.isOrder, isTrue);
      expect(EntryKind.sales.isOrder, isFalse);
    });

    test('an order is meaningless without lines', () {
      expect(EntryKind.salesOrder.needsLines, isTrue);
      expect(EntryKind.purchaseOrder.needsLines, isTrue);
      // An invoice can legitimately be a single figure with no stock behind it.
      expect(EntryKind.sales.needsLines, isFalse);
    });

    test('the two sides are named the way the person thinks of them', () {
      expect(EntryKind.receipt.partyLabel, 'Cr: Party');
      expect(EntryKind.receipt.accountLabel, 'Dr: Cash/Bank');
      expect(EntryKind.payment.partyLabel, 'Dr: Party');
      expect(EntryKind.payment.accountLabel, 'Cr: Cash/Bank');
    });

    test('an unknown kind in a link lands on the form, not a crash', () {
      expect(entryKindFromWire('journal'), EntryKind.receipt);
      expect(entryKindFromWire(null), EntryKind.receipt);
      expect(entryKindFromWire('purchase_order'), EntryKind.purchaseOrder);
    });
  });

  group('entry result', () {
    test('saved and waiting is not the same as saved', () {
      final EntryResult waiting = EntryResult.fromJson(const <String, Object?>{
        'ok': true,
        'awaiting_approval': true,
        'voucher_id': 4821,
      });

      expect(waiting.ok, isTrue);
      expect(waiting.awaitingApproval, isTrue);
      expect(waiting.voucherId, 4821);
    });

    test('a failure carries the reason it failed', () {
      final EntryResult failed = EntryResult.fromJson(const <String, Object?>{
        'ok': false,
        'awaiting_approval': false,
        'message': "Ledger 'Ram Traders' does not exist!",
      });

      expect(failed.ok, isFalse);
      // Tally's own words: something the person can act on.
      expect(failed.message, contains('Ram Traders'));
    });

    test('a retry is offered only when the server says it is safe', () {
      final EntryResult offline = EntryResult.fromJson(const <String, Object?>{
        'ok': false,
        'awaiting_approval': false,
        'can_retry': true,
        'message': 'Your Tally PC is offline, so nothing was saved.',
      });
      final EntryResult timedOut = EntryResult.fromJson(const <String, Object?>{
        'ok': false,
        'awaiting_approval': false,
        'can_retry': false,
        'message': 'TallyPrime did not confirm in time.',
      });

      expect(offline.canRetry, isTrue);
      // The dangerous one. The entry may already be in Tally, so the screen
      // must not put a button in front of somebody that creates a second one.
      expect(timedOut.canRetry, isFalse);
    });

    test('a backend that does not send can_retry offers no retry', () {
      // The cautious default: an older server is treated as "we do not know",
      // which is the same as "do not offer".
      final EntryResult old = EntryResult.fromJson(const <String, Object?>{
        'ok': false,
        'awaiting_approval': false,
      });

      expect(old.canRetry, isFalse);
    });

    test('a reply missing fields does not throw', () {
      // An older backend, or a truncated response. The screen shows a failure
      // rather than crashing on somebody who just tried to record a payment.
      final EntryResult empty = EntryResult.fromJson(const <String, Object?>{});

      expect(empty.ok, isFalse);
      expect(empty.awaitingApproval, isFalse);
      expect(empty.voucherId, isNull);
    });
  });

  group('pending entries', () {
    test('queued is not the same as saved', () {
      final EntryResult queued = EntryResult.fromJson(const <String, Object?>{
        'ok': false,
        'awaiting_approval': false,
        'queued': true,
        'pending_id': 'abc123',
      });

      // Both matter. Treating queued as saved is how somebody walks away from
      // a counter believing a receipt is recorded; treating it as a failure
      // would have them type it in again and create two.
      expect(queued.queued, isTrue);
      expect(queued.ok, isFalse);
      expect(queued.pendingId, 'abc123');
    });

    test('only a waiting entry can be withdrawn', () {
      PendingEntry at(String state) => PendingEntry.fromJson(<String, Object?>{
            'id': 'x',
            'kind': 'receipt',
            'state': state,
            'attempts': 1,
            'is_open': state == 'waiting' || state == 'sending',
            'created_at': '2026-09-17T10:00:00Z',
          });

      expect(at('waiting').canCancel, isTrue);
      // Already claimed by a delivery -- it may be on the wire this second.
      expect(at('sending').canCancel, isFalse);
      expect(at('sent').canCancel, isFalse);
      expect(at('failed').canCancel, isFalse);
    });

    test('only open entries count towards the badge', () {
      List<PendingEntry> of(List<String> states) => <PendingEntry>[
            for (final String s in states)
              PendingEntry.fromJson(<String, Object?>{
                'id': s,
                'kind': 'receipt',
                'state': s,
                'attempts': 0,
                'is_open': s == 'waiting' || s == 'sending',
                'created_at': '2026-09-17T10:00:00Z',
              }),
          ];

      final List<PendingEntry> all =
          of(<String>['waiting', 'sending', 'sent', 'failed', 'cancelled']);

      expect(all.where((PendingEntry e) => e.isOpen).length, 2);
    });

    test('a kind is shown as a name, never a wire value', () {
      PendingEntry of(String kind) => PendingEntry.fromJson(<String, Object?>{
            'id': 'x',
            'kind': kind,
            'state': 'waiting',
            'attempts': 0,
            'is_open': true,
            'created_at': '2026-09-17T10:00:00Z',
          });

      expect(of('purchase_order').label, 'Purchase order');
      expect(of('sales').label, 'Sales invoice');
    });
  });

  group('a missing master', () {
    EntryResult missing(String? kind, String? name) =>
        EntryResult.fromJson(<String, Object?>{
          'ok': false,
          'awaiting_approval': false,
          'missing_kind': kind,
          'missing_name': name,
          'message': "Ledger 'Ram Traders' does not exist!",
        });

    test('is offered when the server named it', () {
      final EntryResult result = missing('ledger', 'Ram Traders');

      expect(result.canCreateMissing, isTrue);
      expect(result.missingName, 'Ram Traders');
      // The word a shopkeeper uses, not Tally's.
      expect(result.missingLabel, 'customer');
    });

    test('an item is labelled as an item', () {
      expect(missing('stock item', 'Sugar 1kg').missingLabel, 'item');
    });

    test('is not offered for something the app may not create', () {
      // A stock group or a unit is a decision about how a business classifies
      // things. The server sends no name for those, so nothing is offered.
      expect(missing(null, null).canCreateMissing, isFalse);
    });

    test('is not offered when only half of it arrives', () {
      // Defensive: a partial response must not produce a dialog offering to
      // create something with no name.
      expect(missing('ledger', null).canCreateMissing, isFalse);
      expect(missing(null, 'Ram Traders').canCreateMissing, isFalse);
    });
  });

  group('taxes', () {
    test('a rate is read out of the ledger name when it is there', () {
      expect(rateInName('Output CGST 9%'), 9);
      expect(rateInName('IGST @ 18 %'), 18);
      expect(rateInName('CGST 2.5%'), 2.5);
      expect(rateInName('Cess'), isNull);
    });

    test('a tax is sent as the amount shown, on top of the taxable value', () {
      final EntryDraft draft = EntryDraft(
        kind: EntryKind.sales,
        date: DateTime(2026, 9, 19),
        party: 'Ram Traders',
        amount: 1234.5,
        taxes: const <EntryTax>[
          EntryTax(ledger: 'Output CGST 9%', rate: 9),
          EntryTax(ledger: 'Output SGST 9%', rate: 9),
        ],
      );

      final Map<String, Object?> json = draft.toJson();
      // The taxable value stays the amount; tax is on top, to the paisa.
      expect(json['amount'], '1234.50');
      expect(json['taxes'], <Map<String, Object?>>[
        <String, Object?>{'ledger': 'Output CGST 9%', 'amount': '111.11'},
        <String, Object?>{'ledger': 'Output SGST 9%', 'amount': '111.11'},
      ]);
      expect(draft.grandTotal, closeTo(1456.72, 0.001));
    });
  });
}
