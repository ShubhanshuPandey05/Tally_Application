import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
class OutstandingScreen extends ConsumerWidget {
  const OutstandingScreen({super.key, required this.kind});

  final OutstandingKind kind;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
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

    final OutstandingArgs args = (companyId: companyId, kind: kind);
    final AsyncValue<Fresh<OutstandingReport>> state =
        ref.watch(outstandingProvider(args));

    return ReportScaffold<OutstandingReport>(
      title: kind.title,
      subtitle: kind.question,
      state: state,
      onRefresh: () => refreshReport<OutstandingReport>(
        ref,
        outstandingProvider(args),
        (ReportsRepository repository) =>
            repository.outstanding(companyId, kind: kind, mode: FetchMode.live),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.check_circle_outline,
        title: kind == OutstandingKind.receivable
            ? 'Nothing outstanding'
            : 'You owe nothing',
        message: kind == OutstandingKind.receivable
            ? 'Every bill has been settled.'
            : 'All supplier bills are settled.',
      ),
      builder: (BuildContext context, OutstandingReport report) {
        if (report.bills.isEmpty) return const <Widget>[];
        final ThemeData theme = Theme.of(context);

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
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
            const SizedBox(height: 6),
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
