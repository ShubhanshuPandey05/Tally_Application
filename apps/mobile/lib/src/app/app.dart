import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/settings/application/theme_controller.dart';
import '../features/updates/presentation/update_gate.dart';
import 'router.dart';
import 'theme.dart';

class TallyFlowApp extends ConsumerWidget {
  const TallyFlowApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final GoRouter router = ref.watch(routerProvider);
    final AppThemeMode mode = ref.watch(themeModeProvider);

    return MaterialApp.router(
      title: 'TallyFlow',
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
        final MediaQueryData media = MediaQuery.of(context);
        return MediaQuery(
          data: media.copyWith(
            textScaler: media.textScaler.clamp(minScaleFactor: 0.9, maxScaleFactor: 1.35),
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
