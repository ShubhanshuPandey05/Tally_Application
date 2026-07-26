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
