import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/freshness.dart';
import '../../../core/widgets/states.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../application/connector_providers.dart';
import '../domain/connector.dart';

class ConnectorsScreen extends ConsumerWidget {
  const ConnectorsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<Connector>> state = ref.watch(connectorsProvider);
    final UserRole role =
        ref.watch(authControllerProvider).user?.role ?? UserRole.viewer;

    return Scaffold(
      appBar: AppBar(title: const Text('Tally PCs')),
      floatingActionButton: role.canManageConnectors
          ? FloatingActionButton.extended(
              onPressed: () => context.push(Routes.pairConnector),
              icon: const Icon(Icons.add),
              label: const Text('Add a PC'),
            )
          : null,
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(connectorsProvider),
        child: state.when(
          skipLoadingOnRefresh: true,
          loading: () => const LoadingState(),
          error: (Object error, StackTrace stack) => ErrorState(
            error: error,
            onRetry: () => ref.invalidate(connectorsProvider),
          ),
          data: (List<Connector> connectors) {
            if (connectors.isEmpty) {
              return ListView(
                children: <Widget>[
                  SizedBox(height: MediaQuery.sizeOf(context).height * 0.15),
                  EmptyState(
                    icon: Icons.desktop_windows_outlined,
                    title: 'No PCs connected',
                    message: 'Install the TallyFlow Connector on the computer '
                        'running TallyPrime to get started.',
                    action: role.canManageConnectors
                        ? FilledButton.icon(
                            onPressed: () => context.push(Routes.pairConnector),
                            icon: const Icon(Icons.add_link),
                            label: const Text('Set up a connection'),
                          )
                        : null,
                  ),
                ],
              );
            }
            return ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 88),
              children: <Widget>[
                for (final Connector connector in connectors)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: ConnectorCard(
                      connector: connector,
                      onTap: () => context.push('${Routes.connectors}/${connector.id}'),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class ConnectorCard extends StatelessWidget {
  const ConnectorCard({super.key, required this.connector, this.onTap});

  final Connector connector;
  final VoidCallback? onTap;

  static Color colourFor(ConnectorHealth health, BuildContext context) =>
      switch (health) {
        ConnectorHealth.healthy => context.positiveColor,
        ConnectorHealth.tallyClosed => context.cautionColor,
        ConnectorHealth.offline => context.negativeColor,
        ConnectorHealth.revoked => context.mutedColor,
      };

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color colour = colourFor(connector.health, context);

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Container(
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(color: colour, shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      connector.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleSmall,
                    ),
                  ),
                  Text(
                    connector.health.label,
                    style: theme.textTheme.labelMedium?.copyWith(color: colour),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                connector.health.advice,
                style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 14,
                runSpacing: 4,
                children: <Widget>[
                  if (connector.hostname != null)
                    _Meta(icon: Icons.computer, label: connector.hostname!),
                  _Meta(
                    icon: Icons.folder_outlined,
                    label: '${connector.companyCount} companies',
                  ),
                  if (connector.lastSeenAt != null)
                    _Meta(
                      icon: Icons.schedule,
                      label: 'seen ${Freshness.relativeTime(connector.lastSeenAt!)}',
                    ),
                  if (connector.version != null)
                    _Meta(icon: Icons.tag, label: 'v${connector.version!}'),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Meta extends StatelessWidget {
  const _Meta({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Icon(icon, size: 13, color: context.mutedColor),
        const SizedBox(width: 4),
        Text(
          label,
          style: Theme.of(context)
              .textTheme
              .labelSmall
              ?.copyWith(color: context.mutedColor),
        ),
      ],
    );
  }
}
