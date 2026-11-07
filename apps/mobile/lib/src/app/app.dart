import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/application/auth_controller.dart';
import '../features/auth/application/auth_features.dart';
import '../features/notifications/push_registration.dart';
import '../features/settings/application/theme_controller.dart';
import '../features/updates/presentation/update_gate.dart';
import 'router.dart';
import 'theme.dart';

class PrimeAnalystApp extends ConsumerStatefulWidget {
  const PrimeAnalystApp({super.key});

  @override
  ConsumerState<PrimeAnalystApp> createState() => _PrimeAnalystAppState();
}

class _PrimeAnalystAppState extends ConsumerState<PrimeAnalystApp> {
  StreamSubscription<RemoteMessage>? _opened;

  @override
  void initState() {
    super.initState();
    if (ref.read(firebaseReadyProvider)) {
      // A notification tapped while the app was closed, then any tapped while
      // it is running in the background.
      FirebaseMessaging.instance.getInitialMessage().then(_open).ignore();
      _opened = FirebaseMessaging.onMessageOpenedApp.listen(_open);
    }
  }

  @override
  void dispose() {
    _opened?.cancel();
    super.dispose();
  }

  void _open(RemoteMessage? message) {
    if (message == null) return;
    final String? route = routeForNotification(message.data, Routes.pendingEntries);
    // Through the router, so its redirect still decides: a tap on a signed-out
    // phone lands on sign-in, not on somebody's entries.
    if (route != null) ref.read(routerProvider).go(route);
  }

  @override
  Widget build(BuildContext context) {
    // Every sign-in, restored or fresh, registers this phone for alerts.
    ref.listen<AuthState>(authControllerProvider, (AuthState? before, AuthState now) {
      if (now.isSignedIn && !(before?.isSignedIn ?? false)) {
        ref.read(pushRegistrarProvider).register();
      }
    });
    final GoRouter router = ref.watch(routerProvider);
    final AppThemeMode mode = ref.watch(themeModeProvider);

    return MaterialApp.router(
      title: 'Prime Analyst',
      debugShowCheckedModeBanner: false,
      // One theme, chosen by the customer, and `themeMode` pinned so the
      // platform's own light/dark setting cannot override it. Three skins do
      // not fit into Flutter's two slots, and half-using them would mean the
      // system decided which of "dim" and "dark" a customer got.
      theme: AppTheme.forMode(mode),
      themeMode: ThemeMode.light,
      routerConfig: router,
      builder: (BuildContext context, Widget? child) {
        // Financial figures must stay legible at large accessibility text
        // sizes, but an unbounded scale breaks every KPI tile into ellipses.
        //
        // The floor is 1.0 rather than 0.9 because the app's own type ramp is
        // already compressed to fit more rows on a screen. Letting the phone's
        // "small text" setting take another tenth off that would put captions
        // under 10pt in front of exactly the readers -- shop owners and their
        // accountants -- who are least likely to manage it.
        final MediaQueryData media = MediaQuery.of(context);
        return MediaQuery(
          data: media.copyWith(
            textScaler: media.textScaler.clamp(minScaleFactor: 1.0, maxScaleFactor: 1.35),
          ),
          // Outside the router, so a build below the supported floor cannot be
          // reached by any route -- including the ones a deep link lands on.
          // It renders `child` untouched unless an update is genuinely
          // required, so nothing else here has to know it exists.
          child: UpdateGate(child: child ?? const SizedBox.shrink()),
        );
      },
    );
  }
}
