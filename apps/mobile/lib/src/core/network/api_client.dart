import 'dart:async';

import 'package:dio/dio.dart';

import '../config/app_config.dart';
import '../model/freshness.dart';
import '../storage/token_store.dart';
import 'api_exception.dart';
import 'auth_interceptor.dart';
import 'version_interceptor.dart';

/// The only place in the app that speaks HTTP.
///
/// Repositories call typed methods here and receive parsed maps or an
/// [ApiException]; no `DioException` escapes this file. That keeps the choice
/// of HTTP client an implementation detail and, more usefully, means every
/// failure the UI can encounter already carries a message written for a
/// business owner.
class ApiClient {
  ApiClient({
    required AppConfig config,
    required TokenStore tokens,
    required Future<void> Function() onSignedOut,
    VersionInterceptor? version,
    Dio? dio,
    Dio? refreshDio,
  })  : _tokens = tokens,
        _version = version,
        _dio = dio ?? Dio(_options(config)) {
    final Dio refreshClient = refreshDio ?? Dio(_options(config));
    _dio.interceptors.add(
      AuthInterceptor(
        store: tokens,
        refreshClient: refreshClient,
        onSignedOut: onSignedOut,
      ),
    );
    if (version != null) {
      // After the auth interceptor, so the version headers are attached to the
      // retry a token refresh replays as well as to the original request. Also
      // means a 426 is seen here *after* the refresh machinery has declined to
      // treat it as an auth problem, which it is not.
      _dio.interceptors.add(version);
      // The refresh client bypasses the chain above, and its request is the one
      // an old app makes first on a cold start -- so without this, the backend
      // would never see a version on the only call that matters at launch.
      refreshClient.interceptors.add(version);
    }
  }

  final Dio _dio;
  final TokenStore _tokens;
  final VersionInterceptor? _version;

  TokenStore get tokens => _tokens;
  Dio get raw => _dio;

  /// Tells the backend which build this is, from here on.
  ///
  /// Called once from `main()` after `PackageInfo` resolves. Requests made
  /// before that go out unstamped, which the backend reads as "no opinion"
  /// rather than as version zero -- the alternative, blocking startup on a
  /// plugin call, would put a platform channel on the path to the first frame.
  void setClientVersion({required String version, String? build}) {
    _version?.setVersion(version: version, build: build);
  }

  static BaseOptions _options(AppConfig config) => BaseOptions(
        baseUrl: config.baseUrl,
        // Generous, because the far end of a read may be a desktop PC running
        // an export in TallyPrime. Anything shorter would time out on exactly
        // the large reports people most want.
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 90),
        sendTimeout: const Duration(seconds: 30),
        contentType: 'application/json',
        responseType: ResponseType.json,
        // Errors are read from the body, not raised by status code, so the
        // envelope reaches [ApiException] intact.
        validateStatus: (int? status) => status != null && status < 400,
        headers: <String, Object?>{'accept': 'application/json'},
      );

  Future<Map<String, Object?>> getJson(
    String path, {
    Map<String, Object?>? query,
    CancelToken? cancelToken,
  }) async {
    final Object? body = await _send<Object?>(
      () => _dio.get<Object?>(path, queryParameters: _clean(query), cancelToken: cancelToken),
      // GETs are idempotent, so a dropped connection or a connector that
      // timed out mid-export is worth one silent retry before the user sees
      // "something went wrong" -- most of what a batch read hits is a
      // desktop PC being briefly slow, not a real failure.
      retries: _defaultRetries,
    );
    return _asMap(body);
  }

  Future<List<Map<String, Object?>>> getList(
    String path, {
    Map<String, Object?>? query,
    CancelToken? cancelToken,
  }) async {
    final Object? body = await _send<Object?>(
      () => _dio.get<Object?>(path, queryParameters: _clean(query), cancelToken: cancelToken),
      retries: _defaultRetries,
    );
    if (body is! List) return const <Map<String, Object?>>[];
    return body
        .whereType<Map<Object?, Object?>>()
        .map(_asMap)
        .toList(growable: false);
  }

  Future<Map<String, Object?>> postJson(
    String path, {
    Map<String, Object?>? body,
  }) async {
    final Object? result = await _send<Object?>(
      () => _dio.post<Object?>(path, data: body),
    );
    return _asMap(result);
  }

  Future<void> post(String path, {Map<String, Object?>? body}) async {
    await _send<Object?>(() => _dio.post<Object?>(path, data: body));
  }

  /// A partial update. Not retried: PATCH is not idempotent in general, and
  /// replaying "set this person's access to these companies" against a request
  /// that actually succeeded could undo a change made in between.
  Future<Map<String, Object?>> patchJson(
    String path, {
    Map<String, Object?>? body,
  }) async {
    final Object? result = await _send<Object?>(
      () => _dio.patch<Object?>(path, data: body),
    );
    return _asMap(result);
  }

  Future<void> delete(String path) async {
    await _send<Object?>(() => _dio.delete<Object?>(path));
  }

  /// A DELETE whose response body matters -- cancelling a sync answers with the
  /// state it left the job in, which is what the screen renders next.
  Future<Map<String, Object?>> deleteJson(String path) async {
    final Object? body = await _send<Object?>(
      () => _dio.delete<Object?>(path),
    );
    return _asMap(body);
  }

  /// Two retries, not zero and not unbounded: the connector's own reconnect
  /// loop and Tally's export time already account for most slow requests, so
  /// anything still failing after this many attempts is worth surfacing
  /// rather than papering over with a longer wait.
  static const int _defaultRetries = 2;
  static const Duration _retryBaseDelay = Duration(milliseconds: 400);

  Future<T?> _send<T>(
    Future<Response<T>> Function() request, {
    int retries = 0,
  }) async {
    int attempt = 0;
    while (true) {
      try {
        final Response<T> response = await request();
        return response.data;
      } on DioException catch (error) {
        final ApiException apiError = ApiException.fromDio(error);
        final bool canRetry =
            attempt < retries && (apiError.retryable || apiError.isConnectivity);
        if (!canRetry) throw apiError;
        attempt++;
        await Future<void>.delayed(_retryBaseDelay * attempt);
      }
    }
  }

  static Map<String, Object?> _asMap(Object? body) {
    if (body is Map<String, Object?>) return body;
    if (body is Map) {
      return body.map((Object? key, Object? value) =>
          MapEntry<String, Object?>(key.toString(), value));
    }
    return <String, Object?>{};
  }

  /// Null query values would be sent as the literal string "null".
  static Map<String, Object?>? _clean(Map<String, Object?>? query) {
    if (query == null) return null;
    final Map<String, Object?> cleaned = <String, Object?>{};
    query.forEach((String key, Object? value) {
      if (value != null) cleaned[key] = value;
    });
    return cleaned.isEmpty ? null : cleaned;
  }
}

/// Helper for the `{data, meta}` envelope every report endpoint returns.
extension DataEnvelope on Map<String, Object?> {
  Map<String, Object?> get envelopeData => ApiClient._asMap(this['data']);

  Freshness get envelopeFreshness {
    final Object? meta = this['meta'];
    return Freshness.fromJson(meta is Map ? ApiClient._asMap(meta) : null);
  }
}
