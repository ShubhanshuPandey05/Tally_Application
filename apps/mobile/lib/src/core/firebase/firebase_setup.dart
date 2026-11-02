import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';

/// Firebase, set up from `--dart-define`s rather than from
/// `google-services.json` / `GoogleService-Info.plist`.
///
/// Two reasons. The same source builds the UAT and production apps, and each
/// has its own Firebase project; a config file checked into the tree would be
/// one of them. And a build made without any Firebase settings -- a fresh
/// checkout, a developer's laptop -- must still build and run: the Google
/// Services Gradle plugin fails the whole build when its file is missing,
/// where this simply leaves Firebase off.
///
/// Off means: no SMS code at signup (the server is told nothing, and decides
/// for itself whether that is acceptable) and no push notifications.
/// Everything else in the app is unaffected.
///
/// The values are not secrets -- they are in every installed copy of the
/// app. `run.py` passes them from `apps/mobile/firebase.json` (see
/// `firebase.example.json`).
class FirebaseSetup {
  const FirebaseSetup._();

  static const String _projectId = String.fromEnvironment('FIREBASE_PROJECT_ID');
  static const String _senderId = String.fromEnvironment('FIREBASE_MESSAGING_SENDER_ID');
  static const String _apiKey = String.fromEnvironment('FIREBASE_API_KEY');

  // Firebase issues a different app id, and usually a different API key, per
  // platform. Each falls back to the shared key when only one was given.
  static const String _androidAppId = String.fromEnvironment('FIREBASE_ANDROID_APP_ID');
  static const String _androidApiKey = String.fromEnvironment('FIREBASE_ANDROID_API_KEY');
  static const String _iosAppId = String.fromEnvironment('FIREBASE_IOS_APP_ID');
  static const String _iosApiKey = String.fromEnvironment('FIREBASE_IOS_API_KEY');
  static const String _iosBundleId = String.fromEnvironment('FIREBASE_IOS_BUNDLE_ID');
  static const String _webAppId = String.fromEnvironment('FIREBASE_WEB_APP_ID');
  static const String _webApiKey = String.fromEnvironment('FIREBASE_WEB_API_KEY');
  static const String _authDomain = String.fromEnvironment('FIREBASE_AUTH_DOMAIN');

  /// The options for the platform this build is running on, or null when
  /// this build was not given any -- or runs somewhere Firebase is not used,
  /// which is the desktop: the connector's own window shares this package
  /// and has no business texting anyone.
  static FirebaseOptions? get options {
    if (_projectId.isEmpty || _senderId.isEmpty) return null;
    String key(String specific) => specific.isNotEmpty ? specific : _apiKey;

    if (kIsWeb) {
      if (_webAppId.isEmpty || key(_webApiKey).isEmpty) return null;
      return FirebaseOptions(
        apiKey: key(_webApiKey),
        appId: _webAppId,
        messagingSenderId: _senderId,
        projectId: _projectId,
        authDomain: _authDomain.isEmpty ? '$_projectId.firebaseapp.com' : _authDomain,
      );
    }
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        if (_androidAppId.isEmpty || key(_androidApiKey).isEmpty) return null;
        return FirebaseOptions(
          apiKey: key(_androidApiKey),
          appId: _androidAppId,
          messagingSenderId: _senderId,
          projectId: _projectId,
        );
      case TargetPlatform.iOS:
        if (_iosAppId.isEmpty || key(_iosApiKey).isEmpty) return null;
        return FirebaseOptions(
          apiKey: key(_iosApiKey),
          appId: _iosAppId,
          messagingSenderId: _senderId,
          projectId: _projectId,
          iosBundleId: _iosBundleId.isEmpty ? null : _iosBundleId,
        );
      case TargetPlatform.fuchsia:
      case TargetPlatform.linux:
      case TargetPlatform.macOS:
      case TargetPlatform.windows:
        return null;
    }
  }

  /// Start Firebase if this build has settings for it. True when it is up.
  ///
  /// Never throws. A Firebase that cannot start costs the SMS step and push
  /// notifications; it must never cost the app, which people open to see
  /// their figures with no signal at all.
  static Future<bool> initialise() async {
    final FirebaseOptions? config = options;
    if (config == null) return false;
    try {
      await Firebase.initializeApp(options: config);
      return true;
    } catch (error) {
      debugPrint('Firebase did not start: $error');
      return false;
    }
  }
}
