import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../dashboard/presentation/widgets/dashboard_sections.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/report_scaffold.dart';

/// Every voucher in a window, grouped by day.
///
/// Windows are preset rather than a free date picker: the backend clamps
/// requests to 400 days because a five-year day book is tens of megabytes of
/// XML that would block the shop's own Tally for minutes. Offering a range the
/// server will silently narrow would be worse than not offering it.
class DaybookScreen extends ConsumerStatefulWidget {
  const DaybookScreen({super.key});

  @override
  ConsumerState<DaybookScreen> createState() => _DaybookScreenState();
}

class _DaybookScreenState extends ConsumerState<DaybookScreen> {
  _Window _window = _Window.today;
  String? _kind;

  @override
  Widget build(BuildContext context) {
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
      range: _window.range,
      kind: _kind,
    );
    final AsyncValue<Fresh<DaybookReport>> state = ref.watch(daybookProvider(args));

    return ReportScaffold<DaybookReport>(
      title: 'Day book',
      subtitle: _window.label,
      state: state,
      onRefresh: () => refreshReport<DaybookReport>(
        ref,
        daybookProvider(args),
        (ReportsRepository repository) => repository.daybook(
          companyId,
          range: _window.range,
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
              for (final _Window window in _Window.values)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(window.label),
                    selected: _window == window,
                    onSelected: (_) => setState(() => _window = window),
                  ),
                ),
              const SizedBox(width: 8),
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

enum _Window {
  today('Today'),
  week('Last 7 days'),
  month('This month'),
  quarter('Last 90 days');

  const _Window(this.label);

  final String label;

  DateRange get range => switch (this) {
        _Window.today => DateRange.today(),
        _Window.week => DateRange.lastDays(7),
        _Window.month => DateRange.thisMonth(),
        _Window.quarter => DateRange.lastDays(90),
      };
}
