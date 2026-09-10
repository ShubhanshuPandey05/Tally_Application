/// The connector's state, as the window receives it.
///
/// A mirror of `UiState.as_json` in
/// `apps/connector/src/tally_connector/ui/state.py`, which is why both files
/// carry the same field names in the same order.
///
/// Every field here is read defensively. The window and the connector ship in
/// one installer, but a machine that failed halfway through an update runs one
/// old half against one new half -- and a status window that throws on a
/// missing key is a status window that cannot report the very failure it is
/// looking at.
library;

/// Reads a string, whatever the connector actually sent.
String _text(Object? value) => value is String ? value : '';

int _count(Object? value) => value is int ? value : 0;

bool _flag(Object? value) => value is bool && value;

/// Reads a tri-state flag: true, false, or "not looked yet".
bool? _tristate(Object? value) => value is bool ? value : null;

Map<String, Object?> _object(Object? value) =>
    value is Map<String, Object?> ? value : const <String, Object?>{};

List<Map<String, Object?>> _rows(Object? value) {
  if (value is! List<Object?>) {
    return const <Map<String, Object?>>[];
  }
  return value.whereType<Map<String, Object?>>().toList(growable: false);
}

List<String> _strings(Object? value) {
  if (value is! List<Object?>) {
    return const <String>[];
  }
  return value.whereType<String>().toList(growable: false);
}

DateTime? _moment(Object? value) =>
    value is String ? DateTime.tryParse(value)?.toLocal() : null;

/// Everything the window draws.
class ConnectorState {
  const ConnectorState({
    required this.version,
    required this.uptime,
    required this.paired,
    required this.connectorId,
    required this.connectorName,
    required this.pairing,
    required this.backend,
    required this.tally,
    required this.account,
    required this.logDir,
  });

  factory ConnectorState.fromJson(Map<String, Object?> json) => ConnectorState(
        version: _text(json['version']),
        uptime: Duration(seconds: _count(json['uptime_seconds'])),
        paired: _flag(json['paired']),
        connectorId: _text(json['connector_id']),
        connectorName: _text(json['connector_name']),
        pairing: PairingState.fromJson(_object(json['pairing'])),
        backend: BackendState.fromJson(_object(json['backend'])),
        tally: TallyState.fromJson(_object(json['tally'])),
        account: AccountState.fromJson(_object(json['account'])),
        logDir: _text(json['log_dir']),
      );

  final String version;
  final Duration uptime;
  final bool paired;
  final String connectorId;
  final String connectorName;
  final PairingState pairing;
  final BackendState backend;
  final TallyState tally;
  final AccountState account;
  final String logDir;

  /// What the window leads with: a code to scan, or this machine's status.
  ///
  /// Keyed off the pairing code rather than off [paired], because the two
  /// disagree for a few seconds in both directions -- while a scan is being
  /// collected, and after a re-pair revokes this machine -- and the code on
  /// screen is the one of the two somebody is acting on.
  bool get isPairing => pairing.waiting;
}

/// The code on screen, while there is one.
class PairingState {
  const PairingState({
    required this.waiting,
    required this.payload,
    required this.secondsLeft,
    required this.detail,
  });

  factory PairingState.fromJson(Map<String, Object?> json) => PairingState(
        waiting: _flag(json['waiting']),
        payload: _text(json['payload']),
        secondsLeft: _count(json['seconds_left']),
        detail: _text(json['detail']),
      );

  final bool waiting;

  /// The QR's contents. Compared between polls to decide whether the code
  /// already drawn is still the right one, so the window never redraws a symbol
  /// that has not changed.
  final String payload;

  /// Recomputed by the connector on every read, never counted down here. A
  /// window ticking its own copy would keep promising time on a code the
  /// backend had already expired.
  final int secondsLeft;

  final String detail;
}

/// Whether TallyFlow is answering.
class BackendState {
  const BackendState({
    required this.url,
    required this.connected,
    required this.detail,
    required this.sessionId,
    required this.connectedFor,
  });

  factory BackendState.fromJson(Map<String, Object?> json) => BackendState(
        url: _text(json['url']),
        connected: _flag(json['connected']),
        detail: _text(json['detail']),
        sessionId: _text(json['session_id']),
        connectedFor: json['connected_seconds'] is int
            ? Duration(seconds: json['connected_seconds']! as int)
            : null,
      );

  final String url;
  final bool connected;
  final String detail;
  final String sessionId;

  /// How long this socket has been up. Null when it is not.
  final Duration? connectedFor;
}

/// Whether TallyPrime is answering, and what it has open.
class TallyState {
  const TallyState({
    required this.host,
    required this.port,
    required this.online,
    required this.companiesOpen,
  });

  factory TallyState.fromJson(Map<String, Object?> json) => TallyState(
        host: _text(json['host']),
        port: _count(json['port']),
        online: _tristate(json['online']),
        companiesOpen: _strings(json['companies_open']),
      );

  final String host;
  final int port;

  /// Null before the first probe. Not false: "we have not looked yet" and
  /// "TallyPrime is not answering" are different things to tell somebody
  /// standing in front of the machine, and the second one sends them to restart
  /// software that is running perfectly.
  final bool? online;

  final List<String> companiesOpen;
}

/// What the account looks like from here: names, roles and sync times.
///
/// Never a figure. A shop PC that could be asked for a balance over its own
/// loopback socket would be a read path into the books outside every check the
/// backend makes.
class AccountState {
  const AccountState({
    required this.organisation,
    required this.status,
    required this.companies,
    required this.users,
    required this.asOf,
  });

  factory AccountState.fromJson(Map<String, Object?> json) => AccountState(
        organisation: _text(json['organisation']),
        status: _text(json['status']),
        companies: _rows(json['companies'])
            .map(FedCompany.fromJson)
            .toList(growable: false),
        users: _rows(json['users'])
            .map(RosterUser.fromJson)
            .toList(growable: false),
        asOf: _moment(json['as_of']),
      );

  final String organisation;
  final String status;
  final List<FedCompany> companies;
  final List<RosterUser> users;

  /// When the roster last arrived. Null until one has.
  final DateTime? asOf;

  bool get isEmpty => companies.isEmpty && users.isEmpty;
}

/// A company this computer feeds.
class FedCompany {
  const FedCompany({
    required this.id,
    required this.name,
    required this.tallyName,
    required this.isActive,
    required this.lastSyncedAt,
  });

  factory FedCompany.fromJson(Map<String, Object?> json) => FedCompany(
        id: _text(json['id']),
        name: _text(json['name']),
        tallyName: _text(json['tally_name']),
        isActive: _flag(json['is_active']),
        lastSyncedAt: _moment(json['last_synced_at']),
      );

  final String id;
  final String name;

  /// What the company is called inside TallyPrime, when that differs from what
  /// the account calls it. The two drift apart the moment somebody renames one,
  /// and the difference is what explains an empty dashboard.
  final String tallyName;

  final bool isActive;

  /// Null means never synced -- which is "not synced yet", never "synced never"
  /// and never a zero.
  final DateTime? lastSyncedAt;
}

/// Somebody in the business who can see what this computer feeds.
class RosterUser {
  const RosterUser({
    required this.name,
    required this.email,
    required this.role,
    required this.hasAccess,
  });

  factory RosterUser.fromJson(Map<String, Object?> json) => RosterUser(
        name: _text(json['name']),
        email: _text(json['email']),
        role: _text(json['role']),
        hasAccess: _flag(json['has_access']),
      );

  final String name;
  final String email;
  final String role;

  /// Whether this person can see this computer's companies. A colleague on the
  /// account who cannot is a normal, deliberate state, so it is shown rather
  /// than filtered out.
  final bool hasAccess;
}

/// What came back from one of the four buttons.
class ActionResult {
  const ActionResult({required this.ok, required this.message});

  factory ActionResult.fromJson(Map<String, Object?> json) => ActionResult(
        ok: _flag(json['ok']),
        message: _text(json['message']),
      );

  final bool ok;
  final String message;
}
