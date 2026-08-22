import 'package:flutter/material.dart';
import '../../../core/layout/adaptive.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/shell.dart';
import '../../../app/theme.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';

/// The report index.
///
/// Grouped by the question each one answers rather than by Tally's menu
/// structure. "Who owes me money?" is a question a shop owner has; "Bills
/// Receivable (Ledger-wise)" is a menu path they have to be trained on.
class ReportsScreen extends ConsumerWidget {
  const ReportsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Reports')),
      body: ContentPane(
        child: companyId == null
            ? const EmptyState(
                icon: Icons.folder_off_outlined,
                title: 'No company connected',
                message: 'Connect your Tally PC to see reports.',
              )
            : ListView(
                padding: EdgeInsets.fromLTRB(16, 8, 16, HomeShell.contentInset(context)),
                children: const <Widget>[
                  _ReportGroup(
                    tint: AppTheme.tileBlue,
                    heading: 'Money',
                    entries: <_ReportEntry>[
                      _ReportEntry(
                        icon: Icons.call_received,
                        title: 'Receivables',
                        subtitle: 'Who owes me money, and for how long',
                        route: '${Routes.outstanding}?kind=receivable',
                      ),
                      _ReportEntry(
                        icon: Icons.call_made,
                        title: 'Payables',
                        subtitle: 'What I owe my suppliers',
                        route: '${Routes.outstanding}?kind=payable',
                      ),
                      _ReportEntry(
                        icon: Icons.groups_outlined,
                        title: 'Debtors by group',
                        subtitle: 'Every party under Sundry Debtors, net of advances',
                        route: '${Routes.groupOutstanding}?kind=receivable',
                      ),
                      _ReportEntry(
                        icon: Icons.local_shipping_outlined,
                        title: 'Creditors by group',
                        subtitle: 'Every party under Sundry Creditors, net of advances',
                        route: '${Routes.groupOutstanding}?kind=payable',
                      ),
                      _ReportEntry(
                        icon: Icons.account_balance_outlined,
                        title: 'Ledger balances',
                        subtitle: 'Closing balances across all accounts',
                        route: Routes.ledgers,
                      ),
                    ],
                  ),
                  SizedBox(height: 18),
                  _ReportGroup(
                    tint: AppTheme.tileViolet,
                    heading: 'Transactions',
                    entries: <_ReportEntry>[
                      _ReportEntry(
                        icon: Icons.receipt_long_outlined,
                        title: 'Day book',
                        subtitle: 'Every voucher, day by day',
                        route: Routes.daybook,
                      ),
                    ],
                  ),
                  SizedBox(height: 18),
                  _ReportGroup(
                    tint: AppTheme.tileAmber,
                    heading: 'Stock',
                    entries: <_ReportEntry>[
                      _ReportEntry(
                        icon: Icons.inventory_2_outlined,
                        title: 'Stock summary',
                        subtitle: 'What I hold and what it is worth',
                        route: Routes.stock,
                      ),
                      _ReportEntry(
                        icon: Icons.warning_amber_outlined,
                        title: 'Running low',
                        subtitle: 'Items at or below reorder level',
                        route: '${Routes.stock}?only=low',
                      ),
                      _ReportEntry(
                        icon: Icons.error_outline,
                        title: 'Negative stock',
                        subtitle: 'Sold more than the books say you received',
                        route: '${Routes.stock}?only=negative',
                      ),
                      _ReportEntry(
                        icon: Icons.hourglass_bottom,
                        title: 'Slow movers',
                        subtitle: 'Stock on hand that is not selling',
                        route: Routes.slowMoving,
                      ),
                    ],
                  ),
                ],
              ),
      ),
    );
  }
}

class _ReportGroup extends StatelessWidget {
  const _ReportGroup({
    required this.heading,
    required this.entries,
    required this.tint,
  });

  final String heading;
  final List<_ReportEntry> entries;
  final Color tint;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 0, 0, 8),
          child: Text(
            heading,
            style: theme.textTheme.titleMedium?.copyWith(color: context.mutedColor),
          ),
        ),
        Card(
          clipBehavior: Clip.antiAlias,
          child: Column(
            children: <Widget>[
              for (int i = 0; i < entries.length; i++) ...<Widget>[
                if (i > 0) const Divider(indent: 68, height: 1),
                entries[i].withTint(tint),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

class _ReportEntry extends StatelessWidget {
  const _ReportEntry({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.route,
    this.tint,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final String route;

  /// Filled in by the group, so a row never has to know its own colour.
  final Color? tint;

  _ReportEntry withTint(Color colour) => _ReportEntry(
        icon: icon,
        title: title,
        subtitle: subtitle,
        route: route,
        tint: colour,
      );

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListTile(
      onTap: () => context.push(route),
      leading: IconTile(icon: icon, colour: tint ?? AppTheme.tileBlue, size: 40),
      title: Text(
        title,
        style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
      ),
      subtitle: Text(
        subtitle,
        style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
      ),
      trailing: Icon(Icons.chevron_right, color: context.mutedColor),
    );
  }
}
