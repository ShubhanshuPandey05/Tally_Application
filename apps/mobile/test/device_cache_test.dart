import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/config/app_config.dart';
import 'package:tallyflow/src/core/model/freshness.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/core/network/reachability.dart';
import 'package:tallyflow/src/core/storage/read_cache.dart';
import 'package:tallyflow/src/core/storage/token_store.dart';

import 'support/fake_http.dart';

const AppConfig _config = AppConfig(baseUrl: 'https://api.test', environment: 'test');
const String _stock = '/v1/companies/c1/reports/stock';

Map<String, Object?> _report(String marker) => <String, Object?>{
      'data': <String, Object?>{'marker': marker},
      'meta': <String, Object?>{
        'refreshed_at': DateTime.now().toUtc().toIso8601String(),
        'connector_online': true,
      },
    };

/// A cache whose entries can be given any age, which [MemoryReadCache] -- by
/// design -- cannot.
class _AgedCache implements ReadCache {
  final Map<String, SavedRead> entries = <String, SavedRead>{};

  void seed(String key, Object? body, {required Duration age}) =>
      entries[key] = SavedRead(body, DateTime.now().subtract(age));

  @override
  Future<SavedRead?> read(String key) async => entries[key];

  @override
  Future<void> write(String key, Object? body) async =>
      entries[key] = SavedRead(body, DateTime.now());

  @override
  Future<void> clear() async => entries.clear();
}

void main() {
  late FakeAdapter adapter;
  late _AgedCache cache;
  late Reachability reachability;

  ApiClient build() {
    final TokenStore tokens = TokenStore(storage: FakeSecureStorage());
    unawaited(tokens.write(
      StoredSession(
        accessToken: 'access',
        refreshToken: 'refresh',
        accessExpiresAt: DateTime.now().add(const Duration(hours: 1)),
      ),
    ));
    return ApiClient(
      config: _config,
      tokens: tokens,
      cache: cache,
      reachability: reachability,
      dio: Dio(BaseOptions(baseUrl: _config.baseUrl))..httpClientAdapter = adapter,
      refreshDio: Dio(BaseOptions(baseUrl: _config.baseUrl))..httpClientAdapter = adapter,
      onSignedOut: () async {},
    );
  }

  setUp(() {
    adapter = FakeAdapter();
    cache = _AgedCache();
    reachability = Reachability();
  });

  group('the key a read is saved under', () {
    test('ignores the fetch mode, so a refresh replaces what screens read', () {
      expect(
        ReadCache.keyFor(_stock, <String, Object?>{'mode': 'live', 'only': 'low'}),
        ReadCache.keyFor(_stock, <String, Object?>{'only': 'low', 'mode': 'auto'}),
      );
    });

    test('distinguishes everything else', () {
      expect(
        ReadCache.keyFor(_stock, <String, Object?>{'only': 'low'}),
        isNot(ReadCache.keyFor(_stock, <String, Object?>{'only': 'negative'})),
      );
    });
  });

  group('keep', () {
    test('saves what the server said', () async {
      adapter.always(_stock, body: _report('server'));

      await build().getJson(_stock, cache: CachePolicy.keep);

      final SavedRead? saved = await cache.read(ReadCache.keyFor(_stock, null));
      expect((saved!.body! as Map<String, Object?>)['data'], <String, Object?>{'marker': 'server'});
    });

    test('answers from the phone with no signal, and says so', () async {
      cache.seed(ReadCache.keyFor(_stock, null), _report('saved'),
          age: const Duration(hours: 3));
      adapter.offline = true;

      final Map<String, Object?> body =
          await build().getJson(_stock, cache: CachePolicy.keep);

      expect(body.envelopeData['marker'], 'saved');
      expect(body.envelopeFreshness.fromDevice, isTrue);
      expect(reachability.online, isFalse);
    });

    test('with no signal and no copy, the failure is still offline', () async {
      adapter.offline = true;

      await expectLater(
        build().getJson(_stock, cache: CachePolicy.keep),
        throwsA(isA<ApiException>()
            .having((ApiException e) => e.isUnreachable, 'isUnreachable', isTrue)),
      );
    });

    test('an answer from the server is never hidden behind an old copy', () async {
      // A lapsed subscription served as yesterday's figures would be a lapsed
      // subscription nobody is ever told about.
      cache.seed(ReadCache.keyFor(_stock, null), _report('saved'),
          age: const Duration(hours: 3));
      adapter.always(_stock, status: 402, body: <String, Object?>{
        'error': <String, Object?>{'code': 'subscription_inactive', 'message': 'Lapsed.'},
      });

      await expectLater(
        build().getJson(_stock, cache: CachePolicy.keep),
        throwsA(isA<ApiException>().having(
            (ApiException e) => e.isSubscriptionInactive, 'isSubscriptionInactive', isTrue)),
      );
      expect(reachability.online, isTrue);
    });
  });

  group('device first', () {
    test('a copy from moments ago answers without asking the server', () async {
      cache.seed(ReadCache.keyFor(_stock, null), _report('saved'),
          age: const Duration(seconds: 5));
      int newer = 0;

      final Map<String, Object?> body = await build()
          .getJson(_stock, cache: CachePolicy.deviceFirst(onNewer: () => newer++));

      expect(body.envelopeData['marker'], 'saved');
      expect(adapter.requests, isEmpty);
      expect(newer, 0);
    });

    test('an older copy answers at once, then is checked behind the screen',
        () async {
      cache.seed(ReadCache.keyFor(_stock, null), _report('saved'),
          age: ApiClient.revalidateAfter + const Duration(seconds: 1));
      adapter.always(_stock, body: _report('server'));
      final Completer<void> newer = Completer<void>();

      final Map<String, Object?> body = await build()
          .getJson(_stock, cache: CachePolicy.deviceFirst(onNewer: newer.complete));

      expect(body.envelopeData['marker'], 'saved');
      await newer.future;
      final SavedRead? saved = await cache.read(ReadCache.keyFor(_stock, null));
      expect((saved!.body! as Map<String, Object?>)['data'], <String, Object?>{'marker': 'server'});
      expect(saved.age, lessThan(const Duration(seconds: 5)));
    });

    test('a failed check leaves the screen alone rather than rebuilding it',
        () async {
      // Rebuilding on failure would read the same old copy, find it old,
      // check again, and loop for as long as the phone has no signal.
      cache.seed(ReadCache.keyFor(_stock, null), _report('saved'),
          age: const Duration(hours: 1));
      adapter.offline = true;
      int newer = 0;

      await build()
          .getJson(_stock, cache: CachePolicy.deviceFirst(onNewer: () => newer++));
      // Past both retries' backoff.
      await Future<void>.delayed(const Duration(seconds: 2));

      expect(newer, 0);
    });

    test('with no copy yet, it is an ordinary read that keeps one', () async {
      adapter.always(_stock, body: _report('server'));

      final Map<String, Object?> body = await build()
          .getJson(_stock, cache: CachePolicy.deviceFirst(onNewer: () {}));

      expect(body.envelopeFreshness.fromDevice, isFalse);
      expect(await cache.read(ReadCache.keyFor(_stock, null)), isNotNull);
    });
  });

  group('a copy is judged by the age of its figures', () {
    test('an old copy needs attention even though it was fresh when saved', () {
      // `is_stale` was the server's verdict hours ago, on a phone with no
      // signal since. A green tick over it would be a claim we cannot make.
      final Freshness saved = Freshness(
        available: true,
        refreshedAt: DateTime.now().subtract(const Duration(hours: 2)),
        connectorOnline: true,
      ).savedOnDevice(DateTime.now().subtract(const Duration(hours: 2)));

      expect(saved.isStale, isFalse);
      expect(saved.needsAttention, isTrue);
    });

    test('a recent copy does not cry wolf', () {
      final Freshness saved = Freshness(
        available: true,
        refreshedAt: DateTime.now().subtract(const Duration(minutes: 2)),
        connectorOnline: true,
      ).savedOnDevice(DateTime.now());

      expect(saved.needsAttention, isFalse);
    });
  });
}
