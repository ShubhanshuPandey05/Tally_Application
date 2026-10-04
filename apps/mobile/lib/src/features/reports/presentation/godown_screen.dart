import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/report_scaffold.dart';

/// Stock by the godown it sits in -- Tally's Godown Summary.
///
/// Laid out the way Tally lays it out, because the person reading it has read
/// that screen for years: each godown as a heading with its own total, and the
/// items it holds underneath, both in name order. The items sit behind the
/// arrow, as bills do on the outstanding report, so a company with a dozen
/// godowns opens on a dozen totals rather than on a scroll of every item.
/// There is no period filter.
/// Tally reports these balances as they stand now, and a date picker here
/// would promise a figure as of a day that nothing behind it can give.
class GodownScreen extends ConsumerStatefulWidget {
  const GodownScreen({super.key});

  @override
  ConsumerState<GodownScreen> createState() => _GodownScreenState();
}

class _GodownScreenState extends ConsumerState<GodownScreen> {
  String _search = '';
  final Set<String> _expanded = <String>{};

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

    final AsyncValue<Fresh<GodownReport>> state =
        ref.watch(godownsProvider(companyId));

    return ReportScaffold<GodownReport>(
      title: 'Godown summary',
      subtitle: 'Closing stock, as it stands now',
      state: state,
      onRefresh: () => refreshReport<GodownReport>(
        ref,
        godownsProvider(companyId),
        (ReportsRepository repository) =>
            repository.godowns(companyId, mode: FetchMode.live),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(56),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
          child: TextField(
            onChanged: (String value) => setState(() => _search = value),
            decoration: const InputDecoration(
              hintText: 'Search godowns and items',
              prefixIcon: Icon(Icons.search),
              isDense: true,
            ),
          ),
        ),
      ),
      emptyBuilder: (BuildContext context) => const EmptyState(
        icon: Icons.warehouse_outlined,
        title: 'No stock in any godown',
        message: 'TallyPrime has no closing stock recorded against a godown '
            'for this company.',
      ),
      builder: (BuildContext context, GodownReport report) {
        if (report.godowns.isEmpty) return const <Widget>[];

        final List<GodownHolding> shown = _matching(report.godowns);
        // A search is a request to see the item, so what it finds is opened:
        // a match hidden behind a closed card looks like no match at all.
        final bool searching = _search.trim().isNotEmpty;
        final bool allExpanded = report.godowns
            .every((GodownHolding godown) => _expanded.contains(godown.name));

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: _Stat(
                        label: 'Stock value',
                        value: MoneyFormat.full(report.value),
                        emphasis: true,
                      ),
                    ),
                    _Stat(label: 'Godowns', value: '${report.godowns.length}'),
                    const SizedBox(width: 6),
                    IconButton(
                      tooltip: allExpanded ? 'Collapse all' : 'Expand all',
                      onPressed: () => setState(() {
                        if (allExpanded) {
                          _expanded.clear();
                        } else {
                          _expanded.addAll(
                            report.godowns
                                .map((GodownHolding godown) => godown.name),
                          );
                        }
                      }),
                      icon: Icon(
                          allExpanded ? Icons.unfold_less : Icons.unfold_more),
                    ),
                  ],
                ),
              ),
            ),
          ),
          if (shown.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 48),
              child: EmptyState(
                icon: Icons.search_off,
                title: 'No matches',
                message: 'No godown or item name contains that text.',
              ),
            ),
          for (final GodownHolding godown in shown)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: _GodownCard(
                godown: godown,
                expanded: searching || _expanded.contains(godown.name),
                onToggle: () => setState(() {
                  if (!_expanded.remove(godown.name)) {
                    _expanded.add(godown.name);
                  }
                }),
              ),
            ),
        ];
      },
    );
  }

  /// A godown whose own name matches is shown whole; otherwise only the items
  /// in it that match. Its heading keeps the godown's real total either way --
  /// a total that shrank with the search box would read as the godown's worth.
  List<GodownHolding> _matching(List<GodownHolding> godowns) {
    final String needle = _search.trim().toLowerCase();
    if (needle.isEmpty) return godowns;

    final List<GodownHolding> shown = <GodownHolding>[];
    for (final GodownHolding godown in godowns) {
      if (godown.name.toLowerCase().contains(needle)) {
        shown.add(godown);
        continue;
      }
      final List<StockLine> items = godown.items
          .where((StockLine item) => item.name.toLowerCase().contains(needle))
          .toList(growable: false);
      if (items.isEmpty) continue;
      shown.add(
        GodownHolding(
          name: godown.name,
          value: godown.value,
          items: items,
          quantity: godown.quantity,
          unit: godown.unit,
          rate: godown.rate,
        ),
      );
    }
    return shown;
  }
}

class _Stat extends StatelessWidget {
  const _Stat(
      {required this.label, required this.value, this.emphasis = false});

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
            style: theme.textTheme.labelMedium
                ?.copyWith(color: context.mutedColor)),
        const SizedBox(height: 4),
        Text(
          value,
          style: emphasis
              ? theme.textTheme.titleLarge
              : theme.textTheme.titleMedium,
        ),
      ],
    );
  }
}

/// "125 Mtr · ₹36.00 each", or just the quantity where there is no rate.
String _quantityAndRate(double quantity, String? unit, Money? rate) {
  final String held = MoneyFormat.quantity(quantity, unit);
  return rate == null ? held : '$held · ${MoneyFormat.full(rate)} each';
}

String _itemCount(int count) => count == 1 ? '1 item' : '$count items';

class _GodownCard extends StatelessWidget {
  const _GodownCard({
    required this.godown,
    required this.expanded,
    required this.onToggle,
  });

  final GodownHolding godown;
  final bool expanded;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final double? quantity = godown.quantity;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        children: <Widget>[
          // Two targets, as on the outstanding report: the arrow lists the
          // items right here, and the card opens a page holding only them.
          InkWell(
            onTap: () => context.push(
              '${Routes.godownItems}?godown=${Uri.encodeQueryComponent(godown.name)}',
            ),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(14, 10, 4, 10),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          godown.name,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodyLarge?.copyWith(
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          // The count always, since it is what the arrow opens.
                          quantity == null
                              ? _itemCount(godown.items.length)
                              : '${_itemCount(godown.items.length)} · '
                                  '${_quantityAndRate(quantity, godown.unit, godown.rate)}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    MoneyFormat.full(godown.value),
                    style: theme.textTheme.bodyLarge
                        ?.merge(AppTheme.amount)
                        .copyWith(fontWeight: FontWeight.w700),
                  ),
                  IconButton(
                    tooltip: expanded ? 'Hide items' : 'Show items',
                    onPressed: onToggle,
                    icon: AnimatedRotation(
                      turns: expanded ? 0.5 : 0,
                      duration: const Duration(milliseconds: 200),
                      child: const Icon(Icons.expand_more),
                    ),
                  ),
                ],
              ),
            ),
          ),
          AnimatedSize(
            duration: const Duration(milliseconds: 200),
            alignment: Alignment.topCenter,
            child: expanded
                ? Column(
                    children: <Widget>[
                      for (final StockLine item in godown.items) ...<Widget>[
                        const Divider(height: 1, indent: 14, endIndent: 14),
                        _ItemRow(item: item),
                      ],
                    ],
                  )
                : const SizedBox(width: double.infinity),
          ),
        ],
      ),
    );
  }
}

class _ItemRow extends StatelessWidget {
  const _ItemRow({required this.item});

  final StockLine item;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return InkWell(
      onTap: () => context.push(
        '${Routes.stockMovement}?item=${Uri.encodeQueryComponent(item.name)}',
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 14, 8),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    item.name,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    _quantityAndRate(item.quantity, item.unit, item.rate),
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: item.isNegative
                          ? context.negativeColor
                          : context.mutedColor,
                      fontWeight: item.isNegative ? FontWeight.w600 : null,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            // Negative stock has no value to show: it is goods the books say
            // were never received. A dash, not ₹0.00, which would be a figure.
            Text(
              item.isNegative && item.value.isZero
                  ? '--'
                  : MoneyFormat.full(item.value),
              style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
            ),
          ],
        ),
      ),
    );
  }
}

/// One godown on a page of its own: what it holds, item by item.
///
/// Read from the same report as the summary rather than asked for separately,
/// so the page and the card that opened it cannot show different figures.
class GodownItemsScreen extends ConsumerStatefulWidget {
  const GodownItemsScreen({super.key, required this.godown});

  final String godown;

  @override
  ConsumerState<GodownItemsScreen> createState() => _GodownItemsScreenState();
}

class _GodownItemsScreenState extends ConsumerState<GodownItemsScreen> {
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

    final AsyncValue<Fresh<GodownReport>> state =
        ref.watch(godownsProvider(companyId));

    return ReportScaffold<GodownReport>(
      title: widget.godown,
      subtitle: 'Closing stock, as it stands now',
      state: state,
      onRefresh: () => refreshReport<GodownReport>(
        ref,
        godownsProvider(companyId),
        (ReportsRepository repository) =>
            repository.godowns(companyId, mode: FetchMode.live),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(56),
        child: Padding(
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
      ),
      emptyBuilder: (BuildContext context) => const EmptyState(
        icon: Icons.warehouse_outlined,
        title: 'Nothing in this godown',
        message: 'TallyPrime has no closing stock recorded against it.',
      ),
      builder: (BuildContext context, GodownReport report) {
        final GodownHolding? godown = report.godowns
            .where((GodownHolding g) => g.name == widget.godown)
            .firstOrNull;
        if (godown == null || godown.items.isEmpty) return const <Widget>[];

        final String needle = _search.trim().toLowerCase();
        final List<StockLine> items = needle.isEmpty
            ? godown.items
            : godown.items
                .where((StockLine item) => item.name.toLowerCase().contains(needle))
                .toList(growable: false);
        final double? quantity = godown.quantity;

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: _Stat(
                        label: 'Stock value',
                        value: MoneyFormat.full(godown.value),
                        emphasis: true,
                      ),
                    ),
                    _Stat(
                      label: quantity == null ? 'Items' : 'Quantity',
                      value: quantity == null
                          ? '${godown.items.length}'
                          : MoneyFormat.quantity(quantity, godown.unit),
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
                message: 'No item name contains that text.',
              ),
            )
          else
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Card(
                clipBehavior: Clip.antiAlias,
                child: Column(
                  children: <Widget>[
                    for (int i = 0; i < items.length; i++) ...<Widget>[
                      if (i > 0) const Divider(height: 1, indent: 14, endIndent: 14),
                      _ItemRow(item: items[i]),
                    ],
                  ],
                ),
              ),
            ),
        ];
      },
    );
  }
}
