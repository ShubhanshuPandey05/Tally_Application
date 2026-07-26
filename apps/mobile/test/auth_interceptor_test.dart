import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/config/app_config.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/core/storage/token_store.dart';

import 'support/fake_http.dart';

const AppConfig _config = AppConfig(baseUrl: 'https://api.test', environment: 'test');

Map<String, Object?> _tokenBody(String suffix) => <String, Object?>{
      'access_token': 'access-$suffix',
      'refresh_token': 'refresh-$suffix',
      'token_type': 'Bearer',
      'expires_in': 900,
    };

Map<String, Object?> _error(String code, String message) => <String, Object?>{
      'error': <String, Object?>{
        'code': code,
        'message': message,
        'retryable': false,
      }
    };

void main() {
  late FakeAdapter adapter;
  late FakeSecureStorage storage;
  late TokenStore tokens;
  late int signedOutCalls;

  ApiClient build() {
    final Dio dio = Dio(BaseOptions(baseUrl: _config.baseUrl))..httpClientAdapter = adapter;
    final Dio refreshDio = Dio(BaseOptions(baseUrl: _config.baseUrl))
      ..httpClientAdapter = adapter;
    return ApiClient(
      config: _config,
      tokens: tokens,
      dio: dio,
      refreshDio: refreshDio,
      onSignedOut: () async => signedOutCalls++,
    );
  }

  Future<void> seedSession({required bool expired}) async {
    await tokens.write(
      StoredSession(
        accessToken: 'access-old',
        refreshToken: 'refresh-old',
        accessExpiresAt: DateTime.now().add(
          expired ? const Duration(seconds: -10) : const Duration(minutes: 30),
        ),
      ),
    );
  }

  setUp(() {
    adapter = FakeAdapter();
    storage = FakeSecureStorage();
    tokens = TokenStore(storage: storage);
    signedOutCalls = 0;
  });

  test('attaches the access token to authenticated requests', () async {
    await seedSession(expired: false);
    adapter.always('/v1/companies', body: <String, Object?>{'ok': true});

    await build().getJson('/v1/companies');

    expect(adapter.requests.single.headers['Authorization'], 'Bearer access-old');
  });

  test('never sends a token to the sign-in endpoint', () async {
    await seedSession(expired: false);
    adapter.enqueue('/v1/auth/login', body: _tokenBody('new'));

    await build().postJson('/v1/auth/login', body: <String, Object?>{'email': 'x'});

    expect(adapter.requests.single.headers.containsKey('Authorization'), isFalse);
  });

  test('renews a lapsed token before sending, not after being refused', () async {
    // Waiting for the 401 would cost an extra round trip on mobile data for
    // every screen opened after the token expires.
    await seedSession(expired: true);
    adapter.enqueue('/v1/auth/refresh', body: _tokenBody('fresh'));
    adapter.always('/v1/companies', body: <String, Object?>{'ok': true});

    await build().getJson('/v1/companies');

    expect(adapter.requests.first.path, '/v1/auth/refresh');
    expect(adapter.requests.last.headers['Authorization'], 'Bearer access-fresh');
    expect(adapter.countFor('/v1/companies'), 1);
  });

  test('retries once after a 401 and returns the retried response', () async {
    await seedSession(expired: false);
    adapter.enqueue('/v1/companies',
        status: 401, body: _error('unauthenticated', 'Please sign in again.'));
    adapter.enqueue('/v1/auth/refresh', body: _tokenBody('fresh'));
    adapter.enqueue('/v1/companies', body: <String, Object?>{'ok': true});

    final Map<String, Object?> result = await build().getJson('/v1/companies');

    expect(result['ok'], true);
    expect(adapter.countFor('/v1/companies'), 2);
    expect(adapter.countFor('/v1/auth/refresh'), 1);
  });

  test('does not loop when the retried request is refused again', () async {
    await seedSession(expired: false);
    adapter.always('/v1/companies',
        status: 401, body: _error('unauthenticated', 'Please sign in again.'));
    adapter.enqueue('/v1/auth/refresh', body: _tokenBody('fresh'));

    await expectLater(
      build().getJson('/v1/companies'),
      throwsA(isA<ApiException>()),
    );

    // Original + one retry. A second refresh here would be an infinite loop
    // against a server that has decided this session is over.
    expect(adapter.countFor('/v1/companies'), 2);
    expect(adapter.countFor('/v1/auth/refresh'), 1);
  });

  test('concurrent requests share a single refresh', () async {
    // The load-bearing one. The backend rotates refresh tokens and treats a
    // reused one as a stolen-token replay, revoking the whole family. Two
    // parallel refreshes would present the same token twice and sign the user
    // out of every device for opening two screens at once.
    await seedSession(expired: true);
    adapter.enqueue('/v1/auth/refresh',
        body: _tokenBody('fresh'), delay: const Duration(milliseconds: 40));
    adapter.always('/v1/companies', body: <String, Object?>{'ok': true});

    final ApiClient client = build();
    await Future.wait<Object?>(<Future<Object?>>[
      client.getJson('/v1/companies'),
      client.getJson('/v1/companies'),
      client.getJson('/v1/companies'),
      client.getJson('/v1/companies'),
    ]);

    expect(adapter.countFor('/v1/auth/refresh'), 1,
        reason: 'a second refresh would look like a replay to the server');
    expect(adapter.countFor('/v1/companies'), 4);
  });

  test('a rotated refresh token is persisted, not just used', () async {
    await seedSession(expired: true);
    adapter.enqueue('/v1/auth/refresh', body: _tokenBody('rotated'));
    adapter.always('/v1/companies', body: <String, Object?>{'ok': true});

    await build().getJson('/v1/companies');

    final Map<String, Object?> stored =
        jsonDecode(storage.values['tallyflow.session']!) as Map<String, Object?>;
    // Keeping the old one would make the *next* launch present a spent token,
    // which the server reads as a replay.
    expect(stored['refresh_token'], 'refresh-rotated');
    expect(stored['access_token'], 'access-rotated');
  });

  test('a rejected refresh clears the session and signals sign-out', () async {
    await seedSession(expired: true);
    adapter.enqueue('/v1/auth/refresh',
        status: 401, body: _error('unauthenticated', 'Please sign in again.'));

    await expectLater(
      build().getJson('/v1/companies'),
      throwsA(isA<ApiException>().having(
        (ApiException error) => error.isAuthFailure,
        'isAuthFailure',
        isTrue,
      )),
    );

    expect(storage.values, isEmpty, reason: 'dead credentials must not linger');
    expect(signedOutCalls, 1);
    expect(adapter.countFor('/v1/companies'), 0);
  });

  test('requesting without a session fails fast instead of hitting the API',
      () async {
    adapter.always('/v1/companies', body: <String, Object?>{'ok': true});

    await expectLater(
      build().getJson('/v1/companies'),
      throwsA(isA<ApiException>().having(
        (ApiException error) => error.code,
        'code',
        'unauthenticated',
      )),
    );
    expect(adapter.requests, isEmpty);
  });

  group('error translation', () {
    test('keeps the backend message written for a business owner', () async {
      await seedSession(expired: false);
      adapter.always(
        '/v1/companies/x/dashboard',
        status: 503,
        body: <String, Object?>{
          'error': <String, Object?>{
            'code': 'connector_offline',
            'message': 'Your Tally PC is offline. Data shown was last updated earlier.',
            'retryable': true,
          }
        },
      );

      try {
        await build().getJson('/v1/companies/x/dashboard');
        fail('expected an ApiException');
      } on ApiException catch (error) {
        expect(error.code, 'connector_offline');
        expect(error.message, contains('Tally PC is offline'));
        expect(error.retryable, isTrue);
        expect(error.isConnectivity, isTrue);
      }
    });

    test('marks the first-run case so the app shows an empty state', () async {
      await seedSession(expired: false);
      adapter.always(
        '/v1/companies/x/reports/stock',
        status: 503,
        body: <String, Object?>{
          'error': <String, Object?>{
            'code': 'no_data_yet',
            'message': 'We have not been able to read this from Tally yet.',
            'retryable': true,
          }
        },
      );

      try {
        await build().getJson('/v1/companies/x/reports/stock');
        fail('expected an ApiException');
      } on ApiException catch (error) {
        // Rendered as "waiting for your first read", not as a failure.
        expect(error.isFirstRun, isTrue);
      }
    });

    test('reads field errors out of a 422', () async {
      await seedSession(expired: false);
      adapter.always(
        '/v1/companies',
        status: 422,
        body: <String, Object?>{
          'error': <String, Object?>{
            'code': 'invalid_request',
            'message': 'Some of the information sent was not valid.',
            'retryable': false,
            'detail': <String, Object?>{
              'fields': <Object?>[
                <String, Object?>{'field': 'email', 'problem': 'not a valid email'},
              ],
            },
          }
        },
      );

      try {
        await build().getJson('/v1/companies');
        fail('expected an ApiException');
      } on ApiException catch (error) {
        expect(error.fields['email'], 'not a valid email');
      }
    });

    test('a dead network is a retryable, plainly worded failure', () async {
      await seedSession(expired: false);
      final Dio dio = Dio(BaseOptions(baseUrl: 'https://unreachable.invalid'));
      final ApiClient client = ApiClient(
        config: _config,
        tokens: tokens,
        dio: dio,
        refreshDio: Dio(),
        onSignedOut: () async {},
      );

      try {
        await client.getJson('/v1/companies');
        fail('expected an ApiException');
      } on ApiException catch (error) {
        expect(error.isConnectivity, isTrue);
        expect(error.retryable, isTrue);
        expect(error.message, isNot(contains('DioException')));
      }
    });
  });
}
