import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// A scripted HTTP layer.
///
/// Written by hand rather than pulled from a mocking package because the thing
/// under test is the interceptor's *sequencing* -- which request goes out, in
/// what order, and how many times. Recording every request is the assertion
/// surface, and a matcher-based mock hides exactly that.
class FakeAdapter implements HttpClientAdapter {
  FakeAdapter();

  final List<RequestOptions> requests = <RequestOptions>[];
  final Map<String, List<_Reply>> _scripted = <String, List<_Reply>>{};

  /// Adds one reply for a path. Replies are consumed in order, so a path can
  /// answer 401 first and 200 after a refresh.
  void enqueue(
    String path, {
    int status = 200,
    Map<String, Object?> body = const <String, Object?>{},
    Duration? delay,
  }) {
    _scripted
        .putIfAbsent(path, () => <_Reply>[])
        .add(_Reply(status: status, body: body, delay: delay));
  }

  /// A reply reused for every call to a path.
  void always(String path, {int status = 200, Map<String, Object?> body = const {}}) {
    _scripted[path] = <_Reply>[_Reply(status: status, body: body, sticky: true)];
  }

  int countFor(String path) =>
      requests.where((RequestOptions request) => request.path == path).length;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);

    final List<_Reply>? replies = _scripted[options.path];
    if (replies == null || replies.isEmpty) {
      return ResponseBody.fromString(
        jsonEncode(<String, Object?>{
          'error': <String, Object?>{'code': 'not_found', 'message': 'No script'}
        }),
        404,
        headers: _jsonHeaders,
      );
    }

    final _Reply reply = replies.first;
    if (!reply.sticky) replies.removeAt(0);
    if (reply.delay != null) await Future<void>.delayed(reply.delay!);

    return ResponseBody.fromString(
      jsonEncode(reply.body),
      reply.status,
      headers: _jsonHeaders,
    );
  }

  @override
  void close({bool force = false}) {}

  static const Map<String, List<String>> _jsonHeaders = <String, List<String>>{
    Headers.contentTypeHeader: <String>['application/json'],
  };
}

class _Reply {
  _Reply({
    required this.status,
    required this.body,
    this.delay,
    this.sticky = false,
  });

  final int status;
  final Map<String, Object?> body;
  final Duration? delay;
  final bool sticky;
}

/// In-memory secure storage, so tests do not need a platform channel.
class FakeSecureStorage implements FlutterSecureStorage {
  final Map<String, String> values = <String, String>{};

  @override
  Future<String?> read({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      values[key];

  @override
  Future<void> write({
    required String key,
    required String? value,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    if (value == null) {
      values.remove(key);
    } else {
      values[key] = value;
    }
  }

  @override
  Future<void> delete({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    values.remove(key);
  }

  @override
  Future<void> deleteAll({
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    values.clear();
  }

  @override
  Future<Map<String, String>> readAll({
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      Map<String, String>.from(values);

  @override
  Future<bool> containsKey({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async =>
      values.containsKey(key);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
