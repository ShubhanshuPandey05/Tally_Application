import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// A signed-in session, as persisted between launches.
class StoredSession {
  const StoredSession({
    required this.accessToken,
    required this.refreshToken,
    required this.accessExpiresAt,
  });

  final String accessToken;
  final String refreshToken;
  final DateTime accessExpiresAt;

  /// Treated as expired slightly early so a request does not set off in the
  /// last second of a token's life and arrive after it has lapsed.
  bool get isAccessUsable =>
      DateTime.now().isBefore(accessExpiresAt.subtract(const Duration(seconds: 30)));

  Map<String, Object?> toJson() => <String, Object?>{
        'access_token': accessToken,
        'refresh_token': refreshToken,
        'access_expires_at': accessExpiresAt.toIso8601String(),
      };

  static StoredSession? fromJson(Map<String, Object?> json) {
    final Object? access = json['access_token'];
    final Object? refresh = json['refresh_token'];
    if (access is! String || refresh is! String) return null;
    return StoredSession(
      accessToken: access,
      refreshToken: refresh,
      accessExpiresAt:
          DateTime.tryParse(json['access_expires_at'] as String? ?? '') ??
              DateTime.now(),
    );
  }
}

/// Where tokens live.
///
/// The Keychain on iOS and EncryptedSharedPreferences on Android, never
/// SharedPreferences: a refresh token is a long-lived credential to a company's
/// complete financial history, and on a rooted or jailbroken device plain
/// preferences are a text file.
class TokenStore {
  TokenStore({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
            );

  final FlutterSecureStorage _storage;
  static const String _key = 'tallyflow.session';

  StoredSession? _cached;

  /// Read once at startup, then served from memory: the interceptor consults
  /// this on every request and a Keychain hit per call is real latency.
  Future<StoredSession?> read() async {
    if (_cached != null) return _cached;
    final String? raw = await _storage.read(key: _key);
    if (raw == null) return null;
    try {
      final Object? decoded = jsonDecode(raw);
      if (decoded is Map<String, Object?>) {
        return _cached = StoredSession.fromJson(decoded);
      }
    } on FormatException {
      await clear();
    }
    return null;
  }

  StoredSession? get current => _cached;

  Future<void> write(StoredSession session) async {
    _cached = session;
    await _storage.write(key: _key, value: jsonEncode(session.toJson()));
  }

  Future<void> clear() async {
    _cached = null;
    await _storage.delete(key: _key);
  }
}
