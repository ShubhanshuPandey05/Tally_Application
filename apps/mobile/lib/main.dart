import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'src/app/app.dart';
import 'src/core/providers.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Loaded before the first frame so the last-used company is known
  // synchronously -- otherwise the app briefly renders one company's figures
  // under another company's name, which in an accounting product is not a
  // cosmetic bug.
  final SharedPreferences preferences = await SharedPreferences.getInstance();

  // Read here, alongside the preferences, so the very first request already
  // carries it. The backend checks the version on every request and refuses a
  // build below the floor, so an unstamped login would be the one call that
  // slipped through -- and it is the call an out-of-date app makes first.
  final PackageInfo package = await PackageInfo.fromPlatform();

  runApp(
    ProviderScope(
      overrides: <Override>[
        sharedPreferencesProvider.overrideWithValue(preferences),
      ],
      child: _VersionedApp(package: package),
    ),
  );
}

/// Hands the running build's version to the HTTP layer before anything is fetched.
///
/// A widget rather than a call in `main()` because the interceptor lives in the
/// provider graph, and the graph does not exist until [ProviderScope] is built.
class _VersionedApp extends ConsumerStatefulWidget {
  const _VersionedApp({required this.package});

  final PackageInfo package;

  @override
  ConsumerState<_VersionedApp> createState() => _VersionedAppState();
}

class _VersionedAppState extends ConsumerState<_VersionedApp> {
  @override
  void initState() {
    super.initState();
    // Synchronous, and before the first frame: no request can have gone out yet,
    // so there is no window in which the app talks to the backend anonymously.
    ref.read(apiClientProvider).setClientVersion(
          version: widget.package.version,
          build: widget.package.buildNumber,
        );
  }

  @override
  Widget build(BuildContext context) => const TallyFlowApp();
}
