import 'package:decimal/decimal.dart';
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
import '../../dashboard/presentation/widgets/dashboard_sections.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/report_scaffold.dart';

/// Outstanding for one ledger group -- Tally's Group Outstanding.
///
/// The party list is built by the backend here, not by the app as on
/// [OutstandingScreen]: only the server knows which group a party's ledger sits
/// under, and guessing it from the bill would be guessing.
///
/// The number an owner reads first is the **net**, because that is what the
/// group is actually worth once prepayments are taken off. What is chaseable
/// and what is held as advance are both shown beside it rather than folded in,
/// so the figure can be reconciled instead of taken on trust.
class GroupOutstandingScreen extends ConsumerStatefulWidget {
  const GroupOutstandingScreen({super.key, required this.kind, this.group});

  final OutstandingKind kind;
  final String? group;

  @override
  ConsumerState<GroupOutstandingScreen> createState() => _GroupOutstandingScreenState();
}

class _GroupOutstandingScreenState extends ConsumerState<GroupOutstandingScreen>
    with SingleTickerProviderStateMixin {
  DateTime? _asOf;

  /// Ledgers: every party, worst overdue first. Group: the same parties under
  /// the sub-groups they are filed in, as Tally's own group view shows them.
  late final TabController _tabs = TabController(length: 2, vsync: this)
    ..addListener(() => setState(() {}));

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _pickAsOf(BuildContext context) async {
    final DateTime now = DateTime.now();
    final DateTime picked = await showDatePicker(
          context: context,
          initialDate: _asOf ?? now,
          firstDate: DateTime(now.year - 6),
          lastDate: now,
          helpText: 'View outstanding as of',
        ) ??
        _asOf ??
        now;
    if (!context.mounted) return;
    setState(() => _asOf = _isToday(picked) ? null : picked);
  }

  @override
  Widget build(BuildContext context) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see outstanding bills.',
        ),
      );
    }

    final GroupOutstandingArgs args = (
      companyId: companyId,
      kind: widget.kind,
      group: widget.group,
      asOf: _asOf,
    );
    final AsyncValue<Fresh<GroupOutstandingReport>> state =
        ref.watch(groupOutstandingProvider(args));

    return ReportScaffold<GroupOutstandingReport>(
      // The group actually read comes back in the response, so a renamed group
      // titles its own screen. Falls back to the stock name while loading.
      title: state.valueOrNull?.data.group ?? widget.kind.defaultGroup,
      subtitle: _asOf == null
          ? widget.kind.groupQuestion
          : 'As of ${_formatDate(_asOf!)}',
      actions: <Widget>[
        IconButton(
          tooltip: 'Choose a date',
          onPressed: () => _pickAsOf(context),
          icon: Icon(
            _asOf == null ? Icons.calendar_month_outlined : Icons.event_available,
          ),
        ),
      ],
      state: state,
      onRefresh: () => refreshReport<GroupOutstandingReport>(
        ref,
        groupOutstandingProvider(args),
        (ReportsRepository repository) => repository.outstandingByGroup(
          companyId,
          kind: widget.kind,
          group: widget.group,
          asOf: _asOf,
          mode: FetchMode.live,
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.check_circle_outline,
        title: 'Nothing outstanding',
        message:
            'No party under ${state.valueOrNull?.data.group ?? widget.kind.defaultGroup} '
            'has an unsettled bill.',
      ),
      builder: (BuildContext context, GroupOutstandingReport report) {
        if (report.parties.isEmpty) return const <Widget>[];

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: _GroupSummaryCard(report: report),
          ),
          // Under the summary, not in the app bar: the summary is the same in
          // both views, and the switch belongs with the list it changes.
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: TabBar(
              controller: _tabs,
              isScrollable: true,
              tabAlignment: TabAlignment.start,
              dividerColor: Colors.transparent,
              tabs: const <Tab>[Tab(text: 'Ledgers'), Tab(text: 'Group')],
            ),
          ),
          if (_tabs.index == 0)
            ...<Widget>[
              for (final GroupParty party in report.parties)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                  child: _GroupPartyCard(party: party, kind: report.kind),
                ),
            ]
          else
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: _GroupTree(report: report),
            ),
          if (report.ungroupedPartyCount > 0)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 6, 16, 0),
              child: _UngroupedNotice(count: report.ungroupedPartyCount),
            ),
        ];
      },
    );
  }

  static bool _isToday(DateTime date) {
    final DateTime now = DateTime.now();
    return date.year == now.year && date.month == now.month && date.day == now.day;
  }

  static String _formatDate(DateTime date) => '${date.day}/${date.month}/${date.year}';
}

class _GroupSummaryCard extends StatelessWidget {
  const _GroupSummaryCard({required this.report});

  final GroupOutstandingReport report;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'Net position',
                        style: theme.textTheme.labelMedium
                            ?.copyWith(color: context.mutedColor),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        MoneyFormat.full(report.net),
                        style: theme.textTheme.headlineSmall,
                      ),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: <Widget>[
                    Text(
                      'Overdue',
                      style: theme.textTheme.labelMedium
                          ?.copyWith(color: context.mutedColor),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      MoneyFormat.full(report.overdue),
                      style: theme.textTheme.titleMedium?.copyWith(
                        color: report.overdue.isZero ? null : context.negativeColor,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            if (report.hasAdvances) ...<Widget>[
              const SizedBox(height: 12),
              _NetBreakdown(report: report),
            ],
            const SizedBox(height: 14),
            AgeingBar(ageing: report.ageing, total: report.total),
            const SizedBox(height: 10),
            Text(
              '${report.billCount} bills · ${report.partyCount} parties',
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
            ),
          ],
        ),
      ),
    );
  }
}

/// Shown only when advances exist, because otherwise net == total and spelling
/// out an identity is noise.
class _NetBreakdown extends StatelessWidget {
  const _NetBreakdown({required this.report});

  final GroupOutstandingReport report;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? label =
        theme.textTheme.bodySmall?.copyWith(color: context.mutedColor);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.5),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('Billed', style: label),
                const SizedBox(height: 2),
                Text(
                  MoneyFormat.compact(report.total),
                  style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
                ),
              ],
            ),
          ),
          Text('−', style: theme.textTheme.titleMedium?.copyWith(color: context.mutedColor)),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(left: 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('Advances held', style: label),
                  const SizedBox(height: 2),
                  Text(
                    MoneyFormat.compact(report.advances),
                    style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _GroupPartyCard extends StatelessWidget {
  const _GroupPartyCard({required this.party, required this.kind});

  final GroupParty party;
  final OutstandingKind kind;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Theme(
        // The default expansion tile divider fights the card border.
        data: theme.copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          title: Text(
            party.party,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
          ),
          subtitle: Text(
            _subtitle(party),
            style: theme.textTheme.bodySmall?.copyWith(
              color: party.isOverdue ? context.negativeColor : context.mutedColor,
            ),
          ),
          trailing: Text(
            MoneyFormat.compact(party.net),
            style: theme.textTheme.titleSmall,
          ),
          children: <Widget>[
            for (final OutstandingBill bill in party.bills) _BillRow(bill: bill),
            _PartyStatementLink(party: party.party),
          ],
        ),
      ),
    );
  }

  /// Advances are named in the subtitle rather than left to be inferred from a
  /// smaller trailing figure. A party whose net is below their billed total has
  /// paid ahead, and that is worth knowing before ringing them about a bill.
  static String _subtitle(GroupParty party) {
    final String bills = '${party.billCount} ${party.billCount == 1 ? 'bill' : 'bills'}';
    if (party.hasAdvances) {
      return '$bills · ${MoneyFormat.compact(party.advances)} advance';
    }
    if (party.isOverdue) {
      return '$bills · oldest ${party.daysOverdue} days overdue';
    }
    return '$bills · not yet due';
  }
}

/// The group, its sub-groups nested under it, and each party under the group
/// it is actually filed in.
///
/// Built from the tree the backend sends rather than from names: nothing about
/// "Electronics Supplier" says it is a group and not a party. Sub-groups with
/// nothing outstanding are left out, because a row of ₹0 reads as a figure.
class _GroupTree extends StatefulWidget {
  const _GroupTree({required this.report});

  final GroupOutstandingReport report;

  @override
  State<_GroupTree> createState() => _GroupTreeState();
}

class _GroupTreeState extends State<_GroupTree> {
  /// The top group starts open and its sub-groups closed: the first thing seen
  /// is the shape of the group, one tap from any party in it.
  late final Set<String> _open = <String>{widget.report.group};

  @override
  Widget build(BuildContext context) {
    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(children: _rows(_GroupNode.of(widget.report), 0)),
    );
  }

  List<Widget> _rows(_GroupNode node, int depth) {
    final bool open = _open.contains(node.name);
    final List<_GroupNode> groups = node.groups
        .where((_GroupNode group) => !group.isEmpty)
        .toList()
      ..sort((_GroupNode a, _GroupNode b) =>
          a.name.toLowerCase().compareTo(b.name.toLowerCase()));

    return <Widget>[
      _TreeRow(
        depth: depth,
        label: node.name,
        amount: node.net,
        expanded: open,
        onTap: () => setState(() {
          if (open) {
            _open.remove(node.name);
          } else {
            _open.add(node.name);
          }
        }),
      ),
      if (open) ...<Widget>[
        for (final GroupParty party in node.parties)
          _TreeRow(
            depth: depth + 1,
            label: party.party,
            amount: party.net,
            onTap: () => context.push(
              '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(party.party)}',
            ),
          ),
        for (final _GroupNode group in groups) ..._rows(group, depth + 1),
      ],
    ];
  }
}

class _GroupNode {
  _GroupNode(this.name);

  final String name;
  final List<_GroupNode> groups = <_GroupNode>[];
  final List<GroupParty> parties = <GroupParty>[];

  bool get isEmpty => parties.isEmpty && groups.every((_GroupNode g) => g.isEmpty);

  /// Summed on the signed value, because a group can hold a party in advance
  /// beside parties who owe, and adding magnitudes would count both as debt.
  Decimal get _signed =>
      parties.fold(Decimal.zero, (Decimal sum, GroupParty p) => sum + p.net.signed) +
      groups.fold(Decimal.zero, (Decimal sum, _GroupNode g) => sum + g._signed);

  Money get net {
    final Decimal signed = _signed;
    return Money(
      amount: signed.abs(),
      side: signed < Decimal.zero ? MoneySide.credit : MoneySide.debit,
      signed: signed,
      currency: 'INR',
    );
  }

  static _GroupNode of(GroupOutstandingReport report) {
    // Folded the way the backend folds Tally names, which keep whatever
    // spacing and case the shop typed.
    String key(String? name) =>
        (name ?? '').trim().split(RegExp(r'\s+')).join(' ').toLowerCase();

    final _GroupNode root = _GroupNode(report.group);
    final Map<String, _GroupNode> byName = <String, _GroupNode>{key(report.group): root};
    for (final SubGroup sub in report.subGroups) {
      byName.putIfAbsent(key(sub.name), () => _GroupNode(sub.name));
    }
    for (final SubGroup sub in report.subGroups) {
      final _GroupNode node = byName[key(sub.name)]!;
      final _GroupNode parent = byName[key(sub.parent)] ?? root;
      if (!identical(node, parent)) parent.groups.add(node);
    }
    // A party whose group is not in the tree still belongs to this report, so
    // it goes under the top group rather than out of the view.
    for (final GroupParty party in report.parties) {
      (byName[key(party.group)] ?? root).parties.add(party);
    }
    return root;
  }
}

class _TreeRow extends StatelessWidget {
  const _TreeRow({
    required this.depth,
    required this.label,
    required this.amount,
    required this.onTap,
    this.expanded,
  });

  final int depth;
  final String label;
  final Money amount;
  final VoidCallback onTap;

  /// Null for a party, which has nothing to open.
  final bool? expanded;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: EdgeInsets.fromLTRB(8 + 16.0 * depth, 10, 16, 10),
        child: Row(
          children: <Widget>[
            SizedBox(
              width: 24,
              child: expanded == null
                  ? null
                  : Icon(
                      expanded! ? Icons.expand_less : Icons.expand_more,
                      size: 18,
                      color: context.mutedColor,
                    ),
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: depth == 0 ? theme.textTheme.titleSmall : theme.textTheme.bodyMedium,
              ),
            ),
            const SizedBox(width: 12),
            Text(
              MoneyFormat.full(amount),
              style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
            ),
          ],
        ),
      ),
    );
  }
}

class _BillRow extends StatelessWidget {
  const _BillRow({required this.bill});

  final OutstandingBill bill;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  bill.billName ?? 'Bill',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium,
                ),
                Text(
                  bill.isOverdue
                      ? '${bill.daysOverdue} days overdue'
                      : ageingLabel(bill.ageingBucket),
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: bill.isOverdue ? context.negativeColor : context.mutedColor,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Text(
                MoneyFormat.full(bill.amount),
                style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
              ),
              if (bill.isAdvance)
                Text(
                  'Advance',
                  style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// The report is incomplete and says so.
///
/// A party whose ledger was not in this read cannot be placed in any group. The
/// alternative to showing this is a total that is quietly too small, which is
/// exactly the kind of number this product refuses to render without a caveat.
class _UngroupedNotice extends StatelessWidget {
  const _UngroupedNotice({required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Icon(Icons.info_outline, size: 15, color: context.mutedColor),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            '$count ${count == 1 ? 'party is' : 'parties are'} not in this group '
            'or could not be placed, and ${count == 1 ? 'is' : 'are'} excluded above.',
            style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
          ),
        ),
      ],
    );
  }
}

/// The way from a party's bills to everything that party has ever done.
///
/// Bills answer "what is outstanding"; the ledger answers "and what have they
/// been paying like". Kept as an explicit action at the foot of the expansion
/// rather than a tap on the party row, which already has a job -- opening and
/// closing the bills.
class _PartyStatementLink extends StatelessWidget {
  const _PartyStatementLink({required this.party});

  final String party;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 0, 8, 4),
        child: TextButton.icon(
          onPressed: () => context.push(
            '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(party)}',
          ),
          icon: const Icon(Icons.receipt_long_outlined, size: 17),
          label: const Text('Statement for this party'),
        ),
      ),
    );
  }
}
