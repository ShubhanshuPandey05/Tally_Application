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
import 'widgets/ledger_row.dart';
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
      title: 'Ledgers',
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
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: state.valueOrNull == null
                      ? const SizedBox.shrink()
                      : _GroupFilterButton(
                          groups: _distinctGroups(state.valueOrNull!.data.ledgers),
                          selected: _group,
                          onSelected: (String? group) =>
                              setState(() => _group = group),
                        ),
                ),
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

        // One group open by itself is not a choice the reader made -- it is the
        // only answer there is -- so a filtered or searched list opens.
        final bool expanded =
            needle.isNotEmpty || _group != null || groups.length == 1;

        return <Widget>[
          for (final String group in groups)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: _GroupCard(
                key: ValueKey<String>(group),
                group: group,
                lines: byGroup[group]!,
                initiallyExpanded: expanded,
              ),
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

/// One button naming the group in force, opening a list of every group.
///
/// It was a row of chips, which on a real chart of accounts is thirty groups
/// in a strip that shows two -- the one being looked for was always off the
/// edge. Built from the report itself rather than a fixed list, since a
/// company's chart of accounts is its own.
class _GroupFilterButton extends StatelessWidget {
  const _GroupFilterButton({
    required this.groups,
    required this.selected,
    required this.onSelected,
  });

  final List<String> groups;
  final String? selected;
  final ValueChanged<String?> onSelected;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ActionChip(
      avatar: Icon(Icons.filter_list, size: 17, color: theme.colorScheme.primary),
      label: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Flexible(
            child: Text(
              selected ?? 'All groups',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: 2),
          Icon(Icons.arrow_drop_down, size: 18, color: context.mutedColor),
        ],
      ),
      onPressed: () async {
        final _GroupChoice? choice = await showModalBottomSheet<_GroupChoice>(
          context: context,
          showDragHandle: true,
          isScrollControlled: true,
          builder: (BuildContext sheetContext) =>
              _GroupSheet(groups: groups, selected: selected),
        );
        if (choice != null) onSelected(choice.group);
      },
    );
  }
}

/// Wraps the answer so that "All groups" (a null group) is distinguishable
/// from the sheet being dismissed without a choice.
class _GroupChoice {
  const _GroupChoice(this.group);
  final String? group;
}

class _GroupSheet extends StatelessWidget {
  const _GroupSheet({required this.groups, required this.selected});

  final List<String> groups;
  final String? selected;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    Widget option(String label, String? group) => ListTile(
          dense: true,
          title: Text(label, style: theme.textTheme.bodyMedium),
          trailing: selected == group
              ? Icon(Icons.check, size: 18, color: theme.colorScheme.primary)
              : null,
          onTap: () => Navigator.of(context).pop(_GroupChoice(group)),
        );

    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.7,
      ),
      child: ListView(
        shrinkWrap: true,
        padding: const EdgeInsets.only(bottom: 16),
        children: <Widget>[
          option('All groups', null),
          for (final String group in groups) option(group, group),
        ],
      ),
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
    return Card(
      child: Column(
        children: <Widget>[
          for (final LedgerLine line in lines)
            LedgerRow(line: line, subtitle: line.group ?? 'Other'),
          const SizedBox(height: 4),
        ],
      ),
    );
  }
}

/// One Tally group and the accounts under it, collapsed until asked for.
///
/// A real chart of accounts is thirty groups and several hundred ledgers, so
/// the grouped view was a scroll long enough that the group headings -- the
/// thing that makes it a chart of accounts rather than a list -- were never on
/// screen together. Closed, the screen is the summary; open, it is the detail.
class _GroupCard extends StatefulWidget {
  const _GroupCard({
    super.key,
    required this.group,
    required this.lines,
    required this.initiallyExpanded,
  });

  final String group;
  final List<LedgerLine> lines;

  /// Open from the start when the reader has already narrowed the list. Having
  /// typed a name, being shown a closed group that matches it and nothing else
  /// reads as "not found".
  final bool initiallyExpanded;

  @override
  State<_GroupCard> createState() => _GroupCardState();
}

class _GroupCardState extends State<_GroupCard> {
  late bool _open = widget.initiallyExpanded;

  @override
  void didUpdateWidget(_GroupCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A search that starts matching opens the group; closing one by hand and
    // having the next keystroke reopen it would be worse, so this only follows
    // the flag when it actually changes.
    if (widget.initiallyExpanded != oldWidget.initiallyExpanded) {
      _open = widget.initiallyExpanded;
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Money total = widget.lines.fold(
      Money.zero,
      (Money sum, LedgerLine line) => sum + line.closing,
    );

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          // The tile opens the group's own page; only the arrow folds it in
          // place. Peeking is the quick question, the page is the one that can
          // be shared.
          InkWell(
            onTap: () => context.push(
              '${Routes.ledgerGroup}?group=${Uri.encodeQueryComponent(widget.group)}',
            ),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 4, 4),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(widget.group, style: theme.textTheme.titleSmall),
                  ),
                  Text(
                    MoneyFormat.compact(total),
                    style:
                        theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
                  ),
                  const SizedBox(width: 2),
                  // The count is what a closed group is worth: it says how much
                  // is behind the heading without opening it.
                  Text(
                    ' · ${widget.lines.length}',
                    style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                  ),
                  IconButton(
                    tooltip: _open ? 'Collapse' : 'Expand',
                    onPressed: () => setState(() => _open = !_open),
                    icon: AnimatedRotation(
                      turns: _open ? 0.5 : 0,
                      duration: const Duration(milliseconds: 150),
                      child: Icon(
                        Icons.expand_more,
                        size: 20,
                        color: context.mutedColor,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (_open) ...<Widget>[
            const Divider(height: 1, indent: 16, endIndent: 16),
            const SizedBox(height: 4),
            for (final LedgerLine line in widget.lines)
              LedgerRow(
                line: line,
                subtitle: (line.gstin?.isNotEmpty ?? false) ? line.gstin : null,
              ),
            const SizedBox(height: 8),
          ],
        ],
      ),
    );
  }
}
