/// Entitlement, and the one thing the app must never get wrong about it.
///
/// The backend enforces every rule here — `test_portal.py` covers that. What
/// this file pins is the *direction the app fails in*, because both failures
/// are silent:
///
///  * A control drawn for an account that cannot use it fails only when tapped,
///    which reads as broken software rather than as a subscription that has not
///    been approved.
///  * A rule re-derived on the phone drifts from the one the server enforces.
///    So the app reads `allows_changes` and never recomputes it from status and
///    expiry, and these tests exist to keep it that way.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/features/auth/application/auth_controller.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/subscription/domain/subscription.dart';
import 'package:tallyflow/src/features/subscription/presentation/subscription_notice.dart';

OrgSubscription subscription({
  String status = 'active',
  bool allowsChanges = true,
  bool isExpired = false,
  int maxUsers = 5,
  int usersUsed = 1,
  int maxCompanies = 3,
  int companiesUsed = 1,
  String message = '',
}) {
  return OrgSubscription.fromJson(<String, Object?>{
    'status': status,
    'is_expired': isExpired,
    'allows_changes': allowsChanges,
    'allows_data': true,
    'max_users': maxUsers,
    'max_companies': maxCompanies,
    'users_used': usersUsed,
    'companies_used': companiesUsed,
    'expires_at': null,
    'message': message,
  });
}

AppUser user({String role = 'admin', OrgSubscription? plan}) {
  return AppUser(
    id: 'u1',
    email: 'owner@bhatiastores.in',
    orgId: 'o1',
    orgName: 'Bhatia Supermarket',
    role: UserRole.parse(role),
    subscription: plan ?? subscription(),
  );
}

void main() {
  group('decoding', () {
    test('an unknown status reads as pending, never as active', () {
      // Same direction as UserRole.parse: a future state this build predates
      // must not unlock the account.
      expect(SubscriptionStatus.parse('trialling'), SubscriptionStatus.pending);
      expect(SubscriptionStatus.parse(null), SubscriptionStatus.pending);
    });

    test('a response with no subscription block is locked, not open', () {
      // An old build against a new backend, or the other way round. Knowing
      // nothing about the entitlement is not the same as being entitled.
      final AppUser decoded = AppUser.fromJson(<String, Object?>{
        'id': 'u1',
        'email': 'owner@bhatiastores.in',
        'org_id': 'o1',
        'org_name': 'Bhatia Supermarket',
        'role': 'admin',
      });
      expect(decoded.subscription.allowsChanges, isFalse);
      expect(decoded.canManageConnectors, isFalse);
    });

    test('allows_changes is read, never recomputed', () {
      // The server is the only thing that decides. If the app worked this out
      // from `status` it would eventually work it out differently -- so a
      // status of "active" with the flag withdrawn must still be locked.
      final OrgSubscription plan =
          subscription(status: 'active', allowsChanges: false);
      expect(plan.status, SubscriptionStatus.active);
      expect(user(plan: plan).canManageConnectors, isFalse);
    });
  });

  group('what an account may do', () {
    test('a pending account draws no administrative controls', () {
      final AppUser owner = user(
        plan: subscription(status: 'pending', allowsChanges: false, maxUsers: 0,
            maxCompanies: 0, usersUsed: 1, companiesUsed: 0),
      );
      expect(owner.role.isAdmin, isTrue,
          reason: 'they are still the admin of their own business');
      expect(owner.canManageConnectors, isFalse);
      expect(owner.canLinkCompanies, isFalse);
      expect(owner.canManageTeam, isFalse);
    });

    test('an approved account unlocks all three', () {
      final AppUser owner = user();
      expect(owner.canManageConnectors, isTrue);
      expect(owner.canLinkCompanies, isTrue);
      expect(owner.canManageTeam, isTrue);
    });

    test('a live subscription never promotes staff', () {
      // Two independent gates. Both have to open, and entitlement is not a
      // route to a permission the role does not carry.
      final AppUser cashier = user(role: 'staff');
      expect(cashier.subscription.allowsChanges, isTrue);
      expect(cashier.canManageConnectors, isFalse);
      expect(cashier.canManageTeam, isFalse);
    });

    test('an expired term reads as stopped, not as waiting', () {
      // They need different words: "nearly there" versus "ring your partner".
      final OrgSubscription plan =
          subscription(status: 'active', allowsChanges: false, isExpired: true);
      expect(plan.isStopped, isTrue);
      expect(plan.isAwaitingApproval, isFalse);
    });

    test('a pending account is waiting, not stopped', () {
      final OrgSubscription plan =
          subscription(status: 'pending', allowsChanges: false);
      expect(plan.isAwaitingApproval, isTrue);
      expect(plan.isStopped, isFalse);
    });
  });

  group('the subscription error', () {
    test('is recognised by its own status code', () {
      const ApiException error = ApiException(
        code: 'subscription_inactive',
        message: 'Your business is waiting to be approved.',
        statusCode: 402,
      );
      expect(error.isSubscriptionInactive, isTrue);
      // Never retryable, and never a version problem: three refusals that need
      // three completely different screens.
      expect(error.retryable, isFalse);
      expect(error.isUpdateRequired, isFalse);
      expect(error.isConnectivity, isFalse);
    });

    test('an ordinary permission failure is not one', () {
      const ApiException error = ApiException(
        code: 'forbidden',
        message: 'You do not have permission to do that.',
        statusCode: 403,
      );
      expect(error.isSubscriptionInactive, isFalse);
    });
  });

  group('the notice', () {
    Future<void> pump(WidgetTester tester, AppUser signedIn) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: <Override>[
            authControllerProvider.overrideWith(() => _FixedAuth(signedIn)),
          ],
          child: const MaterialApp(
            home: Scaffold(
              body: Column(
                children: <Widget>[SubscriptionNotice(), SubscriptionCard()],
              ),
            ),
          ),
        ),
      );
      await tester.pump();
    }

    testWidgets('says why the buttons are missing, in the server\'s words',
        (WidgetTester tester) async {
      // One sentence, written once, on the server. The app repeating it in its
      // own words is how the owner and their partner end up reading different
      // explanations of the same state.
      const String message = 'Your business is waiting to be approved.';
      await pump(
        tester,
        user(plan: subscription(
            status: 'pending', allowsChanges: false, message: message)),
      );
      expect(find.text(message), findsOneWidget);
    });

    testWidgets('stays silent for a live account', (WidgetTester tester) async {
      await pump(tester, user());
      expect(find.text('Waiting for approval'), findsNothing);
      // The allowance card is still there: knowing "1 of 3 companies" a week
      // early is what turns a blocked action into a planned phone call.
      expect(find.text('Companies'), findsOneWidget);
      expect(find.text('1 of 3'), findsOneWidget);
    });

    testWidgets('never tells a cashier the business is behind',
        (WidgetTester tester) async {
      // Not their problem, not their information, and nothing they could do.
      await pump(
        tester,
        user(role: 'staff', plan: subscription(
            status: 'suspended', allowsChanges: false,
            message: 'This TallyFlow account has been suspended.')),
      );
      expect(find.text('This TallyFlow account has been suspended.'), findsNothing);
      expect(find.text('Companies'), findsNothing);
    });

    testWidgets('an unapproved account reads as "not set yet", not "0 of 0"',
        (WidgetTester tester) async {
      // A full red bar over "0 of 0" would say "you have used everything up".
      // Nothing has been agreed yet, which is a different statement.
      await pump(
        tester,
        user(plan: subscription(
            status: 'pending', allowsChanges: false,
            maxUsers: 0, maxCompanies: 0, usersUsed: 1, companiesUsed: 0)),
      );
      expect(find.text('not set yet'), findsNWidgets(2));
      expect(find.text('0 of 0'), findsNothing);
    });
  });
}

/// An auth controller pinned to one user, so the widgets under test see a
/// settled session without a network stack behind them.
class _FixedAuth extends AuthController {
  _FixedAuth(this._user);

  final AppUser _user;

  @override
  AuthState build() => AuthState(status: AuthStatus.signedIn, user: _user);
}
