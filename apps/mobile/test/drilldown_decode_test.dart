import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/model/figures.dart';
import 'package:tallyflow/src/core/money/money.dart';
import 'package:tallyflow/src/core/money/money_format.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/features/reports/domain/drilldown.dart';

import 'support/fixtures.dart';

/// Decodes the drill-down responses the backend really sends.
///
/// The fixtures come from the backend's own suite against the real ASGI app, so
/// a field rename on the server fails here instead of showing up as an empty
/// voucher on a shopkeeper's phone. These screens are the ones somebody
/// reconciles against Tally itself, which makes a silent decoding gap more
/// expensive here than anywhere else in the app.
void main() {
  group('voucher detail', () {
    VoucherDetail load() =>
        VoucherDetail.fromJson(fixture('voucher').envelopeData);

    test('keeps every ledger line on its own side', () {
      final VoucherDetail detail = load();

      expect(detail.ledgerEntries, hasLength(2));
      expect(detail.ledgerEntries.first.ledger, 'Purchases');
      expect(detail.ledgerEntries.first.amount.side, MoneySide.debit);
      expect(detail.ledgerEntries.last.ledger, 'SARA DISITIBUTOR');
      // The side is the content of the row. Decoding it to a magnitude would
      // make the voucher unreadable as accounting while still looking right.
      expect(detail.ledgerEntries.last.amount.side, MoneySide.credit);
    });

    test('carries both totals so the screen can show it balances', () {
      final VoucherDetail detail = load();

      expect(MoneyFormat.full(detail.debitTotal), '₹2,12,600.00');
      expect(MoneyFormat.full(detail.creditTotal), '₹2,12,600.00');
      expect(detail.balances, isTrue);
    });

    test('an unbalanced voucher is reported, not quietly rendered', () {
      // Sides that disagree mean a line was dropped on the way in. Without this
      // the screen cannot tell that from a voucher with a single entry.
      final Map<String, Object?> body =
          Map<String, Object?>.from(fixture('voucher').envelopeData);
      body['credit_total'] = <String, Object?>{
        'amount': '1.00',
        'side': 'debit',
        'currency': 'INR',
      };

      expect(VoucherDetail.fromJson(body).balances, isFalse);
    });

    test('knows the voucher and where it came from', () {
      final VoucherDetail detail = load();

      expect(detail.voucherType, 'Purchase');
      expect(detail.voucherNumber, '3');
      expect(detail.party, 'SARA DISITIBUTOR');
      expect(detail.kind, 'purchase');
      expect(detail.key, isNotEmpty);
      expect(detail.isExcluded, isFalse);
    });

    test('a missing narration stays null rather than becoming empty text', () {
      // The screen hides the Details card entirely when there is nothing in it.
      // An empty string would render a labelled blank instead.
      expect(load().narration, isNull);
      expect(load().reference, isNull);
    });
  });

  group('ledger statement', () {
    LedgerStatement load() =>
        LedgerStatement.fromJson(fixture('ledger_statement').envelopeData);

    test('decodes the entries with their running total', () {
      final LedgerStatement statement = load();

      expect(statement.ledger, 'Reliance Retail');
      expect(statement.group, 'Sundry Debtors');
      expect(statement.entries, isNotEmpty);
      expect(statement.voucherCount, statement.entries.length);
      for (final StatementEntry entry in statement.entries) {
        expect(entry.line.key, isNotNull,
            reason: 'every statement row must open its voucher');
      }
    });

    test('the running total ends where the movement says it should', () {
      final LedgerStatement statement = load();
      final StatementEntry last = statement.entries.last;

      // The running column is the movement accumulated, so its final value and
      // the card's net figure are the same number arrived at two ways. They
      // drifting apart is exactly the bug this screen would hide well.
      expect(MoneyFormat.full(last.running), MoneyFormat.full(statement.netMovement));
      expect(last.running.side, statement.netMovement.side);
    });

    test('entries are drawn oldest first', () {
      final List<DateTime> dates =
          load().entries.map((StatementEntry e) => e.line.date).toList();
      // A running total that counts down the page is not a running total.
      expect(dates, List<DateTime>.from(dates)..sort());
    });

    test('movement and balance are separate figures', () {
      final LedgerStatement statement = load();

      // Tally evaluates a closing balance against today whatever window was
      // asked for, so these two can never be the same number and the screen
      // must not be able to render one as the other.
      expect(statement.netMovement, isNotNull);
      expect(
        statement.closingBalance,
        isNotNull,
        reason: 'the sample company has this ledger in its master list',
      );
    });

    test('a missing balance decodes to null, never to zero', () {
      // "We could not read the balance" and "the balance is nil" are different
      // statements about somebody's books.
      final Map<String, Object?> body =
          Map<String, Object?>.from(fixture('ledger_statement').envelopeData);
      body['closing_balance'] = null;

      expect(LedgerStatement.fromJson(body).closingBalance, isNull);
    });
  });

  group('registers', () {
    test('a sales register decodes its three cuts of the window', () {
      final RegisterReport report =
          RegisterReport.fromJson(fixture('register_sales').envelopeData);

      expect(report.kind, 'sales');
      expect(report.voucherCount, greaterThan(0));
      expect(report.months, isNotEmpty);
      expect(report.months.first.label, isNotEmpty);
      expect(report.byParty, isNotEmpty);
      expect(report.vouchers, isNotEmpty);
    });

    test('a purchase register is the same shape read the other way', () {
      final RegisterReport report =
          RegisterReport.fromJson(fixture('register_purchase').envelopeData);

      expect(report.kind, 'purchase');
      expect(report.byParty.first.name, 'SARA DISITIBUTOR');
    });

    test('the count describes the register, not the truncated list', () {
      final Map<String, Object?> body =
          Map<String, Object?>.from(fixture('register_sales').envelopeData);
      body['voucher_count'] = 900;
      body['truncated'] = true;

      final RegisterReport report = RegisterReport.fromJson(body);
      // The screen prints "showing N of 900". Reading the count off the list
      // would make a capped register look like a smaller one.
      expect(report.voucherCount, 900);
      expect(report.vouchers.length, lessThan(report.voucherCount));
      expect(report.truncated, isTrue);
    });

    test('every listed voucher can be opened', () {
      final RegisterReport report =
          RegisterReport.fromJson(fixture('register_sales').envelopeData);
      for (final TransactionLine line in report.vouchers) {
        expect(line.key, isNotNull);
      }
    });
  });

  group('stock movement', () {
    ItemMovementReport load() =>
        ItemMovementReport.fromJson(fixture('stock_movement').envelopeData);

    test('splits what came in from what went out', () {
      final ItemMovementReport report = load();

      expect(report.item, 'Rice');
      expect(report.movements, isNotEmpty);
      expect(report.quantityOut, greaterThan(0));
      expect(report.netQuantity, report.quantityIn - report.quantityOut);
    });

    test('direction is a decoded value, not a sign on the quantity', () {
      // Tally signs inventory lines inconsistently between voucher types, so
      // the app must never infer direction from the number.
      final ItemMovementReport report = load();
      expect(
        report.movements.map((ItemMovementLine m) => m.direction),
        everyElement(isIn(MovementDirection.values)),
      );
      expect(report.movements.every((ItemMovementLine m) => m.quantity >= 0), isTrue);
    });

    test('an unclassifiable voucher decodes as an adjustment', () {
      // Stock journals are listed and counted in neither total. Decoding one to
      // "in" or "out" would move a figure somebody is reconciling against.
      expect(MovementDirection.parse('anything else'), MovementDirection.other);
      expect(MovementDirection.parse(null), MovementDirection.other);
      expect(MovementDirection.parse('in'), MovementDirection.inward);
      expect(MovementDirection.parse('out'), MovementDirection.outward);
    });
  });

  group('voucher identity', () {
    test('every day book row carries the key its detail is fetched by', () {
      final Map<String, Object?> body = fixture('daybook').envelopeData;
      final List<Object?> rows = body['vouchers'] as List<Object?>;

      expect(rows, isNotEmpty);
      for (final Object? row in rows) {
        final TransactionLine line =
            TransactionLine.fromJson(row! as Map<String, Object?>);
        expect(line.key, isNotNull);
        expect(line.key, isNotEmpty);
      }
    });

    test('a row from an older backend simply does not open', () {
      // Forward compatibility in the direction that matters: a backend that
      // does not send a key must produce an inert row, not a tap that fails.
      final TransactionLine line = TransactionLine.fromJson(<String, Object?>{
        'date': '2026-03-15',
        'kind': 'sales',
        'amount': <String, Object?>{'amount': '10.00', 'side': 'debit'},
      });
      expect(line.key, isNull);
    });
  });
}
