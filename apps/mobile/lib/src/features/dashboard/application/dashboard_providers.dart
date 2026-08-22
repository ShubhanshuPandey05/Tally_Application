import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../../../core/widgets/period_picker.dart';
import '../../reports/domain/reports.dart';
import '../data/dashboard_repository.dart';
import '../domain/dashboard.dart';

final Provider<DashboardRepository> dashboardRepositoryProvider =
    Provider<DashboardRepository>(
  (Ref ref) => DashboardRepository(ref.watch(apiClientProvider)),
);

/// Which period the dashboard is being read over, per company.
///
/// Null means the ordinary today view. Deliberately kept outside
/// [DashboardController] rather than as a field on it: the controller is
/// recreated whenever the provider is invalidated -- the sync does exactly that
/// when history lands -- and a period held on the instance would be silently
/// reset at that moment, moving every figure on screen without the user
/// touching anything.
final NotifierProviderFamily<DashboardPeriodController, PeriodSelection?, String>
    dashboardPeriodProvider =
    NotifierProvider.family<DashboardPeriodController, PeriodSelection?, String>(
  DashboardPeriodController.new,
);

class DashboardPeriodController extends FamilyNotifier<PeriodSelection?, String> {
  @override
  // ignore: avoid_renaming_method_parameters
  PeriodSelection? build(String companyId) => null;

  /// A single day that is today is stored as null rather than as a one-day
  /// period. It keeps the request identical to the one the background
  /// refresher warms, and makes "are we looking at something other than today?"
  /// a single null check everywhere downstream.
  void select(PeriodSelection? selection) {
    if (selection == null) {
      state = null;
      return;
    }
    final DateRange range = selection.range;
    state = range.isSingleDay && range.endsToday ? null : selection;
  }

  void backToToday() => state = null;
}

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
  Future<Dashboard> build(String companyId) {
    // Watched, not read: picking a period is what re-runs this, so the period
    // selector needs no knowledge of how the dashboard reloads itself.
    final PeriodSelection? period = ref.watch(dashboardPeriodProvider(companyId));
    return ref
        .watch(dashboardRepositoryProvider)
        .load(companyId, period: period?.range);
  }

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
      () => ref.read(dashboardRepositoryProvider).load(
            arg,
            mode: FetchMode.live,
            period: ref.read(dashboardPeriodProvider(arg))?.range,
          ),
    );
  }
}
