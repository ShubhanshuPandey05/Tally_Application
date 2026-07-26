import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/widgets/states.dart';
import '../application/company_providers.dart';
import '../domain/company.dart';

class CompanyPickerScreen extends ConsumerWidget {
  const CompanyPickerScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<Company>> state = ref.watch(companiesProvider);
    final String? activeId = ref.watch(activeCompanyProvider).valueOrNull?.id;

    return Scaffold(
      appBar: AppBar(title: const Text('Switch company')),
      body: state.when(
        loading: () => const LoadingState(),
        error: (Object error, StackTrace stack) => ErrorState(
          error: error,
          onRetry: () => ref.invalidate(companiesProvider),
        ),
        data: (List<Company> companies) => ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            for (final Company company in companies)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Card(
                  clipBehavior: Clip.antiAlias,
                  child: ListTile(
                    selected: company.id == activeId,
                    leading: Icon(
                      company.id == activeId
                          ? Icons.radio_button_checked
                          : Icons.radio_button_unchecked,
                      color: company.id == activeId
                          ? Theme.of(context).colorScheme.primary
                          : context.mutedColor,
                    ),
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
                ),
              ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () => context.push(Routes.connectors),
              icon: const Icon(Icons.add),
              label: const Text('Connect another company'),
            ),
          ],
        ),
      ),
    );
  }
}
