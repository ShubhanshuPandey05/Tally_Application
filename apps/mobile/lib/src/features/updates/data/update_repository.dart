import 'package:dio/dio.dart';

import '../domain/app_release.dart';

/// Reads the published update manifest.
///
/// Deliberately not routed through [ApiClient]: the manifest is a static file
/// on the same host but outside `/v1`, needs no token, and must stay readable
/// when the API itself is the thing that is broken. An app too old to
/// authenticate is exactly the one that most needs to be told to update, and
/// hanging that check off an authenticated call would strand it.
class UpdateRepository {
  UpdateRepository({required String baseUrl, Dio? dio})
      : _manifestUrl = _manifestUrlFor(baseUrl),
        _dio = dio ?? Dio();

  final Dio _dio;
  final String _manifestUrl;

  String get manifestUrl => _manifestUrl;

  static String _manifestUrlFor(String baseUrl) {
    final String host = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    return '$host/downloads/manifest.json';
  }

  /// Which manifest key describes the build this app was compiled as.
  ///
  /// Delegates to the domain rather than deciding again: the HTTP interceptor
  /// asks the same question to decide what version to declare, and two answers
  /// that could disagree would mean the backend judging a build against one
  /// platform's floor while the app fetched another platform's artefact.
  ///
  /// Previously read `dart:io`'s `Platform`, which does not exist on web -- and
  /// `run.py release` builds a web target.
  static String? get platformKey => currentPlatformKey;

  /// The published release for this platform, or null if there is none.
  ///
  /// Failures return null rather than throwing: a manifest that cannot be
  /// reached is the ordinary state of a phone on a train, and it must never
  /// stop the app from opening.
  Future<AppRelease?> fetch({String? platform}) async {
    final String? key = platform ?? platformKey;
    if (key == null) return null;

    try {
      final Response<Object?> response = await _dio.getUri<Object?>(
        Uri.parse(_manifestUrl),
        options: Options(
          // Short: nothing waits on this, and a slow check should give up
          // rather than hold a connection open on someone's mobile data.
          receiveTimeout: const Duration(seconds: 10),
          sendTimeout: const Duration(seconds: 10),
          responseType: ResponseType.json,
        ),
      );

      final Object? body = response.data;
      if (body is! Map) return null;
      final Object? entry = body[key];
      if (entry is! Map) return null;

      return AppRelease.fromJson(Map<String, Object?>.from(entry));
    } on DioException {
      return null;
    } on FormatException {
      return null;
    }
  }

  /// Absolute URL of the artefact, resolved against the manifest's own address.
  Uri downloadUri(AppRelease release) => Uri.parse(_manifestUrl).resolve(release.url);
}
