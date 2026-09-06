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
                    Text(
                      '${report.billCount} bills · ${report.partyCount} parties',
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: context.mutedColor),
                    ),
                  ],
                ),
              ),
            ),
          ),
          for (final PartyBills party in report.byParty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: _PartyCard(party: party),
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

class _PartyCard extends StatelessWidget {
  const _PartyCard({required this.party});

  final PartyBills party;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool overdue = party.maxDaysOverdue > 0;

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
            overdue
                ? '${party.bills.length} bills · oldest ${party.maxDaysOverdue} days overdue'
                : '${party.bills.length} bills · not yet due',
            style: theme.textTheme.bodySmall?.copyWith(
              color: overdue ? context.negativeColor : context.mutedColor,
            ),
          ),
          trailing: Text(
            MoneyFormat.compact(party.total),
            style: theme.textTheme.titleSmall,
          ),
          children: <Widget>[
            for (final OutstandingBill bill in party.bills)
              _BillRow(bill: bill),
            _PartyStatementLink(party: party.party),
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
                  _subtitle(bill),
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

  static String _subtitle(OutstandingBill bill) {
    final String? due = bill.dueDate == null
        ? null
        : 'due ${bill.dueDate!.day}/${bill.dueDate!.month}/${bill.dueDate!.year}';
    if (bill.isOverdue) {
      return '${bill.daysOverdue} days overdue${due == null ? '' : ' · $due'}';
    }
    return due ?? ageingLabel(bill.ageingBucket);
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
