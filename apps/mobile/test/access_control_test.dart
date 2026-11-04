/// What a staff member may see inside a company, and the screens that set it.
///
/// The server enforces every bit of this (`test_access_policy.py`). What is
/// pinned here is the part that could quietly mislead an admin: the switches
/// must save exactly what they show, a switched-off row must not offer a way
/// inside it, and a staff member's menus must leave out what they cannot open.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/team/application/team_providers.dart';
import 'package:tallyflow/src/features/team/data/team_repository.dart';
import 'package:tallyflow/src/features/team/domain/access_policy.dart';
import 'package:tallyflow/src/features/team/domain/team_member.dart';
import 'package:tallyflow/src/features/team/presentation/member_access_screen.dart';
import 'package:tallyflow/src/features/top_reports/presentation/top_reports_screen.dart';

const Company _company = Company(
  id: 'company-1',
  name: 'Bhatia Supermarket',
  tallyName: 'Bhatia Supermarket',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

final TeamMember _ravi = TeamMember.fromJson(<String, Object?>{
  'id': 'u1',
  'email': 'ravi@bhatiastores.in',
  'full_name': 'Ravi Kumar',
  'role': 'staff',
  'company_ids': <String>['company-1'],
});

class _FakeTeamRepository implements TeamRepository {
  _FakeTeamRepository({this.policy = AccessPolicy.all});

  AccessPolicy policy;
  final List<AccessPolicy> saved = <AccessPolicy>[];

  @override
  Future<AccessPolicy> memberAccess(String memberId, String companyId) async => policy;

  @override
  Future<AccessPolicy> setMemberAccess(
    String memberId,
    String companyId,
    AccessPolicy next,
  ) async {
    saved.add(next);
    return policy = next;
  }

  @override
  Future<AccessCatalog> accessCatalog(String companyId) async => AccessCatalog.fromJson(
        <String, Object?>{
          'masters_read': true,
          'ledger_groups': <Object?>[
            <String, Object?>{'name': 'Current Assets', 'parent': 'Primary'},
            <String, Object?>{'name': 'Current Liabilities', 'parent': 'Primary'},
            <String, Object?>{'name': 'Sundry Debtors', 'parent': 'Current Assets'},
            <String, Object?>{'name': 'Sundry Creditors', 'parent': 'Current Liabilities'},
          ],
          'ledgers': <Object?>[
            <String, Object?>{'name': 'Reliance Retail', 'group': 'Sundry Debtors'},
            <String, Object?>{'name': 'SARA DISITIBUTOR', 'group': 'Sundry Creditors'},
          ],
          'stock_groups': <Object?>[
            <String, Object?>{'name': 'Grains'},
          ],
          'stock_items': <Object?>[
            <String, Object?>{'name': 'Rice', 'group': 'Grains'},
          ],
        },
      );

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<void> _pump(WidgetTester tester, Widget home, List<Override> overrides) async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final SharedPreferences preferences = await SharedPreferences.getInstance();
  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        sharedPreferencesProvider.overrideWithValue(preferences),
        companiesProvider.overrideWith((Ref ref) async => <Company>[_company]),
        ...overrides,
      ],
      child: MaterialApp(theme: AppTheme.light(), home: home),
    ),
  );
  await tester.pumpAndSettle();
}

Finder _switchOf(String title) => find.descendant(
      of: find.widgetWithText(ToggleRow, title),
      matching: find.byType(Switch),
    );

Finder _chevronOf(String title) => find.descendant(
      of: find.widgetWithText(ToggleRow, title),
      matching: find.byIcon(Icons.chevron_right),
    );

void main() {
  group('the policy', () {
    test('a fresh grant allows everything', () {
      expect(AccessPolicy.all.isUnrestricted, isTrue);
      expect(AccessPolicy.all.allowsReport('daybook'), isTrue);
      expect(AccessPolicy.all.allowsPrimeReport('customers'), isTrue);
    });

    test('a switched-off section hides its reports whatever their own switches say', () {
      const AccessPolicy noPrime = AccessPolicy(primeReports: false);
      expect(noPrime.allowsPrimeReport('customers'), isFalse);
      expect(noPrime.allowsReport('daybook'), isTrue);

      const AccessPolicy none = AccessPolicy(reports: false);
      expect(none.allowsReport('daybook'), isFalse);
    });

    test('survives the round trip to the server unchanged', () {
      const AccessPolicy policy = AccessPolicy(
        primeReports: false,
        deniedReports: <String>{'daybook', 'top:customers'},
        deniedLedgerGroups: <String>{'Sundry Creditors'},
        deniedStockItems: <String>{'Rice'},
      );
      final AccessPolicy back = AccessPolicy.fromJson(policy.toJson());
      expect(back.toJson(), policy.toJson());
      expect(back.isUnrestricted, isFalse);
    });
  });

  group('the access screen', () {
    testWidgets('drills into a group and saves exactly the switch that moved',
        (WidgetTester tester) async {
      final _FakeTeamRepository repository = _FakeTeamRepository();
      await _pump(
        tester,
        MemberAccessScreen(member: _ravi, company: _company),
        <Override>[teamRepositoryProvider.overrideWithValue(repository)],
      );

      expect(find.text('Reports'), findsOneWidget);
      expect(find.text('Ledger Groups'), findsOneWidget);
      expect(find.text('Stock Groups'), findsOneWidget);

      // Ledger Groups -> Current Liabilities -> Sundry Creditors, off.
      await tester.tap(find.text('Ledger Groups'));
      await tester.pumpAndSettle();
      expect(find.text('Current Assets'), findsOneWidget);
      await tester.tap(find.text('Current Liabilities'));
      await tester.pumpAndSettle();
      await tester.tap(_switchOf('Sundry Creditors'));
      await tester.pumpAndSettle();

      // Switched off: nothing inside it is offered.
      expect(_chevronOf('Sundry Creditors'), findsNothing);

      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.text('1 group or ledger switched off'), findsOneWidget);

      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(repository.saved, hasLength(1));
      expect(repository.saved.single.deniedLedgerGroups, <String>{'Sundry Creditors'});
      expect(repository.saved.single.deniedLedgers, isEmpty);
      expect(repository.saved.single.reports, isTrue);
    });

    testWidgets('a switched-off section cannot be opened', (WidgetTester tester) async {
      await _pump(
        tester,
        MemberAccessScreen(member: _ravi, company: _company),
        <Override>[
          teamRepositoryProvider.overrideWithValue(
            _FakeTeamRepository(policy: const AccessPolicy(stock: false)),
          ),
        ],
      );

      expect(_chevronOf('Stock Groups'), findsNothing);
      expect(_chevronOf('Ledger Groups'), findsOneWidget);
      expect(find.text('No stock items, and no entries that use one'), findsOneWidget);
    });

    testWidgets('nothing to save until something moves', (WidgetTester tester) async {
      await _pump(
        tester,
        MemberAccessScreen(member: _ravi, company: _company),
        <Override>[teamRepositoryProvider.overrideWithValue(_FakeTeamRepository())],
      );
      final FilledButton save = tester.widget(find.widgetWithText(FilledButton, 'Save'));
      expect(save.onPressed, isNull);
    });
  });

  testWidgets("a staff member's Top Prime Reports leave out the ones switched off",
      (WidgetTester tester) async {
    await _pump(
      tester,
      const TopReportsScreen(),
      <Override>[
        activeCompanyIdResolvedProvider.overrideWithValue('company-1'),
        myAccessProvider('company-1').overrideWith(
          (Ref ref) async => const AccessPolicy(deniedReports: <String>{'top:customers'}),
        ),
      ],
    );

    expect(find.text('Top Prime Customers'), findsNothing);
    expect(find.text('Top Prime Suppliers'), findsOneWidget);
  });
}
