import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/model/freshness.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/states.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../application/connector_providers.dart';
import '../domain/connector.dart';
import 'connectors_screen.dart';

class ConnectorDetailScreen extends ConsumerWidget {
  const ConnectorDetailScreen({super.key, required this.connectorId});

  final String connectorId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<Connector> state = ref.watch(connectorStatusProvider(connectorId));
    final UserRole role =
        ref.watch(authControllerProvider).user?.role ?? UserRole.viewer;

    return Scaffold(
      appBar: AppBar(
        title: Text(state.valueOrNull?.name ?? 'Tally PC'),
        actions: <Widget>[
          if (role.canManageConnectors)
            IconButton(
              tooltip: 'Remove this PC',
              onPressed: () => _confirmRevoke(context, ref),
              icon: const Icon(Icons.link_off),
            ),
        ],
      ),
      body: state.when(
        skipLoadingOnRefresh: true,
        loading: () => const LoadingState(),
        error: (Object error, StackTrace stack) => ErrorState(
          error: error,
          onRetry: () => ref.invalidate(connectorStatusProvider(connectorId)),
        ),
        data: (Connector connector) => _Body(connector: connector, role: role),
      ),
    );
  }

  Future<void> _confirmRevoke(BuildContext context, WidgetRef ref) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Remove this PC?'),
        // Says what actually happens, including the part that is not obvious:
        // the socket is dropped straight away, not at some later reconnect.
        content: const Text(
          'The connector will be disconnected immediately and will stop sending '
          'data. Your books in TallyPrime are not affected.\n\n'
          'To reconnect this PC later you will need to pair it again.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;
    try {
      await ref.read(connectorRepositoryProvider).revoke(connectorId);
      ref.invalidate(connectorsProvider);
      ref.invalidate(companiesProvider);
      if (context.mounted) context.go(Routes.connectors);
    } on ApiException catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }
}

class _Body extends ConsumerWidget {
  const _Body({required this.connector, required this.role});

  final Connector connector;
  final UserRole role;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final List<Company> linked = (ref.watch(companiesProvider).valueOrNull ??
            const <Company>[])
        .where((Company company) => company.connectorId == connector.id)
        .toList(growable: false);

    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        ConnectorCard(connector: connector),
        const SizedBox(height: 18),
        Text('Connected companies', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        if (linked.isEmpty)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                'No companies from this PC yet.',
                style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
              ),
            ),
          )
        else
          Card(
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: <Widget>[
                for (final Company company in linked)
                  ListTile(
                    leading: const Icon(Icons.folder_outlined),
                    title: Text(company.name),
                    subtitle: company.tallyName != company.name
                        ? Text(company.tallyName)
                        : null,
                    onTap: () async {
                      await ref
                          .read(activeCompanyIdProvider.notifier)
                          .select(company.id);
                      if (context.mounted) context.go(Routes.dashboard);
                    },
                  ),
              ],
            ),
          ),
        const SizedBox(height: 16),
        if (role.canLinkCompanies)
          OutlinedButton.icon(
            onPressed: connector.online
                ? () => context.push('${Routes.connectors}/${connector.id}/link')
                : null,
            icon: const Icon(Icons.add),
            label: Text(
              connector.online
                  ? 'Add a company from this PC'
                  : 'PC must be online to add companies',
            ),
          ),
        const SizedBox(height: 24),
        Text('Details', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        Card(
          child: Column(
            children: <Widget>[
              _DetailRow(label: 'Connector ID', value: connector.id),
              if (connector.hostname != null)
                _DetailRow(label: 'Computer', value: connector.hostname!),
              if (connector.os != null)
                _DetailRow(label: 'Operating system', value: connector.os!),
              if (connector.version != null)
                _DetailRow(label: 'Connector version', value: connector.version!),
              _DetailRow(
                label: 'Last seen',
                value: connector.lastSeenAt == null
                    ? 'Never'
                    : Freshness.relativeTime(connector.lastSeenAt!),
              ),
              if (connector.companiesOpen.isNotEmpty)
                _DetailRow(
                  label: 'Open in Tally',
                  value: connector.companiesOpen.join(', '),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(
            width: 130,
            child: Text(
              label,
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
            ),
          ),
          Expanded(
            child: Text(value, style: theme.textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }
}
