import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme.dart';
import '../../application/sync_providers.dart';
import '../../domain/sync_status.dart';

/// Progress for a job measured in minutes, not milliseconds.
///
/// A spinner is an honest widget for a two-second wait and a dishonest one for
/// a four-minute wait: it says "something is happening" while the user is
/// asking "is this broken, and how long do I wait?". Reading a real set of
/// books out of TallyPrime takes minutes -- it is done in date slices so the
/// shop's till stays usable -- so everything here exists to answer the second
/// question: a determinate bar, the months being read right now, how many
/// slices of how many are done, and a running count of what has arrived.
///
/// Two presentations of the same state:
///
/// [SyncProgressPanel]
///     First run. There are no figures yet, so this *is* the screen.
/// [SyncProgressStrip]
///     Every run after the first slice lands. The figures are the screen and
///     this is one line above them -- because the backfill reads newest months
///     first, the dashboard is already answering "what did I sell this week?"
///     while the older years are still arriving.
class SyncProgressPanel extends ConsumerWidget {
  const SyncProgressPanel({super.key, required this.companyId});

  final String companyId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final SyncStatus? status = ref.watch(syncStatusProvider(companyId)).valueOrNull;
    if (status == null) return const SizedBox.shrink();

    final ThemeData theme = Theme.of(context);
    final SyncController controller =
        ref.read(syncStatusProvider(companyId).notifier);

    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              padding: const EdgeInsets.all(18),
              decoration: BoxDecoration(
                color: theme.colorScheme.primary.withOpacity(0.08),
                shape: BoxShape.circle,
              ),
              child: Icon(
                status.needsAttention
                    ? Icons.cloud_off_outlined
                    : Icons.history_toggle_off,
                size: 32,
                color: theme.colorScheme.primary,
              ),
            ),
            const SizedBox(height: 20),
            Text(
              status.needsAttention
                  ? 'Your history is not finished yet'
                  : 'Reading your books from Tally',
              style: theme.textTheme.titleMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              status.error ?? _explanation(status),
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
            ),
            const SizedBox(height: 26),
            if (status.running) ...<Widget>[
              _Bar(status: status),
              const SizedBox(height: 14),
              _Detail(status: status),
            ],
            const SizedBox(height: 26),
            if (status.running)
              // Offered because the alternative is force-quitting the app while
              // an export runs. It stops after the current slice rather than
              // mid-read: an export already on the wire has cost Tally its time
              // either way, so abandoning it would waste the work for nothing.
              TextButton(
                onPressed: controller.cancel,
                child: const Text('Stop for now'),
              )
            else if (!status.running)
              FilledButton.tonalIcon(
                onPressed: () => controller.start(),
                icon: const Icon(Icons.play_arrow),
                label: Text(
                  status.needsAttention ? 'Continue reading' : 'Read my history',
                ),
              ),
          ],
        ),
      ),
    );
  }

  /// Why this takes minutes -- said once, plainly, so it reads as care rather
  /// than as slowness.
  static String _explanation(SyncStatus status) {
    if (!status.running) {
      return 'We have not read your past transactions yet. This happens once, '
          'and only new changes are read after that.';
    }
    return 'We read a few months at a time so TallyPrime stays usable on your '
        'PC while this runs. Your most recent months come first.';
  }
}

/// The one-line version, shown above real figures while the rest fills in.
class SyncProgressStrip extends ConsumerWidget {
  const SyncProgressStrip({super.key, required this.companyId});

  final String companyId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final SyncStatus? status = ref.watch(syncStatusProvider(companyId)).valueOrNull;
    // Silent unless there is something worth saying: a finished sync, or a
    // delta that takes two seconds, must not put furniture on the dashboard.
    if (status == null || !status.isNoteworthy) return const SizedBox.shrink();

    final ThemeData theme = Theme.of(context);
    final SyncController controller =
        ref.read(syncStatusProvider(companyId).notifier);
    final bool failed = status.needsAttention;

    return Container(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
      decoration: BoxDecoration(
        color: (failed ? context.cautionColor : theme.colorScheme.primary)
            .withOpacity(0.08),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(
              failed ? Icons.error_outline : Icons.downloading,
              size: 18,
              color: failed ? context.cautionColor : theme.colorScheme.primary,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  failed
                      ? 'Older history is still missing'
                      : 'Still reading your older history',
                  style: theme.textTheme.labelLarge,
                ),
                const SizedBox(height: 2),
                Text(
                  failed
                      ? (status.error ?? 'It will continue when your PC is reachable.')
                      : _line(status),
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: context.mutedColor),
                ),
                if (status.running) ...<Widget>[
                  const SizedBox(height: 10),
                  _Bar(status: status, compact: true),
                ],
              ],
            ),
          ),
          if (failed)
            TextButton(
              onPressed: () => controller.start(),
              child: const Text('Retry'),
            ),
        ],
      ),
    );
  }

  static String _line(SyncStatus status) {
    final String where = status.currentLabel ?? '';
    final String step = status.stepLabel;
    final String? eta = status.etaLabel;
    return <String>[
      if (where.isNotEmpty) where,
      if (step.isNotEmpty) step,
      if (eta != null) eta,
    ].join(' · ');
  }
}

class _Bar extends StatelessWidget {
  const _Bar({required this.status, this.compact = false});

  final SyncStatus status;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    // Indeterminate only until the plan exists. Once the slices are counted the
    // bar is real, and a real bar is the entire reason this widget exists.
    final double? value = status.totalChunks > 0 ? status.progress : null;

    return ClipRRect(
      borderRadius: BorderRadius.circular(6),
      child: LinearProgressIndicator(
        value: value,
        minHeight: compact ? 5 : 8,
        backgroundColor: Theme.of(context).colorScheme.onSurface.withOpacity(0.08),
      ),
    );
  }
}

class _Detail extends StatelessWidget {
  const _Detail({required this.status});

  final SyncStatus status;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String? eta = status.etaLabel;

    return Column(
      children: <Widget>[
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text(
              status.currentLabel ?? 'Working out how much there is',
              style: theme.textTheme.labelLarge,
            ),
            if (status.stepLabel.isNotEmpty)
              Text(
                status.stepLabel,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: context.mutedColor),
              ),
          ],
        ),
        const SizedBox(height: 6),
        Text(
          <String>[
            if (status.vouchersIngested > 0)
              '${_grouped(status.vouchersIngested)} transactions read',
            // Never a fabricated number: before the first slice finishes there
            // is nothing to project an estimate from, and saying so is better
            // than inventing one the next slice will contradict.
            eta ?? 'Estimating how long this will take',
          ].join(' · '),
          textAlign: TextAlign.center,
          style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
        ),
      ],
    );
  }

  /// Indian grouping, matching how every other number in the app is written.
  static String _grouped(int value) {
    final String digits = value.toString();
    if (digits.length <= 3) return digits;

    final String head = digits.substring(0, digits.length - 3);
    final String tail = digits.substring(digits.length - 3);
    final StringBuffer buffer = StringBuffer();
    for (int i = 0; i < head.length; i++) {
      if (i > 0 && (head.length - i) % 2 == 0) buffer.write(',');
      buffer.write(head[i]);
    }
    return '$buffer,$tail';
  }
}
