import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/documents/party_bills_document.dart';
import '../../../core/documents/share_document.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/reports.dart';
import 'widgets/bill_row.dart';
import 'widgets/report_scaffold.dart';

/// The open bills of one party, and nothing else.
///
/// Reached by tapping a party on Receivables, Payables, Sundry Debtors or
/// Sundry Creditors. It reads the same report the list came from, with the same
/// date, so arriving costs nothing and the bills here are exactly the ones on
/// the card that was tapped -- a ledger statement would add every settled
/// invoice and receipt in its window, which is the opposite of what somebody
/// chasing a payment wants to see.
class PartyOutstandingScreen extends ConsumerWidget {
  const PartyOutstandingScreen({
    super.key,
    required this.kind,
    required this.party,
    this.asOf,
    this.fromGroup = false,
    this.group,
  });

  final OutstandingKind kind;
  final String party;
  final DateTime? asOf;

  /// True when reached from a group's outstanding (Sundry Debtors and the
  /// like). That report is built by the server and nets advances off per
  /// party, so it is read again here rather than the plain bill list, whose
  /// figures would not match the card that was tapped.
  final bool fromGroup;

  /// The group read, when [fromGroup]. Null means the kind's default group.
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
    final Company? company = ref.watch(activeCompanyProvider).valueOrNull;

    if (fromGroup) {
      final GroupOutstandingArgs args =
          (companyId: companyId, kind: kind, group: group, asOf: asOf);
      final AsyncValue<Fresh<GroupOutstandingReport>> state =
          ref.watch(groupOutstandingProvider(args));
      return _scaffold<GroupOutstandingReport>(
        context,
        state: state,
        company: company,
        heading: state.valueOrNull?.data.group ?? group ?? kind.defaultGroup,
        pick: (GroupOutstandingReport report) {
          final GroupParty? p =
              report.parties.where((GroupParty p) => p.party == party).firstOrNull;
          return p == null ? null : _PartyView.ofGroup(p);
        },
        onRefresh: () => refreshReport<GroupOutstandingReport>(
          ref,
          groupOutstandingProvider(args),
          (ReportsRepository repository) => repository.outstandingByGroup(
            companyId,
            kind: kind,
            group: group,
            asOf: asOf,
            mode: FetchMode.live,
          ),
        ),
      );
    }

    final OutstandingArgs args = (companyId: companyId, kind: kind, asOf: asOf);
    final AsyncValue<Fresh<OutstandingReport>> state = ref.watch(outstandingProvider(args));
    return _scaffold<OutstandingReport>(
      context,
      state: state,
      company: company,
      heading: kind.title,
      pick: (OutstandingReport report) {
        final PartyBills? p =
            report.byParty.where((PartyBills p) => p.party == party).firstOrNull;
        return p == null ? null : _PartyView.ofBills(p);
      },
      onRefresh: () => refreshReport<OutstandingReport>(
        ref,
        outstandingProvider(args),
        (ReportsRepository repository) => repository.outstanding(
          companyId,
          kind: kind,
          asOf: asOf,
          mode: FetchMode.live,
        ),
      ),
    );
  }

  Widget _scaffold<T>(
    BuildContext context, {
    required AsyncValue<Fresh<T>> state,
    required Company? company,
    required String heading,
    required _PartyView? Function(T report) pick,
    required Future<void> Function() onRefresh,
  }) {
    final Fresh<T>? fresh = state.valueOrNull;
    final _PartyView? view = fresh == null ? null : pick(fresh.data);

    return ReportScaffold<T>(
      title: party,
      subtitle: asOf == null
          ? heading
          : '$heading · as of ${asOf!.day}/${asOf!.month}/${asOf!.year}',
      state: state,
      actions: <Widget>[
        // Waits for bills: a document of nothing is not worth sending.
        if (view != null && view.bills.isNotEmpty && company != null)
          IconButton(
            icon: const Icon(Icons.ios_share),
            tooltip: 'Preview and share',
            onPressed: () {
              final PartyBillsDocument document = PartyBillsDocument(
                party: party,
                heading: heading,
                bills: view.bills,
                total: view.net,
                overdue: view.overdue,
                advances: view.advances,
                companyName: company.name,
                // The report's own date, never "now": a past as-of must print
                // as that day, and today's figures as when they were read.
                asOf: asOf ?? fresh!.freshness.refreshedAt ?? DateTime.now(),
              );
              ShareDocument.preview(
                context,
                fileName: document.fileName,
                subject: 'Outstanding bills - $party',
                build: document.build,
              );
            },
          ),
      ],
      onRefresh: onRefresh,
      emptyBuilder: (BuildContext context) => _settled(),
      builder: (BuildContext context, T report) {
        final _PartyView? view = pick(report);
        // Settled since the list was opened, or a stale link: say so rather
        // than show an empty card that reads like a failed load.
        if (view == null || view.bills.isEmpty) {
          return <Widget>[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 48),
              child: _settled(),
            ),
          ];
        }
        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: _Summary(view: view),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(
                  children: <Widget>[
                    for (final OutstandingBill bill in view.bills) BillRow(bill: bill),
                  ],
                ),
              ),
            ),
          ),
        ];
      },
    );
  }

  Widget _settled() => EmptyState(
        icon: Icons.check_circle_outline,
        title: 'Nothing outstanding',
        message: kind == OutstandingKind.receivable
            ? '$party has no open bills.'
            : 'No open bills from $party.',
      );
}

/// One party's figures, whichever report they came from.
class _PartyView {
  _PartyView({
    required this.bills,
    required this.net,
    required this.advances,
    required this.maxDaysOverdue,
  });

  factory _PartyView.ofBills(PartyBills p) => _PartyView(
        bills: p.bills,
        net: p.total,
        advances: Money.zero,
        maxDaysOverdue: p.maxDaysOverdue,
      );

  factory _PartyView.ofGroup(GroupParty p) => _PartyView(
        bills: p.bills,
        net: p.net,
        advances: p.advances,
        maxDaysOverdue: p.daysOverdue,
      );

  final List<OutstandingBill> bills;
  final Money net;
  final Money advances;
  final int maxDaysOverdue;

  Money get overdue => bills
      .where((OutstandingBill b) => b.isOverdue && !b.isAdvance)
      .fold(Money.zero, (Money sum, OutstandingBill b) => sum + b.amount);
}

class _Summary extends StatelessWidget {
  const _Summary({required this.view});

  final _PartyView view;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Money overdue = view.overdue;
    final int count = view.bills.length;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    'Outstanding',
                    style: theme.textTheme.labelMedium?.copyWith(color: context.mutedColor),
                  ),
                  const SizedBox(height: 4),
                  Text(MoneyFormat.full(view.net), style: theme.textTheme.headlineSmall),
                  const SizedBox(height: 2),
                  Text(
                    // Named rather than left to be inferred from a net smaller
                    // than the bills: a party who has paid ahead is worth
                    // knowing about before ringing them.
                    view.advances.isZero
                        ? countOf(count, 'bill')
                        : '${countOf(count, 'bill')} · '
                            '${MoneyFormat.compact(view.advances)} advance',
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Text(
                  'Overdue',
                  style: theme.textTheme.labelMedium?.copyWith(color: context.mutedColor),
                ),
                const SizedBox(height: 4),
                Text(
                  MoneyFormat.full(overdue),
                  style: theme.textTheme.titleMedium?.copyWith(
                    color: overdue.isZero ? null : context.negativeColor,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  view.maxDaysOverdue > 0 ? 'oldest ${view.maxDaysOverdue} days' : 'none yet',
                  style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
