import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../money/money.dart';
import '../money/money_format.dart';

/// A headline figure with its context.
///
/// The amount is compact (₹12.4L) because that is the shape an owner reads at a
/// glance, and the exact value is one tap away on the detail screen. A tile
/// that shows ₹1,243,891.50 makes you count digits.
class KpiCard extends StatelessWidget {
  const KpiCard({
    super.key,
    required this.label,
    required this.amount,
    this.caption,
    this.changePct,
    this.icon,
    this.onTap,
    this.tone = KpiTone.neutral,
  });

  final String label;
  final Money amount;
  final String? caption;

  /// Null renders as "--". Percent change against a zero baseline is not a
  /// movement of 0% or 100%; it is an unanswerable question.
  final double? changePct;

  final IconData? icon;
  final VoidCallback? onTap;
  final KpiTone tone;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color accent = switch (tone) {
      KpiTone.positive => context.positiveColor,
      KpiTone.negative => context.negativeColor,
      KpiTone.caution => context.cautionColor,
      KpiTone.neutral => theme.colorScheme.primary,
    };

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Row(
                children: <Widget>[
                  if (icon != null) ...<Widget>[
                    Icon(icon, size: 16, color: accent),
                    const SizedBox(width: 6),
                  ],
                  Expanded(
                    child: Text(
                      label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelMedium
                          ?.copyWith(color: context.mutedColor),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.centerLeft,
                child: Text(
                  MoneyFormat.compact(amount),
                  style: theme.textTheme.headlineSmall?.copyWith(color: accent),
                ),
              ),
              const SizedBox(height: 6),
              Row(
                children: <Widget>[
                  if (changePct != null) ChangeChip(changePct: changePct!),
                  if (changePct != null && caption != null) const SizedBox(width: 6),
                  if (caption != null)
                    Expanded(
                      child: Text(
                        caption!,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: context.mutedColor),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

enum KpiTone { neutral, positive, negative, caution }

class ChangeChip extends StatelessWidget {
  const ChangeChip({super.key, required this.changePct});

  final double changePct;

  @override
  Widget build(BuildContext context) {
    final bool up = changePct >= 0;
    final Color colour = up ? context.positiveColor : context.negativeColor;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: colour.withOpacity(0.10),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(up ? Icons.arrow_upward : Icons.arrow_downward, size: 11, color: colour),
          const SizedBox(width: 2),
          Text(
            '${changePct.abs().toStringAsFixed(changePct.abs() >= 100 ? 0 : 1)}%',
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: colour,
                  fontWeight: FontWeight.w700,
                ),
          ),
        ],
      ),
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
  });

  final String title;
  final String? subtitle;
  final Widget child;
  final String? action;
  final VoidCallback? onAction;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 8, 8),
            child: Row(
              children: <Widget>[
                if (icon != null) ...<Widget>[
                  Icon(icon, size: 18, color: theme.colorScheme.primary),
                  const SizedBox(width: 8),
                ],
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(title, style: theme.textTheme.titleSmall),
                      if (subtitle != null)
                        Text(
                          subtitle!,
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                    ],
                  ),
                ),
                if (action != null)
                  TextButton(onPressed: onAction, child: Text(action!)),
              ],
            ),
          ),
          child,
          const SizedBox(height: 8),
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
  const SectionUnavailable({super.key, required this.title, this.reason, this.onRetry});

  final String title;
  final String? reason;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: <Widget>[
            Icon(Icons.cloud_off_outlined, size: 20, color: context.mutedColor),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(title, style: theme.textTheme.titleSmall),
                  const SizedBox(height: 2),
                  Text(
                    reason ?? 'Could not read this from Tally.',
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            if (onRetry != null)
              IconButton(onPressed: onRetry, icon: const Icon(Icons.refresh, size: 20)),
          ],
        ),
      ),
    );
  }
}

/// One name-and-amount row: top customers, cash accounts, ledger balances.
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
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(
          children: <Widget>[
            if (leading != null) ...<Widget>[leading!, const SizedBox(width: 12)],
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
                      style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
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
                  style: theme.textTheme.bodyMedium?.merge(AppTheme.amount).copyWith(
                        color: amount.isNegative ? context.negativeColor : null,
                      ),
                ),
                if (trailing != null)
                  Text(
                    trailing!,
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
