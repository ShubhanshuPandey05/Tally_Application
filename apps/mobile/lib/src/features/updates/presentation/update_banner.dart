import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../application/update_providers.dart';
import '../domain/app_release.dart';

/// Mentions an optional update without getting in the way.
///
/// Renders nothing at all unless there is genuinely something to install, so it
/// can be dropped into a screen unconditionally. Required updates are not shown
/// here -- those are [UpdateGate]'s job, and a banner someone can scroll past
/// is the wrong shape for "this build no longer works".
class UpdateBanner extends ConsumerStatefulWidget {
  const UpdateBanner({super.key});

  @override
  ConsumerState<UpdateBanner> createState() => _UpdateBannerState();
}

class _UpdateBannerState extends ConsumerState<UpdateBanner> {
  /// Dismissal lives in the widget, not in storage: it should last for this
  /// visit and come back next launch. An optional update that can be silenced
  /// forever is one nobody ever takes.
  bool _dismissed = false;

  @override
  Widget build(BuildContext context) {
    if (_dismissed) return const SizedBox.shrink();

    final UpdateStatus? status = ref.watch(updateStatusProvider).valueOrNull;
    if (status == null || status.action != UpdateAction.optional) {
      return const SizedBox.shrink();
    }

    final AppRelease release = status.release!;
    final ThemeData theme = Theme.of(context);

    return Container(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
      decoration: BoxDecoration(
        color: theme.colorScheme.primaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: <Widget>[
          Icon(
            Icons.system_update_rounded,
            size: 20,
            color: theme.colorScheme.onPrimaryContainer,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'TallyFlow ${release.version} is available',
                  style: theme.textTheme.labelLarge?.copyWith(
                    color: theme.colorScheme.onPrimaryContainer,
                  ),
                ),
                if (release.notes.isNotEmpty)
                  Text(
                    release.notes,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onPrimaryContainer,
                    ),
                  ),
              ],
            ),
          ),
          TextButton(
            onPressed: () => ref.read(startUpdateProvider)(release),
            child: const Text('Get it'),
          ),
          IconButton(
            onPressed: () => setState(() => _dismissed = true),
            icon: const Icon(Icons.close_rounded, size: 18),
            tooltip: 'Dismiss',
          ),
        ],
      ),
    );
  }
}
