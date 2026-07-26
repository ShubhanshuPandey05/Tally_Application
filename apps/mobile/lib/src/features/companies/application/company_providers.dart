import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../../core/providers.dart';
import '../data/company_repository.dart';
import '../domain/company.dart';

final Provider<CompanyRepository> companyRepositoryProvider =
    Provider<CompanyRepository>(
  (Ref ref) => CompanyRepository(ref.watch(apiClientProvider)),
);

final FutureProvider<List<Company>> companiesProvider =
    FutureProvider<List<Company>>(
  (Ref ref) => ref.watch(companyRepositoryProvider).list(),
);

/// Which company's books are on screen.
///
/// Persisted, because an accountant with four companies should not have to
/// re-pick on every launch -- and it is the single piece of state that changes
/// the meaning of every figure in the app.
final NotifierProvider<ActiveCompanyController, String?> activeCompanyIdProvider =
    NotifierProvider<ActiveCompanyController, String?>(ActiveCompanyController.new);

class ActiveCompanyController extends Notifier<String?> {
  static const String _key = 'tallyflow.active_company';

  @override
  String? build() => ref.watch(sharedPreferencesProvider).getString(_key);

  Future<void> select(String companyId) async {
    if (state == companyId) return;
    state = companyId;
    await ref.read(sharedPreferencesProvider).setString(_key, companyId);
  }

  Future<void> clear() async {
    state = null;
    await ref.read(sharedPreferencesProvider).remove(_key);
  }
}

/// The active company, resolved against the list actually available.
///
/// Falls back to the first company rather than showing an empty screen: a
/// stored id can outlive the company it points at (unlinked on another device,
/// access revoked), and that must not strand the user on a blank dashboard.
final Provider<AsyncValue<Company?>> activeCompanyProvider =
    Provider<AsyncValue<Company?>>((Ref ref) {
  final AsyncValue<List<Company>> companies = ref.watch(companiesProvider);
  final String? selected = ref.watch(activeCompanyIdProvider);

  return companies.whenData((List<Company> list) {
    if (list.isEmpty) return null;
    for (final Company company in list) {
      if (company.id == selected) return company;
    }
    return list.first;
  });
});

/// Convenience for screens that cannot render without a company.
final Provider<String?> activeCompanyIdResolvedProvider = Provider<String?>(
  (Ref ref) => ref.watch(activeCompanyProvider).valueOrNull?.id,
);

/// Wires the preferences instance created in `main()` into the provider tree.
Override sharedPreferencesOverride(SharedPreferences preferences) =>
    sharedPreferencesProvider.overrideWithValue(preferences);
