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
  ///
  /// [period] scopes the voucher-derived sections to a window. Null means the
  /// ordinary today view, and sends no date parameters at all rather than
  /// spelling today out -- the params are part of a snapshot's identity, so a
  /// spelled-out default would miss the row the background refresher warms on
  /// every single load.
  Future<Dashboard> load(
    String companyId, {
    FetchMode mode = FetchMode.auto,
    DateRange? period,
  }) async {
    final Map<String, Object?> json = await _api.getJson(
      '/v1/companies/$companyId/dashboard',
      query: <String, Object?>{
        'mode': mode.wire,
        'from_date': period?.fromWire,
        'to_date': period?.toWire,
      },
    );
    return Dashboard.fromJson(json);
  }
}
