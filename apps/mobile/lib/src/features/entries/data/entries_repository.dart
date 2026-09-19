import '../../../core/network/api_client.dart';
import '../domain/entry_draft.dart';

/// The one write in this app.
///
/// No caching, no retry and no optimistic update. A read that fails can be
/// served from a snapshot and tried again; a voucher that may or may not have
/// reached Tally must not be re-sent on the app's own initiative, and showing
/// an entry as saved before the server confirms it would be showing somebody a
/// receipt that does not exist.
class EntriesRepository {
  EntriesRepository(this._api);

  final ApiClient _api;

  Future<EntryResult> create(String companyId, EntryDraft draft) async {
    final Map<String, Object?> body = await _api.postJson(
      '/v1/companies/$companyId/vouchers',
      body: draft.toJson(),
    );
    return EntryResult.fromJson(body);
  }

  /// Entries that have not reached TallyPrime yet, newest first.
  ///
  /// Settled ones come back too. Somebody who watched an entry go into the
  /// queue needs to find out what became of it, and a list that quietly drops
  /// the failures is how a receipt goes missing.
  Future<List<PendingEntry>> pending(String companyId) async {
    final List<Map<String, Object?>> rows = await _api.getList(
      '/v1/companies/$companyId/vouchers/pending',
    );
    return <PendingEntry>[
      for (final Map<String, Object?> row in rows) PendingEntry.fromJson(row),
    ];
  }

  Future<void> cancelPending(String companyId, String pendingId) =>
      _api.delete('/v1/companies/$companyId/vouchers/pending/$pendingId');

  /// Customers and suppliers, for the party field.
  ///
  /// Names only -- no balances. A shop with two thousand ledgers should not
  /// send two thousand closing balances to fill a dropdown, and this is served
  /// from the snapshot the refresher already keeps, so typing costs the shop's
  /// TallyPrime nothing.
  Future<List<String>> parties(String companyId, {String? query}) =>
      _names('/v1/companies/$companyId/masters/parties', query);

  /// Stock items, for the line sheet, with the unit and rate a pick fills in.
  Future<List<ItemOption>> items(String companyId) async {
    final List<Map<String, Object?>> rows =
        await _api.getList('/v1/companies/$companyId/masters/items');
    return <ItemOption>[
      for (final Map<String, Object?> row in rows)
        if (row['name'] is String) ItemOption.fromJson(row),
    ];
  }

  /// Duty and tax ledgers -- CGST, SGST, IGST -- for a sale or an order.
  Future<List<String>> taxes(String companyId) =>
      _names('/v1/companies/$companyId/masters/taxes', null);

  /// Sales ledgers, for a sale's or sales order's goods.
  Future<List<String>> salesAccounts(String companyId) =>
      _names('/v1/companies/$companyId/masters/sales-accounts', null);

  /// Purchase ledgers, for a purchase order's goods.
  Future<List<String>> purchaseAccounts(String companyId) =>
      _names('/v1/companies/$companyId/masters/purchase-accounts', null);

  /// Cash and bank ledgers, for the other side of a receipt or payment.
  Future<List<String>> accounts(String companyId) =>
      _names('/v1/companies/$companyId/masters/accounts', null);

  Future<List<String>> _names(String path, String? query) async {
    final List<Map<String, Object?>> rows = await _api.getList(
      path,
      query: <String, Object?>{if (query != null && query.isNotEmpty) 'q': query},
    );
    return <String>[
      for (final Map<String, Object?> row in rows)
        if (row['name'] is String) row['name']! as String,
    ];
  }

  /// Add a customer or supplier to TallyPrime.
  ///
  /// Only ever called after somebody confirmed the name. Creating one on every
  /// unrecognised spelling is how a chart of accounts ends up holding "Ram
  /// Traders", "Ram traders" and "Ram Trader".
  Future<String?> createLedger(
    String companyId,
    String name, {
    String role = 'customer',
  }) async {
    final Map<String, Object?> body = await _api.postJson(
      '/v1/companies/$companyId/ledgers',
      body: <String, Object?>{'name': name, 'role': role},
    );
    return body['ok'] == true ? null : body['message'] as String? ?? 'Not created.';
  }

  Future<String?> createStockItem(String companyId, String name, {String? unit}) async {
    final Map<String, Object?> body = await _api.postJson(
      '/v1/companies/$companyId/stock-items',
      body: <String, Object?>{'name': name, if (unit != null) 'unit': unit},
    );
    return body['ok'] == true ? null : body['message'] as String? ?? 'Not created.';
  }
}
