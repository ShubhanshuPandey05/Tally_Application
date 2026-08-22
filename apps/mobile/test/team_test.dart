/// Roles, and the parts of the team screen that carry a security meaning.
///
/// The app never enforces authorisation — the backend does, and `test_team.py`
/// covers that. What is worth pinning here is the two places where a mistake in
/// Dart would quietly *mislead* an admin:
///
///  * A role string the app does not recognise must read as staff, never admin.
///    Falling the other way would show administrative controls to whoever a
///    future backend called something new.
///  * An admin's company list is whatever the server sent. If the app inferred
///    "admin means all" for itself, that rule would exist twice and could drift.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/team/domain/team_member.dart';
import 'package:tallyflow/src/features/team/presentation/member_editor_sheet.dart';

TeamMember member({
  String role = 'staff',
  List<String> companies = const <String>[],
  bool mustChange = false,
  bool active = true,
}) {
  return TeamMember.fromJson(<String, Object?>{
    'id': 'u1',
    'email': 'ravi@bhatiastores.in',
    'full_name': 'Ravi Kumar',
    'role': role,
    'is_active': active,
    'must_change_password': mustChange,
    'company_ids': companies,
    'last_login_at': null,
  });
}

void main() {
  group('roles', () {
    test('an unknown role is staff, never admin', () {
      // Fail-safe direction: a future backend inventing a third role must not
      // hand a stranger the "add a Tally PC" button.
      expect(UserRole.parse('superuser'), UserRole.staff);
      expect(UserRole.parse(null), UserRole.staff);
      expect(UserRole.parse(''), UserRole.staff);
    });

    test('admin is the only role that may change the account', () {
      expect(UserRole.admin.canManageConnectors, isTrue);
      expect(UserRole.admin.canLinkCompanies, isTrue);
      expect(UserRole.admin.canManageTeam, isTrue);

      expect(UserRole.staff.canManageConnectors, isFalse);
      expect(UserRole.staff.canLinkCompanies, isFalse);
      expect(UserRole.staff.canManageTeam, isFalse);
    });

    test('the old role names are gone, not silently mapped to admin', () {
      // The rename migrates owner -> admin server-side. If a stale client ever
      // sees the old string it must degrade to staff rather than assume power.
      expect(UserRole.parse('owner'), UserRole.staff);
      expect(UserRole.parse('accountant'), UserRole.staff);
    });
  });

  group('decoding a member', () {
    test('reads the grant list the server sent', () {
      final TeamMember decoded = member(companies: <String>['c1', 'c2']);
      expect(decoded.companyIds, <String>['c1', 'c2']);
      expect(decoded.role, UserRole.staff);
    });

    test("an admin's reach comes from the server, not from an app-side rule", () {
      final TeamMember admin = member(role: 'admin', companies: <String>['c1', 'c2']);
      expect(admin.role, UserRole.admin);
      expect(admin.companyIds, hasLength(2));
    });

    test('a missing name falls back to the email local part', () {
      final TeamMember decoded = TeamMember.fromJson(<String, Object?>{
        'id': 'u2',
        'email': 'sunita@bhatiastores.in',
        'role': 'staff',
        'company_ids': <String>[],
      });
      expect(decoded.displayName, 'sunita');
      expect(decoded.initials, 'S');
    });

    test('the one-time password survives decoding intact', () {
      final NewTeamMember created = NewTeamMember.fromJson(<String, Object?>{
        'member': <String, Object?>{
          'id': 'u3',
          'email': 'new@bhatiastores.in',
          'role': 'staff',
          'company_ids': <String>[],
        },
        'temporary_password': 'Kp7mQx2ratT',
      });
      expect(created.temporaryPassword, 'Kp7mQx2ratT');
      expect(created.member.email, 'new@bhatiastores.in');
    });
  });

  group('the member editor', () {
    final List<Company> companies = <Company>[
      Company.fromJson(<String, Object?>{
        'id': 'c1',
        'name': 'Bhatia Supermarket',
        'tally_name': 'Bhatia Supermarket',
        'connector_id': 'k1',
        'base_currency': 'INR',
      }),
      Company.fromJson(<String, Object?>{
        'id': 'c2',
        'name': 'Private Family Trust',
        'tally_name': 'Private Family Trust',
        'connector_id': 'k1',
        'base_currency': 'INR',
      }),
    ];

    Future<void> open(WidgetTester tester, {TeamMember? existing}) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (BuildContext context) => Scaffold(
              body: ElevatedButton(
                onPressed: () => showMemberEditor(
                  context,
                  companies: companies,
                  title: 'Add a person',
                  existing: existing,
                ),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
    }

    testWidgets('offers both roles and defaults a new person to staff',
        (WidgetTester tester) async {
      await open(tester);

      expect(find.text('Admin'), findsOneWidget);
      expect(find.text('Staff'), findsOneWidget);

      final RadioListTile<UserRole> staff = tester.widget(
        find.widgetWithText(RadioListTile<UserRole>, 'Staff'),
      );
      expect(staff.groupValue, UserRole.staff,
          reason: 'least privilege is the default for a new colleague');
    });

    testWidgets('warns plainly when a staff member has no companies ticked',
        (WidgetTester tester) async {
      // An empty selection is legitimate, so it must not look like a broken
      // form -- but it must also not look like the person can see everything.
      await open(tester);

      expect(
        find.text('They will not see any company until you tick one.'),
        findsOneWidget,
      );
    });

    testWidgets('hides the company picker for an admin', (WidgetTester tester) async {
      // An admin's access follows from the role, so tick boxes there would
      // imply a choice that does not exist.
      await open(tester);
      await tester.tap(find.text('Admin'));
      await tester.pumpAndSettle();

      expect(find.text('Companies they can see'), findsNothing);
      expect(find.text('Bhatia Supermarket'), findsNothing);
    });

    testWidgets('shows every company as a tick box for staff',
        (WidgetTester tester) async {
      await open(tester);

      expect(find.text('Companies they can see'), findsOneWidget);
      expect(find.byType(CheckboxListTile), findsNWidgets(2));
      expect(find.text('Private Family Trust'), findsOneWidget);
    });

    testWidgets('an existing grant comes back ticked', (WidgetTester tester) async {
      await open(tester, existing: member(companies: <String>['c1']));

      final CheckboxListTile first = tester.widget(
        find.widgetWithText(CheckboxListTile, 'Bhatia Supermarket'),
      );
      final CheckboxListTile second = tester.widget(
        find.widgetWithText(CheckboxListTile, 'Private Family Trust'),
      );

      expect(first.value, isTrue);
      expect(second.value, isFalse);
    });

    testWidgets('the email cannot be edited once the account exists',
        (WidgetTester tester) async {
      // Changing it would move the account itself, so it is disabled rather
      // than accepted and silently ignored on save.
      await open(tester, existing: member());

      final TextField email = tester.widget(
        find.widgetWithText(TextField, 'ravi@bhatiastores.in'),
      );
      expect(email.enabled, isFalse);
    });

    testWidgets('a new person has nothing to remove or reset',
        (WidgetTester tester) async {
      await open(tester);

      expect(find.text('Remove from this business'), findsNothing);
      expect(find.text('Reset their password'), findsNothing);
      expect(find.text('Add person'), findsOneWidget);
    });

    testWidgets('an existing person can be reset or removed',
        (WidgetTester tester) async {
      await open(tester, existing: member());

      expect(find.text('Remove from this business'), findsOneWidget);
      expect(find.text('Reset their password'), findsOneWidget);
      expect(find.text('Save changes'), findsOneWidget);
    });
  });
}
