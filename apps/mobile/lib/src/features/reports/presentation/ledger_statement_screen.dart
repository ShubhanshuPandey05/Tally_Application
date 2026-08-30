import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/date_range.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/charts.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../application/report_providers.dart';
import '../domain/drilldown.dart';
import 'widgets/report_scaffold.dart';

/// Everything that went through one account.
///
/// The screen an owner reaches for when a figure looks wrong. Two numbers on it
/// mean different things and the difference is the whole point of the layout:
/// the **running** column is movement inside the chosen window, and the
/// **balance today** is what Tally says the account stands at right now. Tally
/// evaluates a closing balance against the current date and accepts no date to
/// evaluate against, so the second can never be the end of the first, and the
/// screen must not let them read as one column.
class LedgerStatementScreen extends ConsumerStatefulWidget {
  const LedgerStatementScreen({super.key, required this.ledger});

  final String ledger;

  @override
  ConsumerState<LedgerStatementScreen> createState() => _LedgerStatementScreenState();
}

class _LedgerStatementScreenState extends ConsumerState<LedgerStatementScreen> {
  /// A quarter, not a day. A statement opened on one day of movement answers
  /// nothing; the question behind this screen is always "what has been going
  /// through here lately?".
  DateRange _range = DateRange.lastDays(90);
  String _periodLabel = 'Last 90 days';

  Future<void> _pickPeriod() async {
    final PeriodSelection? picked = await showPeriodPicker(context, current: _range);
    if (picked == null) return;
    setState(() {
      _range = picked.range;
      _periodLabel = picked.label;
    });
  }

  @override
  Widget build(BuildContext context) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to see a ledger statement.',
        ),
      );
    }

    final LedgerStatementArgs args =
        (companyId: companyId, ledger: widget.ledger, range: _range);
    final AsyncValue<Fresh<LedgerStatement>> state =
        ref.watch(ledgerStatementProvider(args));

    return ReportScaffold<LedgerStatement>(
      title: widget.ledger,
      subtitle: _periodLabel,
      state: state,
      // No live read: this screen is reached by tapping, and a customer walking
      // through five ledgers must not queue five exports against the PC running
      // their till. Re-reading the provider is the honest refresh here.
      onRefresh: () async {
        ref.invalidate(ledgerStatementProvider(args));
        await ref.read(ledgerStatementProvider(args).future);
      },
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(48),
        child: SizedBox(
          height: 48,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Align(
              alignment: Alignment.centerLeft,
              child: PeriodField(label: _periodLabel, onTap: _pickPeriod),
            ),
          ),
        ),
      ),
      emptyBuilder: (BuildContext context) => EmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'Nothing went through this account',
        message: 'No voucher touched ${widget.ledger} in this period. '
            'Try a longer one.',
        action: FilledButton.tonalIcon(
          onPressed: _pickPeriod,
          icon: const Icon(Icons.date_range),
          label: const Text('Change period'),
        ),
      ),
      builder: (BuildContext context, LedgerStatement statement) {
        if (statement.entries.isEmpty) return const <Widget>[];
        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: _StatementSummary(statement: statement, periodLabel: _periodLabel),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: SectionCard(
              title: 'Entries',
              subtitle:
                  '${countOf(statement.voucherCount, 'voucher')}, oldest first',
              icon: Icons.list_alt_outlined,
              child: Column(
                children: <Widget>[
                  for (final StatementEntry entry in statement.entries)
                    _StatementRow(entry: entry),
                ],
              ),
            ),
          ),
        ];
      },
    );
  }
}

class _StatementSummary extends StatelessWidget {
  const _StatementSummary({required this.statement, required this.periodLabel});

  final LedgerStatement statement;
  final String periodLabel;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return SectionCard(
      title: 'Movement',
      subtitle: statement.group == null
          ? periodLabel
          : '${statement.group} · $periodLabel',
      icon: Icons.account_balance_outlined,
      child: Column(
        children: <Widget>[
          // The running balance, drawn. A column of forty rows answers "what
          // happened"; the line answers "which way has this account been
          // going", which is the question somebody opens a statement with.
          //
          // Two entries at least. One movement has no direction to show, and a
          // chart frame holding "not enough history yet" spends a third of the
          // card saying nothing -- worse than a card that simply starts with
          // its figures.
          if (statement.entries.length > 1)
            TrendChart(
            series: <ChartSeries>[
              ChartSeries(
                label: 'Running',
                colour: AppTheme.tileBlue,
                values: <double>[
                  for (final StatementEntry e in statement.entries) e.running.asDouble,
                ],
              ),
            ],
            dates: <DateTime>[
              for (final StatementEntry e in statement.entries) e.line.date,
            ],
            currency: statement.netMovement.currency,
            height: 150,
          ),
          if (statement.entries.length > 1) ...<Widget>[
            const SizedBox(height: 6),
            const Divider(indent: 16, endIndent: 16),
          ],
          const SizedBox(height: 10),
          StatStrip(
            stats: <Stat>[
              Stat(label: 'Debits', value: MoneyFormat.compact(statement.debitTotal)),
              Stat(label: 'Credits', value: MoneyFormat.compact(statement.creditTotal)),
              Stat(
                label: 'Net',
                value: MoneyFormat.withSide(statement.netMovement),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Deliberately below the strip and separately labelled. It is not the
          // end of the running column, and putting it in the strip alongside
          // the movement figures is exactly how a reader would conclude that it
          // was.
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Container(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
              decoration: BoxDecoration(
                color: context.surfaceColor,
                borderRadius: BorderRadius.circular(AppTheme.radiusTile),
              ),
              child: Row(
                children: <Widget>[
                  Icon(Icons.today, size: 16, color: context.mutedColor),
                  const SizedBox(width: 9),
                  Expanded(
                    child: Text(
                      // Tally reports a closing balance as it stands now,
                      // whatever window is on screen. Saying so is the
                      // difference between a statement an owner can reconcile
                      // and one that quietly mixes two dates.
                      'Balance today',
                      style: theme.textTheme.labelMedium
                          ?.copyWith(color: context.mutedColor),
                    ),
                  ),
                  Text(
                    // Never a zero standing in for a missing read.
                    statement.closingBalance == null
                        ? 'not available'
                        : MoneyFormat.withSide(statement.closingBalance!),
                    style: statement.closingBalance == null
                        ? theme.textTheme.labelMedium
                            ?.copyWith(color: context.mutedColor)
                        : theme.textTheme.titleSmall?.merge(AppTheme.amount),
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

/// One line of the statement: what the voucher was, what it moved, where the
/// running total stood after it.
class _StatementRow extends StatelessWidget {
  const _StatementRow({required this.entry});

  final StatementEntry entry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool isDebit = entry.movement.side == MoneySide.debit;
    final String? key = entry.line.key;

    return InkWell(
      onTap: key == null
          ? null
          : () => context.push(
                '${Routes.voucher}?key=${Uri.encodeQueryComponent(key)}'
                '&on=${_isoDate(entry.line.date)}',
              ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 9, 16, 9),
        child: Row(
          children: <Widget>[
            SizedBox(
              width: 44,
              child: Text(
                '${entry.line.date.day}/${entry.line.date.month}',
                style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    entry.line.party ?? entry.line.voucherType ?? 'Voucher',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(fontWeight: FontWeight.w600),
                  ),
                  Text(
                    <String?>[
                      entry.line.voucherType,
                      entry.line.voucherNumber == null
                          ? null
                          : '#${entry.line.voucherNumber}',
                    ].whereType<String>().join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Text(
                  '${MoneyFormat.full(entry.movement)} ${isDebit ? 'Dr' : 'Cr'}',
                  style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
                ),
                Text(
                  MoneyFormat.withSide(entry.running),
                  style:
                      theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

String _isoDate(DateTime value) =>
    '${value.year.toString().padLeft(4, '0')}-'
    '${value.month.toString().padLeft(2, '0')}-'
    '${value.day.toString().padLeft(2, '0')}';
