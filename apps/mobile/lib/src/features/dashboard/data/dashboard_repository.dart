import '../../../core/network/api_client.dart';
import '../../reports/domain/reports.dart';
import '../domain/dashboard.dart';

class DashboardRepository {
  DashboardRepository(this._api);

  final ApiClient _api;

  /// One request for the whole home screen.
  ///
  /// Not one call per tile: a phone on mobile data pays far more for eight
  /// round trips than for one larger response, and eight calls would each race
  /// to refresh the same underlying datasets on the shop's PC.
  Future<Dashboard> load(
    String companyId, {
    FetchMode mode = FetchMode.auto,
  }) async {
    final Map<String, Object?> json = await _api.getJson(
      '/v1/companies/$companyId/dashboard',
      query: <String, Object?>{'mode': mode.wire},
    );
    return Dashboard.fromJson(json);
  }
}
