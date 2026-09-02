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

  /// Enter the shared demo, with nothing to type.
  ///
  /// The demo is an ordinary account with an ordinary password -- that is what
  /// makes every screen behind it the real product -- but the app deliberately
  /// does not carry that password. A credential compiled into a released build
  /// cannot be rotated without shipping a new one to every phone, and the old
  /// builds keep working with the old value or stop working with the new one.
  /// So the server, which already knows the account, hands out the session.
  Future<AppUser> signInAsDemo({String? deviceName}) async {
    final Map<String, Object?> tokens = await _api.postJson(
      '/v1/auth/demo',
      body: <String, Object?>{'device_name': deviceName},
    );
    await _persist(tokens);
    return me();
  }

  /// Whether this server has a demo to offer at all.
  ///
  /// Asked rather than assumed: most deployments have none, and "Explore the
  /// demo" on one of those is a button that can only disappoint. False on any
  /// failure, which hides the button -- the wrong direction to fail in would be
  /// offering a door into nothing.
  Future<bool> demoAvailable() async {
    try {
      final Map<String, Object?> config = await _api.getJson('/v1/public/config');
      return config['demo_available'] as bool? ?? false;
    } on ApiException {
      return false;
    }
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
