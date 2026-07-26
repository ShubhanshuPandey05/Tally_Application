import '../../../core/network/api_client.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/storage/token_store.dart';
import '../domain/app_user.dart';

/// Sign-in, sign-up, and session teardown.
///
/// Token persistence lives here rather than in the controller so that the
/// interceptor and the UI cannot disagree about whether someone is signed in --
/// there is exactly one writer of [TokenStore] on the success path.
class AuthRepository {
  AuthRepository(this._api);

  final ApiClient _api;

  Future<AppUser> signIn({
    required String email,
    required String password,
    String? deviceName,
  }) async {
    final Map<String, Object?> tokens = await _api.postJson(
      '/v1/auth/login',
      body: <String, Object?>{
        'email': email.trim(),
        'password': password,
        'device_name': deviceName,
      },
    );
    await _persist(tokens);
    return me();
  }

  Future<AppUser> signUp({
    required String email,
    required String password,
    required String orgName,
    String? fullName,
    String? deviceName,
  }) async {
    final Map<String, Object?> tokens = await _api.postJson(
      '/v1/auth/register',
      body: <String, Object?>{
        'email': email.trim(),
        'password': password,
        'org_name': orgName.trim(),
        'full_name': fullName?.trim(),
        'device_name': deviceName,
      },
    );
    await _persist(tokens);
    return me();
  }

  Future<AppUser> me() async {
    final Map<String, Object?> json = await _api.getJson('/v1/auth/me');
    return AppUser.fromJson(json);
  }

  /// Best-effort revocation, then always clear locally.
  ///
  /// If the network call fails the user still expects to be signed out of this
  /// phone; leaving the tokens in place because the server was unreachable
  /// would be the worst possible reading of their intent.
  Future<void> signOut() async {
    final StoredSession? session = await _api.tokens.read();
    if (session != null) {
      try {
        await _api.post(
          '/v1/auth/logout',
          body: <String, Object?>{'refresh_token': session.refreshToken},
        );
      } on ApiException {
        // Ignored deliberately -- see above.
      }
    }
    await _api.tokens.clear();
  }

  Future<void> signOutEverywhere() async {
    try {
      await _api.post('/v1/auth/logout-all');
    } on ApiException {
      // Ignored: the local session is cleared regardless.
    }
    await _api.tokens.clear();
  }

  Future<void> _persist(Map<String, Object?> tokens) async {
    await _api.tokens.write(
      StoredSession(
        accessToken: tokens['access_token'] as String? ?? '',
        refreshToken: tokens['refresh_token'] as String? ?? '',
        accessExpiresAt: DateTime.now()
            .add(Duration(seconds: (tokens['expires_in'] as num?)?.toInt() ?? 900)),
      ),
    );
  }
}
