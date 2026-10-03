import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/documents/ledger_group_document.dart';
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
import 'widgets/ledger_row.dart';
import 'widgets/report_scaffold.dart';

/// One Tally group on a page of its own -- the same rows the Ledgers screen
/// folds open, with a way to send them.
///
/// Reads the same ledger report as that screen rather than a group-scoped one,
/// so arriving here costs nothing and the two can never disagree on a balance.
class LedgerGroupScreen extends ConsumerWidget {
  const LedgerGroupScreen({super.key, required this.group});

  final String group;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
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
    final Company? company = ref.watch(activeCompanyProvider).valueOrNull;
    final List<LedgerLine> lines = _linesOf(state.valueOrNull?.data);

    return ReportScaffold<LedgerReport>(
      title: group,
      subtitle: state.valueOrNull == null ? null : countOf(lines.length, 'ledger'),
      state: state,
      actions: <Widget>[
        if (lines.isNotEmpty && company != null)
          IconButton(
            icon: const Icon(Icons.ios_share),
            tooltip: 'Preview and share',
            onPressed: () {
              final LedgerGroupDocument document = LedgerGroupDocument(
                group: group,
                lines: lines,
                total: _total(lines),
                companyName: company.name,
                asOf: state.valueOrNull!.freshness.refreshedAt ?? DateTime.now(),
              );
              ShareDocument.preview(
                context,
                fileName: document.fileName,
                subject: '$group from ${company.name}',
                build: document.build,
              );
            },
          ),
      ],
      onRefresh: () => refreshReport<LedgerReport>(
        ref,
        ledgersProvider(args),
        (ReportsRepository repository) =>
            repository.ledgers(companyId, mode: FetchMode.live),
      ),
      emptyBuilder: (BuildContext context) => const EmptyState(
        icon: Icons.account_balance_outlined,
        title: 'No ledgers',
        message: 'TallyPrime returned no ledger accounts for this company.',
      ),
      builder: (BuildContext context, LedgerReport report) {
        if (report.ledgers.isEmpty) return const <Widget>[];
        final List<LedgerLine> rows = _linesOf(report);
        if (rows.isEmpty) {
          return <Widget>[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 48),
              child: EmptyState(
                icon: Icons.search_off,
                title: 'No ledgers in this group',
                message: 'Nothing in this company sits under $group.',
              ),
            ),
          ];
        }
        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
            child: Card(
              child: Column(
                children: <Widget>[
                  _TotalRow(total: _total(rows)),
                  const Divider(height: 1, indent: 16, endIndent: 16),
                  const SizedBox(height: 4),
                  for (final LedgerLine line in rows)
                    LedgerRow(
                      line: line,
                      subtitle: (line.gstin?.isNotEmpty ?? false) ? line.gstin : null,
                    ),
                  const SizedBox(height: 8),
                ],
              ),
            ),
          ),
        ];
      },
    );
  }

  /// Largest balance first: the page is opened to find who matters in a group.
  List<LedgerLine> _linesOf(LedgerReport? report) {
    if (report == null) return const <LedgerLine>[];
    return report.ledgers
        .where((LedgerLine line) => (line.group ?? 'Other') == group)
        .toList()
      ..sort((LedgerLine a, LedgerLine b) =>
          b.closing.amount.abs().compareTo(a.closing.amount.abs()));
  }

  static Money _total(List<LedgerLine> lines) => lines.fold(
        Money.zero,
        (Money sum, LedgerLine line) => sum + line.closing,
      );
}

class _TotalRow extends StatelessWidget {
  const _TotalRow({required this.total});

  final Money total;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 34, 12),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              'Total today',
              style: theme.textTheme.labelMedium?.copyWith(color: context.mutedColor),
            ),
          ),
          Text(
            MoneyFormat.withSide(total),
            style: theme.textTheme.titleSmall?.merge(AppTheme.amount),
          ),
        ],
      ),
    );
  }
}
