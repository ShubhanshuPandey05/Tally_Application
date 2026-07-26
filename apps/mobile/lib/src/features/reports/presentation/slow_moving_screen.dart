import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/report_scaffold.dart';

/// "Which products aren't selling?"
///
/// Capital sitting on a shelf. The window is adjustable and stated on screen,
/// because "slow moving" means nothing without it -- ninety days of no sales is
/// alarming for milk and unremarkable for a display cabinet.
class SlowMovingScreen extends ConsumerStatefulWidget {
  const SlowMovingScreen({super.key});

  @override
  ConsumerState<SlowMovingScreen> createState() => _SlowMovingScreenState();
}

class _SlowMovingScreenState extends ConsumerState<SlowMovingScreen> {
  int _days = 90;

  @override
  Widget build(BuildContext context) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see this.',
        ),
      );
    }

    final SlowMovingArgs args = (companyId: companyId, days: _days);
    final AsyncValue<Fresh<SlowMovingReport>> state = ref.watch(slowMovingProvider(args));

    return ReportScaffold<SlowMovingReport>(
      title: 'Slow movers',
      subtitle: 'No sales in $_days days',
      state: state,
      onRefresh: () => refreshReport<SlowMovingReport>(
        ref,
        slowMovingProvider(args),
        (ReportsRepository repository) =>
            repository.slowMoving(companyId, days: _days, mode: FetchMode.live),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(48),
        child: SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: <Widget>[
              for (final int days in <int>[30, 60, 90, 180])
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text('$days days'),
                    selected: _days == days,
                    onSelected: (_) => setState(() => _days = days),
                  ),
                ),
            ],
          ),
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.check_circle_outline,
        title: 'Everything is moving',
        message: 'Every item you hold has sold at least once in the last $_days days.',
      ),
      builder: (BuildContext context, SlowMovingReport report) {
        if (report.items.isEmpty) return const <Widget>[];
        final ThemeData theme = Theme.of(context);
        final Money tied = report.items.fold(
          Money.zero,
          (Money sum, StockLine item) => sum + item.value,
        );

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      'Capital sitting still',
                      style: theme.textTheme.labelMedium
                          ?.copyWith(color: context.mutedColor),
                    ),
                    const SizedBox(height: 4),
                    Text(MoneyFormat.full(tied), style: theme.textTheme.headlineSmall),
                    const SizedBox(height: 6),
                    Text(
                      '${report.items.length} items held in stock with no sale in the '
                      'last ${report.windowDays} days.',
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: context.mutedColor),
                    ),
                  ],
                ),
              ),
            ),
          ),
          for (final StockLine item in report.items)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              item.name,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodyLarge,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              MoneyFormat.quantity(item.quantity, item.unit),
                              style: theme.textTheme.bodySmall
                                  ?.copyWith(color: context.mutedColor),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 12),
                      Text(
                        MoneyFormat.full(item.value),
                        style: theme.textTheme.bodyLarge?.merge(AppTheme.amount),
                      ),
                    ],
                  ),
                ),
              ),
            ),
        ];
      },
    );
  }
}
