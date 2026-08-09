import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/application/auth_controller.dart';
import '../features/auth/presentation/sign_in_screen.dart';
import '../features/auth/presentation/sign_up_screen.dart';
import '../features/companies/presentation/company_picker_screen.dart';
import '../features/companies/presentation/link_company_screen.dart';
import '../features/connectors/presentation/connector_detail_screen.dart';
import '../features/connectors/presentation/connectors_screen.dart';
import '../features/connectors/presentation/pair_connector_screen.dart';
import '../features/dashboard/presentation/dashboard_screen.dart';
import '../features/reports/presentation/daybook_screen.dart';
import '../features/reports/presentation/group_outstanding_screen.dart';
import '../features/reports/presentation/ledgers_screen.dart';
import '../features/reports/presentation/outstanding_screen.dart';
import '../features/reports/presentation/reports_screen.dart';
import '../features/reports/presentation/slow_moving_screen.dart';
import '../features/reports/presentation/stock_screen.dart';
import '../features/reports/domain/reports.dart';
import '../features/settings/presentation/settings_screen.dart';
import 'shell.dart';

class Routes {
  const Routes._();

  static const String splash = '/splash';
  static const String signIn = '/sign-in';
  static const String signUp = '/sign-up';
  static const String dashboard = '/';
  static const String reports = '/reports';
  static const String settings = '/settings';
  static const String companies = '/companies';
  static const String connectors = '/connectors';
  static const String pairConnector = '/connectors/new';
  static const String daybook = '/reports/daybook';
  static const String outstanding = '/reports/outstanding';
  static const String groupOutstanding = '/reports/outstanding/group';
  static const String stock = '/reports/stock';
  static const String ledgers = '/reports/ledgers';
  static const String slowMoving = '/reports/slow-moving';
}

final GlobalKey<NavigatorState> _rootKey = GlobalKey<NavigatorState>();
final GlobalKey<NavigatorState> _shellKey = GlobalKey<NavigatorState>();

final Provider<GoRouter> routerProvider = Provider<GoRouter>((Ref ref) {
  final _AuthRefresh refresh = _AuthRefresh(ref);
  ref.onDispose(refresh.dispose);

  return GoRouter(
    navigatorKey: _rootKey,
    initialLocation: Routes.splash,
    refreshListenable: refresh,
    // One place decides what an unauthenticated user may see. Per-screen guards
    // are how a screen eventually gets added without one.
    redirect: (BuildContext context, GoRouterState state) {
      final AuthState auth = ref.read(authControllerProvider);
      final String location = state.matchedLocation;
      final bool onAuthScreen =
          location == Routes.signIn || location == Routes.signUp;

      if (auth.status == AuthStatus.unknown) {
        return location == Routes.splash ? null : Routes.splash;
      }
      if (!auth.isSignedIn) {
        return onAuthScreen ? null : Routes.signIn;
      }
      if (onAuthScreen || location == Routes.splash) {
        return Routes.dashboard;
      }
      return null;
    },
    routes: <RouteBase>[
      GoRoute(
        path: Routes.splash,
        builder: (BuildContext context, GoRouterState state) => const _SplashScreen(),
      ),
      GoRoute(
        path: Routes.signIn,
        builder: (BuildContext context, GoRouterState state) => const SignInScreen(),
      ),
      GoRoute(
        path: Routes.signUp,
        builder: (BuildContext context, GoRouterState state) => const SignUpScreen(),
      ),
      ShellRoute(
        navigatorKey: _shellKey,
        builder: (BuildContext context, GoRouterState state, Widget child) =>
            HomeShell(location: state.matchedLocation, child: child),
        routes: <RouteBase>[
          GoRoute(
            path: Routes.dashboard,
            pageBuilder: (BuildContext context, GoRouterState state) =>
                const NoTransitionPage<void>(child: DashboardScreen()),
          ),
          GoRoute(
            path: Routes.reports,
            pageBuilder: (BuildContext context, GoRouterState state) =>
                const NoTransitionPage<void>(child: ReportsScreen()),
          ),
          GoRoute(
            path: Routes.settings,
            pageBuilder: (BuildContext context, GoRouterState state) =>
                const NoTransitionPage<void>(child: SettingsScreen()),
          ),
        ],
      ),
      GoRoute(
        path: Routes.daybook,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) => const DaybookScreen(),
      ),
      // The longer path is declared first. Both are literal so today's matcher
      // does not care, but the ordering means a later change to either -- a
      // path parameter on `outstanding`, say -- cannot silently swallow this.
      GoRoute(
        path: Routes.groupOutstanding,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) => GroupOutstandingScreen(
          kind: state.uri.queryParameters['kind'] == 'payable'
              ? OutstandingKind.payable
              : OutstandingKind.receivable,
          group: state.uri.queryParameters['group'],
        ),
      ),
      GoRoute(
        path: Routes.outstanding,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) => OutstandingScreen(
          kind: state.uri.queryParameters['kind'] == 'payable'
              ? OutstandingKind.payable
              : OutstandingKind.receivable,
        ),
      ),
      GoRoute(
        path: Routes.stock,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) =>
            StockScreen(only: state.uri.queryParameters['only']),
      ),
      GoRoute(
        path: Routes.ledgers,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) => const LedgersScreen(),
      ),
      GoRoute(
        path: Routes.slowMoving,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) => const SlowMovingScreen(),
      ),
      GoRoute(
        path: Routes.companies,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) =>
            const CompanyPickerScreen(),
      ),
      GoRoute(
        path: Routes.connectors,
        parentNavigatorKey: _rootKey,
        builder: (BuildContext context, GoRouterState state) => const ConnectorsScreen(),
        routes: <RouteBase>[
          GoRoute(
            path: 'new',
            parentNavigatorKey: _rootKey,
            builder: (BuildContext context, GoRouterState state) =>
                const PairConnectorScreen(),
          ),
          GoRoute(
            path: ':connectorId',
            parentNavigatorKey: _rootKey,
            builder: (BuildContext context, GoRouterState state) =>
                ConnectorDetailScreen(
              connectorId: state.pathParameters['connectorId'] ?? '',
            ),
            routes: <RouteBase>[
              GoRoute(
                path: 'link',
                parentNavigatorKey: _rootKey,
                builder: (BuildContext context, GoRouterState state) =>
                    LinkCompanyScreen(
                  connectorId: state.pathParameters['connectorId'] ?? '',
                ),
              ),
            ],
          ),
        ],
      ),
    ],
  );
});

/// Bridges Riverpod's auth state to go_router's imperative refresh.
class _AuthRefresh extends ChangeNotifier {
  _AuthRefresh(Ref ref) {
    _subscription = ref.listen<AuthState>(
      authControllerProvider,
      (AuthState? previous, AuthState next) {
        if (previous?.status != next.status) notifyListeners();
      },
    );
  }

  late final ProviderSubscription<AuthState> _subscription;

  @override
  void dispose() {
    _subscription.close();
    super.dispose();
  }
}

class _SplashScreen extends StatelessWidget {
  const _SplashScreen();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.insights_rounded, size: 48, color: theme.colorScheme.primary),
            const SizedBox(height: 16),
            Text('TallyFlow', style: theme.textTheme.headlineSmall),
            const SizedBox(height: 24),
            const SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.2),
            ),
          ],
        ),
      ),
    );
  }
}
