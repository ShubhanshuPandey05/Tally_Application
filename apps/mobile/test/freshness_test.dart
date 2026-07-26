import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/model/freshness.dart';

/// Freshness is the one thing on screen that is never allowed to overstate
/// itself. These tests pin the behaviour that keeps an owner from acting on a
/// figure they believe is live.
void main() {
  group('Freshness', () {
    test('a missing meta block is "no data", not "fresh"', () {
      expect(Freshness.fromJson(null).available, isFalse);
      expect(Freshness.fromJson(const <String, Object?>{}).available, isFalse);
      // The dangerous default would be the optimistic one.
      expect(Freshness.fromJson(null).connectorOnline, isFalse);
      expect(Freshness.fromJson(null).needsAttention, isTrue);
    });

    test('a report envelope without `available` is still available', () {
      // Report metas carry a timestamp but no `available` flag; treating them
      // as unavailable would put a permanent "no data yet" banner over live
      // figures on five screens.
      final Freshness freshness = Freshness.fromJson(<String, Object?>{
        'refreshed_at': '2026-03-15T09:30:00+00:00',
        'age_seconds': 12.0,
        'is_stale': false,
        'connector_online': true,
        'from_snapshot': true,
      });
      expect(freshness.available, isTrue);
      expect(freshness.needsAttention, isFalse);
    });

    test('needs attention whenever data is stale or the PC is unreachable', () {
      Freshness build({required bool stale, required bool online}) =>
          Freshness.fromJson(<String, Object?>{
            'available': true,
            'refreshed_at': DateTime.now().toUtc().toIso8601String(),
            'is_stale': stale,
            'connector_online': online,
          });

      expect(build(stale: false, online: true).needsAttention, isFalse);
      expect(build(stale: true, online: true).needsAttention, isTrue);
      expect(build(stale: false, online: false).needsAttention, isTrue);
    });

    test('reads timestamps into local time', () {
      final Freshness freshness = Freshness.fromJson(<String, Object?>{
        'refreshed_at': '2026-03-15T09:30:00+00:00',
      });
      expect(freshness.refreshedAt!.isUtc, isFalse,
          reason: '"updated 10 minutes ago" must be in the reader\'s own time');
    });

    test('says how old the figures are in words a shopkeeper reads', () {
      String label(Duration ago) =>
          Freshness.relativeTime(DateTime.now().subtract(ago));

      expect(label(const Duration(seconds: 20)), 'just now');
      expect(label(const Duration(minutes: 12)), '12 min ago');
      expect(label(const Duration(hours: 1)), '1 hour ago');
      expect(label(const Duration(hours: 6)), '6 hours ago');
      expect(label(const Duration(days: 1)), '1 day ago');
      expect(label(const Duration(days: 3)), '3 days ago');
      expect(label(const Duration(days: 400)), startsWith('on '));
    });

    test('the banner label never claims data it does not have', () {
      expect(Freshness.unavailable.label, 'No data yet');
      final Freshness fresh = Freshness.fromJson(<String, Object?>{
        'available': true,
        'refreshed_at': DateTime.now().toUtc().toIso8601String(),
        'connector_online': true,
      });
      expect(fresh.label, 'Updated just now');
    });
  });

  group('Fresh<T>', () {
    test('carries data and its freshness together', () {
      const Fresh<int> value = Fresh<int>(42, Freshness.unavailable);
      expect(value.data, 42);
      expect(value.map<String>((int v) => '$v').data, '42');
      expect(value.map<String>((int v) => '$v').freshness, Freshness.unavailable);
    });
  });
}
