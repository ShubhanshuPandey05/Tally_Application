import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../money/money.dart';
import '../money/money_format.dart';
import 'charts.dart';
import 'primitives.dart';

/// A headline figure, its movement, and the shape behind it.
///
/// The amount is compact (₹12.4L) because that is what an owner reads at a
/// glance, and the exact value is one tap away on the detail screen. A tile
/// that shows ₹12,43,891.50 makes you count digits.
///
/// Every tile carries a second row of evidence under the figure -- a sparkline,
/// or a proportional meter. A number on its own says what; the shape under it
/// says whether that is good, which is the question the owner actually opened
/// the app with.
class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    this.caption,
    this.changePct,
    this.icon,
    this.onTap,
    this.tone = MetricTone.neutral,
    this.spark,
    this.meter,
    this.meterCaption,
  });

  final String label;

  /// Already formatted. Tiles carry counts as often as amounts -- "14 items
  /// running low" is a metric too -- so this is a string rather than a [Money].
  final String value;

  final String? caption;

  /// Null renders as nothing at all. Percent change against a zero baseline is
  /// not a movement of 0% or 100%; it is an unanswerable question.
  final double? changePct;

  final IconData? icon;
  final VoidCallback? onTap;
  final MetricTone tone;

  /// A short series to draw under the figure. Takes precedence over [meter]
  /// when both are given -- a trend says more than a proportion.
  final List<double>? spark;

  /// A proportion, 0..1, for figures whose context is a share rather than a
  /// history: how much of what you are owed is overdue, how much of the stock
  /// is below its reorder level.
  final double? meter;
  final String? meterCaption;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color accent = switch (tone) {
      MetricTone.positive => context.positiveColor,
      MetricTone.negative => context.negativeColor,
      MetricTone.caution => context.cautionColor,
      // The tile palette, not the scheme's primary: on the dark skins primary
      // lightens to stay readable as *text*, and a tile filled with that pale
      // blue no longer matches the identical tile on the card below it.
      MetricTone.neutral => AppTheme.tileBlue,
    };

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          // Fills its cell and spreads whatever is left over between the rows.
          // Every tile in the grid is the same height, but they do not all
          // carry the same things -- a tile with no meter under it used to end
          // early and leave a band of dead space above whatever card came
          // next, which reads as a gap in the page rather than as one short
          // tile.
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: <Widget>[
              Row(
                children: <Widget>[
                  if (icon != null) ...<Widget>[
                    IconTile(icon: icon!, colour: accent, size: 22),
                    const SizedBox(width: 7),
                  ],
                  Expanded(
                    child: Text(
                      label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelSmall
                          ?.copyWith(color: context.mutedColor),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 5),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: <Widget>[
                  Flexible(
                    child: FittedBox(
                      fit: BoxFit.scaleDown,
                      alignment: Alignment.centerLeft,
                      child: Text(value, style: theme.textTheme.titleLarge),
                    ),
                  ),
                  if (changePct != null) ...<Widget>[
                    const SizedBox(width: 6),
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: ChangeChip(changePct: changePct!),
                    ),
                  ],
                ],
              ),
              if (caption != null) ...<Widget>[
                const SizedBox(height: 2),
                Text(
                  caption!,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: context.mutedColor),
                ),
              ],
              if (spark != null && spark!.length > 1) ...<Widget>[
                const SizedBox(height: 6),
                Sparkline(values: spark!, colour: accent, height: 20),
              ] else if (meter != null) ...<Widget>[
                const SizedBox(height: 7),
                Meter(fraction: meter, colour: accent),
                if (meterCaption != null) ...<Widget>[
                  const SizedBox(height: 4),
                  Text(
                    meterCaption!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelSmall
                        ?.copyWith(color: context.mutedColor),
                  ),
                ],
              ],
            ],
          ),
        ),
      ),
    );
  }
}

enum MetricTone { neutral, positive, negative, caution }

class ChangeChip extends StatelessWidget {
  const ChangeChip({super.key, required this.changePct, this.compact = false});

  final double changePct;

  /// Drop the tinted pill and show the arrow and figure alone. For places that
  /// are already inside a coloured or crowded row, where a second pill starts
  /// competing with the figure it is annotating.
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final bool up = changePct >= 0;
    final Color colour = up ? context.positiveColor : context.negativeColor;
    final Widget body = Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Icon(up ? Icons.arrow_upward : Icons.arrow_downward,
            size: 11, color: colour),
        const SizedBox(width: 2),
        Text(
          '${changePct.abs().toStringAsFixed(changePct.abs() >= 100 ? 0 : 1)}%',
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: colour,
                fontWeight: FontWeight.w700,
              ),
        ),
      ],
    );

    if (compact) return body;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: colour.withOpacity(0.10),
        borderRadius: BorderRadius.circular(999),
      ),
      child: body,
    );
  }
}

/// Wraps a group of related rows under a heading, with an optional "See all".
class SectionCard extends StatelessWidget {
  const SectionCard({
    super.key,
    required this.title,
    required this.child,
    this.subtitle,
    this.action,
    this.onAction,
    this.icon,
    this.tint,
    this.trailing,
  });

  final String title;
  final String? subtitle;
  final Widget child;
  final String? action;
  final VoidCallback? onAction;
  final IconData? icon;

  /// Colour of the header tile. Sections are categories, so they earn one.
  final Color? tint;

  /// A control that belongs to the whole card -- the line/bars switch on the
  /// trend card, most often. Sits where [action] would, and the two are not
  /// used together: a header with a toggle *and* a link has two competing
  /// things to press.
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Padding(
            padding: EdgeInsets.fromLTRB(14, 10, trailing != null ? 14 : 6, 6),
            child: Row(
              children: <Widget>[
                if (icon != null) ...<Widget>[
                  IconTile(
                      icon: icon!, colour: tint ?? AppTheme.tileBlue, size: 26),
                  const SizedBox(width: 9),
                ],
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(title, style: theme.textTheme.titleSmall),
                      if (subtitle != null)
                        Text(
                          subtitle!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                    ],
                  ),
                ),
                if (trailing != null) trailing!,
                if (trailing == null && action != null)
                  TextButton(onPressed: onAction, child: Text(action!)),
              ],
            ),
          ),
          child,
          const SizedBox(height: 6),
        ],
      ),
    );
  }
}

/// Shown in place of a section the backend could not build.
///
/// Section-level degradation is a deliberate product behaviour: three working
/// tiles and one honest failure is far better than a whole dashboard replaced
/// by an error, which reads as "the app is down".
class SectionUnavailable extends StatelessWidget {
  const SectionUnavailable(
      {super.key, required this.title, this.reason, this.onRetry});

  final String title;
  final String? reason;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: <Widget>[
            const IconTile(
                icon: Icons.cloud_off_outlined, size: 26, quiet: true),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(title, style: theme.textTheme.titleSmall),
                  const SizedBox(height: 2),
                  Text(
                    reason ?? 'Could not read this from Tally.',
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            if (onRetry != null)
              IconButton(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh, size: 20)),
          ],
        ),
      ),
    );
  }
}

/// One name-and-amount row: cash accounts, ledger balances, bill lists.
class AmountRow extends StatelessWidget {
  const AmountRow({
    super.key,
    required this.title,
    required this.amount,
    this.subtitle,
    this.trailing,
    this.leading,
    this.onTap,
    this.emphasis = false,
  });

  final String title;
  final Money amount;
  final String? subtitle;
  final String? trailing;
  final Widget? leading;
  final VoidCallback? onTap;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Row(
          children: <Widget>[
            if (leading != null) ...<Widget>[
              leading!,
              const SizedBox(width: 12)
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontWeight: emphasis ? FontWeight.w600 : FontWeight.w500,
                    ),
                  ),
                  if (subtitle != null)
                    Text(
                      subtitle!,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelSmall
                          ?.copyWith(color: context.mutedColor),
                    ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Text(
                  MoneyFormat.full(amount),
                  style: theme.textTheme.bodyMedium
                      ?.merge(AppTheme.amount)
                      .copyWith(
                        color: amount.isNegative ? context.negativeColor : null,
                      ),
                ),
                if (trailing != null)
                  Text(
                    trailing!,
                    style: theme.textTheme.labelSmall
                        ?.copyWith(color: context.mutedColor),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
