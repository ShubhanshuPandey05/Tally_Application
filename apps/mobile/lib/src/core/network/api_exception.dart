import 'dart:io';

import 'package:dio/dio.dart';

/// The failure type the UI is allowed to see.
///
/// The backend gives every error one envelope -- `{"error": {code, message,
/// retryable}}` -- with `message` already written for a business owner. This
/// class carries that through unchanged rather than re-wording it in the app,
/// so the two halves cannot drift into telling the user different stories.
class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.retryable = false,
    this.statusCode,
    this.fields = const <String, String>{},
  });

  /// Stable machine code: `connector_offline`, `no_data_yet`, `unauthenticated`.
  final String code;

  /// Safe to show on screen as-is.
  final String message;

  final bool retryable;
  final int? statusCode;

  /// Field-level problems from a 422, keyed by field name.
  final Map<String, String> fields;

  bool get isAuthFailure => statusCode == 401 || code == 'unauthenticated';

  /// The connector or Tally is unreachable -- the app shows last-known figures
  /// with a banner rather than an error page.
  bool get isConnectivity => const <String>{
        'connector_offline',
        'connector_timeout',
        'tally_unavailable',
        'network',
        'timeout',
      }.contains(code);

  /// Nothing has ever been read for this company. A first-run empty state, not
  /// an error -- and emphatically not zeroes, which would read as "no sales".
  bool get isFirstRun => code == 'no_data_yet';

  /// This build is below the published floor and the backend will not serve it.
  ///
  /// Never shown as an error by a screen: `VersionInterceptor` has already
  /// pushed the verdict into `UpdateSignal`, so `UpdateGate` replaces the whole
  /// app before any repository gets a chance to render this. The flag exists so
  /// a retry loop can recognise it as terminal -- retrying from the same build
  /// can only fail again, and burning three attempts first just delays the one
  /// screen that can actually help.
  bool get isUpdateRequired => statusCode == 426 || code == 'update_required';

  /// The business itself is not live — waiting for approval, suspended, or its
  /// term has ended.
  ///
  /// Never retryable and never a red error state: the `message` the backend
  /// sends already explains the situation and names who to contact, so screens
  /// render it as a notice rather than a failure. Distinguished by its own
  /// status code so it cannot be confused with "you are not an admin", which
  /// needs a completely different screen.
  bool get isSubscriptionInactive =>
      statusCode == 402 || code == 'subscription_inactive';

  factory ApiException.fromDio(DioException error) {
    final Response<Object?>? response = error.response;
    if (response != null) {
      return ApiException.fromResponse(response);
    }

    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        return const ApiException(
          code: 'timeout',
          message: 'The server is taking too long to respond. Please try again.',
          retryable: true,
        );
      case DioExceptionType.cancel:
        return const ApiException(code: 'cancelled', message: 'Request cancelled.');
      default:
        final bool offline = error.error is SocketException;
        return ApiException(
          code: 'network',
          message: offline
              ? 'No internet connection. Showing the last data we have.'
              : 'Could not reach TallyFlow. Please check your connection.',
          retryable: true,
        );
    }
  }

  factory ApiException.fromResponse(Response<Object?> response) {
    final Object? body = response.data;
    if (body is Map && body['error'] is Map) {
      final Map<Object?, Object?> error = body['error'] as Map<Object?, Object?>;
      return ApiException(
        code: (error['code'] as String?) ?? 'error',
        message: (error['message'] as String?) ?? 'Something went wrong.',
        retryable: (error['retryable'] as bool?) ?? false,
        statusCode: response.statusCode,
        fields: _fields(error['detail']),
      );
    }
    return ApiException(
      code: 'error',
      message: 'Something went wrong. Please try again.',
      statusCode: response.statusCode,
      retryable: (response.statusCode ?? 500) >= 500,
    );
  }

  static Map<String, String> _fields(Object? detail) {
    if (detail is! Map || detail['fields'] is! List) return const <String, String>{};
    final Map<String, String> parsed = <String, String>{};
    for (final Object? entry in detail['fields'] as List<Object?>) {
      if (entry is Map && entry['field'] is String) {
        parsed[entry['field'] as String] = (entry['problem'] as String?) ?? 'Invalid';
      }
    }
    return parsed;
  }

  @override
  String toString() => 'ApiException($code): $message';
}
