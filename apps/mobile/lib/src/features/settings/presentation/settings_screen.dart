import 'package:flutter/material.dart';
import '../../../core/layout/adaptive.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../core/widgets/primitives.dart';
import '../application/theme_controller.dart';
import '../../../app/shell.dart';
import '../../../app/theme.dart';
import '../../../core/config/app_config.dart';
import '../../../core/providers.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../../connectors/application/connector_providers.dart';
import '../../connectors/domain/connector.dart';
import '../../subscription/presentation/subscription_notice.dart';
import '../../updates/application/update_providers.dart';
import '../../updates/domain/app_release.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final AppUser? user = ref.watch(authControllerProvider).user;
    final List<Company> companies =
        ref.watch(companiesProvider).valueOrNull ?? const <Company>[];
    final List<Connector> connectors =
        ref.watch(connectorsProvider).valueOrNull ?? const <Connector>[];
    final AppConfig config = ref.watch(appConfigProvider);

    final int offline = connectors
        .where((Connector connector) =>
            connector.status != ConnectorStatus.revoked && !connector.online)
        .length;

    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: ContentPane(
        child: ListView(
          padding: EdgeInsets.fromLTRB(16, 8, 16, HomeShell.contentInset(context)),
          children: <Widget>[
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: <Widget>[
                    CircleAvatar(
                      radius: 24,
                      backgroundColor: theme.colorScheme.primary.withOpacity(0.12),
                      child: Text(
                        user?.initials ?? '?',
                        style: theme.textTheme.titleMedium
                            ?.copyWith(color: theme.colorScheme.primary),
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(user?.displayName ?? '',
                              style: theme.textTheme.titleSmall),
                          Text(
                            user?.email ?? '',
                            style: theme.textTheme.bodySmall
                                ?.copyWith(color: context.mutedColor),
                          ),
                          const SizedBox(height: 6),
                          Row(
                            children: <Widget>[
                              Chip(
                                label: Text(user?.role.label ?? ''),
                                visualDensity: VisualDensity.compact,
                                padding: EdgeInsets.zero,
                              ),
                              const SizedBox(width: 8),
                              Flexible(
                                child: Text(
                                  user?.orgName ?? '',
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
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            // Above the things it governs, so an owner reads why the buttons
            // below are missing before hunting for them.
            const SubscriptionNotice(),
            if (user?.shouldExplainSubscription ?? false) const SizedBox(height: 14),
            Card(
              clipBehavior: Clip.antiAlias,
              child: Column(
                children: <Widget>[
                  ListTile(
                    leading: const IconTile(
                      icon: Icons.desktop_windows_outlined,
                      colour: AppTheme.tileBlue,
                      size: 40,
                    ),
                    title: const Text('Tally PCs'),
                    subtitle: Text(
                      connectors.isEmpty
                          ? 'None connected'
                          : offline == 0
                              ? '${connectors.length} connected, all online'
                              : '$offline of ${connectors.length} offline',
                    ),
                    trailing: Icon(
                      Icons.chevron_right,
                      color: offline > 0 ? context.cautionColor : context.mutedColor,
                    ),
                    onTap: () => context.push(Routes.connectors),
                  ),
                  const Divider(indent: 68, height: 1),
                  ListTile(
                    leading: const IconTile(
                      icon: Icons.folder_outlined,
                      colour: AppTheme.tileGreen,
                      size: 40,
                    ),
                    title: const Text('Companies'),
                    subtitle: Text('${companies.length} connected'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push(Routes.companies),
                  ),
                  // Admin-only, and hidden rather than disabled: a control that
                  // exists but refuses reads as a fault, while its absence reads
                  // as "not your job". The backend refuses either way.
                  if (user?.role.canManageTeam ?? false) ...<Widget>[
                    const Divider(indent: 68, height: 1),
                    ListTile(
                      leading: const IconTile(
                        icon: Icons.group_outlined,
                        colour: AppTheme.tileViolet,
                        size: 40,
                      ),
                      title: const Text('People'),
                      subtitle: const Text('Who can see which companies'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push(Routes.team),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 14),
            const _AppearanceCard(),
            const SizedBox(height: 14),
            const SubscriptionCard(),
            if (user?.role.isAdmin ?? false) const SizedBox(height: 14),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Row(
                      children: <Widget>[
                        Icon(Icons.lock_outline, size: 18, color: context.positiveColor),
                        const SizedBox(width: 8),
                        Text('Read-only', style: theme.textTheme.titleSmall),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'TallyFlow can only read from TallyPrime. It cannot create, '
                      'change or delete vouchers, ledgers or stock. Nothing you do '
                      'in this app can alter your books.',
                      style:
                          theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            Card(
              clipBehavior: Clip.antiAlias,
              child: Column(
                children: <Widget>[
                  ListTile(
                    leading: const Icon(Icons.password),
                    title: const Text('Change password'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push(Routes.changePassword),
                  ),
                  const Divider(indent: 68, height: 1),
                  ListTile(
                    leading: const Icon(Icons.logout),
                    title: const Text('Sign out'),
                    onTap: () => ref.read(authControllerProvider.notifier).signOut(),
                  ),
                  const Divider(indent: 68, height: 1),
                  ListTile(
                    leading: Icon(Icons.devices_other, color: context.negativeColor),
                    title: Text(
                      'Sign out on all devices',
                      style: TextStyle(color: context.negativeColor),
                    ),
                    subtitle: const Text('Use this if a phone was lost or stolen'),
                    onTap: () => _confirmSignOutEverywhere(context, ref),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            const _UpdateCard(),
            const SizedBox(height: 24),
            Center(
              child: Text(
                'TallyFlow${config.isProduction ? '' : ' · ${config.environment}'}',
                style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmSignOutEverywhere(BuildContext context, WidgetRef ref) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Sign out everywhere?'),
        content: const Text(
          'Every phone and tablet signed in to this account will be signed out. '
          'Your connected PCs keep working.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Sign out everywhere'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await ref.read(authControllerProvider.notifier).signOutEverywhere();
    }
  }
}

/// Version, and whatever the update check has to say about it.
///
/// The one place a support call can ask "what are you running?" and get an
/// answer without the caller navigating anywhere. Also the manual trigger: the
/// background check runs on its own schedule, and somebody told an update
/// exists should not have to wait for it.
class _UpdateCard extends ConsumerWidget {
  const _UpdateCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final AsyncValue<UpdateStatus> status = ref.watch(updateStatusProvider);

    return Card(
      clipBehavior: Clip.antiAlias,
      child: status.when(
        loading: () => const ListTile(
          leading: Icon(Icons.system_update_rounded),
          title: Text('Checking for updates...'),
        ),
        // Never an error state the user has to act on: a failed check means the
        // phone has no signal or a deploy is in progress, and neither is
        // something a business owner can do anything about.
        error: (Object _, StackTrace __) => const ListTile(
          leading: Icon(Icons.system_update_rounded),
          title: Text('TallyFlow'),
          subtitle: Text('Could not check for updates just now'),
        ),
        data: (UpdateStatus value) {
          final AppRelease? release = value.release;
          if (release == null) {
            return ListTile(
              leading: const Icon(Icons.check_circle_outline_rounded),
              title: Text('Version ${value.currentVersion}'),
              subtitle: const Text('Up to date'),
              trailing: IconButton(
                icon: const Icon(Icons.refresh_rounded),
                tooltip: 'Check again',
                onPressed: () => ref.invalidate(updateStatusProvider),
              ),
            );
          }

          return ListTile(
            leading: Icon(
              Icons.system_update_rounded,
              color: theme.colorScheme.primary,
            ),
            title: Text('Version ${release.version} available'),
            subtitle: Text(
              release.notes.isEmpty
                  ? 'You have ${value.currentVersion} · ${release.sizeLabel}'
                  : '${release.notes}\nYou have ${value.currentVersion} · ${release.sizeLabel}',
            ),
            isThreeLine: release.notes.isNotEmpty,
            trailing: FilledButton(
              onPressed: () => ref.read(startUpdateProvider)(release),
              child: const Text('Update'),
            ),
          );
        },
      ),
    );
  }
}

/// The skin picker.
///
/// A segmented control rather than a list of radio rows: three options that are
/// instant and reversible are a switch, not a decision, and seeing all three at
/// once is what makes trying them cheap.
class _AppearanceCard extends ConsumerWidget {
  const _AppearanceCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final AppThemeMode mode = ref.watch(themeModeProvider);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const IconTile(
                  icon: Icons.contrast_rounded,
                  colour: AppTheme.tileViolet,
                  size: 34,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text('Appearance', style: theme.textTheme.titleMedium),
                      Text(
                        'Applies to this device only',
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: context.mutedColor),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            SegmentedPill<AppThemeMode>(
              value: mode,
              onChanged: (AppThemeMode next) =>
                  ref.read(themeModeProvider.notifier).select(next),
              segments: <({AppThemeMode value, String label})>[
                for (final AppThemeMode option in AppThemeMode.values)
                  (value: option, label: option.label),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
