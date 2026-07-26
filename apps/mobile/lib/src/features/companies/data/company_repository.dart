import '../../../core/network/api_client.dart';
import '../domain/company.dart';

class CompanyRepository {
  CompanyRepository(this._api);

  final ApiClient _api;

  Future<List<Company>> list() async {
    final List<Map<String, Object?>> rows = await _api.getList('/v1/companies');
    return rows.map(Company.fromJson).toList(growable: false);
  }

  Future<Company> detail(String companyId) async =>
      Company.fromJson(await _api.getJson('/v1/companies/$companyId'));

  /// Lists what is *currently open* in TallyPrime on that PC.
  ///
  /// Only open companies are visible by design: whoever is sitting at the shop's
  /// computer decides what can be seen by choosing what to load, so linking an
  /// account can never silently expose a set of books nobody meant to share.
  Future<List<DiscoveredCompany>> discover(String connectorId) async {
    final List<Map<String, Object?>> rows =
        await _api.getList('/v1/connectors/$connectorId/discover');
    return rows.map(DiscoveredCompany.fromJson).toList(growable: false);
  }

  Future<Company> link({
    required String connectorId,
    required String tallyName,
    String? displayName,
  }) async {
    final Map<String, Object?> json = await _api.postJson(
      '/v1/connectors/$connectorId/companies',
      body: <String, Object?>{
        'tally_name': tallyName,
        'display_name': displayName,
      },
    );
    return Company.fromJson(json);
  }

  Future<void> unlink(String companyId) => _api.delete('/v1/companies/$companyId');
}
