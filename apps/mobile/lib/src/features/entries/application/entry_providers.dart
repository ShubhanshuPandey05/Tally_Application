import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../data/entries_repository.dart';
import '../domain/entry_draft.dart';

final Provider<EntriesRepository> entriesRepositoryProvider =
    Provider<EntriesRepository>(
  (Ref ref) => EntriesRepository(ref.watch(apiClientProvider)),
);

/// Entries waiting for this company's PC.
///
/// Auto-disposed and re-read on demand rather than polled: the queue changes
/// when somebody makes an entry or a PC comes back, and a timer on a phone
/// would spend a shop's data to tell it nothing most of the time.
final AutoDisposeFutureProviderFamily<List<PendingEntry>, String>
    pendingEntriesProvider =
    FutureProvider.autoDispose.family<List<PendingEntry>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).pending(companyId),
);

/// Whether the entry form may offer "regular" as well as "optional".
final AutoDisposeFutureProviderFamily<bool, String> canPostRegularProvider =
    FutureProvider.autoDispose.family<bool, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).canPostRegular(companyId),
);

/// Party and item names for the entry form's pickers.
///
/// Fetched once per company and held for the life of the screen rather than
/// re-queried on every keystroke: the whole list is a few kilobytes of names,
/// and filtering it on the device is instant where a request per character
/// would be a request per character on a shop's broadband.
final AutoDisposeFutureProviderFamily<List<String>, String> partyNamesProvider =
    FutureProvider.autoDispose.family<List<String>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).parties(companyId),
);

final AutoDisposeFutureProviderFamily<List<ItemOption>, String>
    itemOptionsProvider =
    FutureProvider.autoDispose.family<List<ItemOption>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).items(companyId),
);

final AutoDisposeFutureProviderFamily<List<String>, String>
    taxLedgerNamesProvider =
    FutureProvider.autoDispose.family<List<String>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).taxes(companyId),
);

final AutoDisposeFutureProviderFamily<List<String>, String>
    salesLedgerNamesProvider =
    FutureProvider.autoDispose.family<List<String>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).salesAccounts(companyId),
);

final AutoDisposeFutureProviderFamily<List<String>, String>
    purchaseLedgerNamesProvider =
    FutureProvider.autoDispose.family<List<String>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).purchaseAccounts(companyId),
);

final AutoDisposeFutureProviderFamily<List<String>, String>
    cashBankNamesProvider =
    FutureProvider.autoDispose.family<List<String>, String>(
  (Ref ref, String companyId) =>
      ref.watch(entriesRepositoryProvider).accounts(companyId),
);
