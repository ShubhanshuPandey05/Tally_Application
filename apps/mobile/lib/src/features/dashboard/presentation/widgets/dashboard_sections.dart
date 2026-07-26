import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/router.dart';
import '../../../../app/theme.dart';
import '../../../../core/model/figures.dart';
import '../../../../core/money/money.dart';
import '../../../../core/money/money_format.dart';
import '../../../../core/widgets/cards.dart';
import '../../../../core/widgets/trend_chart.dart';
import '../../domain/dashboard.dart';

/// The sales chart plus the month-on-month comparison.
class TrendSection extends StatelessWidget {
  const TrendSection({
    super.key,
    required this.title,
    required this.summary,
    required this.currency,
  });

  final String title;
  final TradeSummary summary;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return SectionCard(
      title: title,
      subtitle: 'Last 30 days',
      icon: Icons.show_chart,
      child: Column(
        children: <Widget>[
          TrendChart(points: summary.trend, currency: currency),
          const Divider(indent: 16, endIndent: 16),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: _MiniStat(
                    label: 'This month',
                    value: MoneyFormat.compact(summary.thisMonth),
                  ),
                ),
                Expanded(
                  child: _MiniStat(
                    label: 'Last month',
                    value: MoneyFormat.compact(summary.lastMonth),
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'Change',
                        style: theme.textTheme.labelSmall
                            ?.copyWith(color: context.mutedColor),
                      ),
                      const SizedBox(height: 4),
                      if (summary.changePct == null)
                        // No baseline is not a movement of zero. "--" is the
                        // only honest rendering.
                        Text('--', style: theme.textTheme.titleSmall)
                      else
                        ChangeChip(changePct: summary.changePct!),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _MiniStat extends StatelessWidget {
  const _MiniStat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(label,
            style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor)),
        const SizedBox(height: 4),
        Text(value, style: theme.textTheme.titleSmall),
      ],
    );
  }
}

/// "Who owes me money?" -- the single most-asked question in the brief.
class OutstandingSection extends StatelessWidget {
  const OutstandingSection({
    super.key,
    required this.title,
    required this.summary,
    required this.kindQuery,
  });

  final String title;
  final OutstandingSummary summary;
  final String kindQuery;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool anythingOverdue = !summary.overdue.isZero;

    return SectionCard(
      title: title,
      subtitle: '${summary.billCount} bills across ${summary.partyCount} parties',
      icon: Icons.account_balance_wallet_outlined,
      action: 'See all',
      onAction: () => context.push('${Routes.outstanding}?kind=$kindQuery'),
      child: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: _MiniStat(
                    label: 'Total',
                    value: MoneyFormat.compact(summary.total),
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text('Overdue',
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: context.mutedColor)),
                      const SizedBox(height: 4),
                      Text(
                        MoneyFormat.compact(summary.overdue),
                        style: theme.textTheme.titleSmall?.copyWith(
                          color: anythingOverdue ? context.negativeColor : null,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          if (anythingOverdue) ...<Widget>[
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: AgeingBar(ageing: summary.ageing, total: summary.total),
            ),
          ],
          if (summary.topParties.isNotEmpty) ...<Widget>[
            const SizedBox(height: 8),
            const Divider(indent: 16, endIndent: 16),
            for (final PartyTotal party in summary.topParties.take(4))
              AmountRow(
                title: party.name,
                amount: party.amount,
                subtitle: party.daysOverdue > 0
                    ? '${party.daysOverdue} days overdue'
                    : 'Not yet due',
              ),
          ],
        ],
      ),
    );
  }
}

/// Ageing as a single proportional bar.
///
/// A stacked bar answers "how bad is it?" in one look, where six numbers make
/// the reader do the arithmetic. The buckets are Tally's own, so the colours
/// only have to encode "older is worse".
class AgeingBar extends StatelessWidget {
  const AgeingBar({super.key, required this.ageing, required this.total});

  final Map<String, Money> ageing;
  final Money total;

  static const Map<String, Color> _colours = <String, Color>{
    'not_due': Color(0xFF12805C),
    '1_30': Color(0xFF7BA428),
    '31_60': Color(0xFFB86E00),
    '61_90': Color(0xFFD1541F),
    '91_180': Color(0xFFC4314B),
    '180_plus': Color(0xFF8B1E36),
  };

  @override
  Widget build(BuildContext context) {
    final double totalValue = total.amount.toDouble();
    if (totalValue <= 0) return const SizedBox.shrink();

    final List<Widget> segments = <Widget>[];
    for (final String bucket in ageingOrder) {
      final double value = ageing[bucket]?.amount.toDouble() ?? 0;
      if (value <= 0) continue;
      segments.add(
        Expanded(
          flex: (value / totalValue * 1000).round().clamp(1, 1000),
          child: Container(color: _colours[bucket]),
        ),
      );
    }
    if (segments.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: SizedBox(height: 8, child: Row(children: segments)),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 12,
          runSpacing: 4,
          children: <Widget>[
            for (final String bucket in ageingOrder)
              if ((ageing[bucket]?.amount.toDouble() ?? 0) > 0)
                _LegendDot(
                  colour: _colours[bucket]!,
                  label: ageingLabel(bucket),
                  value: MoneyFormat.compact(ageing[bucket]!),
                ),
          ],
        ),
      ],
    );
  }
}

class _LegendDot extends StatelessWidget {
  const _LegendDot({required this.colour, required this.label, required this.value});

  final Color colour;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: colour, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(
          '$label  $value',
          style: Theme.of(context)
              .textTheme
              .labelSmall
              ?.copyWith(color: context.mutedColor),
        ),
      ],
    );
  }
}

/// "Which products are running out?" and "what is my stock worth?"
class InventorySection extends StatelessWidget {
  const InventorySection({super.key, required this.summary});

  final InventorySummary summary;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return SectionCard(
      title: 'Inventory',
      subtitle: '${summary.itemCount} items',
      icon: Icons.inventory_2_outlined,
      action: 'See all',
      onAction: () => context.push(Routes.stock),
      child: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: _MiniStat(
                    label: 'Stock value',
                    value: MoneyFormat.compact(summary.value),
                  ),
                ),
                Expanded(
                  child: _CountStat(
                    label: 'Running low',
                    count: summary.lowStockCount,
                    colour: summary.lowStockCount > 0 ? context.cautionColor : null,
                    onTap: summary.lowStockCount > 0
                        ? () => context.push('${Routes.stock}?only=low')
                        : null,
                  ),
                ),
                Expanded(
                  child: _CountStat(
                    label: 'Negative',
                    count: summary.negativeStockCount,
                    colour: summary.negativeStockCount > 0 ? context.negativeColor : null,
                    onTap: summary.negativeStockCount > 0
                        ? () => context.push('${Routes.stock}?only=negative')
                        : null,
                  ),
                ),
              ],
            ),
          ),
          if (summary.lowStock.isNotEmpty) ...<Widget>[
            const SizedBox(height: 10),
            const Divider(indent: 16, endIndent: 16),
            for (final StockLine item in summary.lowStock.take(4))
              ListTile(
                dense: true,
                title: Text(item.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: item.reorderLevel == null
                    ? null
                    : Text('Reorder at ${MoneyFormat.quantity(item.reorderLevel!, item.unit)}'),
                trailing: Text(
                  MoneyFormat.quantity(item.quantity, item.unit),
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: context.cautionColor,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

class _CountStat extends StatelessWidget {
  const _CountStat({
    required this.label,
    required this.count,
    this.colour,
    this.onTap,
  });

  final String label;
  final int count;
  final Color? colour;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return InkWell(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(label,
              style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor)),
          const SizedBox(height: 4),
          Text('$count', style: theme.textTheme.titleSmall?.copyWith(color: colour)),
        ],
      ),
    );
  }
}

/// Cash and bank, with the accounts behind the total.
class FundsSection extends StatelessWidget {
  const FundsSection({super.key, required this.summary});

  final FundsSummary summary;

  @override
  Widget build(BuildContext context) {
    final List<BalanceLine> accounts = <BalanceLine>[
      ...summary.cashAccounts,
      ...summary.bankAccounts,
    ];

    return SectionCard(
      title: 'Cash & bank',
      subtitle: '${accounts.length} accounts',
      icon: Icons.savings_outlined,
      child: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: _MiniStat(
                    label: 'Cash in hand',
                    value: MoneyFormat.compact(summary.cash),
                  ),
                ),
                Expanded(
                  child: _MiniStat(
                    label: 'In bank',
                    value: MoneyFormat.compact(summary.bank),
                  ),
                ),
              ],
            ),
          ),
          if (accounts.isNotEmpty) ...<Widget>[
            const SizedBox(height: 8),
            const Divider(indent: 16, endIndent: 16),
            for (final BalanceLine account in accounts.take(6))
              AmountRow(title: account.name, amount: account.balance),
          ],
        ],
      ),
    );
  }
}

/// "What happened since yesterday?"
class ActivitySection extends StatelessWidget {
  const ActivitySection({super.key, required this.summary});

  final ActivitySummary summary;

  @override
  Widget build(BuildContext context) {
    if (summary.recent.isEmpty) {
      return SectionCard(
        title: 'Recent activity',
        icon: Icons.history,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: Text(
            'No vouchers in this period.',
            style: Theme.of(context)
                .textTheme
                .bodyMedium
                ?.copyWith(color: context.mutedColor),
          ),
        ),
      );
    }

    return SectionCard(
      title: 'Recent activity',
      subtitle: '${summary.voucherCount} vouchers in the period',
      icon: Icons.history,
      action: 'Day book',
      onAction: () => context.push(Routes.daybook),
      child: Column(
        children: <Widget>[
          for (final TransactionLine line in summary.recent.take(6))
            TransactionTile(line: line),
        ],
      ),
    );
  }
}

/// One voucher, rendered by family rather than by Tally's voucher-type name --
/// a shop can call its sales voucher anything, and the icon must still be right.
class TransactionTile extends StatelessWidget {
  const TransactionTile({super.key, required this.line});

  final TransactionLine line;

  static const Map<String, IconData> _icons = <String, IconData>{
    'sales': Icons.trending_up,
    'purchase': Icons.trending_down,
    'receipt': Icons.south_west,
    'payment': Icons.north_east,
    'credit_note': Icons.undo,
    'debit_note': Icons.redo,
    'journal': Icons.swap_horiz,
    'contra': Icons.sync_alt,
    'stock_journal': Icons.inventory_2_outlined,
  };

  Color _colour(BuildContext context) => switch (line.kind) {
        'sales' || 'receipt' => context.positiveColor,
        'purchase' || 'payment' => context.negativeColor,
        _ => Theme.of(context).colorScheme.primary,
      };

  @override
  Widget build(BuildContext context) {
    final Color colour = _colour(context);

    return AmountRow(
      leading: Container(
        width: 34,
        height: 34,
        decoration: BoxDecoration(
          color: colour.withOpacity(0.10),
          borderRadius: BorderRadius.circular(9),
        ),
        child: Icon(_icons[line.kind] ?? Icons.receipt_long, size: 17, color: colour),
      ),
      title: line.party ?? line.voucherType ?? 'Voucher',
      subtitle: <String?>[
        line.voucherType,
        line.voucherNumber == null ? null : '#${line.voucherNumber}',
        '${line.date.day}/${line.date.month}',
      ].whereType<String>().join(' · '),
      amount: line.amount,
    );
  }
}
