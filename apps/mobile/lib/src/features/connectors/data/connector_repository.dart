import '../../../core/network/api_client.dart';
import '../domain/connector.dart';

class ConnectorRepository {
  ConnectorRepository(this._api);

  final ApiClient _api;

  Future<List<Connector>> list() async {
    final List<Map<String, Object?>> rows = await _api.getList('/v1/connectors');
    return rows.map(Connector.fromJson).toList(growable: false);
  }

  Future<Connector> detail(String connectorId) async =>
      Connector.fromJson(await _api.getJson('/v1/connectors/$connectorId'));

  Future<ConnectorPairing> create({String name = 'Tally PC'}) async {
    final Map<String, Object?> json = await _api.postJson(
      '/v1/connectors',
      body: <String, Object?>{'name': name},
    );
    return ConnectorPairing.fromJson(json);
  }

  /// Issues a fresh secret for a PC that is already registered.
  ///
  /// The recovery path for a lost secret. Deliberately not "add another PC":
  /// companies are unique per connector, so pairing a second one links the same
  /// books again and the shop sees the company twice, each half-synced.
  Future<ConnectorPairing> rePair(String connectorId) async {
    final Map<String, Object?> json =
        await _api.postJson('/v1/connectors/$connectorId/secret');
    return ConnectorPairing.fromJson(json);
  }

  /// Which machine is behind a scanned code, before anybody adopts it.
  ///
  /// Adopting a PC hands it access to a business's books, so it is a decision
  /// and not a side effect of pointing a camera at something. This is what the
  /// confirmation screen shows.
  Future<ClaimPreview> previewClaim(String code) async => ClaimPreview.fromJson(
        await _api.getJson('/v1/connectors/claims/${Uri.encodeComponent(code)}'),
      );

  /// Adopts a scanned PC as a new connector.
  ///
  /// No secret comes back, and that is the improvement: the credential goes
  /// straight to the machine that drew the code, over TLS, and is shown to
  /// nobody at all.
  Future<Connector> claimNew(String code, {required String name}) async =>
      Connector.fromJson(await _api.postJson(
        '/v1/connectors/claims',
        body: <String, Object?>{'code': code, 'name': name},
      ));

  /// Points an existing connector at a scanned PC, keeping its companies.
  ///
  /// The completion of "re-pair this computer". Deliberately not "add another
  /// PC": companies belong to one connector, so a second row for the same books
  /// forks the shop's data into two half-synced copies of every company.
  Future<Connector> claimExisting(String connectorId, String code) async =>
      Connector.fromJson(await _api.postJson(
        '/v1/connectors/$connectorId/claims',
        body: <String, Object?>{'code': code},
      ));

  /// Cuts a PC off now, and puts it back on its own pairing screen.
  ///
  /// Separate from [claimExisting] so that "stop trusting that machine"
  /// happens when the owner asks -- not once they have walked over to the PC
  /// and found the code. The connector's companies and their history stay.
  Future<void> repair(String connectorId) =>
      _api.post('/v1/connectors/$connectorId/repair');

  /// Revokes credentials and drops the live socket immediately.
  Future<void> revoke(String connectorId) => _api.delete('/v1/connectors/$connectorId');
}
