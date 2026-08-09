import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/model/freshness.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';

final Provider<ReportsRepository> reportsRepositoryProvider =
    Provider<ReportsRepository>(
  (Ref ref) => ReportsRepository(ref.watch(apiClientProvider)),
);

/// Family arguments are records: structural equality comes for free, so two
/// screens asking for the same window share one request instead of racing.
typedef DaybookArgs = ({String companyId, DateRange range, String? kind});
typedef OutstandingArgs = ({String companyId, OutstandingKind kind});
typedef GroupOutstandingArgs = ({String companyId, OutstandingKind kind, String? group});
typedef StockArgs = ({String companyId, String? only});
typedef LedgerArgs = ({String companyId, String? group});
typedef SlowMovingArgs = ({String companyId, int days});

final AutoDisposeFutureProviderFamily<Fresh<DaybookReport>, DaybookArgs>
    daybookProvider =
    FutureProvider.autoDispose.family<Fresh<DaybookReport>, DaybookArgs>(
  (Ref ref, DaybookArgs args) => ref.watch(reportsRepositoryProvider).daybook(
        args.companyId,
        range: args.range,
        kind: args.kind,
      ),
);

final AutoDisposeFutureProviderFamily<Fresh<OutstandingReport>, OutstandingArgs>
    outstandingProvider =
    FutureProvider.autoDispose.family<Fresh<OutstandingReport>, OutstandingArgs>(
  (Ref ref, OutstandingArgs args) =>
      ref.watch(reportsRepositoryProvider).outstanding(args.companyId, kind: args.kind),
);

final AutoDisposeFutureProviderFamily<Fresh<GroupOutstandingReport>, GroupOutstandingArgs>
    groupOutstandingProvider = FutureProvider.autoDispose
        .family<Fresh<GroupOutstandingReport>, GroupOutstandingArgs>(
  (Ref ref, GroupOutstandingArgs args) =>
      ref.watch(reportsRepositoryProvider).outstandingByGroup(
            args.companyId,
            kind: args.kind,
            group: args.group,
          ),
);

final AutoDisposeFutureProviderFamily<Fresh<StockReport>, StockArgs> stockProvider =
    FutureProvider.autoDispose.family<Fresh<StockReport>, StockArgs>(
  (Ref ref, StockArgs args) =>
      ref.watch(reportsRepositoryProvider).stock(args.companyId, only: args.only),
);

final AutoDisposeFutureProviderFamily<Fresh<LedgerReport>, LedgerArgs> ledgersProvider =
    FutureProvider.autoDispose.family<Fresh<LedgerReport>, LedgerArgs>(
  (Ref ref, LedgerArgs args) =>
      ref.watch(reportsRepositoryProvider).ledgers(args.companyId, group: args.group),
);

final AutoDisposeFutureProviderFamily<Fresh<SlowMovingReport>, SlowMovingArgs>
    slowMovingProvider =
    FutureProvider.autoDispose.family<Fresh<SlowMovingReport>, SlowMovingArgs>(
  (Ref ref, SlowMovingArgs args) =>
      ref.watch(reportsRepositoryProvider).slowMoving(args.companyId, days: args.days),
);

/// Pull-to-refresh for a report: force one live read, then re-read the
/// provider so the screen picks up the snapshot that read just wrote.
///
/// A failed live read is swallowed on purpose. The user asked for newer
/// numbers; if the shop's PC is asleep they should still be left looking at the
/// older ones with a staleness banner, not at an error page where a working
/// screen used to be.
Future<void> refreshReport<T>(
  WidgetRef ref,
  AutoDisposeFutureProvider<Fresh<T>> provider,
  Future<void> Function(ReportsRepository repository) live,
) async {
  try {
    await live(ref.read(reportsRepositoryProvider));
  } on ApiException {
    // Deliberately ignored -- see above.
  }
  ref.invalidate(provider);
  await ref.read(provider.future);
}
