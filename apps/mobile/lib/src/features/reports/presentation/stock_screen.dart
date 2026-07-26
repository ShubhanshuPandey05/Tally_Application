import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/report_scaffold.dart';

/// Stock on hand, by value.
///
/// The `only` filter is passed to the server rather than applied here: a shop
/// can carry thousands of items, and shipping the whole list to a phone so it
/// can throw most of it away is exactly the kind of thing that makes a mobile
/// app feel slow on a train.
class StockScreen extends ConsumerStatefulWidget {
  const StockScreen({super.key, this.only});

  final String? only;

  @override
  ConsumerState<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends ConsumerState<StockScreen> {
  late String? _only = widget.only;
  String _search = '';

  @override
  Widget build(BuildContext context) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see stock.',
        ),
      );
    }

    final StockArgs args = (companyId: companyId, only: _only);
    final AsyncValue<Fresh<StockReport>> state = ref.watch(stockProvider(args));

    return ReportScaffold<StockReport>(
      title: switch (_only) {
        'low' => 'Running low',
        'negative' => 'Negative stock',
        _ => 'Stock summary',
      },
      state: state,
      onRefresh: () => refreshReport<StockReport>(
        ref,
        stockProvider(args),
        (ReportsRepository repository) =>
            repository.stock(companyId, only: _only, mode: FetchMode.live),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(104),
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: TextField(
                onChanged: (String value) => setState(() => _search = value),
                decoration: const InputDecoration(
                  hintText: 'Search items',
                  prefixIcon: Icon(Icons.search),
                  isDense: true,
                ),
              ),
            ),
            SizedBox(
              height: 48,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                children: <Widget>[
                  for (final (String? value, String label) filter in <(String?, String)>[
                    (null, 'All items'),
                    ('low', 'Running low'),
                    ('negative', 'Negative'),
                  ])
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(filter.$2),
                        selected: _only == filter.$1,
                        onSelected: (_) => setState(() => _only = filter.$1),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: _only == null ? Icons.inventory_2_outlined : Icons.check_circle_outline,
        title: switch (_only) {
          'low' => 'Nothing running low',
          'negative' => 'No negative stock',
          _ => 'No stock items',
        },
        message: switch (_only) {
          'low' => 'Every item is above its reorder level.',
          'negative' =>
            'Your books balance: nothing has been billed out that was never received.',
          _ => 'TallyPrime has no stock items for this company.',
        },
      ),
      builder: (BuildContext context, StockReport report) {
        final List<StockLine> items = _search.trim().isEmpty
            ? report.items
            : report.items
                .where((StockLine item) =>
                    item.name.toLowerCase().contains(_search.trim().toLowerCase()))
                .toList(growable: false);

        if (report.items.isEmpty) return const <Widget>[];

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: _Stat(
                        label: 'Stock value',
                        value: MoneyFormat.full(report.value),
                        emphasis: true,
                      ),
                    ),
                    Expanded(
                      child: _Stat(label: 'Items', value: '${report.itemCount}'),
                    ),
                  ],
                ),
              ),
            ),
          ),
          if (items.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 48),
              child: EmptyState(
                icon: Icons.search_off,
                title: 'No matches',
                message: 'No item names contain that text.',
              ),
            ),
          for (final StockLine item in items)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: _StockCard(item: item),
            ),
        ];
      },
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.emphasis = false});

  final String label;
  final String value;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(label,
            style: theme.textTheme.labelMedium?.copyWith(color: context.mutedColor)),
        const SizedBox(height: 4),
        Text(
          value,
          style: emphasis ? theme.textTheme.titleLarge : theme.textTheme.titleMedium,
        ),
      ],
    );
  }
}

class _StockCard extends StatelessWidget {
  const _StockCard({required this.item});

  final StockLine item;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color? flag = item.isNegative
        ? context.negativeColor
        : item.isBelowReorder
            ? context.cautionColor
            : null;

    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    item.name,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyLarge?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Text(
                  MoneyFormat.full(item.value),
                  style: theme.textTheme.bodyLarge?.merge(AppTheme.amount),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Row(
              children: <Widget>[
                Text(
                  MoneyFormat.quantity(item.quantity, item.unit),
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: flag,
                    fontWeight: flag == null ? null : FontWeight.w600,
                  ),
                ),
                if (item.rate != null) ...<Widget>[
                  Text(' · ',
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: context.mutedColor)),
                  Text(
                    '${MoneyFormat.full(item.rate!)} each',
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: context.mutedColor),
                  ),
                ],
                const Spacer(),
                if (item.isNegative)
                  _Flag(label: 'Negative', colour: context.negativeColor)
                else if (item.isBelowReorder)
                  _Flag(label: 'Reorder', colour: context.cautionColor),
              ],
            ),
            if (item.group != null && item.group!.isNotEmpty) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                item.group!,
                style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _Flag extends StatelessWidget {
  const _Flag({required this.label, required this.colour});

  final String label;
  final Color colour;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: colour.withOpacity(0.10),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        label,
        style: Theme.of(context)
            .textTheme
            .labelSmall
            ?.copyWith(color: colour, fontWeight: FontWeight.w700),
      ),
    );
  }
}
