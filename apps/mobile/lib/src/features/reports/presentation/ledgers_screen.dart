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

class _LedgersScreenState extends ConsumerState<LedgersScreen> {
  String _search = '';

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
      onRefresh: () => refreshReport<LedgerReport>(
        ref,
        ledgersProvider(args),
        (ReportsRepository repository) =>
            repository.ledgers(companyId, mode: FetchMode.live),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(56),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
          child: TextField(
            onChanged: (String value) => setState(() => _search = value),
            decoration: const InputDecoration(
              hintText: 'Search ledgers',
              prefixIcon: Icon(Icons.search),
              isDense: true,
            ),
          ),
        ),
      ),
      emptyBuilder: (BuildContext context) => const EmptyState(
        icon: Icons.account_balance_outlined,
        title: 'No ledgers',
        message: 'TallyPrime returned no ledger accounts for this company.',
      ),
      builder: (BuildContext context, LedgerReport report) {
        final String needle = _search.trim().toLowerCase();
        final List<LedgerLine> rows = needle.isEmpty
            ? report.ledgers
            : report.ledgers
                .where((LedgerLine line) =>
                    line.name.toLowerCase().contains(needle) ||
                    (line.group ?? '').toLowerCase().contains(needle))
                .toList(growable: false);

        if (report.ledgers.isEmpty) return const <Widget>[];

        final Map<String, List<LedgerLine>> byGroup = <String, List<LedgerLine>>{};
        for (final LedgerLine line in rows) {
          byGroup.putIfAbsent(line.group ?? 'Other', () => <LedgerLine>[]).add(line);
        }
        final List<String> groups = byGroup.keys.toList()..sort();

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
