import 'package:flutter/material.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';

/// One row on the Reports screen.
///
/// [key] is the name the backend's access policy knows the report by
/// (`services/access_policy.py: STANDARD_REPORTS`). It is what an admin
/// switches off for a colleague, and what the server refuses with a 403 when
/// they open it anyway, so the two lists must agree.
class ReportEntry {
  const ReportEntry({
    required this.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.route,
  });

  final String key;
  final IconData icon;
  final String title;
  final String subtitle;
  final String route;
}

class ReportSection {
  const ReportSection(this.heading, this.tint, this.entries);

  final String heading;
  final Color tint;
  final List<ReportEntry> entries;
}

/// The Reports screen, grouped by the question each report answers rather
/// than by Tally's menu structure. The same list is what an admin sees when
/// choosing which of these a colleague may open.
const List<ReportSection> reportCatalog = <ReportSection>[
  ReportSection('Outstanding', AppTheme.tileBlue, <ReportEntry>[
    ReportEntry(
      key: 'receivables',
      icon: Icons.call_received,
      title: 'Receivables',
      subtitle: 'Who owes me money, and for how long',
      route: '${Routes.outstanding}?kind=receivable',
    ),
    ReportEntry(
      key: 'payables',
      icon: Icons.call_made,
      title: 'Payables',
      subtitle: 'What I owe my suppliers',
      route: '${Routes.outstanding}?kind=payable',
    ),
    ReportEntry(
      key: 'sundry_debtors',
      icon: Icons.groups_outlined,
      title: 'Sundry Debtors',
      subtitle: 'Every party in the group, net of advances',
      route: '${Routes.groupOutstanding}?kind=receivable',
    ),
    ReportEntry(
      key: 'sundry_creditors',
      icon: Icons.local_shipping_outlined,
      title: 'Sundry Creditors',
      subtitle: 'Every party in the group, net of advances',
      route: '${Routes.groupOutstanding}?kind=payable',
    ),
    ReportEntry(
      key: 'ledgers',
      icon: Icons.account_balance_outlined,
      title: 'Ledgers',
      subtitle: 'Closing balances across all accounts',
      route: Routes.ledgers,
    ),
  ]),
  ReportSection('Transactions', AppTheme.tileViolet, <ReportEntry>[
    ReportEntry(
      key: 'daybook',
      icon: Icons.receipt_long_outlined,
      title: 'Day book',
      subtitle: 'Every voucher, day by day',
      route: Routes.daybook,
    ),
    ReportEntry(
      key: 'sales_register',
      icon: Icons.trending_up,
      title: 'Sales register',
      subtitle: 'Every sale, by month, customer and item',
      route: '${Routes.register}?kind=sales',
    ),
    ReportEntry(
      key: 'purchase_register',
      icon: Icons.trending_down,
      title: 'Purchase register',
      subtitle: 'Every purchase, by month, supplier and item',
      route: '${Routes.register}?kind=purchase',
    ),
  ]),
  ReportSection('Stock', AppTheme.tileAmber, <ReportEntry>[
    ReportEntry(
      key: 'stock_summary',
      icon: Icons.inventory_2_outlined,
      title: 'Stock summary',
      subtitle: 'What I hold and what it is worth',
      route: Routes.stock,
    ),
    ReportEntry(
      key: 'godown_summary',
      icon: Icons.warehouse_outlined,
      title: 'Godown summary',
      subtitle: 'Where my stock is kept, godown by godown',
      route: Routes.godowns,
    ),
    ReportEntry(
      key: 'running_low',
      icon: Icons.warning_amber_outlined,
      title: 'Running low',
      subtitle: 'Items at or below reorder level',
      route: '${Routes.stock}?only=low',
    ),
    ReportEntry(
      key: 'negative_stock',
      icon: Icons.error_outline,
      title: 'Negative stock',
      subtitle: 'Sold more than the books say you received',
      route: '${Routes.stock}?only=negative',
    ),
    ReportEntry(
      key: 'slow_movers',
      icon: Icons.hourglass_bottom,
      title: 'Slow movers',
      subtitle: 'Stock on hand that is not selling',
      route: Routes.slowMoving,
    ),
  ]),
];
