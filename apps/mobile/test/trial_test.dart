/// The free trial, the phone number that starts it, and the partner-code box.
///
/// The backend decides every rule here -- `test_trial.py` covers that. What
/// this file pins is what the phone does with the answers:
///
///  * a trial that has not started says *when* it will, rather than showing a
///    date that does not exist yet;
///  * an older backend that sends no `needs_phone` never locks anybody on a
///    screen whose endpoint is not there;
///  * the partner-code box blocks the form only on a code the server has
///    actually refused, never on a check that could not be made.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/features/auth/application/auth_controller.dart';
import 'package:tallyflow/src/features/auth/data/auth_repository.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/auth/domain/phone.dart';
import 'package:tallyflow/src/features/auth/presentation/partner_code_field.dart';
import 'package:tallyflow/src/features/subscription/domain/subscription.dart';

Map<String, Object?> _plan({
  bool isTrial = true,
  String? trialStartedAt,
  String? expiresAt,
  bool isExpired = false,
}) =>
    <String, Object?>{
      'status': 'active',
      'is_expired': isExpired,
      'allows_changes': !isExpired,
      'allows_data': !isExpired,
      'max_users': 2,
      'max_companies': 1,
      'users_used': 1,
      'companies_used': trialStartedAt == null ? 0 : 1,
      'expires_at': expiresAt,
      'message': '',
      'is_trial': isTrial,
      'trial_started_at': trialStartedAt,
      'trial_days': isTrial ? 7 : null,
    };

/// Answers the live check from a table, and counts how often it was asked.
class _FakeAuth implements AuthRepository {
  _FakeAuth(this.codes, {this.fails = false});

  final Map<String, String> codes;
  final bool fails;
  final List<String> asked = <String>[];

  @override
  Future<PartnerCodeCheck> checkPartnerCode(String code) async {
    asked.add(code);
    if (fails) {
      throw const ApiException(code: 'rate_limited', message: 'Too many requests.');
    }
    final String? name = codes[code];
    return PartnerCodeCheck(valid: name != null, partnerName: name);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<List<PartnerCodeState>> _pump(WidgetTester tester, _FakeAuth fake) async {
  final List<PartnerCodeState> seen = <PartnerCodeState>[];
  final TextEditingController controller = TextEditingController();
  addTearDown(controller.dispose);
  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[authRepositoryProvider.overrideWithValue(fake)],
      child: MaterialApp(
        home: Scaffold(
          body: PartnerCodeField(controller: controller, onStateChanged: seen.add),
        ),
      ),
    ),
  );
  return seen;
}

void main() {
  group('the trial on the wire', () {
    test('a trial waiting for its first company says so, with no end date', () {
      final OrgSubscription plan = OrgSubscription.fromJson(_plan());
      expect(plan.isTrial, isTrue);
      expect(plan.isTrialWaitingToStart, isTrue);
      expect(plan.expiresAt, isNull);
      expect(plan.trialDays, 7);
      expect(plan.stateLabel, 'Free trial');
    });

    test('a running trial has started and ends on a date', () {
      final OrgSubscription plan = OrgSubscription.fromJson(_plan(
        trialStartedAt: '2026-10-24T09:00:00+00:00',
        expiresAt: '2026-10-31T09:00:00+00:00',
      ));
      expect(plan.isTrialWaitingToStart, isFalse);
      expect(plan.trialStartedAt, isNotNull);
      expect(plan.expiresAt, isNotNull);
    });

    test('an ended trial is named as one, not as a lapsed plan', () {
      final OrgSubscription plan = OrgSubscription.fromJson(_plan(
        trialStartedAt: '2026-10-01T09:00:00+00:00',
        expiresAt: '2026-10-08T09:00:00+00:00',
        isExpired: true,
      ));
      expect(plan.stateLabel, 'Trial ended');
      expect(plan.isStopped, isTrue);
    });

    test('a paid account is named by its plan', () {
      final OrgSubscription plan = OrgSubscription.fromJson(<String, Object?>{
        'status': 'active',
        'allows_changes': true,
        'plan_name': 'Premium',
        'expires_at': '2027-10-24T09:00:00+00:00',
      });
      expect(plan.stateLabel, 'Premium plan');
    });

    test('a backend from before trials reads as no trial at all', () {
      final OrgSubscription plan = OrgSubscription.fromJson(<String, Object?>{
        'status': 'active',
        'allows_changes': true,
      });
      expect(plan.isTrial, isFalse);
      expect(plan.stateLabel, 'Active');
    });
  });

  group('the phone number', () {
    test('an older backend that never asks does not lock anyone out', () {
      final AppUser user = AppUser.fromJson(<String, Object?>{
        'id': 'u1',
        'email': 'owner@shop.in',
        'org_id': 'o1',
        'org_name': 'Gupta Electricals',
        'role': 'admin',
      });
      expect(user.needsPhone, isFalse);
      expect(user.phone, isNull);
    });

    test('needs_phone is read as sent', () {
      final AppUser user = AppUser.fromJson(<String, Object?>{
        'id': 'u1',
        'email': 'owner@shop.in',
        'org_id': 'o1',
        'org_name': 'Gupta Electricals',
        'role': 'admin',
        'needs_phone': true,
      });
      expect(user.needsPhone, isTrue);
    });

    test('the ways people type an Indian mobile are all accepted', () {
      for (final String typed in <String>[
        '9876543210',
        '98765 43210',
        '098765-43210',
        '+91 98765 43210',
        '919876543210',
        '+44 7700 900123',
      ]) {
        expect(validatePhone(typed), isNull, reason: typed);
      }
    });

    test('a number that cannot be a mobile is caught before the server', () {
      for (final String typed in <String>['', '12345', '1234567890', '+91 12345 67890']) {
        expect(validatePhone(typed), isNotNull, reason: typed);
      }
    });
  });

  group('the partner-code box', () {
    testWidgets('waits for typing to pause, then names the partner',
        (WidgetTester tester) async {
      final _FakeAuth fake = _FakeAuth(<String, String>{'RAVI-01': 'Ravi Computers'});
      final List<PartnerCodeState> seen = await _pump(tester, fake);

      await tester.enterText(find.byType(TextFormField), 'ravi-01');
      await tester.pump(const Duration(milliseconds: 100));
      expect(fake.asked, isEmpty);
      expect(seen.last, PartnerCodeState.checking);
      expect(seen.last.allowsSubmit, isFalse);

      await tester.pump(const Duration(seconds: 1));
      expect(fake.asked, <String>['RAVI-01']);
      expect(seen.last, PartnerCodeState.valid);
      expect(find.text('Partner: Ravi Computers'), findsOneWidget);
    });

    testWidgets('an unknown code is shown under the box and holds the form',
        (WidgetTester tester) async {
      final _FakeAuth fake = _FakeAuth(<String, String>{});
      final List<PartnerCodeState> seen = await _pump(tester, fake);

      await tester.enterText(find.byType(TextFormField), 'NOPE-99');
      await tester.pump(const Duration(seconds: 1));
      expect(seen.last, PartnerCodeState.invalid);
      expect(seen.last.allowsSubmit, isFalse);
      expect(find.textContaining('not valid'), findsOneWidget);

      // Clearing the box is the other way to carry on.
      await tester.enterText(find.byType(TextFormField), '');
      await tester.pump();
      expect(seen.last, PartnerCodeState.empty);
      expect(seen.last.allowsSubmit, isTrue);
    });

    testWidgets('a check that could not be made does not block signing up',
        (WidgetTester tester) async {
      final _FakeAuth fake = _FakeAuth(<String, String>{}, fails: true);
      final List<PartnerCodeState> seen = await _pump(tester, fake);

      await tester.enterText(find.byType(TextFormField), 'RAVI-01');
      await tester.pump(const Duration(seconds: 1));
      expect(seen.last, PartnerCodeState.unknown);
      expect(seen.last.allowsSubmit, isTrue);
    });

    testWidgets('a code too short to exist is never sent',
        (WidgetTester tester) async {
      final _FakeAuth fake = _FakeAuth(<String, String>{});
      final List<PartnerCodeState> seen = await _pump(tester, fake);

      await tester.enterText(find.byType(TextFormField), 'AB');
      await tester.pump(const Duration(seconds: 1));
      expect(fake.asked, isEmpty);
      expect(seen.last, PartnerCodeState.invalid);
    });
  });

  // Keeps the import honest: the controller is what the router reads.
  test('AuthState carries the user the router gates on', () {
    const AppUser user = AppUser(
      id: 'u1',
      email: 'owner@shop.in',
      orgId: 'o1',
      orgName: 'Gupta Electricals',
      role: UserRole.admin,
      needsPhone: true,
    );
    const AuthState state = AuthState(status: AuthStatus.signedIn, user: user);
    expect(state.user!.needsPhone, isTrue);
  });
}
