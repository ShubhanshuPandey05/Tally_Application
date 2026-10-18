import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../../../core/widgets/primitives.dart';
import '../domain/top_report.dart';

/// Colour per group, from the fixed tile palette.
const List<Color> _tints = <Color>[
  AppTheme.tileBlue,
  AppTheme.tileAmber,
  AppTheme.tileGreen,
  AppTheme.tileRose,
  AppTheme.tileViolet,
];

/// The Top Prime Reports list, grouped by what each one ranks.
///
/// The four order reports are listed though they cannot open yet, with the
/// reason on the row. A report that silently is not there reads as "this
/// product does not do that"; one that says what it is waiting on reads as
/// what it is.
class TopReportsScreen extends StatelessWidget {
  const TopReportsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Top Prime Reports')),
      body: ContentPane(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: <Widget>[
            for (int g = 0; g < topReportCatalog.length; g++) ...<Widget>[
              if (g > 0) const SizedBox(height: 14),
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 0, 0, 8),
                child: Text(
                  topReportCatalog[g].heading,
                  style: theme.textTheme.titleMedium?.copyWith(color: context.mutedColor),
                ),
              ),
              Card(
                clipBehavior: Clip.antiAlias,
                child: Column(
                  children: <Widget>[
                    for (int i = 0; i < topReportCatalog[g].entries.length; i++) ...<Widget>[
                      if (i > 0) const Divider(indent: 62, height: 1),
                      _EntryTile(
                        entry: topReportCatalog[g].entries[i],
                        tint: _tints[g % _tints.length],
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _EntryTile extends StatelessWidget {
  const _EntryTile({required this.entry, required this.tint});

  final TopReportEntry entry;
  final Color tint;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool available = entry.unavailable == null;
    return ListTile(
      enabled: available,
      onTap: available
          ? () => context.push(
                '${Routes.topReport}?key=${Uri.encodeQueryComponent(entry.key)}',
              )
          : null,
      leading: IconTile(icon: entry.icon, colour: tint, size: 34, quiet: !available),
      title: Text(
        entry.title,
        style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
      ),
      subtitle: Text(
        entry.unavailable ?? entry.question,
        style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
      ),
      trailing: available
          ? Icon(Icons.chevron_right, color: context.mutedColor)
          : Icon(Icons.lock_clock_outlined, size: 18, color: context.mutedColor),
    );
  }
}
