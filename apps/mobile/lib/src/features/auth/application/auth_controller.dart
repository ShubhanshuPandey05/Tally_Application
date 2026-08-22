import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_exception.dart';
import '../../../core/providers.dart';
import '../../../core/storage/token_store.dart';
import '../data/auth_repository.dart';
import '../domain/app_user.dart';

final Provider<AuthRepository> authRepositoryProvider = Provider<AuthRepository>(
  (Ref ref) => AuthRepository(ref.watch(apiClientProvider)),
);

/// Where the app is with respect to a session.
///
/// [unknown] exists so the router can hold on the splash for one async tick
/// instead of flashing the sign-in screen at someone who is already signed in --
/// the most visible possible bug on every cold start.
enum AuthStatus { unknown, signedOut, signedIn }

class AuthState {
  const AuthState({required this.status, this.user, this.error});

  final AuthStatus status;
  final AppUser? user;
  final String? error;

  static const AuthState unknown = AuthState(status: AuthStatus.unknown);
  static const AuthState signedOut = AuthState(status: AuthStatus.signedOut);

  bool get isSignedIn => status == AuthStatus.signedIn;

  AuthState copyWith({AuthStatus? status, AppUser? user, String? error}) => AuthState(
        status: status ?? this.status,
        user: user ?? this.user,
        error: error,
      );
}

final NotifierProvider<AuthController, AuthState> authControllerProvider =
    NotifierProvider<AuthController, AuthState>(AuthController.new);

class AuthController extends Notifier<AuthState> {
  @override
  AuthState build() {
    // Restoration is async but the initial state must be synchronous, so the
    // router waits on [AuthStatus.unknown] until this settles.
    Future<void>.microtask(restore);
    return AuthState.unknown;
  }

  AuthRepository get _repository => ref.read(authRepositoryProvider);

  /// Re-read the signed-in account.
  ///
  /// Needed after anything that changes what the *server* thinks of this user
  /// rather than what the app is showing -- clearing `mustChangePassword` is the
  /// case that matters, because the router gate reads it and would otherwise
  /// hold someone on the password screen after they had already changed it.
  ///
  /// Failure is swallowed on purpose: the change itself already succeeded, and
  /// turning a stale local copy into a sign-out would be a worse outcome than
  /// one more launch before it catches up.
  Future<void> refreshUser() async {
    try {
      state = AuthState(status: AuthStatus.signedIn, user: await _repository.me());
    } on ApiException {
      // Left as-is; the next `restore()` will pick it up.
    }
  }

  /// Cold start: do we have a usable session on this device?
  Future<void> restore() async {
    final TokenStore store = ref.read(apiClientProvider).tokens;
    final StoredSession? session = await store.read();
    if (session == null) {
      state = AuthState.signedOut;
      return;
    }

    try {
      final AppUser user = await _repository.me();
      state = AuthState(status: AuthStatus.signedIn, user: user);
    } on ApiException catch (error) {
      // Only a rejected credential means signed-out. A flaky connection at
      // launch must not log someone out of an offline-friendly app -- they
      // would lose the cached figures they opened it to see.
      if (error.isAuthFailure) {
        await store.clear();
        state = AuthState.signedOut;
      } else {
        state = AuthState(
          status: AuthStatus.signedIn,
          user: state.user,
          error: error.message,
        );
      }
    }
  }

  Future<bool> signIn({required String email, required String password}) =>
      _attempt(() => _repository.signIn(
            email: email,
            password: password,
            deviceName: _deviceName,
          ));

  Future<bool> signUp({
    required String email,
    required String password,
    required String orgName,
    String? fullName,
  }) =>
      _attempt(() => _repository.signUp(
            email: email,
            password: password,
            orgName: orgName,
            fullName: fullName,
            deviceName: _deviceName,
          ));

  Future<void> signOut() async {
    await _repository.signOut();
    state = AuthState.signedOut;
    _invalidateEverything();
  }

  Future<void> signOutEverywhere() async {
    await _repository.signOutEverywhere();
    state = AuthState.signedOut;
    _invalidateEverything();
  }

  /// Called by the interceptor when refresh fails. Never shows an error: the
  /// session simply ended, and the router will take it from here.
  void handleSessionExpired() {
    if (state.status == AuthStatus.signedOut) return;
    state = AuthState.signedOut;
    _invalidateEverything();
  }

  Future<bool> _attempt(Future<AppUser> Function() action) async {
    state = state.copyWith(error: null);
    try {
      final AppUser user = await action();
      state = AuthState(status: AuthStatus.signedIn, user: user);
      _invalidateEverything();
      return true;
    } on ApiException catch (error) {
      state = AuthState(status: AuthStatus.signedOut, error: error.message);
      return false;
    }
  }

  /// Drop every cached read on a session change. One person's receivables must
  /// never survive into the next person's screen on a shared phone.
  void _invalidateEverything() {
    ref.invalidate(apiClientProvider);
  }

  static const String _deviceName = 'TallyFlow Mobile';
}
