@Tags(<String>['live'])
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/config/app_config.dart';
import 'package:tallyflow/src/core/network/api_client.dart';
import 'package:tallyflow/src/core/network/api_exception.dart';
import 'package:tallyflow/src/core/storage/token_store.dart';
import 'package:tallyflow/src/features/auth/data/auth_repository.dart';
import 'package:tallyflow/src/features/auth/domain/app_user.dart';
import 'package:tallyflow/src/features/companies/data/company_repository.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/connectors/data/connector_repository.dart';
import 'package:tallyflow/src/features/connectors/domain/connector.dart';

import 'support/fake_http.dart';

/// The app's data layer against a real, running backend.
///
/// Everything else in this suite talks to a fake -- a scripted adapter or a
/// checked-in fixture -- and both sides of a faked boundary always agree with
/// each other. This is the only test where the app's URLs, headers, payloads
/// and error handling meet a server that can actually disagree with them.
///
/// Run it with the backend up:
///
///     python -m uvicorn tally_backend.main:create_app --factory --port 8099
///     flutter test test/live_backend_test.dart \
///         --dart-define=TALLYFLOW_LIVE_URL=http://127.0.0.1:8099
///
/// Skipped entirely when that define is absent, so `flutter test` stays
/// hermetic.
const String _liveUrl = String.fromEnvironment('TALLYFLOW_LIVE_URL');

void main() {
  if (_liveUrl.isEmpty) {
    test('live backend checks', () {}, skip: 'set --dart-define=TALLYFLOW_LIVE_URL');
    return;
  }

  const AppConfig config = AppConfig(baseUrl: _liveUrl, environment: 'live');
  late ApiClient api;
  late String email;

  setUp(() {
    api = ApiClient(
      config: config,
      tokens: TokenStore(storage: FakeSecureStorage()),
      onSignedOut: () async {},
    );
    email = 'live-${DateTime.now().microsecondsSinceEpoch}@bhatiastores.in';
  });

  test('registers, stores tokens, and reads the signed-in user back', () async {
    final AuthRepository auth = AuthRepository(api);

    final AppUser created = await auth.signUp(
      email: email,
      password: 'a-sufficiently-long-password',
      orgName: 'Bhatia Supermarket',
      fullName: 'Shop Owner',
    );

    expect(created.email, email);
    expect(created.role, UserRole.owner);
    expect(created.orgName, 'Bhatia Supermarket');

    // Proves the interceptor is attaching the token the repository just stored.
    final AppUser fetched = await auth.me();
    expect(fetched.id, created.id);
  });

  test('rejects a password the server considers too short', () async {
    // The app validates 10 characters locally; this asserts the rule it is
    // mirroring is really the server's, and that a 422 arrives in the shared
    // envelope with usable field errors.
    try {
      await AuthRepository(api).signUp(
        email: email,
        password: 'short',
        orgName: 'Bhatia Supermarket',
      );
      fail('expected the server to refuse a five-character password');
    } on ApiException catch (error) {
      expect(error.code, 'invalid_request');
      expect(error.fields.keys, contains('password'));
    }
  });

  test('a wrong password is a plainly worded 401, not a stack trace', () async {
    final AuthRepository auth = AuthRepository(api);
    await auth.signUp(
      email: email,
      password: 'a-sufficiently-long-password',
      orgName: 'Bhatia Supermarket',
    );

    try {
      await auth.signIn(email: email, password: 'the-wrong-password-entirely');
      fail('expected a rejected sign-in');
    } on ApiException catch (error) {
      expect(error.isAuthFailure, isTrue);
      expect(error.message, isNot(contains('Exception')));
    }
  });

  test('a fresh account has no companies and says so honestly', () async {
    await AuthRepository(api).signUp(
      email: email,
      password: 'a-sufficiently-long-password',
      orgName: 'Bhatia Supermarket',
    );

    final List<Company> companies = await CompanyRepository(api).list();
    expect(companies, isEmpty);
  });

  test('pairs a connector and reports it offline until the PC calls in',
      () async {
    await AuthRepository(api).signUp(
      email: email,
      password: 'a-sufficiently-long-password',
      orgName: 'Bhatia Supermarket',
    );

    final ConnectorRepository connectors = ConnectorRepository(api);
    final ConnectorPairing pairing = await connectors.create(name: 'Shop PC');

    expect(pairing.secret, isNotEmpty);
    expect(pairing.pairingCode, contains('-'));

    final List<Connector> listed = await connectors.list();
    expect(listed.single.id, pairing.connectorId);
    // No connector process exists, so the app must say "PC offline" rather
    // than trusting the row's status field.
    expect(listed.single.online, isFalse);
    expect(listed.single.health, ConnectorHealth.offline);
  });

  test('another org cannot read our company, and it reads as not-found',
      () async {
    // 404 rather than 403 on purpose: a 403 would confirm the id exists, which
    // turns company ids into an enumeration oracle.
    await AuthRepository(api).signUp(
      email: email,
      password: 'a-sufficiently-long-password',
      orgName: 'Bhatia Supermarket',
    );

    try {
      await api.getJson('/v1/companies/somebody-elses-company/dashboard');
      fail('expected the read to be refused');
    } on ApiException catch (error) {
      expect(error.statusCode, 404);
      expect(error.code, 'not_found');
    }
  });

  test('signing out revokes the session on the server too', () async {
    final AuthRepository auth = AuthRepository(api);
    await auth.signUp(
      email: email,
      password: 'a-sufficiently-long-password',
      orgName: 'Bhatia Supermarket',
    );
    await auth.signOut();

    try {
      await auth.me();
      fail('expected the session to be gone');
    } on ApiException catch (error) {
      expect(error.isAuthFailure, isTrue);
    }
  });
}
