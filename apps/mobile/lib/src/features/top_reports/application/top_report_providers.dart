import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/model/date_range.dart';
import '../../../core/model/freshness.dart';
import '../../../core/network/api_client.dart';
import '../../../core/network/device_first.dart';
import '../../../core/providers.dart';
import '../../reports/domain/reports.dart' show FetchMode;
import '../domain/top_report.dart';

/// Reads for the Top Prime Reports.
class TopReportsRepository {
  TopReportsRepository(this._api);

  final ApiClient _api;

  /// [limit] of 0 is every row. [sort] null is the report's own default.
  Future<Fresh<TopReport>> report(
    String companyId, {
    required String key,
    required DateRange range,
    String? sort,
    int limit = 10,
    FetchMode mode = FetchMode.auto,
    CachePolicy cache = CachePolicy.keep,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/top/${Uri.encodeComponent(key)}',
      query: <String, Object?>{
        'from_date': range.fromWire,
        'to_date': range.toWire,
        'sort': sort,
        'limit': limit,
        'mode': mode.wire,
      },
      cache: cache,
    );
    return Fresh<TopReport>(TopReport.fromJson(body.envelopeData), body.envelopeFreshness);
  }
}

final Provider<TopReportsRepository> topReportsRepositoryProvider =
    Provider<TopReportsRepository>(
  (Ref ref) => TopReportsRepository(ref.watch(apiClientProvider)),
);

typedef TopReportArgs = ({
  String companyId,
  String key,
  DateRange range,
  String? sort,
  int limit,
});

final AutoDisposeFutureProviderFamily<Fresh<TopReport>, TopReportArgs> topReportProvider =
    FutureProvider.autoDispose.family<Fresh<TopReport>, TopReportArgs>(
  (Ref ref, TopReportArgs args) => ref.watch(topReportsRepositoryProvider).report(
        args.companyId,
        key: args.key,
        range: args.range,
        sort: args.sort,
        limit: args.limit,
        cache: deviceFirst(ref),
      ),
);
