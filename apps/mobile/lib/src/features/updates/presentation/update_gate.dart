import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../application/update_providers.dart';
import '../domain/app_release.dart';

/// Blocks the app when the backend will no longer serve this build.
///
/// Wrapped around the router's content rather than checked per screen, for the
/// same reason the auth redirect lives in one place: a guard that has to be
/// remembered is a guard that a new screen ships without.
///
/// The verdict now arrives from the response headers on ordinary API traffic, so
/// this appears within one request of a floor being published rather than at the
/// next cold start. It also appears in response to a 426, which is the case that
/// matters most -- the backend has already refused the request, so there is
/// nothing useful left behind this screen.
///
/// It fails *open*. While the check is loading, and whenever it errors, the app
/// renders normally -- a phone with no signal, or a manifest that 404s during a
/// deploy, must never be the reason a business owner cannot see their books.
/// Only a definite "this version no longer works" blocks anything.
class UpdateGate extends ConsumerWidget {
  const UpdateGate({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<UpdateStatus> status = ref.watch(updateStatusProvider);

    final UpdateStatus? resolved = status.valueOrNull;
    if (resolved == null || !resolved.isRequired) {
      return child;
    }

    return UpdateRequiredScreen(status: resolved);
  }
}

/// The one screen a user cannot navigate away from.
///
/// Starts the download itself on first build instead of waiting to be asked. The
/// user has no alternative action available -- the app behind this screen cannot
/// be served -- so making them press a button first is a step that exists only to
/// be pressed.
///
/// What cannot be automated is the install itself: Android will not install a
/// package without the user confirming it, whatever an app asks for, short of
/// device-owner privileges. So the download starts by itself and the system
/// package installer takes it from there.
class UpdateRequiredScreen extends ConsumerStatefulWidget {
  const UpdateRequiredScreen({required this.status, super.key});

  final UpdateStatus status;

  @override
  ConsumerState<UpdateRequiredScreen> createState() => _UpdateRequiredScreenState();
}

class _UpdateRequiredScreenState extends ConsumerState<UpdateRequiredScreen> {
  /// Guards against a second launch. This widget rebuilds whenever the update
  /// providers do, and handing the same URL to the browser on each rebuild would
  /// open a tab per rebuild.
  bool _started = false;
  bool _launching = false;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    // Deferred by one frame: launching an external application during the first
    // build of a route is fought over by the framework, and a download that
    // silently does not start is worse than one that starts a frame late.
    WidgetsBinding.instance.addPostFrameCallback((_) => _start());
  }

  Future<void> _start() async {
    final AppRelease? release = widget.status.release;
    if (_started || release == null || !release.isDownloadable || !mounted) return;
    _started = true;

    setState(() {
      _launching = true;
      _failed = false;
    });

    bool ok = false;
    try {
      ok = await ref.read(startUpdateProvider)(release);
    } finally {
      if (mounted) {
        setState(() {
          _launching = false;
          _failed = !ok;
        });
      }
    }
  }

  Future<void> _retry() async {
    _started = false;
    await _start();
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppRelease? release = widget.status.release;
    final bool downloadable = release != null && release.isDownloadable;

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Icon(
                  Icons.system_update_rounded,
                  size: 56,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(height: 20),
                Text(
                  'Update TallyFlow to continue',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.headlineSmall,
                ),
                const SizedBox(height: 12),
                Text(
                  // Says why, not just what. "Update required" with no reason
                  // reads as an app being difficult; the real reason is that
                  // this build can no longer read Tally correctly.
                  downloadable
                      ? 'This version can no longer read your data correctly. '
                          'The download has started -- open it to install.'
                      : 'This version can no longer read your data correctly. '
                          'Install the latest build to carry on.',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
                if (release != null && release.notes.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 16),
                  Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: theme.colorScheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      release.notes,
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ],
                const SizedBox(height: 28),
                if (downloadable)
                  FilledButton.icon(
                    // Still a button, even though the download already started:
                    // it is how someone who dismissed the browser, or who lost
                    // the notification, gets a second go.
                    onPressed: _launching ? null : _retry,
                    icon: _launching
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Icon(_failed
                            ? Icons.refresh_rounded
                            : Icons.download_rounded),
                    label: Text(
                      _launching
                          ? 'Opening the download...'
                          : _failed
                              ? 'Try the download again'
                              : 'Download ${release.version}  ${release.sizeBytes > 0 ? " ·  ${release.sizeLabel}" : ""}',
                    ),
                  )
                else
                  const Text(
                    'Open the TallyFlow website to download the latest version.',
                    textAlign: TextAlign.center,
                  ),
                if (_failed) ...<Widget>[
                  const SizedBox(height: 10),
                  Text(
                    'Could not open the download. Check your connection.',
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.error,
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                Text(
                  'You have ${widget.status.currentVersion}',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 8),
                TextButton(
                  // Re-reads rather than serving the cached answer: someone who
                  // has just installed the new build needs a way through without
                  // force-quitting the app.
                  onPressed: () => ref.invalidate(updateStatusProvider),
                  child: const Text('I have already updated'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
