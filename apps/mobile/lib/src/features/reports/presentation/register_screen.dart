import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/model/financial_year.dart';
import '../../../core/widgets/period_scope.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/charts.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../dashboard/presentation/widgets/dashboard_sections.dart';
import '../application/report_providers.dart';
import '../domain/drilldown.dart';
import 'widgets/report_scaffold.dart';

/// The sales and purchase registers.
///
/// One screen with a switch rather than two, because the two are the same
/// report read from opposite ends and an owner compares them in the same sitting.
///
/// Three cuts of one window, in the order the questions come: how much, month
/// by month; who; what. The vouchers themselves are last -- by the time
/// somebody wants the list they already know which month they are looking for.
class RegisterScreen extends ConsumerStatefulWidget {
  const RegisterScreen({super.key, required this.kind});

  /// `sales` or `purchase`.
  final String kind;

  @override
  ConsumerState<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends ConsumerState<RegisterScreen>
    with YearScopedPeriod {
  late String _kind = widget.kind;
  bool get _isSales => _kind == 'sales';

  /// The whole year. A register is the one report an owner reads a year of at
  /// a time -- it is where "how much did we sell this year" is answered.
  @override
  PeriodSelection initialPeriod(FinancialYear year) =>
      PeriodSelection(year.toDate, year.label);

  @override
  Widget build(BuildContext context) {
    watchFinancialYear();
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see your registers.',
        ),
      );
    }

    final RegisterArgs args = (companyId: companyId, kind: _kind, range: range);
    final AsyncValue<Fresh<RegisterReport>> state = ref.watch(registerProvider(args));
    final Color tint = _isSales ? AppTheme.tileBlue : AppTheme.tileViolet;

    return ReportScaffold<RegisterReport>(
      title: _isSales ? 'Sales register' : 'Purchase register',
      subtitle: periodLabel,
      state: state,
      onRefresh: () async {
        ref.invalidate(registerProvider(args));
        await ref.read(registerProvider(args).future);
      },
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(94),
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: SegmentedPill<String>(
                value: _kind,
                onChanged: (String value) => setState(() => _kind = value),
                segments: const <({String value, String label})>[
                  (value: 'sales', label: 'Sales'),
                  (value: 'purchase', label: 'Purchases'),
                ],
              ),
            ),
            SizedBox(
              height: 42,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: PeriodField(label: periodLabel, onTap: pickPeriod),
                ),
              ),
            ),
          ],
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.receipt_long_outlined,
        title: _isSales ? 'No sales in this period' : 'No purchases in this period',
        message: 'Nothing of this kind was recorded in TallyPrime for '
            '${periodLabel.toLowerCase()}.',
        action: FilledButton.tonalIcon(
          onPressed: pickPeriod,
          icon: const Icon(Icons.date_range),
          label: const Text('Change period'),
        ),
      ),
      builder: (BuildContext context, RegisterReport report) {
        if (report.voucherCount == 0) return const <Widget>[];

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: _MonthlyCard(
              report: report,
              tint: tint,
              periodLabel: periodLabel,
            ),
          ),
          if (report.byParty.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              child: RankedSection(
                title: _isSales ? 'Customers' : 'Suppliers',
                subtitle: '${report.byParty.length} shown, largest first',
                icon: _isSales ? Icons.groups_outlined : Icons.local_shipping_outlined,
                tint: tint,
                whole: report.total,
                entries: <RankedEntry>[
                  for (final PartyTotal party in report.byParty)
                    RankedEntry(
                      name: party.name,
                      amount: party.amount,
                      subtitle: '${party.voucherCount} vch',
                      onTap: () => context.push(
                        '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(party.name)}',
                      ),
                    ),
                ],
              ),
            ),
          if (report.byProduct.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              child: RankedSection(
                title: 'Items',
                subtitle: 'By value',
                icon: Icons.local_offer_outlined,
                tint: AppTheme.tileAmber,
                whole: report.total,
                entries: <RankedEntry>[
                  for (final ProductTotal product in report.byProduct)
                    RankedEntry(
                      name: product.name,
                      amount: product.amount,
                      subtitle: MoneyFormat.quantity(product.quantity, product.unit),
                      onTap: () => context.push(
                        '${Routes.stockMovement}?item=${Uri.encodeQueryComponent(product.name)}',
                      ),
                    ),
                ],
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: SectionCard(
              title: 'Vouchers',
              // The honest count, not the length of the list below it. A capped
              // list that named its own length would read as a shorter register
              // than the one that exists.
              subtitle: report.truncated
                  ? 'showing ${report.vouchers.length} of ${report.voucherCount}'
                  : '${report.voucherCount} in this period',
              icon: Icons.receipt_long_outlined,
              tint: tint,
              child: Column(
                children: <Widget>[
                  for (final TransactionLine line in report.vouchers)
                    TransactionTile(line: line),
                  if (report.truncated)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
                      child: Text(
                        'Only the most recent ${report.vouchers.length} are listed. '
                        'Narrow the period to see the rest.',
                        style: Theme.of(context)
                            .textTheme
                            .labelSmall
                            ?.copyWith(color: context.mutedColor),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ];
      },
    );
  }
}

/// The register's shape over time, plus the figures the shape is made of.
class _MonthlyCard extends StatelessWidget {
  const _MonthlyCard({
    required this.report,
    required this.tint,
    required this.periodLabel,
  });

  final RegisterReport report;
  final Color tint;
  final String periodLabel;

  @override
  Widget build(BuildContext context) {
    final List<MonthTotal> months = report.months;

    return SectionCard(
      title: 'By month',
      subtitle: periodLabel,
      icon: Icons.bar_chart_rounded,
      tint: tint,
      child: Column(
        children: <Widget>[
          if (months.length > 1)
            // Bars, not a line. Months are buckets an owner compares with each
            // other; a curve between them would imply a continuum that a
            // monthly total does not have.
            TrendChart(
              shape: TrendShape.bars,
              series: <ChartSeries>[
                ChartSeries(
                  label: 'Total',
                  colour: tint,
                  values: <double>[
                    for (final MonthTotal m in months) m.total.amount.toDouble(),
                  ],
                ),
              ],
              dates: <DateTime>[
                for (final MonthTotal m in months)
                  DateTime(
                    int.parse(m.month.substring(0, 4)),
                    int.parse(m.month.substring(5, 7)),
                  ),
              ],
              currency: report.total.currency,
              height: 150,
            ),
          if (months.length > 1) ...<Widget>[
            const SizedBox(height: 6),
            const Divider(indent: 16, endIndent: 16),
            const SizedBox(height: 10),
          ],
          StatStrip(
            stats: <Stat>[
              Stat(label: 'Total', value: MoneyFormat.compact(report.total)),
              Stat(label: 'Vouchers', value: '${report.voucherCount}'),
              Stat(
                label: 'Average',
                value: report.voucherCount == 0
                    ? '--'
                    : MoneyFormat.compactValue(
                        report.total.amount.toDouble() / report.voucherCount,
                        report.total.currency,
                      ),
              ),
              Stat(
                label: 'Busiest month',
                value: months.isEmpty
                    ? '--'
                    : months
                        .reduce((MonthTotal a, MonthTotal b) =>
                            a.total.amount >= b.total.amount ? a : b)
                        .label,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
