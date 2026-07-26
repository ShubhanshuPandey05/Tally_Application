import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../core/model/figures.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/freshness_banner.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../application/dashboard_providers.dart';
import '../domain/dashboard.dart';
import 'widgets/company_switcher.dart';
import 'widgets/dashboard_sections.dart';

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
      error: (Object error, StackTrace stack) => Scaffold(
        body: ErrorState(
          error: error,
          onRetry: () => ref.invalidate(companiesProvider),
        ),
      ),
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

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<Dashboard> state = ref.watch(dashboardProvider(company.id));

    return Scaffold(
      appBar: AppBar(
        title: CompanySwitcher(subtitle: company.tallyName != company.name
            ? company.tallyName
            : null),
        actions: <Widget>[
          IconButton(
            tooltip: 'Refresh from Tally',
            onPressed: () =>
                ref.read(dashboardProvider(company.id).notifier).refresh(),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
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
            onRefresh: () =>
                ref.read(dashboardProvider(company.id).notifier).refresh(),
          ),
        ),
      ),
    );
  }
}

class _DashboardBody extends StatelessWidget {
  const _DashboardBody({required this.dashboard, required this.onRefresh});

  final Dashboard dashboard;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    if (dashboard.isEmpty) {
      return ListView(
        children: <Widget>[
          SizedBox(height: MediaQuery.sizeOf(context).height * 0.15),
          EmptyState(
            icon: Icons.cloud_off_outlined,
            // Never zeroes. "You sold nothing today" and "we could not reach
            // your Tally" are completely different statements, and only one of
            // them is true here.
            title: 'No figures to show yet',
            message: dashboard.freshness.connectorOnline
                ? 'We could not read anything from TallyPrime. Make sure the '
                    'company is loaded on your PC.'
                : 'Your Tally PC is offline, and we have nothing saved for this '
                    'company yet.',
            action: FilledButton.tonalIcon(
              onPressed: onRefresh,
              icon: const Icon(Icons.refresh),
              label: const Text('Try again'),
            ),
          ),
        ],
      );
    }

    final TradeSummary? sales = dashboard.sales.data;
    final TradeSummary? purchases = dashboard.purchases.data;

    return ListView(
      padding: const EdgeInsets.only(bottom: 28),
      children: <Widget>[
        FreshnessBanner(freshness: dashboard.freshness, onRefresh: onRefresh),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: _KpiGrid(dashboard: dashboard),
        ),
        const SizedBox(height: 14),
        if (dashboard.sales.hasData)
          _Padded(
            child: TrendSection(
              title: 'Sales',
              summary: sales!,
              currency: dashboard.currency,
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
        if (dashboard.funds.hasData)
          _Padded(child: FundsSection(summary: dashboard.funds.data!)),
        if (dashboard.inventory.hasData)
          _Padded(child: InventorySection(summary: dashboard.inventory.data!)),
        if (dashboard.payables.hasData &&
            !dashboard.payables.data!.total.isZero)
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
              subtitle: 'This period',
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
        if (purchases != null && !purchases.thisMonth.isZero)
          _Padded(
            child: TrendSection(
              title: 'Purchases',
              summary: purchases,
              currency: dashboard.currency,
            ),
          ),
        if (dashboard.activity.hasData)
          _Padded(child: ActivitySection(summary: dashboard.activity.data!)),
      ],
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

/// The four figures that answer the brief's first four questions.
class _KpiGrid extends StatelessWidget {
  const _KpiGrid({required this.dashboard});

  final Dashboard dashboard;

  @override
  Widget build(BuildContext context) {
    final TradeSummary? sales = dashboard.sales.data;
    final FundsSummary? funds = dashboard.funds.data;
    final OutstandingSummary? receivables = dashboard.receivables.data;
    final OutstandingSummary? payables = dashboard.payables.data;

    final List<Widget> tiles = <Widget>[
      if (sales != null)
        KpiCard(
          label: "Today's sales",
          amount: sales.today,
          icon: Icons.point_of_sale_outlined,
          tone: KpiTone.positive,
          caption: 'vs yesterday',
          changePct: _versusYesterday(sales),
          onTap: () => context.push(Routes.daybook),
        ),
      if (funds != null)
        KpiCard(
          label: 'Cash & bank',
          amount: funds.total,
          icon: Icons.savings_outlined,
          caption: 'across all accounts',
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

    return GridView.count(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      crossAxisCount: 2,
      crossAxisSpacing: 12,
      mainAxisSpacing: 12,
      childAspectRatio: 1.45,
      children: tiles,
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
class _NoCompaniesScreen extends ConsumerWidget {
  const _NoCompaniesScreen();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
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
