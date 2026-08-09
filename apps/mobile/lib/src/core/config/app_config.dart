import 'package:flutter/foundation.dart';

/// Build-time configuration.
///
/// Supplied with `--dart-define` so a release build cannot accidentally ship
/// pointing at a developer's laptop, and so the same source tree produces the
/// staging and production apps without an edit.
class AppConfig {
  const AppConfig({
    required this.baseUrl,
    required this.environment,
  });

  final String baseUrl;
  final String environment;

  bool get isProduction => environment == 'prod';

  /// `10.0.2.2` is the host machine as seen from the Android emulator;
  /// `localhost` there is the emulator itself, which is the single most common
  /// "why can't the app reach my backend" question.
  static String get _defaultBaseUrl {
    if (kIsWeb) return 'http://localhost:8000';
    return defaultTargetPlatform == TargetPlatform.android
        ? 'http://10.209.236.128:8000'
        : 'http://10.209.236.128:8000';
  }

  factory AppConfig.fromEnvironment() {
    const String url = String.fromEnvironment('TALLYFLOW_API_URL');
    const String env = String.fromEnvironment(
      'TALLYFLOW_ENV',
      defaultValue: 'dev',
    );
    return AppConfig(
      baseUrl: url.isEmpty ? _defaultBaseUrl : url,
      environment: env,
    );
  }
}
