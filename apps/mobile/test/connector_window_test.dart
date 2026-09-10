import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/connector/state.dart';
import 'package:tallyflow/src/connector/window.dart';

/// The connector's window, laid out at the sizes it actually opens at.
///
/// This exists because of one bug that shipped past `flutter analyze` and past
/// a release build without a word: a `Row` with `CrossAxisAlignment.stretch`
/// inside a scroll view asks its children to be infinitely tall. In a debug
/// build that is a loud assertion; in the release build that a customer runs it
/// is a screen with every line of text drawn on top of every other, and there
/// is nothing in any log to explain it. Pumping both panels in debug is what
/// turns that class of mistake back into a test failure.
void main() {
  Widget host(Widget child, {Size size = const Size(940, 760)}) => MediaQuery(
        data: MediaQueryData(size: size),
        child: MaterialApp(
          theme: AppTheme.light(),
          home: Scaffold(body: child),
        ),
      );

  group('status panel', () {
    testWidgets('lays out and names what this computer feeds', (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(940, 760));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      await tester.pumpWidget(host(StatusPanel(
        state: _paired,
        busy: false,
        stale: false,
        onRefresh: () {},
        onRestart: () {},
        onChangePort: () {},
      )));

      expect(tester.takeException(), isNull);
      expect(find.text('Connected'), findsOneWidget);
      expect(find.text('Answering'), findsOneWidget);
      expect(find.text('D.D Enterprises'), findsOneWidget);
      expect(find.text('Ramesh Gupta'), findsOneWidget);
    });

    testWidgets('says "not synced yet" rather than showing a zero',
        (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(940, 760));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      await tester.pumpWidget(host(StatusPanel(
        state: _paired,
        busy: false,
        stale: false,
        onRefresh: () {},
        onRestart: () {},
        onChangePort: () {},
      )));

      expect(find.text('not synced yet'), findsOneWidget);
      expect(find.textContaining('never'), findsNothing);
    });

    testWidgets('reports TallyPrime as unchecked before the first probe',
        (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(940, 760));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      await tester.pumpWidget(host(StatusPanel(
        state: _unprobed,
        busy: false,
        stale: false,
        onRefresh: () {},
        onRestart: () {},
        onChangePort: () {},
      )));

      // Never "Not answering" on a machine nothing has asked yet -- that sends
      // somebody to restart software that is running perfectly.
      expect(find.text('Checking'), findsOneWidget);
      expect(find.text('Not answering'), findsNothing);
    });

    testWidgets('offers no way to unlink, unpair or remove anything',
        (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(940, 760));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      await tester.pumpWidget(host(StatusPanel(
        state: _paired,
        busy: false,
        stale: false,
        onRefresh: () {},
        onRestart: () {},
        onChangePort: () {},
      )));

      // Those decisions belong to whoever holds the account on their phone, not
      // to whoever is standing at the till.
      for (final String forbidden in <String>[
        'Unlink',
        'Unpair',
        'Disconnect',
        'Remove',
        'Stop',
      ]) {
        expect(find.textContaining(forbidden), findsNothing, reason: forbidden);
      }
    });
  });

  group('pairing panel', () {
    testWidgets('lays out with a code on screen', (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(940, 760));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      await tester.pumpWidget(host(PairingPanel(
        state: _waiting,
        rows: _grid,
        busy: false,
        onNewCode: () {},
      )));

      expect(tester.takeException(), isNull);
      expect(find.textContaining('Point the camera'), findsOneWidget);
      expect(find.text('Show a new code'), findsOneWidget);
    });

    testWidgets('lays out before the grid has arrived', (WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(940, 760));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      // The first poll carries the payload; the grid is a second request, so
      // there is always a frame with one and not the other.
      await tester.pumpWidget(host(PairingPanel(
        state: _waiting,
        rows: const <String>[],
        busy: false,
        onNewCode: () {},
      )));

      expect(tester.takeException(), isNull);
    });
  });

  group('state contract', () {
    test('survives a connector that sent nothing it recognises', () {
      final ConnectorState state = ConnectorState.fromJson(<String, Object?>{});

      expect(state.paired, isFalse);
      expect(state.isPairing, isFalse);
      // Null, not false: an older connector that never sent the field must not
      // make this window report TallyPrime as down.
      expect(state.tally.online, isNull);
      expect(state.account.companies, isEmpty);
    });

    test('reads the shape the connector actually sends', () {
      final ConnectorState state = ConnectorState.fromJson(<String, Object?>{
        'version': '0.2.7',
        'uptime_seconds': 90,
        'paired': true,
        'connector_id': 'cn_1',
        'connector_name': 'SHOP-PC',
        'pairing': <String, Object?>{
          'waiting': false,
          'payload': '',
          'seconds_left': 0,
          'detail': '',
        },
        'backend': <String, Object?>{
          'url': 'https://api.example.in',
          'connected': true,
          'detail': '',
          'session_id': 's1',
          'connected_seconds': 60,
        },
        'tally': <String, Object?>{
          'host': '127.0.0.1',
          'port': 9000,
          'online': true,
          'companies_open': <Object?>['D.D Enterprises'],
        },
        'account': <String, Object?>{
          'organisation': 'D.D Enterprises',
          'status': 'active',
          'companies': <Object?>[
            <String, Object?>{
              'id': 'c1',
              'name': 'D.D Enterprises',
              'tally_name': 'D.D Enterprises',
              'is_active': true,
              'last_synced_at': null,
            },
          ],
          'users': <Object?>[
            <String, Object?>{
              'name': 'Shubhanshu',
              'email': 's@example.in',
              'role': 'admin',
              'has_access': true,
            },
          ],
          'as_of': '2026-09-10T12:00:00+00:00',
        },
        'log_dir': r'C:\logs',
      });

      expect(state.connectorName, 'SHOP-PC');
      expect(state.backend.connectedFor, const Duration(seconds: 60));
      expect(state.tally.companiesOpen, <String>['D.D Enterprises']);
      expect(state.account.companies.single.lastSyncedAt, isNull);
      expect(state.account.users.single.hasAccess, isTrue);
      expect(state.account.asOf, isNotNull);
    });
  });
}

const PairingState _noCode = PairingState(
  waiting: false,
  payload: '',
  secondsLeft: 0,
  detail: '',
);

final ConnectorState _paired = ConnectorState(
  version: '0.2.7',
  uptime: const Duration(hours: 3),
  paired: true,
  connectorId: 'cn_7f3a91c2',
  connectorName: 'SHOP-COUNTER-PC',
  pairing: _noCode,
  backend: const BackendState(
    url: 'https://api.example.in',
    connected: true,
    detail: '',
    sessionId: 's_2b91',
    connectedFor: Duration(hours: 3),
  ),
  tally: const TallyState(
    host: '127.0.0.1',
    port: 9000,
    online: true,
    companiesOpen: <String>['D.D Enterprises'],
  ),
  account: AccountState(
    organisation: 'D.D Enterprises',
    status: 'active',
    companies: <FedCompany>[
      FedCompany(
        id: 'c1',
        name: 'D.D Enterprises',
        tallyName: 'D.D Enterprises',
        isActive: true,
        lastSyncedAt: DateTime.now().subtract(const Duration(minutes: 3)),
      ),
      const FedCompany(
        id: 'c2',
        name: 'Anand Electricals',
        tallyName: '',
        isActive: false,
        lastSyncedAt: null,
      ),
    ],
    users: const <RosterUser>[
      RosterUser(
        name: 'Shubhanshu Pandey',
        email: 's@example.in',
        role: 'admin',
        hasAccess: true,
      ),
      RosterUser(
        name: 'Ramesh Gupta',
        email: 'r@example.in',
        role: 'staff',
        hasAccess: false,
      ),
    ],
    asOf: DateTime.now(),
  ),
  logDir: r'C:\Users\shop\AppData\Local\TallyFlow Connector\logs',
);

const ConnectorState _unprobed = ConnectorState(
  version: '0.2.7',
  uptime: Duration.zero,
  paired: true,
  connectorId: 'cn_1',
  connectorName: 'SHOP-PC',
  pairing: _noCode,
  backend: BackendState(
    url: 'https://api.example.in',
    connected: false,
    detail: 'Connecting',
    sessionId: '',
    connectedFor: null,
  ),
  tally: TallyState(
    host: '127.0.0.1',
    port: 9000,
    online: null,
    companiesOpen: <String>[],
  ),
  account: AccountState(
    organisation: '',
    status: '',
    companies: <FedCompany>[],
    users: <RosterUser>[],
    asOf: null,
  ),
  logDir: '',
);

const ConnectorState _waiting = ConnectorState(
  version: '0.2.7',
  uptime: Duration(seconds: 20),
  paired: false,
  connectorId: '',
  connectorName: '',
  pairing: PairingState(
    waiting: true,
    payload: '{"v":1,"c":"K7QW2M9XB4TD6ZP1RY8CVN","h":"api.example.in"}',
    secondsLeft: 823,
    detail: 'Waiting for somebody on the account to scan this.',
  ),
  backend: BackendState(
    url: 'https://api.example.in',
    connected: false,
    detail: '',
    sessionId: '',
    connectedFor: null,
  ),
  tally: TallyState(
    host: '127.0.0.1',
    port: 9000,
    online: null,
    companiesOpen: <String>[],
  ),
  account: AccountState(
    organisation: '',
    status: '',
    companies: <FedCompany>[],
    users: <RosterUser>[],
    asOf: null,
  ),
  logDir: r'C:\logs',
);

/// A stand-in grid: a checkerboard the size of a real pairing code, quiet zone
/// included. The symbol itself is the connector's business -- what is being
/// tested here is that a grid of this shape lays out.
final List<String> _grid = List<String>.generate(
  45,
  (int y) => List<String>.generate(45, (int x) {
    final bool quiet = x < 4 || y < 4 || x >= 41 || y >= 41;
    return quiet || (x + y).isEven ? '0' : '1';
  }).join(),
);
