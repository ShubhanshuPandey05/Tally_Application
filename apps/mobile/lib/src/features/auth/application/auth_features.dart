import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_exception.dart';
import '../../../core/providers.dart';
import '../data/phone_proof.dart';

/// Whether Firebase started in this build. Overridden in `main()` once
/// [FirebaseSetup.initialise] has answered; false everywhere else, which
/// includes tests and the connector's window.
final Provider<bool> firebaseReadyProvider = Provider<bool>((Ref ref) => false);

final Provider<PhoneProof> phoneProofProvider =
    Provider<PhoneProof>((Ref ref) => PhoneProof());

/// What the server can do for somebody who is not signed in yet.
class AuthFeatures {
  const AuthFeatures({required this.phoneVerification, required this.passwordReset});

  /// `off`, `optional` or `required` -- see `/v1/public/config`. Null when the
  /// server could not be asked, which the forms treat as "prove it if you
  /// can": a proof the server does not check is set aside, while a missing one
  /// the server wanted is a refused signup.
  final String? phoneVerification;
  final bool passwordReset;

  bool get checksPhone => phoneVerification != 'off';
  bool get requiresPhoneProof => phoneVerification == 'required';
}

final FutureProvider<AuthFeatures> authFeaturesProvider =
    FutureProvider<AuthFeatures>((Ref ref) async {
  try {
    final Map<String, Object?> json =
        await ref.watch(apiClientProvider).getJson('/v1/public/config');
    return AuthFeatures(
      phoneVerification: json['phone_verification'] as String?,
      passwordReset: json['password_reset'] as bool? ?? false,
    );
  } on ApiException {
    // Offline at the sign-in screen. Offer "Forgot password?" anyway: the
    // screen behind it says plainly if the server cannot send one.
    return const AuthFeatures(phoneVerification: null, passwordReset: true);
  }
});
