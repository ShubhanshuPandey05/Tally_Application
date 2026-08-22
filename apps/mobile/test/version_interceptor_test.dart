/// The version check that rides on every API request.
///
/// The app no longer polls the manifest to find out whether it is current -- it
/// stamps its version on each request and reads the backend's verdict off each
/// response. Two things about that are worth pinning down:
///
///  * **A 426 has to be actionable.** The response that refuses the request
///    carries the release, so the download can start from it. If that parsing
///    breaks, the user gets a blocking screen with nothing to press.
///  * **It has to be quiet.** The headers arrive on every single response, so a
///    signal that notified on each one would rebuild the update state once per
///    API call for the life of the app.
library;

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/core/network/version_interceptor.dart';
import 'package:tallyflow/src/features/updates/domain/app_release.dart';
import 'package:tallyflow/src/features/updates/domain/update_signal.dart';

/// Drives a Dio through the interceptor against a scripted transport.
Future<({UpdateSignal signal, Response<Object?>? response, DioException? error})> call({
  int status = 200,
  Map<String, String> headers = const <String, String>{},
  Object? body,
  String version = '0.1.0',
}) async {
  final UpdateSignal signal = UpdateSignal();
  final VersionInterceptor interceptor =
      VersionInterceptor(signal: signal, platform: 'android')
        ..setVersion(version: version, build: '7');

  final Dio dio = Dio(BaseOptions(
    baseUrl: 'https://api.test',
    validateStatus: (int? code) => code != null && code < 400,
  ));
  dio.interceptors.add(interceptor);
  dio.httpClientAdapter = _ScriptedAdapter(
    status: status,
    headers: headers,
    body: body,
  );

  try {
    final Response<Object?> response = await dio.get<Object?>('/v1/health');
    return (signal: signal, response: response, error: null);
  } on DioException catch (error) {
    return (signal: signal, response: null, error: error);
  }
}

class _ScriptedAdapter implements HttpClientAdapter {
  _ScriptedAdapter({required this.status, required this.headers, this.body});

  final int status;
  final Map<String, String> headers;
  final Object? body;

  /// What the request actually carried, for the stamping tests.
  static Map<String, List<String>> lastRequestHeaders = <String, List<String>>{};

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<List<int>>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    lastRequestHeaders = <String, List<String>>{
      for (final MapEntry<String, dynamic> entry in options.headers.entries)
        entry.key.toLowerCase(): <String>['${entry.value}'],
    };
    return ResponseBody.fromString(
      body == null ? '{}' : _encode(body!),
      status,
      headers: <String, List<String>>{
        Headers.contentTypeHeader: <String>[Headers.jsonContentType],
        for (final MapEntry<String, String> entry in headers.entries)
          entry.key: <String>[entry.value],
      },
    );
  }

  String _encode(Object value) {
    // Hand-rolled rather than dart:convert so the fixture reads as the shape the
    // backend actually sends.
    if (value is String) return value;
    throw ArgumentError('pass the body as a JSON string');
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  group('stamping the request', () {
    test('every request carries the version, platform and build', () async {
      await call(version: '0.4.2');

      final Map<String, List<String>> sent = _ScriptedAdapter.lastRequestHeaders;
      expect(sent[VersionInterceptor.versionHeader]?.single, '0.4.2');
      expect(sent[VersionInterceptor.platformHeader]?.single, 'android');
      expect(sent[VersionInterceptor.buildHeader]?.single, '7');
    });

    test('a platform with no artefact is never stamped at all', () async {
      // The bug this prevents: a version with no platform key gets judged
      // against whichever entry the backend defaults to, so an iOS or web build
      // could be refused with 426 over an Android APK it cannot install.
      final UpdateSignal signal = UpdateSignal();
      final Dio dio = Dio(BaseOptions(baseUrl: 'https://api.test'));
      dio.interceptors.add(
        VersionInterceptor(signal: signal, platform: null)
          ..setVersion(version: '0.1.0', build: '7'),
      );
      dio.httpClientAdapter =
          _ScriptedAdapter(status: 200, headers: const <String, String>{});

      await dio.get<Object?>('/v1/health');

      final Map<String, List<String>> sent = _ScriptedAdapter.lastRequestHeaders;
      expect(sent.containsKey(VersionInterceptor.versionHeader), isFalse);
      expect(sent.containsKey(VersionInterceptor.platformHeader), isFalse);
    });

    test('nothing is stamped before the version is known', () async {
      // At most a frame of requests, but they must not be sent as version zero:
      // an unstamped request is "no opinion" to the backend, which serves it.
      final UpdateSignal signal = UpdateSignal();
      final Dio dio = Dio(BaseOptions(baseUrl: 'https://api.test'));
      dio.interceptors
          .add(VersionInterceptor(signal: signal, platform: 'android'));
      dio.httpClientAdapter =
          _ScriptedAdapter(status: 200, headers: const <String, String>{});

      await dio.get<Object?>('/v1/health');

      expect(
        _ScriptedAdapter.lastRequestHeaders
            .containsKey(VersionInterceptor.versionHeader),
        isFalse,
      );
    });
  });

  group('reading the response', () {
    test('advisory headers on a success become an observation', () async {
      final result = await call(headers: <String, String>{
        VersionInterceptor.latestHeader: '0.2.0',
        VersionInterceptor.minHeader: '0.1.0',
        VersionInterceptor.actionHeader: 'optional',
      });

      final UpdateObservation? observed = result.signal.current;
      expect(observed, isNotNull);
      expect(observed!.latestVersion, '0.2.0');
      expect(observed.minVersion, '0.1.0');
      expect(observed.action, UpdateAction.optional);
      expect(observed.needsDetail, isTrue,
          reason: 'headers say what, not where from');
    });

    test('a response with no advisory headers says nothing at all', () async {
      final result = await call();

      expect(result.signal.current, isNull);
    });

    test('an unrecognised action is read as "nothing to do"', () async {
      // Forward compatibility: a newer backend inventing a third state must not
      // be guessed at, least of all in the blocking direction.
      final result = await call(headers: <String, String>{
        VersionInterceptor.latestHeader: '0.2.0',
        VersionInterceptor.actionHeader: 'something_new',
      });

      expect(result.signal.current!.action, UpdateAction.none);
    });
  });

  group('a refused request', () {
    const String body = '''
{"error":{"code":"update_required","message":"too old","retryable":false,
"detail":{"update":{"version":"0.3.0","url":"/downloads/TallyFlow-0.3.0.apk",
"sha256":"ABC","size_bytes":23583706,"file":"TallyFlow-0.3.0.apk",
"mandatory":true,"min_supported_version":"0.2.0","notes":"Wire change."}}}}
''';

    test('426 becomes a required verdict carrying the release', () async {
      final result = await call(status: 426, body: body);

      expect(result.error, isNotNull);
      final UpdateObservation observed = result.signal.current!;
      expect(observed.action, UpdateAction.required);
      expect(observed.release?.version, '0.3.0');
      expect(observed.release?.isDownloadable, isTrue);
      expect(observed.needsDetail, isFalse,
          reason: 'the refusal already described the build');
    });

    test('the app can recognise it as terminal rather than retrying', () async {
      final result = await call(status: 426, body: body);

      final ApiException mapped = ApiException.fromDio(result.error!);
      expect(mapped.isUpdateRequired, isTrue);
      expect(mapped.retryable, isFalse);
      expect(mapped.isConnectivity, isFalse);
    });

    test('a 426 with no usable body still blocks', () async {
      // The floor is the point; the download details are a convenience. Losing
      // them must not turn a required update into a served request.
      final result = await call(
        status: 426,
        body: '{"error":{"code":"update_required","message":"too old"}}',
        headers: <String, String>{
          VersionInterceptor.latestHeader: '0.3.0',
          VersionInterceptor.actionHeader: 'required',
        },
      );

      expect(result.signal.current!.action, UpdateAction.required);
    });
  });

  group('the signal stays quiet', () {
    test('an unchanged answer does not notify', () async {
      final UpdateSignal signal = UpdateSignal();
      int notifications = 0;
      signal.addListener(() => notifications++);

      for (int i = 0; i < 20; i++) {
        signal.reportHeaders(
          latestVersion: '0.2.0',
          minVersion: '0.1.0',
          action: 'optional',
        );
      }

      expect(notifications, 1,
          reason: 'these arrive on every response for the life of the app');
    });

    test('a changed answer does notify', () async {
      final UpdateSignal signal = UpdateSignal();
      int notifications = 0;
      signal.addListener(() => notifications++);

      signal.reportHeaders(
          latestVersion: '0.2.0', minVersion: '0.1.0', action: 'optional');
      signal.reportHeaders(
          latestVersion: '0.3.0', minVersion: '0.1.0', action: 'optional');

      expect(notifications, 2);
    });

    test('a withdrawn requirement unblocks the app', () async {
      // A floor published by mistake and then corrected has to be able to
      // release a running app. Making `required` sticky would mean every
      // affected user had to restart before they could work again.
      final UpdateSignal signal = UpdateSignal();
      signal.reportRequired(null);
      expect(signal.current!.action, UpdateAction.required);

      signal.reportHeaders(
          latestVersion: '0.2.0', minVersion: '0.1.0', action: 'optional');

      expect(signal.current!.action, UpdateAction.optional);
    });
  });

  group('deciding from an observation', () {
    test('a header-only update still names a version to install', () async {
      final UpdateStatus status = UpdateStatus.fromObservation(
        currentVersion: '0.1.0',
        observation: const UpdateObservation(
          latestVersion: '0.3.0',
          minVersion: '0.2.0',
          action: UpdateAction.required,
        ),
      );

      expect(status.isRequired, isTrue);
      expect(status.release?.version, '0.3.0');
      expect(status.release?.isDownloadable, isFalse,
          reason: 'no url was ever supplied, so no download button is offered');
    });

    test('the backend saying "none" is up to date', () async {
      final UpdateStatus status = UpdateStatus.fromObservation(
        currentVersion: '0.3.0',
        observation: const UpdateObservation(
          latestVersion: '0.3.0',
          minVersion: '0.2.0',
          action: UpdateAction.none,
        ),
      );

      expect(status.hasUpdate, isFalse);
    });

    test('the backend verdict is trusted rather than recomputed', () async {
      // The backend already compared this build against the published floor.
      // Recomputing here would need details the headers do not carry and would
      // mean two implementations of one rule, able to disagree while the user is
      // being refused service.
      final UpdateStatus status = UpdateStatus.fromObservation(
        currentVersion: '0.1.0',
        observation: const UpdateObservation(
          latestVersion: '0.2.0',
          minVersion: '', // no floor reported, yet still required
          action: UpdateAction.required,
        ),
      );

      expect(status.isRequired, isTrue);
    });
  });
}
