import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../../reports/domain/reports.dart';
import '../data/dashboard_repository.dart';
import '../domain/dashboard.dart';

final Provider<DashboardRepository> dashboardRepositoryProvider =
    Provider<DashboardRepository>(
  (Ref ref) => DashboardRepository(ref.watch(apiClientProvider)),
);

/// The home screen's data, one provider per company.
///
/// Not auto-disposed: switching companies and coming back is a normal gesture
/// for an accountant with four sets of books, and re-fetching each time would
/// make the app feel slower than it is for no benefit.
final AsyncNotifierProviderFamily<DashboardController, Dashboard, String>
    dashboardProvider =
    AsyncNotifierProvider.family<DashboardController, Dashboard, String>(
  DashboardController.new,
);

class DashboardController extends FamilyAsyncNotifier<Dashboard, String> {
  // `arg` is meaningless at the call site; the family key is a company id and
  // reads better named as one.
  @override
  // ignore: avoid_renaming_method_parameters
  Future<Dashboard> build(String companyId) =>
      ref.watch(dashboardRepositoryProvider).load(companyId);

  /// Pull-to-refresh: ask for live figures, but keep the old ones on screen
  /// while waiting.
  ///
  /// Blanking the dashboard to a spinner would be a downgrade -- the previous
  /// numbers were real, they are merely a few minutes old, and the backend
  /// throttles forced refreshes anyway so this may legitimately return the same
  /// snapshot.
  Future<void> refresh() async {
    state = const AsyncValue<Dashboard>.loading().copyWithPrevious(state);
    state = await AsyncValue.guard(
      () => ref.read(dashboardRepositoryProvider).load(arg, mode: FetchMode.live),
    );
  }
}
