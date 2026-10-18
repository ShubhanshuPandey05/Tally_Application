import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/financial_year.dart';
import '../../../core/model/freshness.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/choice_sheet_button.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/period_scope.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../reports/domain/reports.dart' show FetchMode;
import '../../reports/presentation/widgets/report_scaffold.dart';
import '../application/top_report_providers.dart';
import '../domain/top_report.dart';

/// How many rows a report shows. 0 is every row.
const List<SheetChoice<int>> _limits = <SheetChoice<int>>[
  SheetChoice<int>(10, 'Top 10'),
  SheetChoice<int>(25, 'Top 25'),
  SheetChoice<int>(50, 'Top 50'),
  SheetChoice<int>(100, 'Top 100'),
  SheetChoice<int>(0, 'All'),
];

/// One Top Prime report, whichever it is.
///
/// Every report arrives as the same table -- columns, ranked rows, a TOTAL
/// row -- so this one screen renders all of them. Each row leads with the
/// figure it is ranked on and carries the rest beneath it: nine columns do not
/// fit across a phone, and a sideways-scrolling grid hides the name a figure
/// belongs to the moment it scrolls.
class TopReportScreen extends ConsumerStatefulWidget {
  const TopReportScreen({super.key, required this.reportKey});

  final String reportKey;

  @override
  ConsumerState<TopReportScreen> createState() => _TopReportScreenState();
}

class _TopReportScreenState extends ConsumerState<TopReportScreen>
    with YearScopedPeriod {
  String? _sort;
  int _limit = 10;

  TopReportEntry? get _entry {
    for (final TopReportGroup group in topReportCatalog) {
      for (final TopReportEntry entry in group.entries) {
        if (entry.key == widget.reportKey) return entry;
      }
    }
    return null;
  }

  /// The year so far: the specification's default period is the current one.
  @override
  PeriodSelection initialPeriod(FinancialYear year) =>
      PeriodSelection(year.toDate, year.label);

  @override
  Widget build(BuildContext context) {
    watchFinancialYear();
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    final TopReportEntry? entry = _entry;
    if (companyId == null || entry == null || entry.unavailable != null) {
      return Scaffold(
        appBar: AppBar(title: Text(entry?.title ?? 'Top Prime Reports')),
        body: EmptyState(
          icon: entry?.unavailable != null ? Icons.lock_clock_outlined : Icons.folder_off_outlined,
          title: entry == null
              ? 'Report not found'
              : entry.unavailable != null
                  ? 'Not available yet'
                  : 'No company connected',
          message: entry?.unavailable ?? 'Connect your Tally PC to see this report.',
        ),
      );
    }

    final TopReportArgs args = (
      companyId: companyId,
      key: widget.reportKey,
      range: range,
      sort: _sort,
      limit: _limit,
    );
    final AsyncValue<Fresh<TopReport>> state = ref.watch(topReportProvider(args));
    final TopReport? loaded = state.valueOrNull?.data;

    Future<void> refresh() async {
      // A deliberate live read, then the provider again so the screen picks
      // up what it wrote. A PC that is off leaves the older figures on screen
      // under their banner rather than an error where a report used to be.
      try {
        await ref.read(topReportsRepositoryProvider).report(
              companyId,
              key: widget.reportKey,
              range: range,
              sort: _sort,
              limit: _limit,
              mode: FetchMode.live,
            );
      } on ApiException {
        // Deliberately ignored -- see above.
      }
      ref.invalidate(topReportProvider(args));
      await ref.read(topReportProvider(args).future);
    }

    return ReportScaffold<TopReport>(
      title: entry.title,
      subtitle: periodLabel,
      state: state,
      onRefresh: refresh,
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(48),
        child: SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            children: <Widget>[
              PeriodField(label: periodLabel, onTap: pickPeriod),
              const SizedBox(width: 8),
              if (loaded != null && loaded.sorts.length > 1) ...<Widget>[
                ChoiceSheetButton<String>(
                  choices: <SheetChoice<String>>[
                    for (final TopSort option in loaded.sorts)
                      SheetChoice<String>(option.key, 'By ${option.label.toLowerCase()}'),
                  ],
                  selected: loaded.sort,
                  onSelected: (String value) => setState(() => _sort = value),
                ),
                const SizedBox(width: 8),
              ],
              ChoiceSheetButton<int>(
                choices: _limits,
                selected: _limit,
                onSelected: (int value) => setState(() => _limit = value),
              ),
            ],
          ),
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: entry.icon,
        title: 'Nothing to rank',
        message: 'Nothing in ${periodLabel.toLowerCase()} qualifies for this report.',
        action: FilledButton.tonalIcon(
          onPressed: pickPeriod,
          icon: const Icon(Icons.date_range),
          label: const Text('Change period'),
        ),
      ),
      builder: (BuildContext context, TopReport report) {
        final List<Widget> notes = <Widget>[
          if (report.notes.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              child: _Notes(notes: report.notes),
            ),
        ];
        if (report.rows.isEmpty) {
          // The notes still matter on an empty report: "history not synced
          // yet" is the reason the list is empty, and must be on screen.
          return notes.isEmpty
              ? const <Widget>[]
              : <Widget>[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 48, 16, 24),
                    child: EmptyState(
                      icon: entry.icon,
                      title: 'Nothing to rank',
                      message: 'Nothing in ${periodLabel.toLowerCase()} qualifies '
                          'for this report.',
                    ),
                  ),
                  ...notes,
                ];
        }

        final String lead = report.activeSort?.column ?? report.columns.last.key;
        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
            child: _TotalCard(report: report),
          ),
          for (final TopRow row in report.rows)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: _RowCard(report: report, row: row, lead: lead),
            ),
          const SizedBox(height: 6),
          ...notes,
        ];
      },
    );
  }
}

/// The TOTAL row: what the rows on screen add up to, and how many there were.
class _TotalCard extends StatelessWidget {
  const _TotalCard({required this.report});

  final TopReport report;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<TopColumn> totalled = <TopColumn>[
      for (final TopColumn column in report.columns)
        if (column.totalled || report.total[column.key] != null) column,
    ];
    final int shown = report.rows.length;
    final String count = shown == report.rowCount
        ? 'All $shown'
        : 'Top $shown of ${report.rowCount}';

    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Text('Total', style: theme.textTheme.titleSmall),
                const Spacer(),
                Text(
                  count,
                  style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                ),
              ],
            ),
            if (totalled.isNotEmpty) ...<Widget>[
              const SizedBox(height: 8),
              _Figures(
                columns: totalled,
                values: report.total,
                strong: true,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _RowCard extends StatelessWidget {
  const _RowCard({required this.report, required this.row, required this.lead});

  final TopReport report;
  final TopRow row;
  final String lead;

  String? get _link {
    final TopDrill? drill = row.drill;
    if (drill == null) return null;
    final String name = Uri.encodeQueryComponent(drill.name);
    switch (drill.type) {
      case 'ledger':
        return '${Routes.ledgerStatement}?ledger=$name';
      case 'item':
        return '${Routes.stockMovement}?item=$name';
      case 'outstanding':
        return '${Routes.partyOutstanding}?kind=${drill.kind ?? 'receivable'}&party=$name';
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    TopColumn? leadColumn;
    for (final TopColumn column in report.columns) {
      if (column.key == lead) leadColumn = column;
    }
    final List<TopColumn> rest = <TopColumn>[
      for (final TopColumn column in report.columns)
        if (column != leadColumn) column,
    ];
    final String? link = _link;

    return Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: link == null ? null : () => context.push(link),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Container(
                    width: 26,
                    height: 26,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: context.surfaceColor,
                      borderRadius: BorderRadius.circular(AppTheme.radiusTile),
                    ),
                    child: Text('${row.rank}', style: theme.textTheme.labelMedium),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      row.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
                    ),
                  ),
                  if (leadColumn != null) ...<Widget>[
                    const SizedBox(width: 8),
                    Text(
                      leadColumn.format(row.cells[leadColumn.key]),
                      style: theme.textTheme.titleSmall?.merge(AppTheme.amount),
                    ),
                  ],
                ],
              ),
              if (rest.isNotEmpty) ...<Widget>[
                const SizedBox(height: 8),
                Padding(
                  padding: const EdgeInsets.only(left: 36),
                  child: _Figures(columns: rest, values: row.cells),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Small labels over their figures, wrapped to as many lines as they need.
class _Figures extends StatelessWidget {
  const _Figures({required this.columns, required this.values, this.strong = false});

  final List<TopColumn> columns;
  final Map<String, Object?> values;
  final bool strong;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Wrap(
      spacing: 18,
      runSpacing: 6,
      children: <Widget>[
        for (final TopColumn column in columns)
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                column.label,
                style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
              ),
              Text(
                column.format(values[column.key]),
                style: (strong ? theme.textTheme.labelLarge : theme.textTheme.labelMedium)
                    ?.merge(column.type.isNumeric ? AppTheme.amount : null),
              ),
            ],
          ),
      ],
    );
  }
}

/// What the report cannot see, said plainly under it.
class _Notes extends StatelessWidget {
  const _Notes({required this.notes});

  final List<String> notes;

  @override
  Widget build(BuildContext context) {
    final TextStyle? style =
        Theme.of(context).textTheme.bodySmall?.copyWith(color: context.mutedColor);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        for (final String note in notes)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.only(top: 1, right: 6),
                  child: Icon(Icons.info_outline, size: 14, color: context.mutedColor),
                ),
                Expanded(child: Text(note, style: style)),
              ],
            ),
          ),
      ],
    );
  }
}
