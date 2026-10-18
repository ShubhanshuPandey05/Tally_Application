/// The Terms of Use and Privacy Policy screens.
///
/// Both are linked from sign-up, so the thing worth pinning is that they open
/// from the auth screens and render every section without an account behind
/// them.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:tallyflow/src/app/router.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/features/legal/domain/legal_documents.dart';
import 'package:tallyflow/src/features/legal/presentation/legal_screen.dart';

Widget _app() {
  final GoRouter router = GoRouter(
    routes: <RouteBase>[
      GoRoute(
        path: '/',
        builder: (BuildContext context, GoRouterState state) => const Scaffold(
          body: Center(
              child:
                  LegalLinks(lead: 'By creating an account you agree to the')),
        ),
      ),
      GoRoute(
        path: Routes.terms,
        builder: (BuildContext context, GoRouterState state) =>
            const LegalScreen(document: termsOfUse),
      ),
      GoRoute(
        path: Routes.privacy,
        builder: (BuildContext context, GoRouterState state) =>
            const LegalScreen(document: privacyPolicy),
      ),
    ],
  );
  return MaterialApp.router(theme: AppTheme.light(), routerConfig: router);
}

void main() {
  for (final LegalDocument document in <LegalDocument>[
    termsOfUse,
    privacyPolicy
  ]) {
    testWidgets('${document.title} renders every section',
        (WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: AppTheme.light(),
        home: LegalScreen(document: document),
      ));

      expect(find.text(document.title), findsOneWidget);
      expect(find.text('Last updated ${document.updated}'), findsOneWidget);
      for (final LegalSection section in document.sections) {
        await tester.scrollUntilVisible(find.text(section.heading), 120);
        expect(find.text(section.heading), findsOneWidget);
      }
    });
  }

  testWidgets('the links open each document and Back returns',
      (WidgetTester tester) async {
    await tester.pumpWidget(_app());

    await tester.tap(find.text('Terms of Use'));
    await tester.pumpAndSettle();
    expect(find.text(termsOfUse.intro), findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Privacy Policy'));
    await tester.pumpAndSettle();
    expect(find.text(privacyPolicy.intro), findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.textContaining('By creating an account'), findsOneWidget);
  });
}
