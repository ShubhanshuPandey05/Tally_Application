import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/documents/share_document.dart';
import '../../../core/documents/statement_document.dart';
import '../../../core/model/date_range.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/model/financial_year.dart';
import '../../../core/widgets/period_scope.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/charts.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../application/report_providers.dart';
import '../data/reports_repository.dart';
import '../domain/drilldown.dart';
import 'widgets/report_scaffold.dart';

/// Everything that went through one account.
///
/// The screen an owner reaches for when a figure looks wrong, laid out as
/// Tally's own Ledger Vouchers: the balance the account opened the period on,
/// each voucher with the balance it left behind, and the balance it closed on.
///
/// **Balance today** is a separate figure and stays separately labelled. A
/// period that ended last month closes on last month's balance, and letting
/// that read as what the account stands at now is how somebody pays the wrong
/// amount. It is shown only when it differs from the closing balance above it.
///
/// When the opening balance cannot be worked out -- the history around the
/// period is not all held yet -- the screen says so and the running column is
/// movement only. It never draws an account as having started from nil.
class LedgerStatementScreen extends ConsumerStatefulWidget {
  const LedgerStatementScreen({super.key, required this.ledger});

  final String ledger;

  @override
  ConsumerState<LedgerStatementScreen> createState() => _LedgerStatementScreenState();
}

class _LedgerStatementScreenState extends ConsumerState<LedgerStatementScreen>
    with YearScopedPeriod {
  /// A quarter, not a day. A statement opened on one day of movement answers
  /// nothing; the question behind this screen is always "what has been going
  /// through here lately?".
  /// The last quarter of the open year, or the whole of a closed one. A
  /// statement is read to answer "what has moved lately", and in a year that
  /// ended there is no lately.
  @override
  PeriodSelection initialPeriod(FinancialYear year) => year.isCurrent
      ? PeriodSelection(year.confine(DateRange.lastDays(90)), 'Last 90 days')
      : PeriodSelection(year.toDate, year.label);

  StatementDocument _document(LedgerStatement statement, Company company) =>
      StatementDocument(
        statement: statement,
        companyName: company.name,
        periodLabel: periodLabel,
        from: range.from,
        to: range.to,
      );

  @override
  Widget build(BuildContext context) {
    watchFinancialYear();
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
        (companyId: companyId, ledger: widget.ledger, range: range);
    final AsyncValue<Fresh<LedgerStatement>> state =
        ref.watch(ledgerStatementProvider(args));
    final Company? company = ref.watch(activeCompanyProvider).valueOrNull;

    return ReportScaffold<LedgerStatement>(
      title: widget.ledger,
      subtitle: periodLabel,
      state: state,
      actions: <Widget>[
        // A statement of nothing is not worth sending, and neither is one
        // built over a skeleton, so the button waits for rows.
        if ((state.valueOrNull?.data.entries.isNotEmpty ?? false) && company != null)
          IconButton(
            icon: const Icon(Icons.ios_share),
            tooltip: 'Preview and share',
            onPressed: () {
              final StatementDocument document =
                  _document(state.valueOrNull!.data, company);
              ShareDocument.preview(
                context,
                fileName: document.fileName,
                subject: '${widget.ledger} statement from ${company.name}',
                build: document.build,
              );
            },
          ),
      ],
      // No live read: this screen is reached by tapping, and a customer walking
      // through five ledgers must not queue five exports against the PC running
      // their till. It does ask the server again, though -- re-reading the
      // provider would only answer from the phone's own copy.
      onRefresh: () => refreshReport<LedgerStatement>(
        ref,
        ledgerStatementProvider(args),
        (ReportsRepository repository) => repository.ledgerStatement(
          companyId,
          ledger: widget.ledger,
          range: range,
        ),
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(48),
        child: SizedBox(
          height: 48,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Align(
              alignment: Alignment.centerLeft,
              child: PeriodField(label: periodLabel, onTap: pickPeriod),
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
          onPressed: pickPeriod,
          icon: const Icon(Icons.date_range),
          label: const Text('Change period'),
        ),
      ),
      builder: (BuildContext context, LedgerStatement statement) {
        if (statement.entries.isEmpty) return const <Widget>[];
        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
            child: _StatementSummary(
              statement: statement,
              periodLabel: periodLabel,
              from: range.from,
              to: range.to,
            ),
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
                  if (statement.hasBalances)
                    _BalanceRow(
                      label: 'Opening balance',
                      date: range.from,
                      balance: statement.openingBalance!,
                    ),
                  for (final StatementEntry entry in statement.entries)
                    _StatementRow(entry: entry),
                  if (statement.hasBalances)
                    _BalanceRow(
                      label: 'Closing balance',
                      date: range.to,
                      balance: statement.periodClosingBalance!,
                    ),
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
  const _StatementSummary({
    required this.statement,
    required this.periodLabel,
    required this.from,
    required this.to,
  });

  final LedgerStatement statement;
  final String periodLabel;
  final DateTime from;
  final DateTime to;

  @override
  Widget build(BuildContext context) {
    final Money? today = statement.closingBalance;
    final Money? closing = statement.periodClosingBalance;
    // The same figure twice under two names would only make a reader wonder
    // what the difference was.
    final bool todayDiffers = today != null &&
        (closing == null || today.amount != closing.amount || today.side != closing.side);

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
                label: statement.hasBalances ? 'Balance' : 'Running',
                colour: AppTheme.tileBlue,
                values: <double>[
                  // The line starts where the account did, not at its first
                  // voucher: an opening balance of two lakh is most of the
                  // picture for an account that then moved a few thousand.
                  if (statement.hasBalances) statement.openingBalance!.asDouble,
                  for (final StatementEntry e in statement.entries) e.running.asDouble,
                ],
              ),
            ],
            dates: <DateTime>[
              if (statement.hasBalances) from,
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
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Container(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
              decoration: BoxDecoration(
                color: context.surfaceColor,
                borderRadius: BorderRadius.circular(AppTheme.radiusTile),
              ),
              child: Column(
                children: <Widget>[
                  _BalanceLine(
                    icon: Icons.first_page,
                    label: 'Opening balance',
                    caption: _shortDate(from),
                    balance: statement.openingBalance,
                  ),
                  if (statement.hasBalances)
                    _BalanceLine(
                      icon: Icons.last_page,
                      label: 'Closing balance',
                      caption: _shortDate(to),
                      balance: closing,
                    ),
                  // Tally reports this one as the account stands now, whatever
                  // period is on screen. Kept apart from the closing balance
                  // and named for what it is whenever the two are not the same
                  // figure -- and always when there is no closing balance to
                  // confuse it with.
                  if (todayDiffers || !statement.hasBalances)
                    _BalanceLine(
                      icon: Icons.today,
                      label: 'Balance today',
                      balance: today,
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

/// One named balance in the summary. A balance that could not be read says
/// "not available" -- never a zero standing in for a missing figure.
class _BalanceLine extends StatelessWidget {
  const _BalanceLine({
    required this.icon,
    required this.label,
    required this.balance,
    this.caption,
  });

  final IconData icon;
  final String label;
  final String? caption;
  final Money? balance;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? muted =
        theme.textTheme.labelMedium?.copyWith(color: context.mutedColor);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: <Widget>[
          Icon(icon, size: 16, color: context.mutedColor),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              caption == null ? label : '$label · $caption',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: muted,
            ),
          ),
          const SizedBox(width: 8),
          Text(
            balance == null ? 'not available' : MoneyFormat.withSide(balance!),
            style: balance == null
                ? muted
                : theme.textTheme.titleSmall?.merge(AppTheme.amount),
          ),
        ],
      ),
    );
  }
}

/// The balance the entries start from, and the one they end on. Drawn in the
/// list itself, as Tally does, so the first voucher's balance has something
/// above it to be the sum of.
class _BalanceRow extends StatelessWidget {
  const _BalanceRow({required this.label, required this.date, required this.balance});

  final String label;
  final DateTime date;
  final Money balance;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 9, 16, 9),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              '${_shortDate(date)} | $label',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            MoneyFormat.withSide(balance),
            style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
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
    final String detail = <String?>[
      entry.line.voucherNumber == null ? null : 'No. ${entry.line.voucherNumber}',
      entry.line.reference == null ? null : 'Ref #: ${entry.line.reference}',
    ].whereType<String>().join(' · ');

    return InkWell(
      onTap: key == null
          ? null
          : () => context.push(
                '${Routes.voucher}?key=${Uri.encodeQueryComponent(key)}'
                '&on=${_isoDate(entry.line.date)}',
              ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 9, 16, 9),
        // No party name: every row on this screen is the same account, which
        // is already the title. The row spends its width on what tells one
        // voucher from the next.
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    '${_shortDate(entry.line.date)} | '
                    '${entry.line.voucherType ?? 'Voucher'}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium,
                  ),
                  if (detail.isNotEmpty)
                    Text(
                      detail,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelSmall
                          ?.copyWith(color: context.mutedColor),
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
                  style: theme.textTheme.bodyMedium?.merge(AppTheme.amount).copyWith(
                        color: isDebit ? context.positiveColor : context.negativeColor,
                      ),
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

/// `04 Jul 26` -- the year matters here, since a statement can span two.
String _shortDate(DateTime value) =>
    '${value.day.toString().padLeft(2, '0')} ${_months[value.month - 1]} '
    '${(value.year % 100).toString().padLeft(2, '0')}';

const List<String> _months = <String>[
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

String _isoDate(DateTime value) =>
    '${value.year.toString().padLeft(4, '0')}-'
    '${value.month.toString().padLeft(2, '0')}-'
    '${value.day.toString().padLeft(2, '0')}';
