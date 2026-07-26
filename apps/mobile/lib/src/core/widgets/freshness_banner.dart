import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../model/freshness.dart';

/// The "as of" line that sits above every set of figures.
///
/// Never hidden when data is stale or the PC is offline. An owner acting on a
/// number they believe is live -- deciding whether to accept a cheque, whether
/// stock is on the shelf -- is the concrete harm this widget exists to prevent.
class FreshnessBanner extends StatelessWidget {
  const FreshnessBanner({
    super.key,
    required this.freshness,
    this.onRefresh,
    this.dense = false,
  });

  final Freshness freshness;
  final VoidCallback? onRefresh;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    if (!freshness.needsAttention) {
      return Padding(
        padding: EdgeInsets.symmetric(horizontal: 16, vertical: dense ? 4 : 8),
        child: Row(
          children: <Widget>[
            Icon(Icons.check_circle_outline, size: 14, color: context.positiveColor),
            const SizedBox(width: 6),
            Text(
              freshness.label,
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
            ),
          ],
        ),
      );
    }

    final _BannerStyle style = _styleFor(freshness, context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: style.colour.withOpacity(0.10),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: style.colour.withOpacity(0.30)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(style.icon, size: 18, color: style.colour),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    style.title,
                    style: theme.textTheme.labelLarge?.copyWith(color: style.colour),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    style.detail,
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
            if (onRefresh != null)
              IconButton(
                onPressed: onRefresh,
                icon: const Icon(Icons.refresh, size: 20),
                tooltip: 'Try again',
                visualDensity: VisualDensity.compact,
              ),
          ],
        ),
      ),
    );
  }

  _BannerStyle _styleFor(Freshness freshness, BuildContext context) {
    if (!freshness.connectorOnline) {
      return _BannerStyle(
        icon: Icons.cloud_off_outlined,
        colour: context.cautionColor,
        title: 'Your Tally PC is offline',
        detail: freshness.available
            ? 'Showing the last figures we read. ${freshness.label}.'
            : 'We have not been able to read anything yet.',
      );
    }
    return _BannerStyle(
      icon: Icons.history,
      colour: context.cautionColor,
      title: 'These figures are not current',
      detail: '${freshness.label}. Pull down to refresh.',
    );
  }
}

class _BannerStyle {
  const _BannerStyle({
    required this.icon,
    required this.colour,
    required this.title,
    required this.detail,
  });

  final IconData icon;
  final Color colour;
  final String title;
  final String detail;
}
