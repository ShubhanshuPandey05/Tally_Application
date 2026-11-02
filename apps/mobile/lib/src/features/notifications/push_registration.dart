import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/application/auth_features.dart';

/// Tells the server where to send this person's notifications.
///
/// Registered after every sign-in, re-registered whenever FCM rotates the
/// token, and removed *before* the session ends -- the removal needs the
/// session, and a phone left registered after sign-out would keep showing the
/// last person's alerts to whoever picks it up next.
///
/// Android and iPhone only. The web build would need a service worker and a
/// VAPID key to receive anything, and is used at a desk where the phone that
/// would get the same alert is lying beside it.
class PushRegistrar {
  PushRegistrar(this._ref);

  final Ref _ref;
  StreamSubscription<String>? _rotation;
  String? _token;

  bool get _supported =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS) &&
      _ref.read(firebaseReadyProvider);

  Future<void> register() async {
    if (!_supported) return;
    try {
      final FirebaseMessaging messaging = FirebaseMessaging.instance;
      // Android 13+ and iOS ask the person; refusing is an answer, not an
      // error, and the rest of the app carries on exactly the same.
      final NotificationSettings settings = await messaging.requestPermission();
      if (settings.authorizationStatus == AuthorizationStatus.denied) return;

      final String? token = await messaging.getToken();
      if (token != null) await _send(token);
      _rotation ??= messaging.onTokenRefresh.listen(_send);
    } catch (error) {
      // iOS has no FCM token until APNs has answered, and a phone with no
      // Google Play services has none at all. Neither is the person's problem.
      debugPrint('push registration skipped: $error');
    }
  }

  Future<void> unregister() async {
    await _rotation?.cancel();
    _rotation = null;
    final String? token = _token;
    _token = null;
    if (token == null) return;
    try {
      await _ref
          .read(apiClientProvider)
          .post('/v1/push/devices/remove', body: <String, Object?>{'token': token});
    } on ApiException {
      // Best effort: sign-out goes ahead regardless, and the next person to
      // sign in on this phone takes the token over on the server anyway.
    }
  }

  Future<void> _send(String token) async {
    try {
      await _ref.read(apiClientProvider).post(
        '/v1/push/devices',
        body: <String, Object?>{
          'token': token,
          'platform': defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android',
        },
      );
      _token = token;
    } on ApiException catch (error) {
      debugPrint('push registration failed: ${error.message}');
    }
  }
}

final Provider<PushRegistrar> pushRegistrarProvider =
    Provider<PushRegistrar>(PushRegistrar.new);

/// Where a tapped notification should open, from the `data` the server put
/// on it. Null opens the app where it was.
String? routeForNotification(Map<String, dynamic> data, String pendingEntriesRoute) {
  switch (data['kind']) {
    case 'queued_entry':
      return pendingEntriesRoute;
    default:
      return null;
  }
}
