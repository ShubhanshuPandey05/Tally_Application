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

  Future<_Renewal>? _inFlight;

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
      final _Renewal renewal = await _refresh();
      final DioException? unreachable = renewal.unreachable;
      if (unreachable != null) {
        // Surfaced as the connectivity failure it is, not as a 401. The
        // session is intact; this request simply could not be made, and the
        // caller falls back to what the phone already holds.
        return handler.reject(_asFailureOf(options, unreachable), true);
      }
      session = renewal.session;
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

    final _Renewal renewal = await _refresh();
    final DioException? unreachable = renewal.unreachable;
    if (unreachable != null) {
      // Passing the original 401 on would read upstream as "signed out" when
      // all that happened is the renewal could not get through.
      return handler.next(_asFailureOf(options, unreachable));
    }
    final StoredSession? session = renewal.session;
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

  Future<_Renewal> _refresh() {
    return _inFlight ??= _performRefresh().whenComplete(() => _inFlight = null);
  }

  Future<_Renewal> _performRefresh() async {
    final StoredSession? current = await _store.read();
    if (current == null) return const _Renewal.rejected();

    try {
      final Response<Object?> response = await _refreshClient.post<Object?>(
        '/v1/auth/refresh',
        data: <String, Object?>{'refresh_token': current.refreshToken},
      );
      final Object? body = response.data;
      if (body is! Map) return _Renewal.unreachable(_unreadable(response));

      final StoredSession renewed = StoredSession(
        accessToken: body['access_token'] as String,
        refreshToken: body['refresh_token'] as String,
        accessExpiresAt: DateTime.now()
            .add(Duration(seconds: (body['expires_in'] as num?)?.toInt() ?? 900)),
      );
      await _store.write(renewed);
      return _Renewal.renewed(renewed);
    } on DioException catch (error) {
      // Only the server saying no ends a session. This used to clear the
      // tokens on *any* failure, so opening the app with no signal -- the
      // access token long expired, the renewal unable to leave the phone --
      // signed people out of the very app that keeps their figures for
      // exactly that moment.
      if (!_isRejection(error)) return _Renewal.unreachable(error);

      // The refresh token is gone, expired, or was revoked as a replay. There
      // is no recovery except signing in again, and holding on to dead
      // credentials would just fail every subsequent screen the same way.
      await _store.clear();
      await _onSignedOut();
      return const _Renewal.rejected();
    }
  }

  /// Whether the backend refused the refresh token itself.
  ///
  /// It answers 401 for an unknown, expired, replayed or deactivated token and
  /// 422 for one it cannot parse. Everything else -- no connection, a timeout,
  /// a 5xx during a deploy, a 429, a 426 asking for an update -- says nothing
  /// about the token, and a customer must not have to sign in again because
  /// the server restarted while they were opening the app.
  static bool _isRejection(DioException error) {
    final int? status = error.response?.statusCode;
    return status == 400 || status == 401 || status == 403 || status == 422;
  }

  static DioException _unreadable(Response<Object?> response) => DioException(
        requestOptions: response.requestOptions,
        type: DioExceptionType.badResponse,
        message: 'unreadable refresh response',
      );

  /// The renewal's failure, re-addressed to the request that was waiting on it.
  ///
  /// Keeps the renewal's own response, when it had one, so a 503 reads as a
  /// 503 -- but never a 401, which by construction it cannot be here.
  static DioException _asFailureOf(RequestOptions options, DioException cause) =>
      DioException(
        requestOptions: options,
        response: cause.response == null
            ? null
            : Response<Object?>(
                requestOptions: options,
                statusCode: cause.response!.statusCode,
                data: cause.response!.data,
              ),
        type: cause.type,
        error: cause.error,
        message: cause.message,
      );

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

/// How a renewal ended. Three outcomes, not two, and conflating the last two is
/// the bug this type exists to prevent: "the server refused the token" ends a
/// session, "the request never arrived" must not.
class _Renewal {
  const _Renewal.renewed(StoredSession this.session) : unreachable = null;
  const _Renewal.rejected()
      : session = null,
        unreachable = null;
  const _Renewal.unreachable(DioException this.unreachable) : session = null;

  final StoredSession? session;
  final DioException? unreachable;
}
