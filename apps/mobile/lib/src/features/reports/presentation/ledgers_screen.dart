import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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

/// Closing balances across every account, grouped as Tally groups them.
///
/// Balances are shown with an explicit `Dr` / `Cr`, not as signed numbers. An
/// accountant reads the side; a minus sign in front of a supplier balance is
/// ambiguous and, given Tally's own inverted XML convention, is exactly the
/// kind of thing that gets misread.
class LedgersScreen extends ConsumerStatefulWidget {
  const LedgersScreen({super.key});

  @override
  ConsumerState<LedgersScreen> createState() => _LedgersScreenState();
}

enum _LedgerSort { groupThenBalance, nameAsc, balanceDesc }

class _LedgersScreenState extends ConsumerState<LedgersScreen> {
  String _search = '';
  String? _group;
  _LedgerSort _sort = _LedgerSort.groupThenBalance;

  @override
  Widget build(BuildContext context) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see ledger balances.',
        ),
      );
    }

    final LedgerArgs args = (companyId: companyId, group: null);
    final AsyncValue<Fresh<LedgerReport>> state = ref.watch(ledgersProvider(args));

    return ReportScaffold<LedgerReport>(
      title: 'Ledger balances',
      state: state,
      actions: <Widget>[
        PopupMenuButton<_LedgerSort>(
          tooltip: 'Sort',
          icon: const Icon(Icons.sort),
          initialValue: _sort,
          onSelected: (_LedgerSort sort) => setState(() => _sort = sort),
          itemBuilder: (BuildContext context) => const <PopupMenuEntry<_LedgerSort>>[
            PopupMenuItem<_LedgerSort>(
              value: _LedgerSort.groupThenBalance,
              child: Text('By group'),
            ),
            PopupMenuItem<_LedgerSort>(
              value: _LedgerSort.nameAsc,
              child: Text('Name (A–Z)'),
            ),
            PopupMenuItem<_LedgerSort>(
              value: _LedgerSort.balanceDesc,
              child: Text('Balance (highest first)'),
            ),
          ],
        ),
      ],
      onRefresh: () => refreshReport<LedgerReport>(
        ref,
        ledgersProvider(args),
        (ReportsRepository repository) =>
            repository.ledgers(companyId, mode: FetchMode.live),
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
                  hintText: 'Search ledgers',
                  prefixIcon: Icon(Icons.search),
                  isDense: true,
                ),
              ),
            ),
            SizedBox(
              height: 48,
              child: state.valueOrNull == null
                  ? const SizedBox.shrink()
                  : _GroupFilterRow(
                      groups: _distinctGroups(state.valueOrNull!.data.ledgers),
                      selected: _group,
                      onSelected: (String? group) => setState(() => _group = group),
                    ),
            ),
          ],
        ),
      ),
      emptyBuilder: (BuildContext context) => const EmptyState(
        icon: Icons.account_balance_outlined,
        title: 'No ledgers',
        message: 'TallyPrime returned no ledger accounts for this company.',
      ),
      builder: (BuildContext context, LedgerReport report) {
        final String needle = _search.trim().toLowerCase();
        List<LedgerLine> rows = report.ledgers;
        if (_group != null) {
          rows = rows.where((LedgerLine line) => (line.group ?? 'Other') == _group).toList();
        }
        if (needle.isNotEmpty) {
          rows = rows
              .where((LedgerLine line) =>
                  line.name.toLowerCase().contains(needle) ||
                  (line.group ?? '').toLowerCase().contains(needle))
              .toList(growable: false);
        }

        if (report.ledgers.isEmpty) return const <Widget>[];

        if (rows.isEmpty) {
          return <Widget>[
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 48),
              child: EmptyState(
                icon: Icons.search_off,
                title: 'No matches',
                message: 'No ledger or group name contains that text.',
              ),
            ),
          ];
        }

        if (_sort == _LedgerSort.nameAsc) {
          rows = <LedgerLine>[...rows]
            ..sort((LedgerLine a, LedgerLine b) =>
                a.name.toLowerCase().compareTo(b.name.toLowerCase()));
          return <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: _FlatLedgerCard(lines: rows),
            ),
          ];
        }
        if (_sort == _LedgerSort.balanceDesc) {
          rows = <LedgerLine>[...rows]
            ..sort((LedgerLine a, LedgerLine b) =>
                b.closing.amount.abs().compareTo(a.closing.amount.abs()));
          return <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: _FlatLedgerCard(lines: rows),
            ),
          ];
        }

        final Map<String, List<LedgerLine>> byGroup = <String, List<LedgerLine>>{};
        for (final LedgerLine line in rows) {
          byGroup.putIfAbsent(line.group ?? 'Other', () => <LedgerLine>[]).add(line);
        }
        final List<String> groups = byGroup.keys.toList()..sort();

        return <Widget>[
          for (final String group in groups)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: _GroupCard(group: group, lines: byGroup[group]!),
            ),
        ];
      },
    );
  }

  static List<String> _distinctGroups(List<LedgerLine> ledgers) {
    final Set<String> groups = <String>{
      for (final LedgerLine line in ledgers) line.group ?? 'Other',
    };
    final List<String> sorted = groups.toList()..sort();
    return sorted;
  }
}

/// "All" plus every group present in the current data -- built from the
/// report itself rather than a fixed list, since a company's chart of
/// accounts is its own.
class _GroupFilterRow extends StatelessWidget {
  const _GroupFilterRow({
    required this.groups,
    required this.selected,
    required this.onSelected,
  });

  final List<String> groups;
  final String? selected;
  final ValueChanged<String?> onSelected;

  @override
  Widget build(BuildContext context) {
    return ListView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.only(right: 8),
          child: ChoiceChip(
            label: const Text('All groups'),
            selected: selected == null,
            onSelected: (_) => onSelected(null),
          ),
        ),
        for (final String group in groups)
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: ChoiceChip(
              label: Text(group),
              selected: selected == group,
              onSelected: (_) => onSelected(group),
            ),
          ),
      ],
    );
  }
}

/// The un-grouped presentation used by the two sort modes that are not
/// "by group" -- a name-ordered or balance-ordered list reads oddly split
/// into group cards.
class _FlatLedgerCard extends StatelessWidget {
  const _FlatLedgerCard({required this.lines});

  final List<LedgerLine> lines;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Column(
        children: <Widget>[
          for (final LedgerLine line in lines)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          line.name,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodyMedium,
                        ),
                        Text(
                          line.group ?? 'Other',
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    MoneyFormat.withSide(line.closing),
                    style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 4),
        ],
      ),
    );
  }
}

class _GroupCard extends StatelessWidget {
  const _GroupCard({required this.group, required this.lines});

  final String group;
  final List<LedgerLine> lines;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Money total = lines.fold(
      Money.zero,
      (Money sum, LedgerLine line) => sum + line.closing,
    );

    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Text(group, style: theme.textTheme.titleSmall),
                ),
                Text(
                  MoneyFormat.compact(total),
                  style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
                ),
              ],
            ),
          ),
          const Divider(indent: 16, endIndent: 16),
          for (final LedgerLine line in lines)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          line.name,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodyMedium,
                        ),
                        if (line.gstin != null && line.gstin!.isNotEmpty)
                          Text(
                            line.gstin!,
                            style: theme.textTheme.labelSmall
                                ?.copyWith(color: context.mutedColor),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    MoneyFormat.withSide(line.closing),
                    style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}
