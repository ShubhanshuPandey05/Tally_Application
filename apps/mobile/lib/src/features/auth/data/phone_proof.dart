import 'dart:async';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';

/// Proving a mobile number with Firebase's SMS code.
///
/// Firebase texts the code and checks it; what comes back is an ID token that
/// says "this phone proved this number a moment ago". That token goes to our
/// server with the signup, and our server checks it. Firebase is signed out of
/// straight afterwards: it is evidence about a number, never how anybody signs
/// in to Prime Analyst.
class PhoneProof {
  PhoneProof({FirebaseAuth? auth}) : _auth = auth ?? FirebaseAuth.instance;

  final FirebaseAuth _auth;

  /// Text a code to [phone] (`+919876543210`).
  ///
  /// On Android the code is sometimes read from the incoming SMS by the
  /// system, or the number is proven without one at all; then the session
  /// comes back already holding its token and the person types nothing.
  Future<PhoneCodeSession> sendCode(String phone, {int? resendToken}) {
    if (kIsWeb) return _sendOnWeb(phone);

    final Completer<PhoneCodeSession> sent = Completer<PhoneCodeSession>();
    final Completer<String> autoToken = Completer<String>();
    _auth.verifyPhoneNumber(
      phoneNumber: phone,
      forceResendingToken: resendToken,
      timeout: const Duration(seconds: 60),
      verificationCompleted: (PhoneAuthCredential credential) async {
        try {
          final String token = await _tokenFor(credential);
          if (!autoToken.isCompleted) autoToken.complete(token);
          if (!sent.isCompleted) {
            sent.complete(PhoneCodeSession._auto(phone, token));
          }
        } catch (error) {
          // The automatic route failed; the typed code still works.
          debugPrint('automatic phone verification failed: $error');
        }
      },
      verificationFailed: (FirebaseAuthException error) {
        final PhoneProofException failure = PhoneProofException.from(error);
        if (!sent.isCompleted) sent.completeError(failure);
      },
      codeSent: (String verificationId, int? resend) {
        if (sent.isCompleted) return;
        sent.complete(
          PhoneCodeSession._typed(
            phone,
            resendToken: resend,
            autoToken: autoToken.future,
            confirm: (String code) => _tokenFor(
              PhoneAuthProvider.credential(
                verificationId: verificationId,
                smsCode: code,
              ),
            ),
          ),
        );
      },
      codeAutoRetrievalTimeout: (_) {},
    );
    return sent.future;
  }

  Future<PhoneCodeSession> _sendOnWeb(String phone) async {
    // On the web Firebase guards the send with an invisible reCAPTCHA, which
    // it creates itself when given none.
    try {
      final ConfirmationResult result = await _auth.signInWithPhoneNumber(phone);
      return PhoneCodeSession._typed(
        phone,
        autoToken: Completer<String>().future,
        confirm: (String code) async => _idTokenThenSignOut(await result.confirm(code)),
      );
    } on FirebaseAuthException catch (error) {
      throw PhoneProofException.from(error);
    }
  }

  Future<String> _tokenFor(PhoneAuthCredential credential) async {
    try {
      return _idTokenThenSignOut(await _auth.signInWithCredential(credential));
    } on FirebaseAuthException catch (error) {
      throw PhoneProofException.from(error);
    }
  }

  Future<String> _idTokenThenSignOut(UserCredential credential) async {
    try {
      final String? token = await credential.user?.getIdToken(true);
      if (token == null || token.isEmpty) {
        throw const PhoneProofException('We could not confirm your number. Please try again.');
      }
      return token;
    } finally {
      // Whatever happened. A Firebase session left behind would sit on the
      // phone holding a proof of somebody's number for no reason.
      await _auth.signOut();
    }
  }
}

/// One code sent to one number.
class PhoneCodeSession {
  PhoneCodeSession._typed(
    this.phone, {
    required Future<String> Function(String code) confirm,
    required this.autoToken,
    this.resendToken,
  })  : _confirm = confirm,
        provenToken = null;

  PhoneCodeSession._auto(this.phone, String token)
      : _confirm = null,
        provenToken = token,
        autoToken = Future<String>.value(token),
        resendToken = null;

  final String phone;
  final Future<String> Function(String code)? _confirm;

  /// Set when the number was proven before any code was typed.
  final String? provenToken;

  /// Completes if Android reads the code from the SMS while the person is
  /// still looking at the box.
  final Future<String> autoToken;

  /// Hands to [PhoneProof.sendCode] to send the same code again, which
  /// Firebase treats more kindly than a fresh request.
  final int? resendToken;

  /// The token for the code the person typed. Throws [PhoneProofException].
  Future<String> confirm(String code) {
    final Future<String> Function(String code)? confirm = _confirm;
    if (confirm == null) return Future<String>.value(provenToken);
    return confirm(code.trim());
  }
}

/// A sentence for the person, never Firebase's own code.
class PhoneProofException implements Exception {
  const PhoneProofException(this.message);

  factory PhoneProofException.from(FirebaseAuthException error) {
    switch (error.code) {
      case 'invalid-phone-number':
        return const PhoneProofException(
          'That mobile number does not look right. Check it and try again.',
        );
      case 'invalid-verification-code':
        return const PhoneProofException('That code is not right. Check the SMS and try again.');
      case 'session-expired':
      case 'code-expired':
        return const PhoneProofException('That code has expired. Ask for a new one.');
      case 'too-many-requests':
      case 'quota-exceeded':
        return const PhoneProofException(
          'Too many codes have been asked for. Please wait a while and try again.',
        );
      case 'network-request-failed':
        return const PhoneProofException(
          'No internet connection. Connect and try again.',
        );
      default:
        debugPrint('phone verification failed: ${error.code} ${error.message}');
        return const PhoneProofException(
          'We could not send a code just now. Please try again.',
        );
    }
  }

  final String message;

  @override
  String toString() => message;
}
