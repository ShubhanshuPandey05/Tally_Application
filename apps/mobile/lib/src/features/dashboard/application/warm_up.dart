import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/model/financial_year.dart';
import '../../../core/network/api_client.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/providers.dart';
import '../../companies/application/financial_year_providers.dart';
import '../../reports/application/report_providers.dart';
import '../../reports/data/reports_repository.dart';
import '../../reports/domain/reports.dart';
import 'dashboard_providers.dart';

/// Puts the reports somebody is most likely to open next onto the phone.
///
/// Runs once per company per launch, after the dashboard has loaded. Every
/// report here is then answered from the phone the first time it is tapped --
/// and still answered with no signal later in the day.
///
/// Three rules keep it from costing anybody anything:
///
/// * **Snapshots only.** Every read is `mode=cached`, so the server answers
///   from what it already holds and never queues an export on the shop's PC.
///   Warming ten reports must not become ten exports against the till.
/// * **One at a time**, in the order people open them, so the first report
///   anybody taps is the first one ready.
/// * **Stops at the first sign it is pointless** -- no signal, no session, a
///   lapsed subscription, a build the server will not serve.
///
/// The arguments must be exactly each screen's defaults: the phone's copy is
/// keyed by the query, so a warmed report with one parameter different is a
/// copy no screen will ever ask for.
final FutureProviderFamily<void, String> warmUpProvider =
    FutureProvider.family<void, String>((Ref ref, String companyId) async {
  final ReportsRepository reports = ref.watch(reportsRepositoryProvider);
  final FinancialYear year = ref.watch(activeFinancialYearProvider);

  bool current = true;
  ref.onDispose(() => current = false);

  // The dashboard is the screen being looked at; it goes first. Its failure is
  // not this provider's business -- the dashboard shows it.
  try {
    await ref.read(dashboardProvider(companyId).future);
  } catch (_) {
    return;
  }

  const FetchMode cached = FetchMode.cached;
  const CachePolicy keep = CachePolicy.keep;
  final DateRange daybook = year.isCurrent ? DateRange.today() : year.toDate;

  final List<Future<Object?> Function()> reads = <Future<Object?> Function()>[
    () => reports.outstanding(companyId, kind: OutstandingKind.receivable, mode: cached, cache: keep),
    () => reports.outstanding(companyId, kind: OutstandingKind.payable, mode: cached, cache: keep),
    () => reports.stock(companyId, mode: cached, cache: keep),
    () => reports.daybook(companyId, range: daybook, mode: cached, cache: keep),
    () => reports.ledgers(companyId, mode: cached, cache: keep),
    () => reports.register(companyId, kind: 'sales', range: year.toDate, cache: keep),
    () => reports.register(companyId, kind: 'purchase', range: year.toDate, cache: keep),
    () => reports.outstandingByGroup(
          companyId,
          kind: OutstandingKind.receivable,
          mode: cached,
          cache: keep,
        ),
    () => reports.outstandingByGroup(
          companyId,
          kind: OutstandingKind.payable,
          mode: cached,
          cache: keep,
        ),
    () => reports.slowMoving(companyId, mode: cached, cache: keep),
  ];

  for (final Future<Object?> Function() read in reads) {
    if (!current || !ref.read(reachabilityProvider).online) return;
    try {
      await read();
    } on ApiException catch (error) {
      if (error.isAuthFailure ||
          error.isSubscriptionInactive ||
          error.isUpdateRequired ||
          error.isUnreachable) {
        return;
      }
      // Anything else -- no snapshot of this report yet, most often -- costs
      // this one report its warm copy and nothing more.
    }
  }
});
