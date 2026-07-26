import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/states.dart';
import '../application/company_providers.dart';
import '../data/company_repository.dart';
import '../domain/company.dart';

/// Choose which of the open companies to connect.
///
/// The list is whatever is open in TallyPrime right now, refreshed on demand.
/// If a company is missing the fix is always the same and is stated on screen:
/// load it on the PC. Anything cleverer would mean reading books the person at
/// the computer did not choose to expose.
class LinkCompanyScreen extends ConsumerStatefulWidget {
  const LinkCompanyScreen({super.key, required this.connectorId});

  final String connectorId;

  @override
  ConsumerState<LinkCompanyScreen> createState() => _LinkCompanyScreenState();
}

class _LinkCompanyScreenState extends ConsumerState<LinkCompanyScreen> {
  late Future<List<DiscoveredCompany>> _discovery = _discover();
  String? _linking;

  Future<List<DiscoveredCompany>> _discover() =>
      ref.read(companyRepositoryProvider).discover(widget.connectorId);

  Future<void> _link(DiscoveredCompany company) async {
    setState(() => _linking = company.tallyName);
    try {
      final CompanyRepository repository = ref.read(companyRepositoryProvider);
      final Company linked = await repository.link(
        connectorId: widget.connectorId,
        tallyName: company.tallyName,
      );
      ref.invalidate(companiesProvider);
      await ref.read(activeCompanyIdProvider.notifier).select(linked.id);
      if (mounted) context.go(Routes.dashboard);
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _linking = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Choose companies'),
        actions: <Widget>[
          IconButton(
            tooltip: 'Look again',
            onPressed: () => setState(() => _discovery = _discover()),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: FutureBuilder<List<DiscoveredCompany>>(
        future: _discovery,
        builder: (BuildContext context,
            AsyncSnapshot<List<DiscoveredCompany>> snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const LoadingState(message: 'Asking your PC what is open...');
          }
          if (snapshot.hasError) {
            return ErrorState(
              error: snapshot.error!,
              onRetry: () => setState(() => _discovery = _discover()),
            );
          }

          final List<DiscoveredCompany> companies =
              snapshot.data ?? const <DiscoveredCompany>[];
          if (companies.isEmpty) {
            return EmptyState(
              icon: Icons.folder_off_outlined,
              title: 'No companies open',
              message: 'Open the company you want to see in TallyPrime on that '
                  'PC, then look again.',
              action: FilledButton.tonalIcon(
                onPressed: () => setState(() => _discovery = _discover()),
                icon: const Icon(Icons.refresh),
                label: const Text('Look again'),
              ),
            );
          }

          return ListView(
            padding: const EdgeInsets.all(16),
            children: <Widget>[
              Text(
                'These companies are open in TallyPrime right now.',
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: context.mutedColor),
              ),
              const SizedBox(height: 14),
              for (final DiscoveredCompany company in companies)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Card(
                    clipBehavior: Clip.antiAlias,
                    child: ListTile(
                      leading: Icon(
                        company.linked ? Icons.check_circle : Icons.folder_outlined,
                        color: company.linked ? context.positiveColor : null,
                      ),
                      title: Text(company.tallyName),
                      subtitle: Text(company.linked ? 'Already connected' : 'Tap to connect'),
                      trailing: _linking == company.tallyName
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : company.linked
                              ? null
                              : const Icon(Icons.add),
                      onTap: company.linked || _linking != null
                          ? null
                          : () => _link(company),
                    ),
                  ),
                ),
            ],
          );
        },
      ),
    );
  }
}
