
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

  /// The production API, so a build made without `--dart-define` still reaches
  /// a real server over TLS instead of a LAN address that stopped existing
  /// when somebody's router handed out a new lease.
  ///
  /// Release builds should still pass `TALLYFLOW_API_URL` explicitly --
  /// `run.py release <url>` does -- because this default follows whichever
  /// environment was current when the app was cut.
  static const String _fallbackBaseUrl = 'https://api-tallyflow.theshubhanshu.dev';

  static String get _defaultBaseUrl => _fallbackBaseUrl;

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
