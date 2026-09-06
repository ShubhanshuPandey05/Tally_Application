import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/router.dart';
import '../../../../app/theme.dart';
import '../../../../core/model/figures.dart';
import '../../../../core/money/money.dart';
import '../../../../core/money/money_format.dart';
import '../../../../core/widgets/cards.dart';
import '../../../../core/widgets/charts.dart';
import '../../domain/dashboard.dart';

/// The dashboard's cards.
///
/// Each one is built the same way and in the same order: the picture first,
/// then the figures that picture is made of, then the rows behind those
/// figures. An owner who only glances gets the shape; an owner who is checking
/// something gets the names and the amounts without leaving the screen.

/// Trade over a window: the totals, how they moved, and who they were with.
///
/// Figures rather than a plotted series. A shop owner reading this on a phone
/// wants the four numbers and the names behind them; the day-by-day shape of
/// the window is a report, and the register is where it lives.
class TradeSection extends StatelessWidget {
  const TradeSection({
    super.key,
    required this.title,
    required this.summary,
    required this.currency,
    this.periodLabel,
    this.tint,
  });

  final String title;
  final TradeSummary summary;

  /// Still needed: the busiest-day figure arrives as a bare double, so it has
  /// to be given its currency back before it can be shown.
  final String currency;

  /// Set when the dashboard is scoped to a period. The figures then describe
  /// that window, so they must not keep calling themselves "this month".
  final String? periodLabel;

  final Color? tint;

  @override
  Widget build(BuildContext context) {
    final TradePeriod? period = summary.period;

    final Money current = period?.total ?? summary.thisMonth;
    final Money? baseline = period == null ? summary.lastMonth : period.previousTotal;
    final double? changePct = period == null ? summary.changePct : period.changePct;

    final List<TrendPoint> points = summary.trend;

    // The busiest day in the window. A headline figure divided by its days is
    // an average; this is the ceiling, and an owner plans stock against the
    // ceiling.
    final double busiest = points.fold<double>(
      0,
      (double highest, TrendPoint p) => math.max(highest, p.value),
    );

    return SectionCard(
      title: title,
      subtitle: periodLabel ?? 'Last ${points.length} days',
      icon: Icons.show_chart,
      tint: tint,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const SizedBox(height: 2),
          StatStrip(
            stats: <Stat>[
              Stat(
                label: period == null ? 'This month' : 'This period',
                value: MoneyFormat.compact(current),
              ),
              Stat(
                label: period == null ? 'Last month' : 'Previous',
                // A baseline outside the window that was read is unknown, not
                // zero -- and a zero here would read as a total collapse in
                // trade that never happened.
                value: baseline == null ? '--' : MoneyFormat.compact(baseline),
              ),
              Stat(
                label: 'Change',
                // No baseline is not a movement of zero, and an empty cell
                // where a percentage usually sits reads as "no change". "--"
                // is the only honest rendering of an unanswerable question.
                value: changePct == null ? '--' : '',
                trailing: changePct == null
                    ? null
                    : ChangeChip(changePct: changePct, compact: true),
              ),
              Stat(
                label: 'Busiest day',
                value: busiest <= 0
                    ? '--'
                    : MoneyFormat.compactValue(busiest, currency),
              ),
            ],
          ),
          if (summary.topParties.isNotEmpty) ...<Widget>[
            const SizedBox(height: 9),
            _SubHeading(
              label: '$title by party',
              trailing: '${summary.topParties.length} shown',
            ),
            ...rankedRows(
              entries: <RankedEntry>[
                for (final PartyTotal party in summary.topParties)
                  RankedEntry(
                    name: party.name,
                    amount: party.amount,
                    subtitle: party.voucherCount > 0
                        ? '${party.voucherCount} vch'
                        : null,
                    // "Who is my biggest customer" is nearly always followed by
                    // "and what have they been doing", which is their ledger.
                    onTap: () => context.push(
                      '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(party.name)}',
                    ),
                  ),
              ],
              whole: current,
              colour: tint ?? AppTheme.tileBlue,
            ),
          ],
        ],
      ),
    );
  }
}

/// "Who owes me money?" -- the single most-asked question in the brief.
///
/// The ring is the point of this card. A total and an overdue figure side by
/// side leave the reader working out the ratio; the ring states it, and the
/// legend keeps every bucket's exact amount next to its wedge.
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
    final bool anythingOverdue = !summary.overdue.isZero;

    return SectionCard(
      title: title,
      subtitle: '${summary.billCount} bills · ${summary.partyCount} parties',
      icon: Icons.account_balance_wallet_outlined,
      tint: anythingOverdue ? AppTheme.tileRose : AppTheme.tileGreen,
      action: 'See all',
      onAction: () => context.push('${Routes.outstanding}?kind=$kindQuery'),
      child: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 2, 16, 8),
            child: DonutBreakdown(
              centreLabel: 'total',
              centreValue: MoneyFormat.compact(summary.total),
              slices: <DonutSlice>[
                for (final String bucket in ageingOrder)
                  if ((summary.ageing[bucket]?.amount.toDouble() ?? 0) > 0)
                    DonutSlice(
                      label: ageingLabel(bucket),
                      value: summary.ageing[bucket]!.amount.toDouble(),
                      colour: ageingColours[bucket]!,
                      display: MoneyFormat.compact(summary.ageing[bucket]!),
                    ),
              ],
            ),
          ),
          StatStrip(
            stats: <Stat>[
              Stat(label: 'Total', value: MoneyFormat.compact(summary.total)),
              Stat(
                label: 'Overdue',
                value: MoneyFormat.compact(summary.overdue),
                colour: anythingOverdue ? context.negativeColor : null,
              ),
              Stat(
                label: 'Overdue share',
                value: anythingOverdue
                    ? '${(summary.overdueShare * 100).round()}%'
                    : 'none',
                colour: anythingOverdue ? context.negativeColor : null,
              ),
            ],
          ),
          if (summary.topParties.isNotEmpty) ...<Widget>[
            const SizedBox(height: 9),
            const _SubHeading(label: 'Largest balances'),
            ...rankedRows(
              entries: <RankedEntry>[
                for (final PartyTotal party in summary.topParties.take(5))
                  RankedEntry(
                    name: party.name,
                    amount: party.amount,
                    subtitle: party.daysOverdue > 0
                        ? '${party.daysOverdue}d late'
                        : 'not due',
                    // Overdue parties borrow the ageing scale's own red, so a
                    // late row here and a late wedge above are the same colour.
                    colour: party.daysOverdue > 0 ? ageingColours['91_180'] : null,
                    onTap: () => context.push(
                      '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(party.name)}',
                    ),
                  ),
              ],
              whole: summary.total,
              colour: AppTheme.tileBlue,
            ),
          ],
        ],
      ),
    );
  }
}

/// The ageing scale. Older is worse, and the ramp says so without a legend
/// having to explain the ordering.
///
/// Public because the ring on the dashboard and the stacked bar on the reports
/// screens must colour the same bucket the same way -- two scales for one set
/// of buckets is how a reader concludes they are looking at different figures.
const Map<String, Color> ageingColours = <String, Color>{
  'not_due': Color(0xFF12805C),
  '1_30': Color(0xFF7BA428),
  '31_60': Color(0xFFB86E00),
  '61_90': Color(0xFFD1541F),
  '91_180': Color(0xFFC4314B),
  '180_plus': Color(0xFF8B1E36),
};

/// Ageing as a single proportional bar.
///
/// The reports' form of the same figures. A ring needs a card's width and a
/// legend; a report screen puts this above a list of two hundred bills, where
/// eight pixels of height is all the summary can afford.
class AgeingBar extends StatelessWidget {
  const AgeingBar({super.key, required this.ageing, required this.total});

  final Map<String, Money> ageing;
  final Money total;

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
          child: Container(color: ageingColours[bucket]),
        ),
      );
    }
    if (segments.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: SizedBox(height: 6, child: Row(children: segments)),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 12,
          runSpacing: 4,
          children: <Widget>[
            for (final String bucket in ageingOrder)
              if ((ageing[bucket]?.amount.toDouble() ?? 0) > 0)
                _LegendDot(
                  colour: ageingColours[bucket]!,
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
          style:
              Theme.of(context).textTheme.labelSmall?.copyWith(color: context.mutedColor),
        ),
      ],
    );
  }
}

/// Marks a section whose figures could not be rewound to the selected date.
///
/// TallyPrime evaluates a ledger's closing balance and a stock item's closing
/// value against the current date, and neither read accepts a date to evaluate
/// against. So on a historical dashboard these two sections are still showing
/// today while everything around them shows the chosen date. Saying so is the
/// difference between a screen an owner can reconcile and one that quietly
/// mixes two dates into the same glance.
class CurrentOnlyNote extends StatelessWidget {
  const CurrentOnlyNote({super.key});

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.info_outline, size: 14, color: context.cautionColor),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              "Today's figures. Tally reports these balances as they stand now, "
              'not as of the date above.',
              style: theme.textTheme.labelSmall?.copyWith(color: context.cautionColor),
            ),
          ),
        ],
      ),
    );
  }
}

/// "Which products are running out?" and "what is my stock worth?"
///
/// The rows are drawn as cover against the reorder level rather than as bare
/// quantities: "40 pcs" means nothing without knowing the item reorders at 200,
/// and a bar that is one fifth full says it before the numbers are read.
class InventorySection extends StatelessWidget {
  const InventorySection({
    super.key,
    required this.summary,
    this.alwaysCurrent = false,
  });

  final InventorySummary summary;

  /// Set when the dashboard is showing a past date, to declare that this
  /// section is not showing it. See [CurrentOnlyNote].
  final bool alwaysCurrent;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return SectionCard(
      title: 'Stock in hand',
      subtitle: '${summary.itemCount} items',
      icon: Icons.inventory_2_outlined,
      tint: AppTheme.tileAmber,
      action: 'See all',
      onAction: () => context.push(Routes.stock),
      child: Column(
        children: <Widget>[
          if (alwaysCurrent) const CurrentOnlyNote(),
          StatStrip(
            stats: <Stat>[
              Stat(label: 'Stock value', value: MoneyFormat.compact(summary.value)),
              Stat(
                label: 'Running low',
                value: '${summary.lowStockCount}',
                colour: summary.lowStockCount > 0 ? context.cautionColor : null,
                onTap: summary.lowStockCount > 0
                    ? () => context.push('${Routes.stock}?only=low')
                    : null,
              ),
              Stat(
                label: 'Negative',
                value: '${summary.negativeStockCount}',
                colour: summary.negativeStockCount > 0 ? context.negativeColor : null,
                onTap: summary.negativeStockCount > 0
                    ? () => context.push('${Routes.stock}?only=negative')
                    : null,
              ),
              Stat(
                label: 'Healthy',
                value:
                    '${math.max(0, summary.itemCount - summary.lowStockCount - summary.negativeStockCount)}',
              ),
            ],
          ),
          if (summary.lowStock.isNotEmpty) ...<Widget>[
            const SizedBox(height: 9),
            const _SubHeading(label: 'Cover against reorder level'),
            for (int i = 0; i < math.min(5, summary.lowStock.length); i++)
              _StockCoverRow(rank: i + 1, item: summary.lowStock[i]),
          ] else if (summary.negativeStock.isNotEmpty) ...<Widget>[
            const SizedBox(height: 9),
            const _SubHeading(label: 'Negative stock'),
            for (final StockLine item in summary.negativeStock.take(5))
              ListTile(
                dense: true,
                onTap: () => context.push(
                  '${Routes.stockMovement}?item=${Uri.encodeQueryComponent(item.name)}',
                ),
                title: Text(item.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                trailing: Text(
                  MoneyFormat.quantity(item.quantity, item.unit),
                  style: theme.textTheme.bodyMedium?.merge(AppTheme.amount).copyWith(
                        color: context.negativeColor,
                      ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

/// One low-stock item, drawn as how far its quantity reaches towards the level
/// it should be reordered at.
class _StockCoverRow extends StatelessWidget {
  const _StockCoverRow({required this.rank, required this.item});

  final int rank;
  final StockLine item;

  @override
  Widget build(BuildContext context) {
    final double? level = item.reorderLevel;
    // With no reorder level there is nothing to measure against. An empty bar
    // would read as "out of stock", so such an item gets a full-width bar in
    // the quiet colour and its quantity as the fact.
    final double fraction =
        level == null || level <= 0 ? 1 : (item.quantity / level).clamp(0.0, 1.0);

    return ShareRow(
      rank: rank,
      title: item.name,
      value: MoneyFormat.quantity(item.quantity, item.unit),
      fraction: fraction,
      colour: item.quantity <= 0 ? context.negativeColor : context.cautionColor,
      subtitle: level == null
          ? 'no level set'
          : 'of ${MoneyFormat.quantity(level, item.unit)}',
      // The item, not the filtered list this row came from. "Why is this low"
      // is answered by what has been moving it, not by the other low items.
      onTap: () => context.push(
        '${Routes.stockMovement}?item=${Uri.encodeQueryComponent(item.name)}',
      ),
    );
  }
}

/// Cash and bank, split by where the money actually sits.
class FundsSection extends StatelessWidget {
  const FundsSection({super.key, required this.summary, this.alwaysCurrent = false});

  final FundsSummary summary;

  /// Set when the dashboard is showing a past date, to declare that this
  /// section is not showing it. See [CurrentOnlyNote].
  final bool alwaysCurrent;

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
      tint: AppTheme.tileGreen,
      child: Column(
        children: <Widget>[
          if (alwaysCurrent) const CurrentOnlyNote(),
          if (!summary.cash.isZero || !summary.bank.isZero)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 2, 16, 8),
              child: DonutBreakdown(
                centreLabel: 'available',
                centreValue: MoneyFormat.compact(summary.total),
                slices: <DonutSlice>[
                  DonutSlice(
                    label: 'Cash in hand',
                    value: summary.cash.amount.toDouble(),
                    colour: AppTheme.tileGreen,
                    display: MoneyFormat.compact(summary.cash),
                  ),
                  DonutSlice(
                    label: 'In bank',
                    value: summary.bank.amount.toDouble(),
                    colour: AppTheme.tileBlue,
                    display: MoneyFormat.compact(summary.bank),
                  ),
                ],
              ),
            ),
          if (accounts.isNotEmpty) ...<Widget>[
            const _SubHeading(label: 'By account'),
            ...rankedRows(
              entries: <RankedEntry>[
                for (final BalanceLine account in accounts.take(6))
                  RankedEntry(
                    name: account.name,
                    amount: account.balance,
                    onTap: () => context.push(
                      '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(account.name)}',
                    ),
                  ),
              ],
              whole: summary.total,
              colour: AppTheme.tileGreen,
            ),
          ],
        ],
      ),
    );
  }
}

/// A ranked list on its own card: top customers, top products.
class RankedSection extends StatelessWidget {
  const RankedSection({
    super.key,
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.entries,
    required this.whole,
    this.tint,
    this.action,
    this.onAction,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final List<RankedEntry> entries;

  /// The total these entries are a part of, for the share percentages. Null
  /// when the whole is unknown -- an entry's share of an unknown total is not
  /// 100%, so the column simply goes away.
  final Money? whole;

  final Color? tint;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      title: title,
      subtitle: subtitle,
      icon: icon,
      tint: tint,
      action: action,
      onAction: onAction,
      child: Column(
        children: rankedRows(
          entries: entries,
          whole: whole,
          colour: tint ?? AppTheme.tileBlue,
        ),
      ),
    );
  }
}

/// One row's worth of ranked-list input.
class RankedEntry {
  const RankedEntry({
    required this.name,
    required this.amount,
    this.subtitle,
    this.colour,
    this.onTap,
  });

  final String name;
  final Money amount;
  final String? subtitle;
  final Color? colour;
  final VoidCallback? onTap;
}

/// Turns entries into bars measured against the largest of them.
///
/// Against the largest rather than against [whole], because a list of five
/// customers out of two hundred would otherwise be five slivers -- the bar is
/// there to compare the rows with each other, and the share percentage beside
/// it is what relates them to the total.
List<Widget> rankedRows({
  required List<RankedEntry> entries,
  required Color colour,
  Money? whole,
}) {
  if (entries.isEmpty) return const <Widget>[];

  final double largest = entries.fold<double>(
    0,
    (double highest, RankedEntry e) => math.max(highest, e.amount.amount.toDouble()),
  );
  final double total = whole?.amount.toDouble() ?? 0;

  return <Widget>[
    for (int i = 0; i < entries.length; i++)
      ShareRow(
        rank: i + 1,
        title: entries[i].name,
        value: MoneyFormat.compact(entries[i].amount),
        fraction:
            largest <= 0 ? 0 : entries[i].amount.amount.toDouble() / largest,
        share: total <= 0
            ? null
            : '${(entries[i].amount.amount.toDouble() / total * 100).round()}%',
        subtitle: entries[i].subtitle,
        colour: entries[i].colour ?? colour,
        onTap: entries[i].onTap,
      ),
  ];
}

/// A quiet label between a card's chart and the rows that follow it.
class _SubHeading extends StatelessWidget {
  const _SubHeading({required this.label, this.trailing});

  final String label;
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 2),
      child: Row(
        children: <Widget>[
          Text(
            label.toUpperCase(),
            style: theme.textTheme.labelSmall?.copyWith(
              color: context.mutedColor,
              letterSpacing: 0.7,
              fontWeight: FontWeight.w700,
            ),
          ),
          const Spacer(),
          if (trailing != null)
            Text(
              trailing!,
              style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
            ),
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
        tint: AppTheme.tileViolet,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: Text(
            'No vouchers in this period.',
            style:
                Theme.of(context).textTheme.bodyMedium?.copyWith(color: context.mutedColor),
          ),
        ),
      );
    }

    // What kinds of voucher the period is made of. Six recent rows show what
    // happened last; this shows what the period was mostly *about*.
    final Map<String, int> byKind = <String, int>{};
    for (final TransactionLine line in summary.recent) {
      byKind[line.kind] = (byKind[line.kind] ?? 0) + 1;
    }

    return SectionCard(
      title: 'Recent activity',
      subtitle: '${summary.voucherCount} vouchers in the period',
      icon: Icons.history,
      tint: AppTheme.tileViolet,
      action: 'Day book',
      onAction: () => context.push(Routes.daybook),
      child: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Wrap(
              spacing: 5,
              runSpacing: 5,
              children: <Widget>[
                for (final MapEntry<String, int> kind in byKind.entries)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: context.surfaceColor,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      '${TransactionTile.kindLabel(kind.key)} ${kind.value}',
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                            color: context.mutedColor,
                            fontWeight: FontWeight.w600,
                          ),
                    ),
                  ),
              ],
            ),
          ),
          const Divider(indent: 16, endIndent: 16),
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

  /// The family's own name, for a chip that has no room for an icon and a word.
  static String kindLabel(String kind) => switch (kind) {
        'sales' => 'Sales',
        'purchase' => 'Purchase',
        'receipt' => 'Receipt',
        'payment' => 'Payment',
        'credit_note' => 'Credit note',
        'debit_note' => 'Debit note',
        'journal' => 'Journal',
        'contra' => 'Contra',
        'stock_journal' => 'Stock journal',
        _ => 'Other',
      };

  Color _colour(BuildContext context) => switch (line.kind) {
        'sales' || 'receipt' => context.positiveColor,
        'purchase' || 'payment' => context.negativeColor,
        _ => Theme.of(context).colorScheme.primary,
      };

  @override
  Widget build(BuildContext context) {
    final Color colour = _colour(context);

    // Every voucher row in the product opens the same detail screen. A row
    // whose backend did not send an identity simply does not tap -- an inert
    // tap that looks live is worse than a row that plainly is not one.
    final String? link = voucherLink(line);

    return AmountRow(
      onTap: link == null ? null : () => context.push(link),
      leading: Container(
        width: 28,
        height: 28,
        decoration: BoxDecoration(
          color: colour.withOpacity(0.10),
          borderRadius: BorderRadius.circular(9),
        ),
        child: Icon(_icons[line.kind] ?? Icons.receipt_long, size: 15, color: colour),
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
