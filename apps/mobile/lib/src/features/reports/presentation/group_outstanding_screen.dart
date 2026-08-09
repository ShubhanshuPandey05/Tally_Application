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
class GroupOutstandingScreen extends ConsumerWidget {
  const GroupOutstandingScreen({super.key, required this.kind, this.group});

  final OutstandingKind kind;
  final String? group;

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

    final GroupOutstandingArgs args =
        (companyId: companyId, kind: kind, group: group);
    final AsyncValue<Fresh<GroupOutstandingReport>> state =
        ref.watch(groupOutstandingProvider(args));

    return ReportScaffold<GroupOutstandingReport>(
      // The group actually read comes back in the response, so a renamed group
      // titles its own screen. Falls back to the stock name while loading.
      title: state.valueOrNull?.data.group ?? kind.defaultGroup,
      subtitle: kind.groupQuestion,
      state: state,
      onRefresh: () => refreshReport<GroupOutstandingReport>(
        ref,
        groupOutstandingProvider(args),
        (ReportsRepository repository) => repository.outstandingByGroup(
          companyId,
          kind: kind,
          group: group,
          mode: FetchMode.live,
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.check_circle_outline,
        title: 'Nothing outstanding',
        message: 'No party under ${state.valueOrNull?.data.group ?? kind.defaultGroup} '
            'has an unsettled bill.',
      ),
      builder: (BuildContext context, GroupOutstandingReport report) {
        if (report.parties.isEmpty) return const <Widget>[];

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: _GroupSummaryCard(report: report),
          ),
          for (final GroupParty party in report.parties)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: _GroupPartyCard(party: party, kind: report.kind),
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
}

class _GroupSummaryCard extends StatelessWidget {
  const _GroupSummaryCard({required this.report});

  final GroupOutstandingReport report;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Card(
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
            const SizedBox(height: 6),
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
