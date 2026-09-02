import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/model/financial_year.dart';
import '../../../core/providers.dart';
import '../../../core/widgets/period_picker.dart';
import '../../companies/application/financial_year_providers.dart';
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
  PeriodSelection? build(String companyId) {
    // Watched, not read: picking a different financial year has to move the
    // window with it. A closed year has no "today" to fall back on, so it
    // opens on the whole year -- whereas the year we are in keeps the ordinary
    // today view, which is the request the background refresher warms.
    final FinancialYear year = ref.watch(activeFinancialYearProvider);
    return year.isCurrent ? null : PeriodSelection(year.toDate, year.label);
  }

  /// A single day that is today is stored as null rather than as a one-day
  /// period. It keeps the request identical to the one the background
  /// refresher warms, and makes "are we looking at something other than today?"
  /// a single null check everywhere downstream.
  void select(PeriodSelection? selection) {
    final FinancialYear year = ref.read(activeFinancialYearProvider);
    if (selection == null) {
      state = year.isCurrent ? null : PeriodSelection(year.toDate, year.label);
      return;
    }
    // Confined here as well as in the picker, because a preset computed a
    // moment ago can straddle 31 March by the time it is applied, and a
    // dashboard quietly reporting on two financial years at once is a wrong
    // figure rather than an untidy one.
    final DateRange range = year.confine(selection.range);
    state = year.isCurrent && range.isSingleDay && range.endsToday
        ? null
        : PeriodSelection(range, selection.label);
  }

  void backToToday() => select(null);
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
