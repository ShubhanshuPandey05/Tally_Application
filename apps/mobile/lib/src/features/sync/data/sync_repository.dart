import '../../../core/network/api_client.dart';
import '../domain/sync_status.dart';

/// Starting, watching and stopping the history sync.
///
/// The odd one out among the repositories: it returns a bare [SyncStatus]
/// rather than a `Fresh<T>`. Freshness describes how old a *figure* is, and
/// none of this is a figure -- it is the progress of a job, which is only ever
/// current or wrong.
class SyncRepository {
  SyncRepository(this._api);

  final ApiClient _api;

  Future<SyncStatus> status(String companyId) async {
    final Map<String, Object?> body =
        await _api.getJson('/v1/companies/$companyId/sync');
    return SyncStatus.fromJson(body);
  }

  /// Start reading history, or resume a run that stopped part-way.
  ///
  /// [full] re-plans from scratch and is the answer to "the numbers look
  /// wrong". It is never the default: resuming costs the shop's Tally only the
  /// slices it has not already served.
  Future<SyncStatus> start(String companyId, {bool full = false}) async {
    final Map<String, Object?> body = await _api.postJson(
      '/v1/companies/$companyId/sync${full ? '?full=true' : ''}',
    );
    return SyncStatus.fromJson(body);
  }

  Future<SyncStatus> cancel(String companyId) async {
    final Map<String, Object?> body =
        await _api.deleteJson('/v1/companies/$companyId/sync');
    return SyncStatus.fromJson(body);
  }
}
