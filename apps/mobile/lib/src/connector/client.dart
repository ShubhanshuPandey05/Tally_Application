import 'dart:convert';
import 'dart:io';

import 'state.dart';

/// The window's only connection to the connector.
///
/// Loopback HTTP, to the server in `tally_connector/ui/server.py`. The window is
/// a client and nothing more: it never starts, stops or supervises the
/// connector, and everything it can do is one of four buttons that connector
/// registered for it.
class ConnectorClient {
  ConnectorClient({required this.port})
      : _http = HttpClient()
          // Short, because both ends are on this machine. The only way a
          // loopback connect takes seconds is that nothing is listening, and
          // saying so quickly is the whole value of the answer.
          ..connectionTimeout = const Duration(seconds: 2)
          // The connector's server is loopback-only and speaks no proxy; a
          // machine with a system proxy configured would otherwise have its
          // status polls sent to a corporate gateway.
          ..findProxy = null;

  /// The connector's `ui_port`. Passed in on the command line by
  /// `tally-connector ui`, so a machine that moved the port keeps a working
  /// shortcut.
  final int port;

  final HttpClient _http;

  /// The header a cross-origin page cannot set.
  ///
  /// It is the connector's CSRF defence, not ours -- but it is why the window
  /// can share a port with a browser that is also on this machine without a
  /// tab being able to press these buttons.
  static const String _localHeader = 'X-TallyFlow-Local';

  /// The connector's current state.
  ///
  /// Throws [ConnectorUnreachable] when nothing is listening, which is the one
  /// failure the window renders as a screen of its own rather than as a line of
  /// detail: it is also the only one with an instruction attached.
  Future<ConnectorState> fetchState() async {
    final Map<String, Object?> body = await _get('/api/state');
    return ConnectorState.fromJson(body);
  }

  /// The pairing code as rows of `0` and `1`, quiet zone included.
  ///
  /// Drawn by the connector's own encoder rather than by a Dart one, so the two
  /// decisions that make a code scannable off a monitor -- a full QR rather
  /// than a Micro QR, and four blank modules of margin -- are made once, in the
  /// file that records why.
  Future<List<String>> fetchQrRows() async {
    final Map<String, Object?> body = await _get('/api/qr');
    final Object? rows = body['rows'];
    if (rows is! List<Object?>) {
      return const <String>[];
    }
    return rows.whereType<String>().toList(growable: false);
  }

  /// Presses one of the connector's four buttons and waits for its answer.
  ///
  /// Never throws for a refused or failed action: the connector answers every
  /// one with a message meant to be read by whoever pressed it, and turning
  /// that into an exception here would replace it with a stack trace.
  Future<ActionResult> invoke(
    String name, {
    Map<String, Object?> payload = const <String, Object?>{},
  }) async {
    try {
      final Map<String, Object?> body = await _post('/api/$name', payload);
      return ActionResult.fromJson(body);
    } on ConnectorUnreachable {
      return const ActionResult(
        ok: false,
        message: 'The connector is not running on this computer.',
      );
    } on Object catch (error) {
      return ActionResult(ok: false, message: '$error');
    }
  }

  void close() => _http.close(force: true);

  // -- plumbing ---------------------------------------------------------

  Future<Map<String, Object?>> _get(String path) async {
    final HttpClientRequest request = await _open('GET', path);
    return _read(await request.close());
  }

  Future<Map<String, Object?>> _post(
    String path,
    Map<String, Object?> payload,
  ) async {
    final HttpClientRequest request = await _open('POST', path);
    request.headers.contentType = ContentType.json;
    request.write(jsonEncode(payload));
    return _read(await request.close());
  }

  Future<HttpClientRequest> _open(String method, String path) async {
    try {
      final HttpClientRequest request =
          await _http.openUrl(method, Uri.parse('http://127.0.0.1:$port$path'));
      request.headers.set(_localHeader, '1');
      return request;
    } on SocketException {
      throw ConnectorUnreachable(_silence);
    } on HttpException {
      throw ConnectorUnreachable(_silence);
    }
  }

  /// What the window shows underneath "the connector is not running".
  ///
  /// Written out rather than passed through from the socket error, whose text
  /// is "HTTP connection timed out after 0:00:02.000000, host: 127.0.0.1, port:
  /// 9787". That is true and tells the person reading it nothing they can act
  /// on; the port is the one fact worth carrying, because a connector
  /// configured onto a different one is exactly how this screen appears on a
  /// machine that is working perfectly.
  String get _silence => 'Nothing is answering on 127.0.0.1:$port.';

  Future<Map<String, Object?>> _read(HttpClientResponse response) async {
    final String text = await response.transform(utf8.decoder).join();
    // An action can legitimately answer 400; its body still carries the message
    // to show. Anything without a JSON object is a genuine protocol failure.
    final Object? decoded = text.isEmpty ? null : jsonDecode(text);
    if (decoded is Map<String, Object?>) {
      return decoded;
    }
    throw ConnectorUnreachable(
      'The connector answered ${response.statusCode} with nothing to show.',
    );
  }
}

/// Nothing is listening on the connector's port.
///
/// Its own type because it is the one failure with a fix a shop owner can
/// carry out: the connector is not running, and the window says so instead of
/// showing an empty status board that looks like a working machine with no
/// data.
class ConnectorUnreachable implements Exception {
  const ConnectorUnreachable(this.detail);

  final String detail;

  @override
  String toString() => detail;
}
