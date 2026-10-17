import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/shell.dart';
import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../dashboard/presentation/widgets/company_switcher.dart';
import '../../settings/presentation/profile_button.dart';

/// The Summary tab: a door to each summary rather than one summary.
///
/// It was the monthly voucher summary itself until a second family of reports
/// arrived. Two cards now, each a whole report set, so the tab can take a
/// third without another redesign -- and the monthly summary is one tap
/// further in, unchanged.
class SummaryScreen extends ConsumerWidget {
  const SummaryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);

    return Scaffold(
      appBar: AppBar(
        leading: const ProfileButton(),
        titleSpacing: 0,
        title: const CompanySwitcher(),
      ),
      body: ContentPane(
        child: companyId == null
            ? const EmptyState(
                icon: Icons.folder_off_outlined,
                title: 'No company connected',
                message: 'Connect your Tally PC to see your summaries.',
              )
            : ListView(
                padding: EdgeInsets.fromLTRB(16, 8, 16, HomeShell.contentInset(context)),
                children: const <Widget>[
                  _SummaryCard(
                    icon: Icons.calendar_view_month_outlined,
                    tint: AppTheme.tileBlue,
                    title: 'Monthly Voucher Summary',
                    subtitle: 'Sales, purchases, receipts and payments, '
                        'month by month through the year',
                    route: Routes.monthlySummary,
                  ),
                  SizedBox(height: 12),
                  _SummaryCard(
                    icon: Icons.leaderboard_outlined,
                    tint: AppTheme.tileViolet,
                    title: 'Top Prime Reports',
                    subtitle: 'Top customers, products, expenses, outstanding '
                        'and profit, ranked',
                    route: Routes.topReports,
                  ),
                ],
              ),
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({
    required this.icon,
    required this.tint,
    required this.title,
    required this.subtitle,
    required this.route,
  });

  final IconData icon;
  final Color tint;
  final String title;
  final String subtitle;
  final String route;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => context.push(route),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 18, 12, 18),
          child: Row(
            children: <Widget>[
              IconTile(icon: icon, colour: tint, size: 46),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(title, style: theme.textTheme.titleMedium),
                    const SizedBox(height: 3),
                    Text(
                      subtitle,
                      style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                    ),
                  ],
                ),
              ),
              Icon(Icons.chevron_right, color: context.mutedColor),
            ],
          ),
        ),
      ),
    );
  }
}
