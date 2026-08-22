import 'package:dio/dio.dart';

import '../../features/updates/domain/app_release.dart';
import '../../features/updates/domain/update_signal.dart';

/// Tells the backend what build this is, on every request, and reads its answer
/// off every response.
///
/// The app used to fetch `manifest.json` once per cold start. That left a phone
/// sitting open on the dashboard unaware of a release for as long as it stayed
/// open, and a build below the floor kept making requests the backend could not
/// serve correctly until someone happened to restart it.
///
/// Riding along on traffic that was going to happen anyway costs three short
/// headers per request and no round trips. The backend answers with the newest
/// version and the floor on every response -- success and failure alike -- and
/// refuses with 426 when this build is past the floor.
///
/// Deliberately not responsible for *deciding* anything. It records what the
/// server said and hands it to [UpdateSignal]; the policy lives in one place, in
/// [UpdateStatus.evaluate], so the header path and the manifest path cannot
/// reach different conclusions about the same release.
class VersionInterceptor extends Interceptor {
  VersionInterceptor({required this.signal, required this.platform});

  /// Where an observed verdict is published.
  final UpdateSignal signal;

  /// Manifest key for this build (`android`), or null on a platform that has no
  /// downloadable artefact. Sent so one backend can serve several clients.
  ///
  /// Supplied rather than resolved here so there is exactly one answer to
  /// "which platform am I?" -- see `currentPlatformKey` in the updates domain.
  final String? platform;

  /// Filled in once [PackageInfo] resolves. Requests made before that -- there
  /// is at most a frame of them -- simply go out unstamped, which the backend
  /// reads as "no opinion" rather than as version zero.
  String? _version;
  String? _build;

  void setVersion({required String version, String? build}) {
    _version = version;
    _build = build;
  }

  static const String versionHeader = 'x-app-version';
  static const String platformHeader = 'x-app-platform';
  static const String buildHeader = 'x-app-build';

  static const String latestHeader = 'x-latest-app-version';
  static const String minHeader = 'x-min-app-version';
  static const String actionHeader = 'x-app-update-action';

  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    final String? version = _version;
    final String? key = platform;
    // Both, or neither. A version with no platform would be judged against
    // whichever entry the backend defaults to -- so an iOS or web build could be
    // refused over an Android APK it has no way to install. Saying nothing means
    // "no opinion", and the request is served.
    if (key != null && version != null && version.isNotEmpty) {
      options.headers[versionHeader] = version;
      options.headers[platformHeader] = key;
      if (_build != null) options.headers[buildHeader] = _build;
    }
    handler.next(options);
  }

  @override
  void onResponse(Response<Object?> response, ResponseInterceptorHandler handler) {
    _readHeaders(response.headers);
    handler.next(response);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    // Errors carry the headers too, and this is the more important half: a 426
    // *is* an error, and the response that refused the request is the one that
    // says what to install.
    final Response<Object?>? response = err.response;
    if (response != null) {
      _readHeaders(response.headers);
      if (response.statusCode == 426) {
        signal.reportRequired(_releaseFromBody(response.data));
      }
    }
    handler.next(err);
  }

  void _readHeaders(Headers headers) {
    final String? latest = headers.value(latestHeader);
    if (latest == null || latest.isEmpty) return;
    signal.reportHeaders(
      latestVersion: latest,
      minVersion: headers.value(minHeader) ?? '',
      action: headers.value(actionHeader) ?? '',
    );
  }

  /// The 426 body carries the whole release entry, so the download can start
  /// from the response that refused the request rather than after another fetch.
  AppRelease? _releaseFromBody(Object? body) {
    if (body is! Map) return null;
    final Object? error = body['error'];
    if (error is! Map) return null;
    final Object? detail = error['detail'];
    if (detail is! Map) return null;
    final Object? update = detail['update'];
    if (update is! Map) return null;
    try {
      return AppRelease.fromJson(<String, Object?>{
        for (final MapEntry<Object?, Object?> entry in update.entries)
          entry.key.toString(): entry.value,
      });
    } on FormatException {
      return null;
    }
  }
}
