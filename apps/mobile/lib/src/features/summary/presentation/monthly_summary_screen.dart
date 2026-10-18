import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../../../core/model/financial_year.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/freshness_banner.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/application/financial_year_providers.dart';
import '../../dashboard/presentation/widgets/company_switcher.dart';
import '../../reports/application/report_providers.dart';
import '../../reports/data/reports_repository.dart';
import '../../reports/domain/drilldown.dart';
import '../../reports/domain/reports.dart';

/// One kind of voucher the summary can be read for.
class _Kind {
  const _Kind(this.wire, this.label, {required this.hasRegister});

  final String wire;
  final String label;

  /// Sales and purchases open their register. Receipts and payments have none,
  /// so a month of them opens the day book narrowed to that kind.
  final bool hasRegister;
}

const List<_Kind> _kinds = <_Kind>[
  _Kind('sales', 'Sales', hasRegister: true),
  _Kind('purchase', 'Purchase', hasRegister: true),
  _Kind('receipt', 'Receipt', hasRegister: false),
  _Kind('payment', 'Payment', hasRegister: false),
];

const List<String> _monthNames = <String>[
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

/// The Monthly Voucher Summary: the financial year, month by month, for one
/// kind of voucher at a time.
///
/// Twelve tiles from April to March, because that is the year these books are
/// kept in. Each is a way in: tapping a month opens the report for that kind
/// on exactly that month, so the figure and the vouchers behind it are one tap
/// apart.
///
/// Reached from the Summary tab's first card, so it is left with the back
/// arrow rather than the profile button the tab's own screens carry.
class MonthlySummaryScreen extends ConsumerStatefulWidget {
  const MonthlySummaryScreen({super.key});

  @override
  ConsumerState<MonthlySummaryScreen> createState() => _MonthlySummaryScreenState();
}

class _MonthlySummaryScreenState extends ConsumerState<MonthlySummaryScreen> {
  _Kind _kind = _kinds.first;

  @override
  Widget build(BuildContext context) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    final FinancialYear year = ref.watch(activeFinancialYearProvider);

    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        // The same title as Home, so the year these twelve months belong to is
        // on screen and can be changed here.
        title: const CompanySwitcher(),
        bottom: companyId == null
            ? null
            : PreferredSize(
                preferredSize: const Size.fromHeight(46),
                child: SizedBox(
                  height: 46,
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.fromLTRB(16, 0, 8, 8),
                    children: <Widget>[
                      for (final _Kind kind in _kinds)
                        Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: ChoiceChip(
                            label: Text(kind.label),
                            selected: kind == _kind,
                            onSelected: (_) => setState(() => _kind = kind),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
      ),
      body: companyId == null
          ? const EmptyState(
              icon: Icons.folder_off_outlined,
              title: 'No company connected',
              message: 'Connect your Tally PC to see your monthly summary.',
            )
          : _Months(companyId: companyId, year: year, kind: _kind),
    );
  }
}

class _Months extends ConsumerWidget {
  const _Months({required this.companyId, required this.year, required this.kind});

  final String companyId;
  final FinancialYear year;
  final _Kind kind;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final RegisterArgs args =
        (companyId: companyId, kind: kind.wire, range: year.toDate);
    final AsyncValue<Fresh<RegisterReport>> state = ref.watch(registerProvider(args));

    // The same read the register makes for a whole year, so the two screens
    // share one answer and cannot disagree about a month.
    Future<void> refresh() => refreshReport<RegisterReport>(
          ref,
          registerProvider(args),
          (ReportsRepository repository) =>
              repository.register(companyId, kind: kind.wire, range: year.toDate),
        );

    return ContentPane(
      maxWidth: Breakpoints.dashboard,
      child: RefreshIndicator(
        onRefresh: refresh,
        child: state.when(
          skipLoadingOnRefresh: true,
          loading: () => const LoadingState(),
          error: (Object error, StackTrace stack) => ListView(
            children: <Widget>[
              SizedBox(height: MediaQuery.sizeOf(context).height * 0.18),
              ErrorState(error: error, onRetry: refresh),
            ],
          ),
          data: (Fresh<RegisterReport> result) => ListView(
            padding: const EdgeInsets.only(bottom: 24),
            children: <Widget>[
              FreshnessBanner(
                freshness: result.freshness,
                onRefresh: refresh,
                dense: true,
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                child: _MonthGrid(year: year, kind: kind, report: result.data),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MonthGrid extends StatelessWidget {
  const _MonthGrid({required this.year, required this.kind, required this.report});

  final FinancialYear year;
  final _Kind kind;
  final RegisterReport report;

  @override
  Widget build(BuildContext context) {
    final Map<String, MonthTotal> byMonth = <String, MonthTotal>{
      for (final MonthTotal month in report.months) month.month: month,
    };

    final List<Widget> tiles = <Widget>[];
    Money runningTotal = Money.zero;
    for (int i = 0; i < 12; i++) {
      // April is month 4 of the year the books start in; January to March
      // fall in the calendar year after it.
      final DateTime first = DateTime(year.startYear, 4 + i);
      final DateTime last = DateTime(first.year, first.month + 1, 0);
      final MonthTotal? month =
          byMonth['${first.year}-${first.month.toString().padLeft(2, '0')}'];
      if (month != null) runningTotal = runningTotal + month.total;

      final bool reached = !first.isAfter(year.lastDay);
      final PeriodSelection period = PeriodSelection(
        year.confine(DateRange(first, last)),
        '${_monthNames[first.month - 1]} ${first.year}',
      );

      // A month that has not started has nothing behind it to open.
      final VoidCallback? onTap = !reached
          ? null
          : () => context.push(
                kind.hasRegister
                    ? registerLink(kind.wire, period)
                    : daybookLink(kind.wire, period),
              );

      tiles.add(
        kind.hasRegister
            ? _RegisterMonthTile(
                name: period.label,
                month: month,
                runningTotal: runningTotal,
                onTap: onTap,
              )
            : _MonthTile(
                name: _monthNames[first.month - 1],
                month: month,
                runningTotal: runningTotal,
                onTap: onTap,
              ),
      );
    }

    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final bool wide = constraints.maxWidth >= 560;
        return GridView(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          // Explicit, or the grid adopts the phone's bottom inset as its own
          // padding -- see the dashboard's tile grid.
          padding: EdgeInsets.zero,
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            // A sale or a purchase carries nine figures a month, which is a
            // row to itself on a phone. A receipt carries two.
            crossAxisCount: kind.hasRegister ? (wide ? 2 : 1) : (wide ? 4 : 2),
            crossAxisSpacing: 9,
            mainAxisSpacing: 9,
            mainAxisExtent: kind.hasRegister ? 132 : 92,
          ),
          children: tiles,
        );
      },
    );
  }
}

/// A month of sales or purchases: the total, and what it is made of.
///
/// The figures Tally's own monthly register prints -- quantity, the goods
/// before tax, the three GST heads -- so an owner checking a month against the
/// PC finds the same numbers in the same order.
class _RegisterMonthTile extends StatelessWidget {
  const _RegisterMonthTile({
    required this.name,
    required this.month,
    required this.runningTotal,
    required this.onTap,
  });

  final String name;
  final MonthTotal? month;
  final Money runningTotal;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final MonthTotal? month = this.month;
    final TextStyle? muted =
        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor);

    // "--" wherever there is no figure. A tax head with nothing under it is
    // not ₹0 of tax to somebody reading for what they owe.
    String money(Money? value) =>
        value == null || value.isZero ? '--' : MoneyFormat.full(value);
    final double? quantity = month?.quantity;

    return Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      name,
                      style: theme.textTheme.labelLarge?.copyWith(
                        color: month == null ? context.mutedColor : null,
                      ),
                    ),
                  ),
                  Text(
                    month == null ? '--' : MoneyFormat.full(month.total),
                    style: theme.textTheme.titleMedium?.merge(AppTheme.amount).copyWith(
                          color: month == null ? context.mutedColor : null,
                        ),
                  ),
                ],
              ),
              const SizedBox(height: 7),
              Row(
                children: <Widget>[
                  _Figure(
                    label: 'Qty',
                    value: quantity == null ? '--' : MoneyFormat.quantity(quantity, null),
                  ),
                  _Figure(label: 'Item value', value: money(month?.itemValue), flex: 2),
                ],
              ),
              const SizedBox(height: 5),
              Row(
                children: <Widget>[
                  _Figure(label: 'IGST', value: money(month?.igst)),
                  _Figure(label: 'CGST', value: money(month?.cgst)),
                  _Figure(label: 'SGST', value: money(month?.sgst)),
                ],
              ),
              const Spacer(),
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      month == null
                          ? 'No entries'
                          : <String>[
                              '${month.voucherCount} '
                                  '${month.voucherCount == 1 ? 'entry' : 'entries'}',
                              if (month.cancelledCount > 0)
                                '${month.cancelledCount} cancelled',
                            ].join(' · '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: muted,
                    ),
                  ),
                  if (onTap != null && !runningTotal.isZero)
                    Text('${MoneyFormat.full(runningTotal)} so far', style: muted),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A small label over its figure, one of two or three across a tile.
class _Figure extends StatelessWidget {
  const _Figure({required this.label, required this.value, this.flex = 1});

  final String label;
  final String value;
  final int flex;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Expanded(
      flex: flex,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
          ),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(value, style: theme.textTheme.labelMedium?.merge(AppTheme.amount)),
          ),
        ],
      ),
    );
  }
}

class _MonthTile extends StatelessWidget {
  const _MonthTile({
    required this.name,
    required this.month,
    required this.runningTotal,
    required this.onTap,
  });

  final String name;

  /// Null when the month has no vouchers of this kind.
  final MonthTotal? month;

  /// The year's total up to and including this month.
  final Money runningTotal;

  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final MonthTotal? month = this.month;
    final TextStyle? muted =
        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor);

    return Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                name,
                style: theme.textTheme.labelMedium?.copyWith(color: context.mutedColor),
              ),
              const SizedBox(height: 3),
              FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.centerLeft,
                child: Text(
                  // A dash, never ₹0: nothing recorded is not a figure.
                  month == null ? '--' : MoneyFormat.full(month.total, decimals: false),
                  style: theme.textTheme.titleMedium?.merge(AppTheme.amount).copyWith(
                        color: month == null ? context.mutedColor : null,
                      ),
                ),
              ),
              const Spacer(),
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      month == null
                          ? 'No entries'
                          : '${month.voucherCount} '
                              '${month.voucherCount == 1 ? 'entry' : 'entries'}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: muted,
                    ),
                  ),
                  if (onTap != null && !runningTotal.isZero)
                    Text('${MoneyFormat.compact(runningTotal)} so far', style: muted),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
