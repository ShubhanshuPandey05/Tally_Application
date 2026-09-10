import 'dart:convert';

/// The lifecycle of a paired PC, as the backend records it.
enum ConnectorStatus {
  pending,
  active,
  revoked;

  static ConnectorStatus parse(String? raw) => switch (raw) {
        'active' => ConnectorStatus.active,
        'revoked' => ConnectorStatus.revoked,
        _ => ConnectorStatus.pending,
      };
}

/// A TallyFlow Connector installed on a shop's Windows PC.
class Connector {
  const Connector({
    required this.id,
    required this.name,
    required this.status,
    required this.online,
    required this.tallyOnline,
    required this.companyCount,
    this.lastSeenAt,
    this.hostname,
    this.os,
    this.version,
    this.companiesOpen = const <String>[],
  });

  final String id;
  final String name;
  final ConnectorStatus status;

  /// The connector process is holding a socket to us.
  final bool online;

  /// The connector is up *and* TallyPrime answered it. Two different states
  /// with two different fixes -- "your PC is off" versus "Tally is closed" --
  /// and telling a shopkeeper the wrong one wastes their afternoon.
  final bool tallyOnline;

  final int companyCount;
  final DateTime? lastSeenAt;
  final String? hostname;
  final String? os;
  final String? version;
  final List<String> companiesOpen;

  bool get isWaitingToPair => status == ConnectorStatus.pending && !online;

  ConnectorHealth get health {
    if (status == ConnectorStatus.revoked) return ConnectorHealth.revoked;
    if (!online) return ConnectorHealth.offline;
    if (!tallyOnline) return ConnectorHealth.tallyClosed;
    return ConnectorHealth.healthy;
  }

  factory Connector.fromJson(Map<String, Object?> json) => Connector(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? 'Tally PC',
        status: ConnectorStatus.parse(json['status'] as String?),
        online: json['online'] as bool? ?? false,
        tallyOnline: json['tally_online'] as bool? ?? false,
        companyCount: (json['company_count'] as num?)?.toInt() ?? 0,
        lastSeenAt: DateTime.tryParse(json['last_seen_at'] as String? ?? '')?.toLocal(),
        hostname: json['hostname'] as String?,
        os: json['os'] as String?,
        version: json['connector_version'] as String?,
        companiesOpen: (json['companies_open'] as List<Object?>? ?? const <Object?>[])
            .whereType<String>()
            .toList(growable: false),
      );
}

enum ConnectorHealth {
  healthy,
  tallyClosed,
  offline,
  revoked;

  String get label => switch (this) {
        ConnectorHealth.healthy => 'Connected',
        ConnectorHealth.tallyClosed => 'TallyPrime closed',
        ConnectorHealth.offline => 'PC offline',
        ConnectorHealth.revoked => 'Revoked',
      };

  String get advice => switch (this) {
        ConnectorHealth.healthy => 'Reading live data from TallyPrime.',
        ConnectorHealth.tallyClosed =>
          'The PC is online but TallyPrime is not responding. Open TallyPrime '
              'and load the company.',
        ConnectorHealth.offline =>
          'We cannot reach this PC. Check that it is switched on and connected '
              'to the internet.',
        ConnectorHealth.revoked =>
          'This connector was removed. Pair the PC again to restore access.',
      };
}

/// A pairing code read off a Tally PC's own screen.
///
/// The connector draws this as a QR; the camera hands back the string inside
/// it. Parsing is deliberately strict and deliberately quiet: a phone camera
/// sees every barcode put in front of it, including the ones on the packets
/// behind the monitor, so anything that is not one of ours has to be *ignored*
/// rather than reported as an error the user has to dismiss.
class PairingCode {
  const PairingCode({required this.code, required this.host});

  /// Identifies the claim. Not a credential on its own -- the connector keeps a
  /// second string, never shown, that is what actually collects the secret.
  final String code;

  /// The TallyFlow server that PC is pointed at. Carried so the app can say
  /// "that computer is set up against a different server" instead of failing
  /// with a code that looks perfectly valid.
  final String host;

  /// The one payload shape this app understands, or null for anything else.
  static PairingCode? tryParse(String raw) {
    final Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      return null;
    }
    if (decoded is! Map<String, Object?>) return null;
    // Version-checked rather than duck-typed. A future connector may show a
    // code this build cannot complete, and saying so beats sending a payload
    // the server will reject for reasons nobody at the shop can act on.
    if (decoded['v'] != 1) return null;
    final Object? code = decoded['c'];
    if (code is! String || code.isEmpty) return null;
    return PairingCode(code: code, host: decoded['h'] as String? ?? '');
  }
}

/// The machine behind a scanned code, shown before anyone adopts it.
class ClaimPreview {
  const ClaimPreview({
    required this.hostname,
    required this.os,
    required this.connectorVersion,
  });

  final String hostname;
  final String os;
  final String connectorVersion;

  String get label => hostname.isEmpty ? 'That computer' : hostname;

  factory ClaimPreview.fromJson(Map<String, Object?> json) => ClaimPreview(
        hostname: json['hostname'] as String? ?? '',
        os: json['os'] as String? ?? '',
        connectorVersion: json['connector_version'] as String? ?? '',
      );
}

/// Returned exactly once, when a connector is created.
class ConnectorPairing {
  const ConnectorPairing({
    required this.connectorId,
    required this.secret,
    required this.name,
    required this.pairingCode,
  });

  final String connectorId;

  /// Shown once and never retrievable. The backend stores it encrypted because
  /// it must verify an HMAC made with it, but it will not hand it back.
  final String secret;

  final String name;
  final String pairingCode;

  factory ConnectorPairing.fromJson(Map<String, Object?> json) => ConnectorPairing(
        connectorId: json['connector_id'] as String? ?? '',
        secret: json['secret'] as String? ?? '',
        name: json['name'] as String? ?? '',
        pairingCode: json['pairing_code'] as String? ?? '',
      );
}
