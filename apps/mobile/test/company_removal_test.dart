import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/features/auth/application/auth_controller.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/data/company_repository.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/companies/presentation/company_picker_screen.dart';
import 'package:tallyflow/src/features/subscription/domain/subscription.dart';

/// Removing a company from TallyFlow.
///
/// The dangerous misreading of this button is that it deletes a set of books,
/// so most of what is pinned here is about how hard it is to reach and what it
/// promises on the way.
const Company _first = Company(
  id: 'company-1',
  name: 'Bhatia Supermarket',
  tallyName: 'Bhatia Supermarket',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

const Company _second = Company(
  id: 'company-2',
  name: 'Bhatia Wholesale',
  tallyName: 'Bhatia Wholesale',
  connectorId: 'connector-1',
  baseCurrency: 'INR',
  isActive: true,
);

class _FakeCompanyRepository implements CompanyRepository {
  final List<String> unlinked = <String>[];

  @override
  Future<void> unlink(String companyId) async => unlinked.add(companyId);

  @override
  Future<List<Company>> list() async => <Company>[_first, _second];

  @override
  Future<Company> detail(String companyId) async => _first;

  @override
  Future<List<DiscoveredCompany>> discover(String connectorId) async =>
      <DiscoveredCompany>[];

  @override
  Future<Company> link({
    required String connectorId,
    required String tallyName,
    String? displayName,
  }) async =>
      _first;
}

AppUser _user({UserRole role = UserRole.admin, bool demo = false}) => AppUser(
      id: 'user-1',
      email: 'owner@bhatiastores.in',
      orgId: 'org-1',
      orgName: 'Bhatia Supermarket',
      role: role,
      subscription: OrgSubscription.fromJson(<String, Object?>{
        'status': demo ? 'active' : 'active',
        'allows_changes': !demo,
        'allows_data': true,
        'is_demo': demo,
      }),
    );

class _SignedIn extends AuthController {
  _SignedIn(this._user);

  final AppUser _user;

  @override
  AuthState build() => AuthState(status: AuthStatus.signedIn, user: _user);
}

Future<ProviderContainer> _pump(
  WidgetTester tester, {
  required _FakeCompanyRepository repository,
  AppUser? user,
}) async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final SharedPreferences preferences = await SharedPreferences.getInstance();

  final ProviderContainer container = ProviderContainer(
    overrides: <Override>[
      sharedPreferencesProvider.overrideWithValue(preferences),
      companyRepositoryProvider.overrideWithValue(repository),
      companiesProvider
          .overrideWith((Ref ref) async => <Company>[_first, _second]),
      authControllerProvider.overrideWith(() => _SignedIn(user ?? _user())),
    ],
  );
  addTearDown(container.dispose);

  await tester.pumpWidget(
    UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
        theme: AppTheme.light(),
        home: const CompanyPickerScreen(),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return container;
}

void main() {
  testWidgets('an admin can remove a company, after being told what happens',
      (WidgetTester tester) async {
    final _FakeCompanyRepository repository = _FakeCompanyRepository();
    final ProviderContainer container =
        await _pump(tester, repository: repository);
    await container
        .read(activeCompanyIdProvider.notifier)
        .select(_second.id);

    await tester.tap(find.byTooltip('Remove ${_second.name}'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Remove from TallyFlow'));
    await tester.pumpAndSettle();

    // The sentence that decides whether anybody dares press the button.
    expect(
      find.textContaining('Nothing in TallyPrime is changed'),
      findsOneWidget,
    );
    expect(repository.unlinked, isEmpty, reason: 'not until it is confirmed');

    await tester.tap(find.widgetWithText(FilledButton, 'Remove'));
    await tester.pumpAndSettle();

    expect(repository.unlinked, <String>[_second.id]);
    // The selection went with it. Leaving a dead id behind is what puts an
    // empty dashboard on screen under a company name that no longer exists.
    expect(container.read(activeCompanyIdProvider), isNull);
  });

  testWidgets('cancelling removes nothing', (WidgetTester tester) async {
    final _FakeCompanyRepository repository = _FakeCompanyRepository();
    await _pump(tester, repository: repository);

    await tester.tap(find.byTooltip('Remove ${_first.name}'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Remove from TallyFlow'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, 'Cancel'));
    await tester.pumpAndSettle();

    expect(repository.unlinked, isEmpty);
  });

  testWidgets('staff are not offered it at all', (WidgetTester tester) async {
    await _pump(
      tester,
      repository: _FakeCompanyRepository(),
      user: _user(role: UserRole.staff),
    );

    expect(find.byTooltip('Remove ${_first.name}'), findsNothing);
  });

  testWidgets('the demo cannot be taken apart by whoever is looking at it',
      (WidgetTester tester) async {
    // Everyone shares the demo account, so one visitor unlinking its company
    // would empty the showroom for everybody else. The server refuses it; this
    // is the app not drawing a button that can only 402.
    await _pump(
      tester,
      repository: _FakeCompanyRepository(),
      user: _user(demo: true),
    );

    expect(find.byTooltip('Remove ${_first.name}'), findsNothing);
  });
}
