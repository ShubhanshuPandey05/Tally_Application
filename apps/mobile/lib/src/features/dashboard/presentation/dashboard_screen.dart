import 'package:flutter/material.dart';
import '../../../core/layout/adaptive.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/shell.dart';
import '../../../app/theme.dart';
import '../../../core/model/date_range.dart';
import '../../../core/model/figures.dart';
import '../../../core/money/money_format.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/freshness_banner.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../../subscription/domain/subscription.dart';
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
    final PeriodSelection? picked = await showPeriodPicker(
      context,
      current: current?.range ?? DateRange.today(),
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
            tooltip: 'Choose a period',
            onPressed: () => _pickPeriod(context, ref),
            icon: Icon(
              period == null ? Icons.calendar_month_outlined : Icons.event_available,
            ),
          ),
          IconButton(
            tooltip: 'Refresh from Tally',
            onPressed: () => ref.read(dashboardProvider(company.id).notifier).refresh(),
            icon: const Icon(Icons.refresh),
          ),
        ],
        // A persistent bar rather than a scrolling one: which window the
        // figures cover changes what every number on the screen means, so it
        // must not be possible to scroll the reminder out of sight.
        bottom: period == null
            ? null
            : _PeriodBar(
                selection: period,
                onClear: () =>
                    ref.read(dashboardPeriodProvider(company.id).notifier).backToToday(),
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
    if (dashboard.isEmpty) {
      return ContentPane(
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

    final TradeSummary? sales = dashboard.sales.data;
    final TradeSummary? purchases = dashboard.purchases.data;

    return ContentPane(
      maxWidth: Breakpoints.dashboard,
      child: ListView(
        padding: EdgeInsets.only(bottom: HomeShell.contentInset(context)),
        children: <Widget>[
          FreshnessBanner(freshness: dashboard.freshness, onRefresh: onRefresh),
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
            child: _KpiGrid(dashboard: dashboard, periodLabel: periodLabel),
          ),
          const SizedBox(height: 14),
          if (dashboard.sales.hasData)
            _Padded(
              child: TrendSection(
                title: 'Sales',
                summary: sales!,
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
          if (sales != null && sales.topParties.isNotEmpty)
            _Padded(
              child: SectionCard(
                title: 'Top customers',
                subtitle: periodLabel ?? 'This period',
                icon: Icons.emoji_events_outlined,
                child: Column(
                  children: <Widget>[
                    for (final PartyTotal party in sales.topParties)
                      AmountRow(
                        title: party.name,
                        amount: party.amount,
                        subtitle: '${party.voucherCount} vouchers',
                      ),
                  ],
                ),
              ),
            ),
          if (sales != null && sales.topProducts.isNotEmpty)
            _Padded(
              child: SectionCard(
                title: 'Top products',
                subtitle: 'By sales value',
                icon: Icons.local_offer_outlined,
                action: 'Slow movers',
                onAction: () => context.push(Routes.slowMoving),
                child: Column(
                  children: <Widget>[
                    for (final ProductTotal product in sales.topProducts)
                      AmountRow(
                        title: product.name,
                        amount: product.amount,
                        subtitle:
                            '${MoneyFormat.quantity(product.quantity, product.unit)} sold',
                      ),
                  ],
                ),
              ),
            ),
          if (purchases != null && !purchases.headline.isZero)
            _Padded(
              child: TrendSection(
                title: 'Purchases',
                summary: purchases,
                currency: dashboard.currency,
                periodLabel: periodLabel,
              ),
            ),
          if (dashboard.activity.hasData)
            _Padded(child: ActivitySection(summary: dashboard.activity.data!)),
        ],
      ),
    );
  }
}

/// The "you are looking at a chosen window" bar, pinned under the app bar.
///
/// Deliberately loud. Every figure below it means something different from
/// what the same screen showed a moment ago, and the one-tap way back to today
/// is what stops a period view from being a trap.
class _PeriodBar extends StatelessWidget implements PreferredSizeWidget {
  const _PeriodBar({required this.selection, required this.onClear});

  final PeriodSelection selection;
  final VoidCallback onClear;

  @override
  Size get preferredSize => const Size.fromHeight(44);

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // A preset names itself ("This month"); a custom range is already spelled
    // out as dates, so repeating them under it would just be noise.
    final String dates = formatRangeLabel(selection.range);
    final String text = selection.label == dates ? dates : '${selection.label} · $dates';

    return Container(
      height: 44,
      width: double.infinity,
      color: theme.scaffoldBackgroundColor,
      padding: const EdgeInsets.fromLTRB(16, 0, 8, 8),
      child: Row(
        children: <Widget>[
          Flexible(
            child: Container(
              height: 32,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              decoration: BoxDecoration(
                color: context.cautionColor.withOpacity(0.12),
                borderRadius: BorderRadius.circular(999),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Icon(Icons.date_range, size: 15, color: context.cautionColor),
                  const SizedBox(width: 7),
                  Flexible(
                    child: Text(
                      text,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelMedium?.copyWith(
                        color: context.cautionColor,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const Spacer(),
          TextButton(onPressed: onClear, child: const Text('Back to today')),
        ],
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
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
        child: child,
      );
}

/// The one figure the screen is built around, on the one dark card.
///
/// Sales answers the brief's first question -- "how much did I sell today?" --
/// so it gets the card the eye lands on, and everything else becomes a tile
/// beneath it. When there is no sales figure there is no dark card either: an
/// empty hero is a hole where the most important thing should be, which reads
/// worse than a screen that simply starts with tiles.
class _Headline extends StatelessWidget {
  const _Headline({required this.dashboard, this.periodLabel});

  final Dashboard dashboard;
  final String? periodLabel;

  @override
  Widget build(BuildContext context) {
    final TradeSummary? sales = dashboard.sales.data;
    if (sales == null) return const SizedBox.shrink();

    final ThemeData theme = Theme.of(context);
    final bool isPeriod = dashboard.period != null;
    final FundsSummary? funds = dashboard.funds.data;
    final double? change =
        isPeriod ? sales.period?.changePct : _KpiGrid._versusYesterday(sales);

    return HeroCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const IconTile(
                icon: Icons.point_of_sale_outlined,
                size: 34,
                background: Color(0x1FFFFFFF),
                foreground: Colors.white,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  isPeriod ? 'Sales · ${periodLabel ?? 'period'}' : "Today's sales",
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.titleSmall?.copyWith(color: Colors.white),
                ),
              ),
              if (change != null) _DarkChangeChip(changePct: change),
            ],
          ),
          const SizedBox(height: 16),
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
          const SizedBox(height: 4),
          Text(
            isPeriod ? 'compared with the previous period' : 'compared with yesterday',
            style: theme.textTheme.bodySmall?.copyWith(color: const Color(0xFF9AA0AE)),
          ),
          if (funds != null) ...<Widget>[
            const SizedBox(height: 16),
            const Divider(height: 1, color: Color(0x1FFFFFFF)),
            const SizedBox(height: 12),
            Row(
              children: <Widget>[
                const Icon(Icons.savings_outlined, size: 16, color: Color(0xFF9AA0AE)),
                const SizedBox(width: 8),
                Text(
                  'Cash & bank',
                  style:
                      theme.textTheme.bodySmall?.copyWith(color: const Color(0xFF9AA0AE)),
                ),
                const Spacer(),
                Text(
                  MoneyFormat.compact(funds.total),
                  style: theme.textTheme.bodyMedium
                      ?.merge(AppTheme.amount)
                      .copyWith(color: Colors.white),
                ),
              ],
            ),
          ],
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

/// The four figures that answer the brief's first four questions.
class _KpiGrid extends StatelessWidget {
  const _KpiGrid({required this.dashboard, this.periodLabel});

  final Dashboard dashboard;
  final String? periodLabel;

  @override
  Widget build(BuildContext context) {
    final TradeSummary? sales = dashboard.sales.data;
    final FundsSummary? funds = dashboard.funds.data;
    final OutstandingSummary? receivables = dashboard.receivables.data;
    final OutstandingSummary? payables = dashboard.payables.data;
    final bool isPeriod = dashboard.period != null;

    final List<KpiCard> tiles = <KpiCard>[
      // Sales and cash & bank are on the dark card above. Repeating a figure a
      // few pixels below itself reads as a bug, not as emphasis.
      if (sales == null && funds != null)
        KpiCard(
          label: 'Cash & bank',
          amount: funds.total,
          icon: Icons.savings_outlined,
          caption: isPeriod ? 'balance today' : 'across all accounts',
        ),
      if (receivables != null)
        KpiCard(
          label: 'You are owed',
          amount: receivables.total,
          icon: Icons.call_received,
          tone: receivables.overdue.isZero ? KpiTone.neutral : KpiTone.caution,
          caption: receivables.overdue.isZero
              ? 'nothing overdue'
              : '${(receivables.overdueShare * 100).round()}% overdue',
          onTap: () => context.push('${Routes.outstanding}?kind=receivable'),
        ),
      if (payables != null)
        KpiCard(
          label: 'You owe',
          amount: payables.total,
          icon: Icons.call_made,
          tone: payables.overdue.isZero ? KpiTone.neutral : KpiTone.negative,
          caption: payables.overdue.isZero
              ? 'nothing overdue'
              : '${(payables.overdueShare * 100).round()}% overdue',
          onTap: () => context.push('${Routes.outstanding}?kind=payable'),
        ),
    ];

    if (tiles.isEmpty) return const SizedBox.shrink();

    // One figure gets the full width rather than half of it with a hole
    // alongside; on a dashboard an empty half-row reads as a tile that failed
    // to load rather than as a tile that was never there.
    if (tiles.length == 1) {
      final KpiCard only = tiles.single;
      return KpiCard(
        label: only.label,
        amount: only.amount,
        caption: only.caption,
        changePct: only.changePct,
        icon: only.icon,
        onTap: only.onTap,
        tone: only.tone,
        wide: true,
      );
    }

    // Four across once the pane is wide enough to hold them at a readable
    // size. A tile is a self-contained block, so extra width should buy another
    // column rather than stretch the three that are already there.
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final int columns = constraints.maxWidth >= 560 ? 4 : 2;
        return GridView.count(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          crossAxisCount: columns,
          crossAxisSpacing: 12,
          mainAxisSpacing: 12,
          childAspectRatio: columns == 4 ? 0.92 : 1.02,
          children: tiles,
        );
      },
    );
  }

  /// Today against yesterday. Null when yesterday was zero -- see [KpiCard].
  static double? _versusYesterday(TradeSummary sales) {
    final double yesterday = sales.yesterday.amount.toDouble();
    if (yesterday == 0) return null;
    final double today = sales.today.amount.toDouble();
    return (today - yesterday) / yesterday * 100;
  }
}

class _DashboardSkeleton extends StatelessWidget {
  const _DashboardSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: const <Widget>[
        SkeletonBox(height: 14, width: 160),
        SizedBox(height: 18),
        Row(
          children: <Widget>[
            Expanded(child: SkeletonBox(height: 92, radius: 16)),
            SizedBox(width: 12),
            Expanded(child: SkeletonBox(height: 92, radius: 16)),
          ],
        ),
        SizedBox(height: 12),
        Row(
          children: <Widget>[
            Expanded(child: SkeletonBox(height: 92, radius: 16)),
            SizedBox(width: 12),
            Expanded(child: SkeletonBox(height: 92, radius: 16)),
          ],
        ),
        SizedBox(height: 16),
        SkeletonBox(height: 220, radius: 16),
        SizedBox(height: 14),
        SkeletonBox(height: 180, radius: 16),
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
