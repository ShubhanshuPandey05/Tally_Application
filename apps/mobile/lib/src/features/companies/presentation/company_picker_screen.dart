import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/states.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../application/company_providers.dart';
import '../domain/company.dart';

class CompanyPickerScreen extends ConsumerWidget {
  const CompanyPickerScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<Company>> state = ref.watch(companiesProvider);
    final String? activeId = ref.watch(activeCompanyProvider).valueOrNull?.id;
    final AppUser? user = ref.watch(authControllerProvider).user;
    final bool canRemove = user?.canRemoveCompanies ?? false;

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
                    // Removal lives behind the overflow rather than a swipe:
                    // this list is tapped to switch books several times a day,
                    // and a destructive action on the same gesture as the
                    // routine one is a mis-swipe away from unlinking a company.
                    trailing: canRemove
                        ? IconButton(
                            tooltip: 'Remove ${company.name}',
                            icon: const Icon(Icons.more_vert),
                            onPressed: () => _showActions(context, ref, company),
                          )
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

  Future<void> _showActions(
    BuildContext context,
    WidgetRef ref,
    Company company,
  ) async {
    final bool? remove = await showModalBottomSheet<bool>(
      context: context,
      showDragHandle: true,
      builder: (BuildContext sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(
              title: Text(
                company.name,
                style: Theme.of(sheetContext).textTheme.titleMedium,
              ),
              subtitle: Text(company.tallyName),
            ),
            const Divider(height: 1),
            ListTile(
              leading: Icon(
                Icons.link_off,
                color: Theme.of(sheetContext).colorScheme.error,
              ),
              title: const Text('Remove from TallyFlow'),
              subtitle: const Text('Your books in TallyPrime are not touched'),
              onTap: () => Navigator.of(sheetContext).pop(true),
            ),
          ],
        ),
      ),
    );

    if (remove != true || !context.mounted) return;
    await _confirmRemove(context, ref, company);
  }

  Future<void> _confirmRemove(
    BuildContext context,
    WidgetRef ref,
    Company company,
  ) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        title: Text('Remove ${company.name}?'),
        // Two things a shop owner needs to hear before tapping, and the first
        // one is the one they are actually worried about. Nothing in this
        // product can touch their books, and a screen that does not say so on
        // a button labelled "Remove" is a screen nobody dares press.
        content: const Text(
          'This company will no longer appear in TallyFlow, and its stored '
          'reports and history will be removed from your account.\n\n'
          'Nothing in TallyPrime is changed — your books, vouchers and masters '
          'stay exactly as they are, and you can connect this company again at '
          'any time.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(dialogContext).colorScheme.error,
            ),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;

    try {
      await ref.read(companyRepositoryProvider).unlink(company.id);
      // Cleared before the list is refreshed, not after: the resolver falls
      // back to the first company when the stored id no longer matches one,
      // and leaving a dead id pointing at nothing for a frame is what puts an
      // empty dashboard on screen with a company still selected in the header.
      if (ref.read(activeCompanyIdProvider) == company.id) {
        await ref.read(activeCompanyIdProvider.notifier).clear();
      }
      ref.invalidate(companiesProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${company.name} was removed from TallyFlow.')),
        );
      }
    } on ApiException catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }
}
