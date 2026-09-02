import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/shell.dart';
import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../../../core/model/date_range.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/financial_year.dart';
import '../../../core/money/money_format.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/charts.dart';
import '../../../core/widgets/freshness_banner.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/application/financial_year_providers.dart';
import '../../companies/domain/company.dart';
import '../../subscription/domain/subscription.dart';
import '../../subscription/presentation/demo_notice.dart';
import '../../sync/application/sync_providers.dart';
import '../../sync/domain/sync_status.dart';
import '../../sync/presentation/widgets/sync_progress.dart';
import '../../updates/presentation/update_banner.dart';
import '../application/dashboard_providers.dart';
import '../domain/dashboard.dart';
import 'widgets/company_switcher.dart';
import 'widgets/dashboard_sections.dart';

/// How long a window the dashboard will ask for. Mirrors the backend's own
/// `MAX_DASHBOARD_DAYS`, so a longer custom range is trimmed on the phone with
/// the user told why, rather than silently trimmed by the server.
const int _maxDashboardDays = 400;

/// The screen the product is judged on.
///
/// The brief is that a business owner opens their phone and, within ten
/// seconds, knows what they sold, who owes them, where their cash is and what
/// is running out. So the order here is by question, not by module: money in
/// and money owed first, then trend, then stock, then detail.
///
/// Every card leads with a picture and keeps its numbers underneath it. That is
/// the difference between a dashboard and a list of balances: an owner reads
/// "the line is flat and half of what I am owed is over ninety days late" in
/// one glance, and only then starts reading names and amounts.
class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<Company>> companies = ref.watch(companiesProvider);

    return companies.when(
      loading: () => const Scaffold(body: LoadingState()),
      error: (Object error, StackTrace stack) {
        // A stopped subscription is not a failure to retry. Offering "Try
        // again" would loop an owner through the same refusal instead of
        // telling them who to ring, so it gets the explanation screen.
        if (error is ApiException && error.isSubscriptionInactive) {
          return const _NoCompaniesScreen();
        }
        return Scaffold(
          body: ErrorState(
            error: error,
            onRetry: () => ref.invalidate(companiesProvider),
          ),
        );
      },
      data: (List<Company> list) {
        if (list.isEmpty) return const _NoCompaniesScreen();
        final Company? company = ref.watch(activeCompanyProvider).valueOrNull;
        if (company == null) return const _NoCompaniesScreen();
        return _Dashboard(company: company);
      },
    );
  }
}

class _Dashboard extends ConsumerWidget {
  const _Dashboard({required this.company});

  final Company company;

  Future<void> _pickPeriod(BuildContext context, WidgetRef ref) async {
    final PeriodSelection? current = ref.read(dashboardPeriodProvider(company.id));
    final FinancialYear year = ref.read(activeFinancialYearProvider);
    final PeriodSelection? picked = await showPeriodPicker(
      context,
      current: current?.range ?? year.confine(DateRange.today()),
      year: year,
      maxDays: _maxDashboardDays,
    );
    if (picked == null) return;
    ref.read(dashboardPeriodProvider(company.id).notifier).select(picked);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<Dashboard> state = ref.watch(dashboardProvider(company.id));
    final SyncStatus? sync = ref.watch(syncStatusProvider(company.id)).valueOrNull;
    final PeriodSelection? period = ref.watch(dashboardPeriodProvider(company.id));

    return Scaffold(
      appBar: AppBar(
        title: CompanySwitcher(
            subtitle: company.tallyName != company.name ? company.tallyName : null),
        actions: <Widget>[
          IconButton(
            tooltip: 'Refresh from Tally',
            onPressed: () => ref.read(dashboardProvider(company.id).notifier).refresh(),
            icon: const Icon(Icons.refresh),
          ),
        ],
        // A persistent bar rather than a scrolling one: which window the
        // figures cover changes what every number on the screen means, so it
        // must not be possible to scroll the reminder out of sight. It is a row
        // of periods rather than a calendar button because switching window is
        // the gesture an owner makes most, and burying it behind an icon made
        // the dashboard feel like a fixed report.
        bottom: _PeriodBar(
          selection: period,
          year: ref.watch(activeFinancialYearProvider),
          onSelect: (PeriodSelection? selection) =>
              ref.read(dashboardPeriodProvider(company.id).notifier).select(selection),
          onCustom: () => _pickPeriod(context, ref),
        ),
      ),
      // The first read of a real set of books takes minutes, so it gets the
      // whole screen and a determinate bar. Everything after it -- including
      // the rest of the same backfill, once its newest slice has landed -- goes
      // above the figures instead, because by then there are figures worth
      // looking at and the history is filling in behind them.
      body: sync != null && sync.ownsTheScreen
          ? SyncProgressPanel(companyId: company.id)
          : RefreshIndicator(
              onRefresh: () => ref.read(dashboardProvider(company.id).notifier).refresh(),
              child: state.when(
                // `skipLoadingOnRefresh` keeps the previous figures on screen while a
                // refresh runs. Replacing real numbers with a spinner every time the
                // user pulls down would make the app feel less trustworthy, not more.
                skipLoadingOnRefresh: true,
                loading: () => const _DashboardSkeleton(),
                error: (Object error, StackTrace stack) => ListView(
                  children: <Widget>[
                    SizedBox(height: MediaQuery.sizeOf(context).height * 0.2),
                    ErrorState(
                      error: error,
                      onRetry: () => ref.invalidate(dashboardProvider(company.id)),
                    ),
                  ],
                ),
                data: (Dashboard dashboard) => _DashboardBody(
                  dashboard: dashboard,
                  companyId: company.id,
                  periodLabel: period?.label,
                  onRefresh: () =>
                      ref.read(dashboardProvider(company.id).notifier).refresh(),
                ),
              ),
            ),
    );
  }
}

class _DashboardBody extends ConsumerWidget {
  const _DashboardBody({
    required this.dashboard,
    required this.companyId,
    required this.onRefresh,
    this.periodLabel,
  });

  final Dashboard dashboard;
  final String companyId;
  final VoidCallback onRefresh;

  /// What the chosen period is called ("This month", "14 Feb – 15 Mar"). Null
  /// on the ordinary today view.
  final String? periodLabel;

  /// Read from the response, not from what was asked for: the backend clamps
  /// long spans, so this is the window the figures on screen actually cover.
  DashboardPeriod? get _period => dashboard.period;

  bool get _isPeriod => _period != null;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (dashboard.isEmpty) return _empty(context, ref);

    final TradeSummary? sales = dashboard.sales.data;
    final TradeSummary? purchases = dashboard.purchases.data;

    return ContentPane(
      maxWidth: Breakpoints.dashboard,
      child: ListView(
        padding: EdgeInsets.only(bottom: HomeShell.contentInset(context)),
        children: <Widget>[
          FreshnessBanner(freshness: dashboard.freshness, onRefresh: onRefresh),
          // Silent on a real account. On the demo it is the first thing above
          // the figures, because that is where somebody decides whether to
          // believe them.
          const DemoNotice(),
          // Renders nothing at all unless a sync is actually in flight or has
          // stopped short, so a settled company keeps a clean dashboard.
          SyncProgressStrip(companyId: companyId),
          // Likewise silent unless an optional update exists. A required one
          // never reaches here -- UpdateGate has already replaced the whole app.
          const UpdateBanner(),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
            child: _Headline(dashboard: dashboard, periodLabel: periodLabel),
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: _MetricGrid(dashboard: dashboard),
          ),
          const SizedBox(height: 14),
          if (dashboard.sales.hasData)
            _Padded(
              child: TradeSection(
                title: 'Sales',
                summary: sales!,
                // Purchases ride on the sales axis rather than getting a card
                // of their own: the gap between the two lines is the margin,
                // and a gap cannot be read across a scroll.
                compareWith: purchases,
                compareLabel: 'Purchases',
                currency: dashboard.currency,
                periodLabel: periodLabel,
              ),
            )
          else
            _Padded(
              child: SectionUnavailable(title: 'Sales', reason: dashboard.sales.error),
            ),
          if (dashboard.receivables.hasData)
            _Padded(
              child: OutstandingSection(
                title: 'Receivables',
                summary: dashboard.receivables.data!,
                kindQuery: 'receivable',
              ),
            )
          else
            _Padded(
              child: SectionUnavailable(
                title: 'Receivables',
                reason: dashboard.receivables.error,
              ),
            ),
          // Cash, bank and stock cannot be rewound -- see [CurrentOnlyNote].
          if (dashboard.funds.hasData)
            _Padded(
              child: FundsSection(
                summary: dashboard.funds.data!,
                alwaysCurrent: _isPeriod,
              ),
            ),
          if (dashboard.inventory.hasData)
            _Padded(
              child: InventorySection(
                summary: dashboard.inventory.data!,
                alwaysCurrent: _isPeriod,
              ),
            ),
          if (dashboard.payables.hasData && !dashboard.payables.data!.total.isZero)
            _Padded(
              child: OutstandingSection(
                title: 'Payables',
                summary: dashboard.payables.data!,
                kindQuery: 'payable',
              ),
            ),
          if (sales != null && sales.topProducts.isNotEmpty)
            _Padded(
              child: RankedSection(
                title: 'Top products',
                subtitle: 'By sales value',
                icon: Icons.local_offer_outlined,
                tint: AppTheme.tileAmber,
                action: 'Slow movers',
                onAction: () => context.push(Routes.slowMoving),
                whole: sales.period?.total ?? sales.thisMonth,
                entries: <RankedEntry>[
                  for (final ProductTotal product in sales.topProducts)
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
          // Purchases only get a card of their own when there is no sales chart
          // for them to sit on. Otherwise this would be the same series drawn
          // twice on one screen.
          if (sales == null && purchases != null && !purchases.headline.isZero)
            _Padded(
              child: TradeSection(
                title: 'Purchases',
                summary: purchases,
                currency: dashboard.currency,
                periodLabel: periodLabel,
                tint: AppTheme.tileViolet,
              ),
            ),
          if (dashboard.activity.hasData)
            _Padded(child: ActivitySection(summary: dashboard.activity.data!)),
        ],
      ),
    );
  }

  Widget _empty(BuildContext context, WidgetRef ref) => ContentPane(
        maxWidth: Breakpoints.dashboard,
        child: ListView(
          children: <Widget>[
            SizedBox(height: MediaQuery.sizeOf(context).height * 0.15),
            EmptyState(
              icon: Icons.cloud_off_outlined,
              // Never zeroes. "You sold nothing today" and "we could not reach
              // your Tally" are completely different statements, and only one of
              // them is true here.
              title: 'No figures to show yet',
              message: _isPeriod
                  // A past window with nothing behind it is far more likely to be
                  // history that was never read than a company that is offline,
                  // so it gets its own wording and its own way out.
                  ? 'We have nothing saved for ${periodLabel ?? 'that period'}. '
                      'Your history may not reach back that far yet.'
                  : dashboard.freshness.connectorOnline
                      ? 'We could not read anything from TallyPrime. Make sure the '
                          'company is loaded on your PC.'
                      : 'Your Tally PC is offline, and we have nothing saved for this '
                          'company yet.',
              action: Column(
                children: <Widget>[
                  if (_isPeriod)
                    FilledButton.tonalIcon(
                      onPressed: () => ref
                          .read(dashboardPeriodProvider(companyId).notifier)
                          .backToToday(),
                      icon: const Icon(Icons.today),
                      label: const Text('Back to today'),
                    )
                  else
                    FilledButton.tonalIcon(
                      onPressed: onRefresh,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Try again'),
                    ),
                  const SizedBox(height: 8),
                  // The way out for a company whose books were never read --
                  // linked before this existed, or linked while the PC was off,
                  // so nothing ever kicked the backfill off in the background.
                  TextButton(
                    onPressed: () =>
                        ref.read(syncStatusProvider(companyId).notifier).start(),
                    child: const Text('Read my history from Tally'),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}

/// The period switcher, pinned under the app bar.
///
/// The five windows an owner actually asks for, as a row of pills, with the
/// calendar for anything else. It is always on screen because every figure
/// below it means something different depending on which pill is lit -- and a
/// custom range takes a pill of its own so a chosen window can never be lit
/// while the row still reads "Today".
class _PeriodBar extends StatelessWidget implements PreferredSizeWidget {
  const _PeriodBar({
    required this.selection,
    required this.year,
    required this.onSelect,
    required this.onCustom,
  });

  final PeriodSelection? selection;

  /// The year every pill here sits inside. Chosen above, under the company
  /// name, and it decides both which pills exist and what they mean.
  final FinancialYear year;

  final ValueChanged<PeriodSelection?> onSelect;
  final VoidCallback onCustom;

  /// The pills, shortest first: a subset of the sheet's presets, because eight
  /// pills is a row nobody reads. In a closed year the shorter windows are
  /// meaningless -- there is no "today" in a year that ended -- so it shows
  /// that year's quarters instead.
  List<PeriodPreset> _pills() {
    final List<PeriodPreset> presets = periodPresetsFor(year);
    if (!year.isCurrent) return presets;
    const Set<String> wanted = <String>{
      'Today',
      'This week',
      'This month',
      'This quarter',
    };
    return <PeriodPreset>[
      for (final PeriodPreset preset in presets)
        if (wanted.contains(preset.label) || preset.label == year.label) preset,
    ];
  }

  @override
  Size get preferredSize => const Size.fromHeight(48);

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final List<PeriodPreset> pills = _pills();
    // Null is today -- see DashboardPeriodController.select.
    final String active = selection?.label ?? 'Today';
    final bool isCustom = !pills.any((PeriodPreset p) => p.label == active);

    return SizedBox(
      height: 48,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(16, 0, 12, 10),
        children: <Widget>[
          for (final PeriodPreset preset in pills)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: _PeriodPill(
                label: preset.label.replaceFirst('This ', ''),
                selected: !isCustom && active == preset.label,
                onTap: () {
                  if (preset.label == 'Today') {
                    onSelect(null);
                    return;
                  }
                  onSelect(PeriodSelection(preset.range(), preset.label));
                },
              ),
            ),
          if (isCustom)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: _PeriodPill(
                label: formatRangeLabel(selection!.range),
                selected: true,
                onTap: onCustom,
              ),
            ),
          // The way to everything the five pills do not cover, and the only
          // control here that opens something rather than switching something.
          Material(
            color: context.surfaceColor,
            shape: const StadiumBorder(),
            clipBehavior: Clip.antiAlias,
            child: InkWell(
              onTap: onCustom,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Icon(
                  Icons.calendar_month_outlined,
                  size: 17,
                  color: theme.colorScheme.onSurface,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _PeriodPill extends StatelessWidget {
  const _PeriodPill({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool isLight = theme.brightness == Brightness.light;
    // The same inversion the nav bar's selected pill makes, so "this is where
    // you are" reads the same way wherever it appears.
    final Color fill = selected
        ? (isLight ? AppTheme.ink : Colors.white)
        : context.surfaceColor;
    final Color ink = selected
        ? (isLight ? Colors.white : AppTheme.ink)
        : context.mutedColor;

    return Material(
      color: fill,
      shape: const StadiumBorder(),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: Center(
            child: Text(
              label,
              style: theme.textTheme.labelMedium?.copyWith(
                color: ink,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Keeps the vertical rhythm in one place instead of on thirty call sites.
class _Padded extends StatelessWidget {
  const _Padded({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        child: child,
      );
}

/// The one figure the screen is built around, on the one dark card.
///
/// Sales answers the brief's first question -- "how much did I sell today?" --
/// so it gets the card the eye lands on, and everything else becomes a tile
/// beneath it. The trend runs across the bottom of the card so the headline is
/// never a number without a direction, and the three figures under it are the
/// rest of the brief's first four questions at a glance.
///
/// When there is no sales figure there is no dark card either: an empty hero is
/// a hole where the most important thing should be, which reads worse than a
/// screen that simply starts with tiles.
class _Headline extends StatelessWidget {
  const _Headline({required this.dashboard, this.periodLabel});

  final Dashboard dashboard;
  final String? periodLabel;

  static const Color _dim = Color(0xFF9AA0AE);

  @override
  Widget build(BuildContext context) {
    final TradeSummary? sales = dashboard.sales.data;
    if (sales == null) return const SizedBox.shrink();

    final ThemeData theme = Theme.of(context);
    final bool isPeriod = dashboard.period != null;
    final FundsSummary? funds = dashboard.funds.data;
    final TradeSummary? purchases = dashboard.purchases.data;
    final double? change =
        isPeriod ? sales.period?.changePct : _versusYesterday(sales);

    return HeroCard(
      padding: const EdgeInsets.fromLTRB(18, 15, 18, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const IconTile(
                icon: Icons.point_of_sale_outlined,
                size: 30,
                background: Color(0x1FFFFFFF),
                foreground: Colors.white,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  isPeriod ? 'Sales · ${periodLabel ?? 'period'}' : "Today's sales",
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelMedium?.copyWith(color: _dim),
                ),
              ),
              if (change != null) _DarkChangeChip(changePct: change),
            ],
          ),
          const SizedBox(height: 10),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              // Exact rupees, no paise. This is the figure the screen exists
              // for, so rounding it to "4.83L" would be hiding the answer; the
              // decimals, on the other hand, are two characters nobody reads
              // and they push the number down a size.
              MoneyFormat.full(sales.headline, decimals: false),
              style: theme.textTheme.displaySmall?.copyWith(color: Colors.white),
            ),
          ),
          Text(
            isPeriod ? 'compared with the previous period' : 'compared with yesterday',
            style: theme.textTheme.labelSmall?.copyWith(color: _dim),
          ),
          if (sales.trend.length > 1) ...<Widget>[
            const SizedBox(height: 10),
            Sparkline(
              values: <double>[for (final TrendPoint p in sales.trend) p.value],
              // White, not the accent: on near-black the brand blue is the one
              // colour that disappears, and this line has to read at a glance
              // from across a counter.
              colour: Colors.white,
              height: 34,
            ),
          ],
          const SizedBox(height: 12),
          const Divider(height: 1, color: Color(0x1FFFFFFF)),
          const SizedBox(height: 11),
          // Deliberately none of the figures that are tiles below. Repeating a
          // number a few pixels under itself reads as a bug rather than as
          // emphasis, so the card carries what the grid does not: what the day
          // cost, where the money is, and what the month has come to so far.
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              if (purchases != null)
                _DarkStat(
                  label: 'Purchases',
                  value: MoneyFormat.compact(purchases.headline),
                ),
              if (funds != null)
                _DarkStat(
                  label: 'Cash & bank',
                  value: MoneyFormat.compact(funds.total),
                ),
              // On a period the headline *is* the period total, so the useful
              // third figure is what it is being compared against. A baseline
              // outside the window that was read is unknown, not zero.
              _DarkStat(
                label: isPeriod ? 'Previous period' : 'Month to date',
                value: isPeriod
                    ? (sales.period?.previousTotal == null
                        ? '--'
                        : MoneyFormat.compact(sales.period!.previousTotal!))
                    : MoneyFormat.compact(sales.thisMonth),
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// Today against yesterday. Null when yesterday was zero -- a percentage
  /// against a zero baseline is not a movement of 0% or 100%, it is an
  /// unanswerable question.
  static double? _versusYesterday(TradeSummary sales) {
    final double yesterday = sales.yesterday.amount.toDouble();
    if (yesterday == 0) return null;
    final double today = sales.today.amount.toDouble();
    return (today - yesterday) / yesterday * 100;
  }
}

/// One of the three supporting figures on the dark card.
class _DarkStat extends StatelessWidget {
  const _DarkStat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelSmall?.copyWith(color: _Headline._dim),
          ),
          const SizedBox(height: 2),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              value,
              style: theme.textTheme.titleSmall
                  ?.merge(AppTheme.amount)
                  .copyWith(color: Colors.white),
            ),
          ),
        ],
      ),
    );
  }
}

/// The change chip again, in the colours that survive on the dark card. The
/// page version's ten-percent tint disappears entirely against near-black.
class _DarkChangeChip extends StatelessWidget {
  const _DarkChangeChip({required this.changePct});

  final double changePct;

  @override
  Widget build(BuildContext context) {
    final bool up = changePct >= 0;
    final Color colour = up ? const Color(0xFF3ED598) : const Color(0xFFFF6B81);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: colour.withOpacity(0.16),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(up ? Icons.arrow_upward : Icons.arrow_downward, size: 12, color: colour),
          const SizedBox(width: 3),
          Text(
            '${changePct.abs().toStringAsFixed(changePct.abs() >= 100 ? 0 : 1)}%',
            style: Theme.of(context)
                .textTheme
                .labelSmall
                ?.copyWith(color: colour, fontWeight: FontWeight.w700),
          ),
        ],
      ),
    );
  }
}

/// The figures that are not on the dark card, each with the shape behind it.
///
/// A tile is a number *and* its evidence: what you are owed carries the share
/// of it that is late, stock carries the share of items below reorder level.
/// Both are one-glance judgements that a bare total cannot make.
class _MetricGrid extends StatelessWidget {
  const _MetricGrid({required this.dashboard});

  final Dashboard dashboard;

  @override
  Widget build(BuildContext context) {
    final TradeSummary? sales = dashboard.sales.data;
    final FundsSummary? funds = dashboard.funds.data;
    final OutstandingSummary? receivables = dashboard.receivables.data;
    final OutstandingSummary? payables = dashboard.payables.data;
    final InventorySummary? inventory = dashboard.inventory.data;
    final ActivitySummary? activity = dashboard.activity.data;

    final List<Widget> tiles = <Widget>[
      // Sales and cash & bank are on the dark card above. Repeating a figure a
      // few pixels below itself reads as a bug, not as emphasis.
      if (sales == null && funds != null)
        MetricTile(
          label: 'Cash & bank',
          value: MoneyFormat.compact(funds.total),
          icon: Icons.savings_outlined,
          tone: MetricTone.positive,
          caption: '${funds.cashAccounts.length + funds.bankAccounts.length} accounts',
        ),
      if (receivables != null)
        MetricTile(
          label: 'You are owed',
          value: MoneyFormat.compact(receivables.total),
          icon: Icons.call_received,
          tone: receivables.overdue.isZero ? MetricTone.neutral : MetricTone.caution,
          meter: receivables.overdueShare,
          meterCaption: receivables.overdue.isZero
              ? 'nothing overdue'
              : '${(receivables.overdueShare * 100).round()}% overdue'
                  ' · ${MoneyFormat.compact(receivables.overdue)}',
          onTap: () => context.push('${Routes.outstanding}?kind=receivable'),
        ),
      if (payables != null)
        MetricTile(
          label: 'You owe',
          value: MoneyFormat.compact(payables.total),
          icon: Icons.call_made,
          tone: payables.overdue.isZero ? MetricTone.neutral : MetricTone.negative,
          meter: payables.overdueShare,
          meterCaption: payables.overdue.isZero
              ? 'nothing overdue'
              : '${(payables.overdueShare * 100).round()}% overdue'
                  ' · ${MoneyFormat.compact(payables.overdue)}',
          onTap: () => context.push('${Routes.outstanding}?kind=payable'),
        ),
      if (inventory != null)
        MetricTile(
          label: 'Stock value',
          value: MoneyFormat.compact(inventory.value),
          icon: Icons.inventory_2_outlined,
          tone: inventory.negativeStockCount > 0
              ? MetricTone.negative
              : inventory.lowStockCount > 0
                  ? MetricTone.caution
                  : MetricTone.neutral,
          // The share of the catalogue that needs attention, not the share that
          // is fine -- a meter that is nearly full of "healthy" says nothing.
          meter: inventory.itemCount == 0
              ? null
              : (inventory.lowStockCount + inventory.negativeStockCount) /
                  inventory.itemCount,
          meterCaption: inventory.lowStockCount == 0 &&
                  inventory.negativeStockCount == 0
              ? '${inventory.itemCount} items, all healthy'
              : '${inventory.lowStockCount} low · '
                  '${inventory.negativeStockCount} negative',
          onTap: () => context.push(Routes.stock),
        ),
      if (activity != null && sales != null)
        MetricTile(
          label: 'Vouchers',
          value: '${activity.voucherCount}',
          icon: Icons.receipt_long_outlined,
          caption: dashboard.period == null ? 'today' : 'in this period',
          // The trend the count belongs to. Amounts, not counts -- the backend
          // sends a value series and not a voucher-count series, and inventing
          // a second axis for a sparkline nobody reads exactly would be worse
          // than showing the money the vouchers moved.
          spark: <double>[for (final TrendPoint p in sales.trend) p.value],
          onTap: () => context.push(Routes.daybook),
        ),
    ];

    if (tiles.isEmpty) return const SizedBox.shrink();

    // Four across once the pane is wide enough to hold them at a readable
    // size. A tile is a self-contained block, so extra width should buy another
    // column rather than stretch the two that are already there.
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final int columns = constraints.maxWidth >= 560 ? 4 : 2;
        return GridView(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: columns,
            crossAxisSpacing: 12,
            mainAxisSpacing: 12,
            // A fixed height rather than an aspect ratio. A tile always holds
            // the same four things -- label, figure, caption, one strip of
            // evidence -- so its height does not depend on its width, and tying
            // the two together parks a band of empty space under every tile as
            // soon as the pane gets wider.
            mainAxisExtent: 138,
          ),
          children: tiles,
        );
      },
    );
  }
}

class _DashboardSkeleton extends StatelessWidget {
  const _DashboardSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: const <Widget>[
        SkeletonBox(height: 168, radius: 20),
        SizedBox(height: 14),
        Row(
          children: <Widget>[
            Expanded(child: SkeletonBox(height: 116, radius: 20)),
            SizedBox(width: 12),
            Expanded(child: SkeletonBox(height: 116, radius: 20)),
          ],
        ),
        SizedBox(height: 12),
        Row(
          children: <Widget>[
            Expanded(child: SkeletonBox(height: 116, radius: 20)),
            SizedBox(width: 12),
            Expanded(child: SkeletonBox(height: 116, radius: 20)),
          ],
        ),
        SizedBox(height: 16),
        SkeletonBox(height: 300, radius: 20),
        SizedBox(height: 14),
        SkeletonBox(height: 260, radius: 20),
      ],
    );
  }
}

/// First run: an account with no books attached to it yet.
///
/// The screen a brand new customer lands on, and therefore the one place the
/// approval flow has to explain itself properly. Three different people arrive
/// here and need three different sentences:
///
///  * an admin of a live account — set up your Tally PC;
///  * an admin of an account nobody has approved yet — nothing to do but wait,
///    and pointing them at a wizard that would be refused is worse than useless;
///  * a staff member — none of this is theirs to do, so it says who to ask.
class _NoCompaniesScreen extends ConsumerWidget {
  const _NoCompaniesScreen();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppUser? user = ref.watch(authControllerProvider).user;
    final OrgSubscription subscription = user?.subscription ?? OrgSubscription.unknown;

    if (!subscription.allowsChanges) {
      return Scaffold(
        appBar: AppBar(title: const Text('TallyFlow')),
        body: EmptyState(
          icon: subscription.isAwaitingApproval
              ? Icons.hourglass_top_rounded
              : Icons.lock_outline,
          title: subscription.isAwaitingApproval
              ? 'Almost there'
              : subscription.status.label,
          // The server's wording, not a second version of it written here.
          message: user?.role.isAdmin ?? false
              ? subscription.message
              : 'Ask an admin of this business to set up TallyFlow. '
                  'Your reports appear here as soon as they do.',
        ),
      );
    }

    if (!(user?.canManageConnectors ?? false)) {
      return Scaffold(
        appBar: AppBar(title: const Text('TallyFlow')),
        body: const EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No companies yet',
          message: 'You have not been given access to any company. An admin of '
              'this business can grant it from the People screen.',
        ),
      );
    }

    return Scaffold(
      appBar: AppBar(title: const Text('TallyFlow')),
      body: EmptyState(
        icon: Icons.desktop_windows_outlined,
        title: 'Connect your Tally PC',
        message: 'Install the TallyFlow Connector on the computer running '
            'TallyPrime. It reads your data and sends it here -- your PC never '
            'has to be exposed to the internet.',
        action: Column(
          children: <Widget>[
            FilledButton.icon(
              onPressed: () => context.push(Routes.pairConnector),
              icon: const Icon(Icons.add_link),
              label: const Text('Set up a connection'),
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: () => context.push(Routes.connectors),
              child: const Text('I already have a connector'),
            ),
          ],
        ),
      ),
    );
  }
}
