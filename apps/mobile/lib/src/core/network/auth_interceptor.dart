import 'dart:async';

import 'package:dio/dio.dart';

import '../storage/token_store.dart';

/// Endpoints that must never carry a token or trigger a refresh.
const Set<String> _unauthenticatedPaths = <String>{
  '/v1/auth/login',
  '/v1/auth/register',
  '/v1/auth/refresh',
  '/v1/auth/logout',
  '/v1/health',
  '/v1/ready',
};

/// Attaches the access token and renews it exactly once at a time.
///
/// **Why single-flight matters here.** The backend rotates refresh tokens on
/// every use and revokes the whole family if a token is replayed -- that is the
/// defence against a stolen token. So two screens refreshing in parallel would
/// present the same refresh token twice, the server would correctly read the
/// second as a replay, and the user would be signed out of every device for the
/// crime of opening two tabs. Serialising refreshes is a correctness
/// requirement, not a performance tweak.
class AuthInterceptor extends Interceptor {
  AuthInterceptor({
    required TokenStore store,
    required Dio refreshClient,
    required Future<void> Function() onSignedOut,
  })  : _store = store,
        _refreshClient = refreshClient,
        _onSignedOut = onSignedOut;

  final TokenStore _store;

  /// A bare Dio without this interceptor. Refreshing through the intercepted
  /// client would recurse the moment the refresh call itself returned 401.
  final Dio _refreshClient;

  final Future<void> Function() _onSignedOut;

  Future<StoredSession?>? _inFlight;

  static bool _isPublic(String path) =>
      _unauthenticatedPaths.any((String candidate) => path.endsWith(candidate));

  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    if (_isPublic(options.path)) {
      return handler.next(options);
    }

    StoredSession? session = await _store.read();
    if (session == null) {
      return handler.reject(
        DioException(
          requestOptions: options,
          response: Response<Object?>(
            requestOptions: options,
            statusCode: 401,
            data: const <String, Object?>{
              'error': <String, Object?>{
                'code': 'unauthenticated',
                'message': 'Please sign in again.',
              },
            },
          ),
        ),
        true,
      );
    }

    // Renew proactively. Waiting for the 401 would cost a wasted round trip on
    // a mobile connection for every screen opened after the token lapses.
    if (!session.isAccessUsable) {
      session = await _refresh();
      if (session == null) {
        return handler.reject(_authFailure(options), true);
      }
    }

    options.headers['Authorization'] = 'Bearer ${session.accessToken}';
    handler.next(options);
  }

  @override
  Future<void> onError(DioException err, ErrorInterceptorHandler handler) async {
    final RequestOptions options = err.requestOptions;
    final bool retryable = err.response?.statusCode == 401 &&
        !_isPublic(options.path) &&
        options.extra['retried'] != true;

    if (!retryable) {
      return handler.next(err);
    }

    final StoredSession? session = await _refresh();
    if (session == null) {
      return handler.next(err);
    }

    options.extra['retried'] = true;
    options.headers['Authorization'] = 'Bearer ${session.accessToken}';
    try {
      final Response<Object?> response = await _refreshClient.fetch<Object?>(options);
      handler.resolve(response);
    } on DioException catch (retryError) {
      handler.next(retryError);
    }
  }

  Future<StoredSession?> _refresh() {
    return _inFlight ??= _performRefresh().whenComplete(() => _inFlight = null);
  }

  Future<StoredSession?> _performRefresh() async {
    final StoredSession? current = await _store.read();
    if (current == null) return null;

    try {
      final Response<Object?> response = await _refreshClient.post<Object?>(
        '/v1/auth/refresh',
        data: <String, Object?>{'refresh_token': current.refreshToken},
      );
      final Object? body = response.data;
      if (body is! Map) return null;

      final StoredSession renewed = StoredSession(
        accessToken: body['access_token'] as String,
        refreshToken: body['refresh_token'] as String,
        accessExpiresAt: DateTime.now()
            .add(Duration(seconds: (body['expires_in'] as num?)?.toInt() ?? 900)),
      );
      await _store.write(renewed);
      return renewed;
    } on DioException {
      // The refresh token is gone, expired, or was revoked as a replay. There
      // is no recovery except signing in again, and holding on to dead
      // credentials would just fail every subsequent screen the same way.
      await _store.clear();
      await _onSignedOut();
      return null;
    }
  }

  DioException _authFailure(RequestOptions options) => DioException(
        requestOptions: options,
        response: Response<Object?>(
          requestOptions: options,
          statusCode: 401,
          data: const <String, Object?>{
            'error': <String, Object?>{
              'code': 'unauthenticated',
              'message': 'Your session has expired. Please sign in again.',
            },
          },
        ),
      );
}
