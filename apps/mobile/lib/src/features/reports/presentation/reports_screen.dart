import 'package:flutter/material.dart';
import '../../../core/layout/adaptive.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/shell.dart';
import '../../../app/theme.dart';
import '../../../core/widgets/primitives.dart';
import '../../../core/widgets/states.dart';
import '../../companies/application/company_providers.dart';
import '../../settings/presentation/profile_button.dart';
import '../../team/application/team_providers.dart';
import '../../team/domain/access_policy.dart';
import '../domain/report_catalog.dart';

/// The report index.
///
/// Grouped by the question each one answers rather than by Tally's menu
/// structure. "Who owes me money?" is a question a shop owner has; "Bills
/// Receivable (Ledger-wise)" is a menu path they have to be trained on.
class ReportsScreen extends ConsumerWidget {
  const ReportsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);

    return Scaffold(
      appBar: AppBar(
        leading: const ProfileButton(),
        titleSpacing: 0,
        title: const Text('Reports'),
      ),
      body: ContentPane(
        child: companyId == null
            ? const EmptyState(
                icon: Icons.folder_off_outlined,
                title: 'No company connected',
                message: 'Connect your Tally PC to see reports.',
              )
            : _ReportIndex(companyId: companyId),
      ),
    );
  }
}

/// The report groups, less any report an admin has switched off for this
/// person. A section with nothing left in it is left out whole, and so is the
/// screen's content when every report is off -- with a line saying why, since
/// an empty Reports tab otherwise reads as a bug.
class _ReportIndex extends ConsumerWidget {
  const _ReportIndex({required this.companyId});

  final String companyId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AccessPolicy policy =
        ref.watch(myAccessProvider(companyId)).valueOrNull ?? AccessPolicy.all;

    final List<ReportSection> sections = <ReportSection>[
      for (final ReportSection section in reportCatalog)
        ReportSection(
          section.heading,
          section.tint,
          section.entries.where((ReportEntry e) => policy.allowsReport(e.key)).toList(),
        ),
    ].where((ReportSection s) => s.entries.isNotEmpty).toList();

    if (sections.isEmpty) {
      return const EmptyState(
        icon: Icons.lock_outline,
        title: 'No reports here',
        message: 'Your admin has not given you any reports for this company.',
      );
    }

    return ListView(
      padding: EdgeInsets.fromLTRB(16, 8, 16, HomeShell.contentInset(context)),
      children: <Widget>[
        for (int i = 0; i < sections.length; i++) ...<Widget>[
          if (i > 0) const SizedBox(height: 14),
          _ReportGroup(
            tint: sections[i].tint,
            heading: sections[i].heading,
            entries: <_ReportEntry>[
              for (final ReportEntry e in sections[i].entries)
                _ReportEntry(
                  icon: e.icon,
                  title: e.title,
                  subtitle: e.subtitle,
                  route: e.route,
                ),
            ],
          ),
        ],
      ],
    );
  }
}

class _ReportGroup extends StatelessWidget {
  const _ReportGroup({
    required this.heading,
    required this.entries,
    required this.tint,
  });

  final String heading;
  final List<_ReportEntry> entries;
  final Color tint;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 0, 0, 8),
          child: Text(
            heading,
            style: theme.textTheme.titleMedium?.copyWith(color: context.mutedColor),
          ),
        ),
        Card(
          clipBehavior: Clip.antiAlias,
          child: Column(
            children: <Widget>[
              for (int i = 0; i < entries.length; i++) ...<Widget>[
                if (i > 0) const Divider(indent: 62, height: 1),
                entries[i].withTint(tint),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

class _ReportEntry extends StatelessWidget {
  const _ReportEntry({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.route,
    this.tint,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final String route;

  /// Filled in by the group, so a row never has to know its own colour.
  final Color? tint;

  _ReportEntry withTint(Color colour) => _ReportEntry(
        icon: icon,
        title: title,
        subtitle: subtitle,
        route: route,
        tint: colour,
      );

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListTile(
      onTap: () => context.push(route),
      leading: IconTile(icon: icon, colour: tint ?? AppTheme.tileBlue, size: 34),
      title: Text(
        title,
        style: theme.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
      ),
      subtitle: Text(
        subtitle,
        style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
      ),
      trailing: Icon(Icons.chevron_right, color: context.mutedColor),
    );
  }
}
