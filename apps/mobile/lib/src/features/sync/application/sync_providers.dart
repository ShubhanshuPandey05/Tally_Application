import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../../dashboard/application/dashboard_providers.dart';
import '../data/sync_repository.dart';
import '../domain/sync_status.dart';

final Provider<SyncRepository> syncRepositoryProvider = Provider<SyncRepository>(
  (Ref ref) => SyncRepository(ref.watch(apiClientProvider)),
);

/// The sync's progress for one company, polled while it is moving.
///
/// Polling rather than a socket: the whole job is a few minutes long, the app
/// only watches it while a screen is open, and a request that costs two indexed
/// database lookups is cheaper to operate than a second live connection. The
/// timer stops the moment the run is not running, so an idle app is idle.
final AsyncNotifierProviderFamily<SyncController, SyncStatus, String>
    syncStatusProvider =
    AsyncNotifierProvider.family<SyncController, SyncStatus, String>(
  SyncController.new,
);

class SyncController extends FamilyAsyncNotifier<SyncStatus, String> {
  /// Fast enough that the bar visibly moves, slow enough to be free. Progress
  /// advances a slice at a time and a slice takes tens of seconds, so anything
  /// tighter would poll for changes that cannot have happened yet.
  static const Duration pollInterval = Duration(seconds: 3);

  Timer? _timer;

  @override
  // ignore: avoid_renaming_method_parameters
  Future<SyncStatus> build(String companyId) async {
    ref.onDispose(_stopPolling);
    final SyncStatus status =
        await ref.watch(syncRepositoryProvider).status(companyId);
    _schedule(status);
    return status;
  }

  /// Begin (or resume) reading this company's history.
  Future<void> start({bool full = false}) async {
    state = const AsyncValue<SyncStatus>.loading().copyWithPrevious(state);
    state = await AsyncValue.guard(
      () => ref.read(syncRepositoryProvider).start(arg, full: full),
    );
    _schedule(state.valueOrNull);
  }

  Future<void> cancel() async {
    state = await AsyncValue.guard(
      () => ref.read(syncRepositoryProvider).cancel(arg),
    );
    _schedule(state.valueOrNull);
  }

  Future<void> refreshNow() async {
    final SyncStatus status = await ref.read(syncRepositoryProvider).status(arg);
    state = AsyncValue<SyncStatus>.data(status);
    _schedule(status);
  }

  void _schedule(SyncStatus? status) {
    _stopPolling();
    if (status == null || !status.running) return;
    _timer = Timer(pollInterval, _tick);
  }

  Future<void> _tick() async {
    SyncStatus? status;
    try {
      status = await ref.read(syncRepositoryProvider).status(arg);
    } catch (_) {
      // A dropped poll is not a failed sync. Replacing a moving progress bar
      // with an error page because one request timed out would be a worse lie
      // than the slightly stale number it is showing -- so the last known state
      // stays put and the next tick tries again.
      _timer = Timer(pollInterval, _tick);
      return;
    }

    final SyncStatus? previous = state.valueOrNull;
    state = AsyncValue<SyncStatus>.data(status);

    // The first slice landing is the moment the dashboard becomes worth
    // rendering, and a finished run is the moment it becomes complete. Both are
    // invisible to the dashboard's own provider, so they are announced here.
    final bool gainedHistory =
        status.history.hasHistory && !(previous?.history.hasHistory ?? false);
    final bool justFinished = previous != null && previous.running && !status.running;
    if (gainedHistory || justFinished) {
      ref.invalidate(dashboardProvider(arg));
    }

    _schedule(status);
  }

  void _stopPolling() {
    _timer?.cancel();
    _timer = null;
  }
}
