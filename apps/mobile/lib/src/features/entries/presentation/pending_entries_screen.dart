import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/layout/adaptive.dart';
import '../../../core/network/api_exception.dart';
import '../../companies/application/company_providers.dart';
import '../application/entry_providers.dart';
import '../domain/entry_draft.dart';

/// Entries that have not reached TallyPrime yet.
///
/// This screen is the reason the queue is allowed to exist. Holding an entry
/// invisibly would let somebody record a ₹5,000 receipt, see it accepted, and
/// walk away from a PC that never comes back. The count is on the dashboard
/// and the list is here, with what went wrong on each row in TallyPrime's own
/// words.
class PendingEntriesScreen extends ConsumerWidget {
  const PendingEntriesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Entries on their way')),
      body: companyId == null
          ? const Center(child: Text('Choose a company first.'))
          : RefreshIndicator(
              onRefresh: () async =>
                  ref.refresh(pendingEntriesProvider(companyId).future),
              child: ref.watch(pendingEntriesProvider(companyId)).when(
                    loading: () =>
                        const Center(child: CircularProgressIndicator()),
                    error: (Object error, _) => _Message(
                      error is ApiException
                          ? error.message
                          : 'That list could not be loaded.',
                    ),
                    data: (List<PendingEntry> entries) => entries.isEmpty
                        ? const _Message(
                            'Nothing is waiting. Every entry you have made has '
                            'reached TallyPrime.',
                          )
                        : _List(companyId: companyId, entries: entries),
                  ),
            ),
    );
  }
}

class _List extends ConsumerWidget {
  const _List({required this.companyId, required this.entries});

  final String companyId;
  final List<PendingEntry> entries;

  Future<void> _cancel(
    BuildContext context,
    WidgetRef ref,
    PendingEntry entry,
  ) async {
    try {
      await ref
          .read(entriesRepositoryProvider)
          .cancelPending(companyId, entry.id);
      ref.invalidate(pendingEntriesProvider(companyId));
    } on ApiException catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ContentPane(
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: entries.length,
        separatorBuilder: (_, __) => const Divider(height: 1),
        itemBuilder: (BuildContext context, int i) => _Row(
          entry: entries[i],
          onCancel: () => _cancel(context, ref, entries[i]),
        ),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.entry, required this.onCancel});

  final PendingEntry entry;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final (String text, Color colour) = _status(entry, scheme);

    return ListTile(
      dense: true,
      title: Text(
        entry.party == null ? entry.label : '${entry.label} · ${entry.party}',
        style: const TextStyle(fontSize: 13.5),
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            text,
            style: TextStyle(fontSize: 11.5, color: colour),
          ),
          // TallyPrime's own words, when there are any. "Ledger 'Ram Traders'
          // does not exist" is something the person can act on; a status alone
          // is not.
          if (entry.lastError != null)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                entry.lastError!,
                style: TextStyle(
                  fontSize: 11,
                  height: 1.3,
                  color: scheme.onSurfaceVariant,
                ),
              ),
            ),
        ],
      ),
      trailing: entry.canCancel
          ? TextButton(onPressed: onCancel, child: const Text('Cancel'))
          : null,
    );
  }

  (String, Color) _status(PendingEntry entry, ColorScheme scheme) {
    switch (entry.state) {
      case 'waiting':
        return ('Waiting for your Tally PC', scheme.onSurfaceVariant);
      case 'sending':
        return ('Sending now', scheme.onSurfaceVariant);
      case 'sent':
        // Says where it went and what still has to happen there, because an
        // optional entry in Tally is not yet in anybody's balances.
        return ('In TallyPrime, waiting for approval', scheme.primary);
      case 'failed':
        return ('Not saved', scheme.error);
      case 'cancelled':
        return ('Cancelled', scheme.onSurfaceVariant);
      default:
        return (entry.state, scheme.onSurfaceVariant);
    }
  }
}

class _Message extends StatelessWidget {
  const _Message(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Text(
          text,
          textAlign: TextAlign.center,
          style: TextStyle(
            fontSize: 12.5,
            height: 1.4,
            color: Theme.of(context).colorScheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }
}
