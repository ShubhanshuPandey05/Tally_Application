import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/model/financial_year.dart';
import '../../../core/widgets/period_scope.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../dashboard/presentation/widgets/dashboard_sections.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import '../../../core/widgets/period_picker.dart';
import 'widgets/report_scaffold.dart';

/// How far back a day book request may reach. Mirrors the backend's own
/// `MAX_REPORT_DAYS` clamp so a custom range is narrowed on the phone, with
/// the user told why, instead of silently narrowed by the server.
const int _maxDaybookDays = 400;

/// Every voucher in a window, grouped by day.
///
/// The window is chosen from the shared period picker -- presets for the
/// common cases, a custom range for everything else, clamped to what the
/// backend will actually serve.
class DaybookScreen extends ConsumerStatefulWidget {
  const DaybookScreen({super.key});

  @override
  ConsumerState<DaybookScreen> createState() => _DaybookScreenState();
}

class _DaybookScreenState extends ConsumerState<DaybookScreen>
    with YearScopedPeriod {
  String? _kind;

  /// Today, in the year we are in. A closed year has no today, so it opens on
  /// the whole of it -- the day book of a year that ended is read from the top.
  @override
  PeriodSelection initialPeriod(FinancialYear year) => year.isCurrent
      ? PeriodSelection.today()
      : PeriodSelection(year.toDate, year.label);

  @override
  Widget build(BuildContext context) {
    watchFinancialYear();
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see the day book.',
        ),
      );
    }

    final DaybookArgs args = (
      companyId: companyId,
      range: range,
      kind: _kind,
    );
    final AsyncValue<Fresh<DaybookReport>> state = ref.watch(daybookProvider(args));

    return ReportScaffold<DaybookReport>(
      title: 'Day book',
      subtitle: periodLabel,
      state: state,
      onRefresh: () => refreshReport<DaybookReport>(
        ref,
        daybookProvider(args),
        (ReportsRepository repository) => repository.daybook(
          companyId,
          range: range,
          kind: _kind,
          mode: FetchMode.live,
        ),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(48),
        child: SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: PeriodField(label: periodLabel, onTap: () => pickPeriod(maxDays: _maxDaybookDays)),
              ),
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: FilterChip(
                  label: const Text('Sales only'),
                  selected: _kind == 'sales',
                  onSelected: (bool on) => setState(() => _kind = on ? 'sales' : null),
                ),
              ),
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: FilterChip(
                  label: const Text('Purchases only'),
                  selected: _kind == 'purchase',
                  onSelected: (bool on) =>
                      setState(() => _kind = on ? 'purchase' : null),
                ),
              ),
            ],
          ),
        ),
      ),
      emptyBuilder: (BuildContext context) => const EmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No vouchers',
        message: 'Nothing was recorded in TallyPrime for this period.',
      ),
      builder: (BuildContext context, DaybookReport report) {
        if (report.vouchers.isEmpty) return const <Widget>[];

        final Map<DateTime, List<TransactionLine>> byDay =
            <DateTime, List<TransactionLine>>{};
        for (final TransactionLine line in report.vouchers) {
          final DateTime day = DateTime(line.date.year, line.date.month, line.date.day);
          byDay.putIfAbsent(day, () => <TransactionLine>[]).add(line);
        }
        final List<DateTime> days = byDay.keys.toList()
          ..sort((DateTime a, DateTime b) => b.compareTo(a));

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            'Total value',
                            style: Theme.of(context)
                                .textTheme
                                .labelMedium
                                ?.copyWith(color: context.mutedColor),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            MoneyFormat.full(report.total),
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                        ],
                      ),
                    ),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: <Widget>[
                        Text(
                          '${report.voucherCount}',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        Text(
                          'vouchers',
                          style: Theme.of(context)
                              .textTheme
                              .bodySmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
          for (final DateTime day in days)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              child: SectionCard(
                title: _dayLabel(day),
                subtitle: '${byDay[day]!.length} vouchers',
                child: Column(
                  children: <Widget>[
                    for (final TransactionLine line in byDay[day]!)
                      TransactionTile(line: line),
                  ],
                ),
              ),
            ),
        ];
      },
    );
  }

  static String _dayLabel(DateTime day) {
    final DateTime now = DateTime.now();
    final DateTime today = DateTime(now.year, now.month, now.day);
    final int difference = today.difference(day).inDays;
    if (difference == 0) return 'Today';
    if (difference == 1) return 'Yesterday';
    return '${day.day}/${day.month}/${day.year}';
  }
}
