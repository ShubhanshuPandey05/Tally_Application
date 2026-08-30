import '../../../core/model/freshness.dart';
import '../../../core/network/api_client.dart';
import '../domain/drilldown.dart';
import '../domain/reports.dart';

/// Reads for the report screens.
///
/// Every method returns a [Fresh] wrapper, so no screen can render figures
/// without also having the "as of" that belongs beside them.
class ReportsRepository {
  ReportsRepository(this._api);

  final ApiClient _api;

  Future<Fresh<DaybookReport>> daybook(
    String companyId, {
    required DateRange range,
    FetchMode mode = FetchMode.auto,
    String? kind,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/daybook',
      query: <String, Object?>{
        'from_date': range.fromWire,
        'to_date': range.toWire,
        'mode': mode.wire,
        'kind': kind,
      },
    );
    return Fresh<DaybookReport>(
      DaybookReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<OutstandingReport>> outstanding(
    String companyId, {
    OutstandingKind kind = OutstandingKind.receivable,
    FetchMode mode = FetchMode.auto,
    DateTime? asOf,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/outstanding',
      query: <String, Object?>{
        'kind': kind.wire,
        'mode': mode.wire,
        'as_of': asOf == null ? null : _isoDate(asOf),
      },
    );
    return Fresh<OutstandingReport>(
      OutstandingReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  /// [group] is left null so the backend picks the stock group for [kind]. A
  /// company that renamed its groups passes its own name; the response always
  /// echoes back which group was actually read.
  Future<Fresh<GroupOutstandingReport>> outstandingByGroup(
    String companyId, {
    OutstandingKind kind = OutstandingKind.receivable,
    String? group,
    FetchMode mode = FetchMode.auto,
    DateTime? asOf,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/outstanding/group',
      query: <String, Object?>{
        'kind': kind.wire,
        'group': group,
        'mode': mode.wire,
        'as_of': asOf == null ? null : _isoDate(asOf),
      },
    );
    return Fresh<GroupOutstandingReport>(
      GroupOutstandingReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<StockReport>> stock(
    String companyId, {
    FetchMode mode = FetchMode.auto,
    String? only,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/stock',
      query: <String, Object?>{'mode': mode.wire, 'only': only},
    );
    return Fresh<StockReport>(
      StockReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<LedgerReport>> ledgers(
    String companyId, {
    FetchMode mode = FetchMode.auto,
    String? group,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/ledgers',
      query: <String, Object?>{'mode': mode.wire, 'group': group},
    );
    return Fresh<LedgerReport>(
      LedgerReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<SlowMovingReport>> slowMoving(
    String companyId, {
    int days = 90,
    FetchMode mode = FetchMode.auto,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/insights/slow-moving',
      query: <String, Object?>{'days': days, 'mode': mode.wire},
    );
    return Fresh<SlowMovingReport>(
      SlowMovingReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  // -- drill-down ----------------------------------------------------------
  //
  // None of these take a [FetchMode]. They are reached by tapping a row, and a
  // customer opening ten vouchers in a row must not queue ten exports against
  // the PC that is also running their till -- so the backend serves them from
  // stored history and says so when it has none. Pull-to-refresh on the list
  // the user came from is the way to get newer data.

  Future<Fresh<VoucherDetail>> voucher(
    String companyId, {
    required String key,
    required DateTime on,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/voucher',
      query: <String, Object?>{'key': key, 'on': _isoDate(on)},
    );
    return Fresh<VoucherDetail>(
      VoucherDetail.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<LedgerStatement>> ledgerStatement(
    String companyId, {
    required String ledger,
    required DateRange range,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/ledger-statement',
      query: <String, Object?>{
        'ledger': ledger,
        'from_date': range.fromWire,
        'to_date': range.toWire,
      },
    );
    return Fresh<LedgerStatement>(
      LedgerStatement.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<RegisterReport>> register(
    String companyId, {
    required String kind,
    required DateRange range,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/register',
      query: <String, Object?>{
        'kind': kind,
        'from_date': range.fromWire,
        'to_date': range.toWire,
      },
    );
    return Fresh<RegisterReport>(
      RegisterReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }

  Future<Fresh<ItemMovementReport>> itemMovement(
    String companyId, {
    required String item,
    required DateRange range,
  }) async {
    final Map<String, Object?> body = await _api.getJson(
      '/v1/companies/$companyId/reports/stock/movement',
      query: <String, Object?>{
        'item': item,
        'from_date': range.fromWire,
        'to_date': range.toWire,
      },
    );
    return Fresh<ItemMovementReport>(
      ItemMovementReport.fromJson(body.envelopeData),
      body.envelopeFreshness,
    );
  }
}

String _isoDate(DateTime value) =>
    '${value.year.toString().padLeft(4, '0')}-'
    '${value.month.toString().padLeft(2, '0')}-'
    '${value.day.toString().padLeft(2, '0')}';
