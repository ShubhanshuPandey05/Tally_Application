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

  /// Revokes credentials and drops the live socket immediately.
  Future<void> revoke(String connectorId) => _api.delete('/v1/connectors/$connectorId');
}
