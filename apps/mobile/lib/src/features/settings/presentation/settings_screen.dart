import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/config/app_config.dart';
import '../../../core/providers.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../../connectors/application/connector_providers.dart';
import '../../connectors/domain/connector.dart';

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
      appBar: AppBar(title: const Text('Account')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
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
                        Text(user?.displayName ?? '', style: theme.textTheme.titleSmall),
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
          const SizedBox(height: 18),
          Card(
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: <Widget>[
                ListTile(
                  leading: const Icon(Icons.desktop_windows_outlined),
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
                const Divider(indent: 56, height: 1),
                ListTile(
                  leading: const Icon(Icons.folder_outlined),
                  title: const Text('Companies'),
                  subtitle: Text('${companies.length} connected'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.push(Routes.companies),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
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
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(color: context.mutedColor),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 18),
          Card(
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: <Widget>[
                ListTile(
                  leading: const Icon(Icons.logout),
                  title: const Text('Sign out'),
                  onTap: () => ref.read(authControllerProvider.notifier).signOut(),
                ),
                const Divider(indent: 56, height: 1),
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
          const SizedBox(height: 24),
          Center(
            child: Text(
              'TallyFlow${config.isProduction ? '' : ' · ${config.environment}'}',
              style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
            ),
          ),
        ],
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
