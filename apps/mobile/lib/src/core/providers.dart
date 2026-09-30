import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../features/auth/application/auth_controller.dart';
import '../features/updates/application/update_providers.dart';
import '../features/updates/domain/app_release.dart';
import 'config/app_config.dart';
import 'network/api_client.dart';
import 'network/reachability.dart';
import 'network/version_interceptor.dart';
import 'storage/read_cache.dart';
import 'storage/token_store.dart';

/// Overridden in `main()` once the plugin has loaded. Kept synchronous
/// afterwards so screens can read the last-used company without an await and
/// without a frame of the wrong company's figures.
final Provider<SharedPreferences> sharedPreferencesProvider =
    Provider<SharedPreferences>(
  (Ref ref) => throw UnimplementedError('sharedPreferencesProvider must be overridden'),
);

final Provider<AppConfig> appConfigProvider =
    Provider<AppConfig>((Ref ref) => AppConfig.fromEnvironment());

final Provider<TokenStore> tokenStoreProvider =
    Provider<TokenStore>((Ref ref) => TokenStore());

/// The phone's copy of what it has read. Overridden in `main()` with the
/// on-disk store once it has opened; memory-only otherwise, which is what tests
/// get and what a phone whose store failed to open falls back to.
///
/// Outlives [apiClientProvider] on purpose. The client is rebuilt on every
/// session change; the copy is *cleared* then instead, explicitly, because a
/// rebuilt client pointing at the same disk would otherwise inherit it.
final Provider<ReadCache> readCacheProvider =
    Provider<ReadCache>((Ref ref) => MemoryReadCache());

/// Whether the phone reached our server on its last try. Outside the client for
/// the same reason as the cache: a sign-out must not reset it to "online".
final ChangeNotifierProvider<Reachability> reachabilityProvider =
    ChangeNotifierProvider<Reachability>((Ref ref) => Reachability());

/// Stamps the app version on every request and reads the backend's verdict off
/// every response.
///
/// Constructed here rather than inside [ApiClient] so it can write into the same
/// [UpdateSignal] the update providers read. That is the whole mechanism: the
/// HTTP path observes, the signal carries, the providers decide.
final Provider<VersionInterceptor> versionInterceptorProvider =
    Provider<VersionInterceptor>((Ref ref) {
  return VersionInterceptor(
    signal: ref.watch(updateSignalProvider),
    platform: currentPlatformKey,
  );
});

final Provider<ApiClient> apiClientProvider = Provider<ApiClient>((Ref ref) {
  return ApiClient(
    config: ref.watch(appConfigProvider),
    tokens: ref.watch(tokenStoreProvider),
    version: ref.watch(versionInterceptorProvider),
    // Read, not watched: neither is ever replaced, and watching the notifier
    // would rebuild the whole client -- and drop its session -- on every
    // change of signal.
    cache: ref.read(readCacheProvider),
    reachability: ref.read(reachabilityProvider),
    // Fires when a refresh token is rejected -- expired, revoked, or replayed.
    // Read lazily: the callback runs long after both providers exist, so this
    // is not the dependency cycle it looks like.
    onSignedOut: () async {
      ref.read(authControllerProvider.notifier).handleSessionExpired();
    },
  );
});
