import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../dashboard/presentation/widgets/dashboard_sections.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/report_scaffold.dart';

/// "Who owes me money?" -- grouped by party, worst first.
///
/// Grouped rather than listed flat because that is how the question gets acted
/// on: an owner rings a customer, not a bill. Each party expands to the
/// individual invoices so the call can be specific.
///
/// Ageing is normally read "as of today," but the calendar action lets an
/// owner ask the same question about a past date -- reconciling against last
/// month's close, say -- since the backend already knows how to answer it.
class OutstandingScreen extends ConsumerStatefulWidget {
  const OutstandingScreen({super.key, required this.kind});

  final OutstandingKind kind;

  @override
  ConsumerState<OutstandingScreen> createState() => _OutstandingScreenState();
}

class _OutstandingScreenState extends ConsumerState<OutstandingScreen> {
  DateTime? _asOf;

  /// Parties whose bills are showing, by name. Held here rather than in each
  /// card so "Expand all" can reach every one of them.
  final Set<String> _expanded = <String>{};

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

    final OutstandingArgs args = (companyId: companyId, kind: widget.kind, asOf: _asOf);
    final AsyncValue<Fresh<OutstandingReport>> state =
        ref.watch(outstandingProvider(args));

    return ReportScaffold<OutstandingReport>(
      title: widget.kind.title,
      subtitle: _asOf == null ? widget.kind.question : 'As of ${_formatDate(_asOf!)}',
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
      onRefresh: () => refreshReport<OutstandingReport>(
        ref,
        outstandingProvider(args),
        (ReportsRepository repository) => repository.outstanding(
          companyId,
          kind: widget.kind,
          asOf: _asOf,
          mode: FetchMode.live,
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.check_circle_outline,
        title: widget.kind == OutstandingKind.receivable
            ? 'Nothing outstanding'
            : 'You owe nothing',
        message: widget.kind == OutstandingKind.receivable
            ? 'Every bill has been settled.'
            : 'All supplier bills are settled.',
      ),
      builder: (BuildContext context, OutstandingReport report) {
        if (report.bills.isEmpty) return const <Widget>[];
        final ThemeData theme = Theme.of(context);
        final bool allExpanded = report.byParty
            .every((PartyBills party) => _expanded.contains(party.party));

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: Card(
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
                                'Total outstanding',
                                style: theme.textTheme.labelMedium
                                    ?.copyWith(color: context.mutedColor),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                MoneyFormat.full(report.total),
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
                                color: report.overdue.isZero
                                    ? null
                                    : context.negativeColor,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                    const SizedBox(height: 14),
                    AgeingBar(ageing: report.ageing, total: report.total),
                    const SizedBox(height: 10),
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            '${report.billCount} bills · ${report.partyCount} parties',
                            style: theme.textTheme.bodySmall
                                ?.copyWith(color: context.mutedColor),
                          ),
                        ),
                        TextButton.icon(
                          style: TextButton.styleFrom(
                            visualDensity: VisualDensity.compact,
                            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                          ),
                          onPressed: () => setState(() {
                            if (allExpanded) {
                              _expanded.clear();
                            } else {
                              _expanded.addAll(
                                report.byParty.map((PartyBills party) => party.party),
                              );
                            }
                          }),
                          icon: Icon(
                            allExpanded ? Icons.unfold_less : Icons.unfold_more,
                            size: 18,
                          ),
                          label: Text(allExpanded ? 'Collapse all' : 'Expand all'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
          for (final PartyBills party in report.byParty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: _PartyCard(
                party: party,
                expanded: _expanded.contains(party.party),
                onToggle: () => setState(() {
                  if (!_expanded.remove(party.party)) _expanded.add(party.party);
                }),
              ),
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

/// A party, its total, and -- behind the arrow -- the bills that make it up.
///
/// Two targets on one card because they answer different questions: the arrow
/// lists what is outstanding right here, and the card itself opens the party's
/// statement, which is where "and what have they been paying like" is answered.
class _PartyCard extends StatelessWidget {
  const _PartyCard({
    required this.party,
    required this.expanded,
    required this.onToggle,
  });

  final PartyBills party;
  final bool expanded;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool overdue = party.maxDaysOverdue > 0;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        children: <Widget>[
          InkWell(
            onTap: () => context.push(
              '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(party.party)}',
            ),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 10, 4, 10),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          party.party,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodyLarge
                              ?.copyWith(fontWeight: FontWeight.w600),
                        ),
                        Text(
                          overdue
                              ? '${party.bills.length} bills · oldest ${party.maxDaysOverdue} days overdue'
                              : '${party.bills.length} bills · not yet due',
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: overdue ? context.negativeColor : context.mutedColor,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    MoneyFormat.compact(party.total),
                    style: theme.textTheme.titleSmall,
                  ),
                  IconButton(
                    tooltip: expanded ? 'Hide bills' : 'Show bills',
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
                ? Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Column(
                      children: <Widget>[
                        for (final OutstandingBill bill in party.bills)
                          _BillRow(bill: bill),
                      ],
                    ),
                  )
                : const SizedBox(width: double.infinity),
          ),
        ],
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
                if (_dates(bill) case final String dates)
                  Text(
                    dates,
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                  ),
                if (bill.isOverdue)
                  Text(
                    '${bill.daysOverdue} days overdue',
                    style: theme.textTheme.bodySmall?.copyWith(color: context.negativeColor),
                  )
                else if (_dates(bill) == null)
                  Text(
                    ageingLabel(bill.ageingBucket),
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
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

  static String? _dates(OutstandingBill bill) {
    String dmy(DateTime date) => '${date.day}/${date.month}/${date.year}';
    final List<String> parts = <String>[
      if (bill.billDate != null) 'bill ${dmy(bill.billDate!)}',
      if (bill.dueDate != null) 'due ${dmy(bill.dueDate!)}',
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }
}
