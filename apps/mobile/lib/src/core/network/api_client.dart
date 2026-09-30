import 'dart:async';

import 'package:dio/dio.dart';

import '../config/app_config.dart';
import '../model/freshness.dart';
import '../storage/read_cache.dart';
import '../storage/token_store.dart';
import 'api_exception.dart';
import 'auth_interceptor.dart';
import 'reachability.dart';
import 'version_interceptor.dart';

/// Whether a read goes through the phone's own copy, and how.
///
/// Opt-in per call. Reads of *books* keep a copy; reads that decide what the
/// app may do -- pending entries, the update manifest, sync progress -- do
/// not, because acting on an old answer there is worse than showing no answer.
class CachePolicy {
  const CachePolicy._({required this.preferDevice, this.onNewer});

  /// Ask the server, keep what it says, and answer from the phone's copy if
  /// the server cannot be reached. The policy for pull-to-refresh.
  static const CachePolicy keep = CachePolicy._(preferDevice: false);

  /// Answer from the phone's copy straight away when there is one, then check
  /// the server behind it and call [onNewer] once a newer copy has been kept.
  /// The policy for opening a screen.
  const CachePolicy.deviceFirst({required void Function() onNewer})
      : this._(preferDevice: true, onNewer: onNewer);

  final bool preferDevice;
  final void Function()? onNewer;
}

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
    ReadCache? cache,
    Reachability? reachability,
    Dio? dio,
    Dio? refreshDio,
  })  : _tokens = tokens,
        _version = version,
        _cache = cache,
        _reachability = reachability,
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
  final ReadCache? _cache;
  final Reachability? _reachability;

  /// Background checks in flight, by key. Two screens opening the same report
  /// share one check rather than each asking the server.
  final Set<String> _revalidating = <String>{};

  /// How old the phone's copy may be before opening a screen also asks the
  /// server behind it. Short, because the check is invisible -- the copy is
  /// already on screen -- and the backend answers from its own snapshots, so
  /// it costs the shop's Tally nothing. Its job is to stop somebody flicking
  /// between two reports from sending a request on every flick.
  static const Duration revalidateAfter = Duration(seconds: 30);

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
    CachePolicy? cache,
  }) async {
    final Object? body = await _get(path, query, cancelToken, cache);
    return _asMap(body);
  }

  Future<List<Map<String, Object?>>> getList(
    String path, {
    Map<String, Object?>? query,
    CancelToken? cancelToken,
    CachePolicy? cache,
  }) async {
    final Object? body = await _get(path, query, cancelToken, cache);
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

  Future<Object?> _get(
    String path,
    Map<String, Object?>? query,
    CancelToken? cancelToken,
    CachePolicy? policy,
  ) {
    final Map<String, Object?>? cleaned = _clean(query);
    Future<Object?> fetch() => _send<Object?>(
          () => _dio.get<Object?>(path, queryParameters: cleaned, cancelToken: cancelToken),
          // GETs are idempotent, so a dropped connection or a connector that
          // timed out mid-export is worth one silent retry before the user sees
          // "something went wrong" -- most of what a batch read hits is a
          // desktop PC being briefly slow, not a real failure.
          retries: _defaultRetries,
        );

    final ReadCache? cache = _cache;
    if (policy == null || cache == null) return fetch();
    return _throughCache(cache, ReadCache.keyFor(path, cleaned), policy, fetch);
  }

  Future<Object?> _throughCache(
    ReadCache cache,
    String key,
    CachePolicy policy,
    Future<Object?> Function() fetch,
  ) async {
    if (policy.preferDevice) {
      final SavedRead? saved = await cache.read(key);
      if (saved != null) {
        if (saved.age >= revalidateAfter) _revalidate(cache, key, fetch, policy.onNewer);
        return _fromDevice(saved);
      }
    }

    try {
      final Object? body = await fetch();
      await cache.write(key, body);
      return body;
    } on ApiException catch (error) {
      // Only when the server could not be reached at all. A server that
      // *answered* -- 402, 404, no data yet -- has said something the screen
      // must show, and papering over it with an older copy would hide a
      // lapsed subscription or a deleted company behind yesterday's figures.
      if (!error.isUnreachable) rethrow;
      final SavedRead? saved = await cache.read(key);
      if (saved == null) rethrow;
      return _fromDevice(saved);
    }
  }

  /// Checks the server behind a copy that is already on screen.
  ///
  /// Every failure is swallowed: the person is looking at real figures with
  /// their age beside them, and a failed background check has nothing to add
  /// to that. [onNewer] fires only after a copy was actually kept, so a phone
  /// with no signal does not rebuild the screen in a loop.
  void _revalidate(
    ReadCache cache,
    String key,
    Future<Object?> Function() fetch,
    void Function()? onNewer,
  ) {
    if (!_revalidating.add(key)) return;
    unawaited(() async {
      try {
        await cache.write(key, await fetch());
        onNewer?.call();
      } on ApiException {
        // Deliberately ignored -- see above.
      } finally {
        _revalidating.remove(key);
      }
    }());
  }

  /// A saved body, stamped with when the phone received it.
  ///
  /// Written under a key the server never sends, beside the payload rather
  /// than inside it, so parsers that do not look for it are unaffected. Lists
  /// carry no stamp; nothing renders freshness for a bare list.
  static Object? _fromDevice(SavedRead saved) {
    final Object? body = saved.body;
    if (body is! Map) return body;
    return <String, Object?>{
      ..._asMap(body),
      deviceCopyKey: <String, Object?>{'saved_at': saved.savedAt.toIso8601String()},
    };
  }

  /// Where [_fromDevice] records that a body came from the phone.
  static const String deviceCopyKey = '_device';

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
        _reachability?.reached();
        return response.data;
      } on DioException catch (error) {
        final ApiException apiError = ApiException.fromDio(error);
        if (apiError.isUnreachable) {
          _reachability?.unreachable();
        } else if (error.response != null) {
          // An error the server sent is still the server answering.
          _reachability?.reached();
        }
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
    return Freshness.fromJson(meta is Map ? ApiClient._asMap(meta) : null)
        .savedOnDevice(deviceSavedAt);
  }

  /// When the phone received this body, if it came from the phone's own copy
  /// rather than straight from the server.
  DateTime? get deviceSavedAt {
    final Object? device = this[ApiClient.deviceCopyKey];
    if (device is! Map) return null;
    return DateTime.tryParse(device['saved_at'] as String? ?? '');
  }
}
