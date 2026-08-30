import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/charts.dart';
import '../../../core/widgets/freshness_banner.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../application/report_providers.dart';
import '../domain/drilldown.dart';

/// One voucher, in full.
///
/// The deepest screen in the product and the only one an accountant will check
/// against TallyPrime itself, so it shows what was posted rather than a reading
/// of it: every ledger line on its own side, every stock line with the
/// arithmetic that produced its amount, and both totals so the reader can see
/// the voucher balances.
class VoucherScreen extends ConsumerWidget {
  const VoucherScreen({super.key, required this.voucherKey, required this.on});

  /// The identity the tapped row carried. Rows without one are not tappable,
  /// so this screen is never reached with nothing to look up.
  final String voucherKey;

  /// The voucher's own date. Narrows the lookup to one day, which is what
  /// keeps opening a voucher cheap however long a company's history is.
  final DateTime on;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      return const Scaffold(
        body: EmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No company connected',
          message: 'Connect your Tally PC to open a voucher.',
        ),
      );
    }

    final VoucherArgs args = (companyId: companyId, key: voucherKey, on: on);
    final AsyncValue<Fresh<VoucherDetail>> state = ref.watch(voucherProvider(args));

    return Scaffold(
      appBar: AppBar(title: const Text('Voucher')),
      body: ContentPane(
        child: state.when(
          loading: () => const _VoucherSkeleton(),
          error: (Object error, StackTrace stack) => ErrorState(
            error: error,
            onRetry: () => ref.invalidate(voucherProvider(args)),
          ),
          data: (Fresh<VoucherDetail> result) =>
              _VoucherBody(detail: result.data, freshness: result.freshness),
        ),
      ),
    );
  }
}

class _VoucherBody extends StatelessWidget {
  const _VoucherBody({required this.detail, required this.freshness});

  final VoucherDetail detail;
  final Freshness freshness;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return ListView(
      padding: const EdgeInsets.only(bottom: 32),
      children: <Widget>[
        FreshnessBanner(freshness: freshness, dense: true),
        if (detail.isExcluded) const _ExcludedNotice(),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
          child: HeroCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    IconTile(
                      icon: _kindIcon(detail.kind),
                      size: 30,
                      background: const Color(0x1FFFFFFF),
                      foreground: Colors.white,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        <String?>[
                          detail.voucherType,
                          detail.voucherNumber == null
                              ? null
                              : '#${detail.voucherNumber}',
                        ].whereType<String>().join('  '),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.labelMedium
                            ?.copyWith(color: const Color(0xFF9AA0AE)),
                      ),
                    ),
                    Text(
                      _fullDate(detail.date),
                      style: theme.textTheme.labelMedium
                          ?.copyWith(color: const Color(0xFF9AA0AE)),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                FittedBox(
                  fit: BoxFit.scaleDown,
                  alignment: Alignment.centerLeft,
                  child: Text(
                    // Exact, to the paisa. This is the screen somebody
                    // reconciles against Tally, so the rounding that is right
                    // on a dashboard tile is wrong here.
                    MoneyFormat.full(detail.amount),
                    style: theme.textTheme.headlineMedium?.copyWith(color: Colors.white),
                  ),
                ),
                if (detail.party != null) ...<Widget>[
                  const SizedBox(height: 4),
                  Text(
                    detail.party!,
                    style: theme.textTheme.bodyMedium?.copyWith(color: Colors.white),
                  ),
                ],
              ],
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
          child: SectionCard(
            title: 'Accounting',
            subtitle: countOf(detail.ledgerEntries.length, 'ledger entry',
                'ledger entries'),
            icon: Icons.account_balance_outlined,
            child: Column(
              children: <Widget>[
                for (final VoucherLedgerLine entry in detail.ledgerEntries)
                  _LedgerEntryRow(entry: entry),
                const Divider(indent: 16, endIndent: 16),
                const SizedBox(height: 8),
                StatStrip(
                  stats: <Stat>[
                    // Exact, not compact. These two are meant to be equal, and
                    // two rounded figures can look equal while the vouchers
                    // they came from are out by a rupee -- which is the whole
                    // thing this row exists to let a reader check.
                    Stat(
                      label: 'Debit',
                      value: MoneyFormat.full(detail.debitTotal),
                    ),
                    Stat(
                      label: 'Credit',
                      value: MoneyFormat.full(detail.creditTotal),
                    ),
                    // Not decoration. Sides that disagree mean a line was
                    // dropped on the way in, and without this the reader
                    // cannot tell that from a voucher with one entry.
                    Stat(
                      label: 'Balanced',
                      value: detail.balances ? 'yes' : 'no',
                      colour:
                          detail.balances ? context.positiveColor : context.negativeColor,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
        if (detail.inventoryOmitted)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: SectionCard(
              title: 'Items',
              subtitle: 'not kept for a voucher this old',
              icon: Icons.inventory_2_outlined,
              tint: AppTheme.tileAmber,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: Text(
                  'Stock lines are only stored for recent vouchers, so we cannot '
                  'show what was on this one. The accounting entries above are '
                  'complete.',
                  style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                ),
              ),
            ),
          )
        else if (detail.inventoryEntries.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: SectionCard(
              title: 'Items',
              subtitle: countOf(detail.inventoryEntries.length, 'stock line'),
              icon: Icons.inventory_2_outlined,
              tint: AppTheme.tileAmber,
              child: Column(
                children: <Widget>[
                  for (final VoucherStockLine item in detail.inventoryEntries)
                    _StockEntryRow(item: item),
                ],
              ),
            ),
          ),
        if (_hasNotes)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: SectionCard(
              title: 'Details',
              icon: Icons.notes_outlined,
              tint: AppTheme.tileViolet,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  if (detail.reference != null)
                    _NoteRow(label: 'Reference', value: detail.reference!),
                  if (detail.narration != null)
                    _NoteRow(label: 'Narration', value: detail.narration!),
                  if (_billReferences.isNotEmpty)
                    _NoteRow(
                      label: _billReferences.length == 1 ? 'Bill' : 'Bills',
                      value: _billReferences.join(', '),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  bool get _hasNotes =>
      detail.reference != null || detail.narration != null || _billReferences.isNotEmpty;

  /// Every bill this voucher settles or raises, across its entries. Shown once
  /// at the bottom rather than repeated on each line: the same bill reference
  /// routinely appears on both sides of a receipt.
  List<String> get _billReferences => <String>{
        for (final VoucherLedgerLine entry in detail.ledgerEntries) ...entry.billReferences,
      }.toList();

  static IconData _kindIcon(String kind) => switch (kind) {
        'sales' => Icons.trending_up,
        'purchase' => Icons.trending_down,
        'receipt' => Icons.south_west,
        'payment' => Icons.north_east,
        'credit_note' => Icons.undo,
        'debit_note' => Icons.redo,
        'journal' => Icons.swap_horiz,
        'contra' => Icons.sync_alt,
        'stock_journal' => Icons.inventory_2_outlined,
        _ => Icons.receipt_long,
      };

  static String _fullDate(DateTime date) =>
      '${date.day}/${date.month}/${date.year}';
}

/// One posted line: the ledger on the left, Dr or Cr on the right.
///
/// The side is a labelled column rather than a colour or a minus sign. An
/// accountant reads Dr/Cr; nobody reads a red number and concludes "credit".
class _LedgerEntryRow extends StatelessWidget {
  const _LedgerEntryRow({required this.entry});

  final VoucherLedgerLine entry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool isDebit = entry.amount.side == MoneySide.debit;

    return InkWell(
      // Straight from a voucher line to that ledger's own statement, which is
      // the move somebody makes when a figure looks wrong: "what else went
      // through this account?"
      onTap: () => context.push(
        '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(entry.ledger)}',
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    entry.ledger,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontWeight: entry.isParty ? FontWeight.w700 : FontWeight.w500,
                    ),
                  ),
                  if (entry.costCentre != null)
                    Text(
                      entry.costCentre!,
                      style: theme.textTheme.labelSmall
                          ?.copyWith(color: context.mutedColor),
                    ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            Container(
              width: 26,
              alignment: Alignment.center,
              child: Text(
                isDebit ? 'Dr' : 'Cr',
                style: theme.textTheme.labelSmall?.copyWith(
                  color: context.mutedColor,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            const SizedBox(width: 8),
            SizedBox(
              width: 108,
              child: Text(
                MoneyFormat.full(entry.amount),
                textAlign: TextAlign.end,
                style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// One stock line, with the arithmetic that produced its amount.
///
/// "25 KG × ₹400" is shown rather than only the total, because an owner
/// checking an invoice is checking the rate as often as the value.
class _StockEntryRow extends StatelessWidget {
  const _StockEntryRow({required this.item});

  final VoucherStockLine item;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String quantity = MoneyFormat.quantity(item.quantity, item.unit);
    final String workings = item.rate == null
        ? quantity
        : '$quantity × ${MoneyFormat.full(item.rate!)}';

    return InkWell(
      onTap: () => context.push(
        '${Routes.stockMovement}?item=${Uri.encodeQueryComponent(item.item)}',
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    item.item,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(fontWeight: FontWeight.w600),
                  ),
                  Text(
                    <String?>[workings, item.godown, item.batch]
                        .whereType<String>()
                        .join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            Text(
              MoneyFormat.full(item.amount),
              style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
            ),
          ],
        ),
      ),
    );
  }
}

class _NoteRow extends StatelessWidget {
  const _NoteRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 2, 16, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            style: theme.textTheme.labelSmall?.copyWith(
              color: context.mutedColor,
              letterSpacing: 0.7,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 3),
          Text(value, style: theme.textTheme.bodyMedium),
        ],
      ),
    );
  }
}

/// A cancelled or optional voucher is in none of the totals anywhere else in
/// the app. Showing one without saying so would be explaining a figure that
/// does not appear in any figure around it.
class _ExcludedNotice extends StatelessWidget {
  const _ExcludedNotice();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: context.cautionColor.withOpacity(0.12),
          borderRadius: BorderRadius.circular(AppTheme.radiusTile),
        ),
        child: Row(
          children: <Widget>[
            Icon(Icons.report_gmailerrorred_outlined,
                size: 18, color: context.cautionColor),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                'This voucher is cancelled or marked optional in Tally, so it is '
                'not counted in any total in this app.',
                style:
                    theme.textTheme.labelMedium?.copyWith(color: context.cautionColor),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _VoucherSkeleton extends StatelessWidget {
  const _VoucherSkeleton();

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.all(16),
        children: const <Widget>[
          SkeletonBox(height: 132, radius: 20),
          SizedBox(height: 14),
          SkeletonBox(height: 220, radius: 20),
          SizedBox(height: 14),
          SkeletonBox(height: 140, radius: 20),
        ],
      );
}
