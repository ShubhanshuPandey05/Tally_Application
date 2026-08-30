import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/date_range.dart';
import '../../../core/model/figures.dart';
import '../../../core/model/freshness.dart';
import '../../../core/money/money_format.dart';
import '../../../core/widgets/cards.dart';
import '../../../core/widgets/charts.dart';
import '../../../core/widgets/period_picker.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../application/report_providers.dart';
import '../domain/drilldown.dart';
import 'widgets/report_scaffold.dart';

/// One stock item's movement: what came in, what went out, on which vouchers.
///
/// Quantities rather than money lead this screen. A stock enquiry is nearly
/// always "how did I end up with this many?", and the answer is a column of
/// ins and outs -- the value is the supporting figure, not the headline.
class StockItemScreen extends ConsumerStatefulWidget {
  const StockItemScreen({super.key, required this.item});

  final String item;

  @override
  ConsumerState<StockItemScreen> createState() => _StockItemScreenState();
}

class _StockItemScreenState extends ConsumerState<StockItemScreen> {
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
          message: 'Connect your Tally PC to see stock movement.',
        ),
      );
    }

    final ItemMovementArgs args =
        (companyId: companyId, item: widget.item, range: _range);
    final AsyncValue<Fresh<ItemMovementReport>> state =
        ref.watch(itemMovementProvider(args));

    return ReportScaffold<ItemMovementReport>(
      title: widget.item,
      subtitle: _periodLabel,
      state: state,
      onRefresh: () async {
        ref.invalidate(itemMovementProvider(args));
        await ref.read(itemMovementProvider(args).future);
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
        icon: Icons.inventory_2_outlined,
        title: 'Nothing moved',
        // Never a row of zeroes: "no vouchers touched this item" and "the item
        // moved nothing" are different statements about a shop's stock.
        message: 'No voucher moved ${widget.item} in this period. Try a longer one.',
        action: FilledButton.tonalIcon(
          onPressed: _pickPeriod,
          icon: const Icon(Icons.date_range),
          label: const Text('Change period'),
        ),
      ),
      builder: (BuildContext context, ItemMovementReport report) {
        if (report.movements.isEmpty) return const <Widget>[];
        final bool hasUnclassified = report.movements
            .any((ItemMovementLine m) => m.direction == MovementDirection.other);

        return <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: SectionCard(
              title: 'Movement',
              subtitle:
                  '${countOf(report.voucherCount, 'voucher')} · $_periodLabel',
              icon: Icons.swap_vert,
              tint: AppTheme.tileAmber,
              child: Column(
                children: <Widget>[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 2, 16, 10),
                    child: DonutBreakdown(
                      centreLabel: 'net',
                      centreValue: MoneyFormat.quantity(
                        report.netQuantity,
                        report.unit,
                      ),
                      slices: <DonutSlice>[
                        DonutSlice(
                          label: 'In',
                          value: report.quantityIn,
                          colour: AppTheme.tileGreen,
                          display: MoneyFormat.quantity(report.quantityIn, report.unit),
                        ),
                        DonutSlice(
                          label: 'Out',
                          value: report.quantityOut,
                          colour: AppTheme.tileRose,
                          display:
                              MoneyFormat.quantity(report.quantityOut, report.unit),
                        ),
                      ],
                    ),
                  ),
                  StatStrip(
                    stats: <Stat>[
                      // "None came in" rather than "₹0 came in". Nothing
                      // was bought in this window, and a zero rupee figure
                      // reads as a purchase that cost nothing.
                      Stat(
                        label: 'Bought',
                        value: report.quantityIn == 0
                            ? 'none'
                            : MoneyFormat.compact(report.valueIn),
                      ),
                      Stat(
                        label: 'Sold',
                        value: report.quantityOut == 0
                            ? 'none'
                            : MoneyFormat.compact(report.valueOut),
                      ),
                      Stat(
                        label: 'Net units',
                        value: MoneyFormat.quantity(report.netQuantity, report.unit),
                        colour: report.netQuantity < 0 ? context.negativeColor : null,
                      ),
                    ],
                  ),
                  if (hasUnclassified) const _UnclassifiedNote(),
                ],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
            child: SectionCard(
              title: 'Vouchers',
              subtitle: 'Newest first',
              icon: Icons.receipt_long_outlined,
              tint: AppTheme.tileAmber,
              child: Column(
                children: <Widget>[
                  for (final ItemMovementLine move in report.movements)
                    _MovementRow(move: move),
                ],
              ),
            ),
          ),
        ];
      },
    );
  }
}

/// One voucher's effect on the item, with the direction stated rather than
/// implied by a sign.
class _MovementRow extends StatelessWidget {
  const _MovementRow({required this.move});

  final ItemMovementLine move;

  @override
  Widget build(BuildContext context) {
    final (Color colour, IconData icon, String label) = switch (move.direction) {
      MovementDirection.inward => (context.positiveColor, Icons.south_west, 'In'),
      MovementDirection.outward => (context.negativeColor, Icons.north_east, 'Out'),
      MovementDirection.other => (context.mutedColor, Icons.swap_horiz, 'Adj'),
    };

    final ThemeData theme = Theme.of(context);
    final String? link = voucherLink(move.line);

    return InkWell(
      onTap: link == null ? null : () => context.push(link),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 9, 16, 9),
        child: Row(
          children: <Widget>[
            Container(
              width: 32,
              height: 32,
              decoration: BoxDecoration(
                color: colour.withOpacity(0.10),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Icon(icon, size: 16, color: colour),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    move.line.party ?? move.line.voucherType ?? 'Voucher',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(fontWeight: FontWeight.w600),
                  ),
                  Text(
                    <String?>[
                      move.line.voucherType,
                      move.line.voucherNumber == null
                          ? null
                          : '#${move.line.voucherNumber}',
                      '${move.line.date.day}/${move.line.date.month}',
                    ].whereType<String>().join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                // The quantity is what this screen is about, so it takes the
                // place the amount holds on every other voucher row, and the
                // value drops to the line beneath it. The direction is a word,
                // not a sign: "-40" beside a sale is ambiguous about whether
                // the shop lost stock or lost money.
                Text(
                  '$label ${MoneyFormat.quantity(move.quantity, move.unit)}',
                  style: theme.textTheme.bodyMedium
                      ?.merge(AppTheme.amount)
                      .copyWith(color: colour),
                ),
                Text(
                  MoneyFormat.full(move.value),
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

/// Stock journals and physical verifications are in neither total. Saying so is
/// the difference between an owner reconciling a shortfall and one wondering
/// why the arithmetic on screen does not add up.
class _UnclassifiedNote extends StatelessWidget {
  const _UnclassifiedNote();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.info_outline, size: 14, color: context.cautionColor),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              'Some vouchers here are adjustments rather than a purchase or a '
              'sale. They are listed, but not counted in the in and out totals.',
              style: theme.textTheme.labelSmall?.copyWith(color: context.cautionColor),
            ),
          ),
        ],
      ),
    );
  }
}
