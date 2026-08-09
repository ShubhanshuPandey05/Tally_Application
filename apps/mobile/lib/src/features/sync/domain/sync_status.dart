/// How far through reading a company's history from TallyPrime we are.
///
/// The first sync of a real set of books takes minutes, not seconds -- the
/// backend reads it in date slices so the shop's own Tally stays usable while
/// it happens. A spinner for that long is indistinguishable from a hang, so
/// every field here exists to let the screen say something specific instead:
/// which months are being read, how many of how many slices are done, and how
/// long the rest is likely to take.
library;

enum SyncPhase {
  /// The one-off read of everything already in the books.
  backfill,

  /// The ongoing "what changed since last time?" read. Seconds, not minutes.
  delta;

  static SyncPhase? parse(String? value) => switch (value) {
        'backfill' => SyncPhase.backfill,
        'delta' => SyncPhase.delta,
        _ => null,
      };
}

enum SyncRunState {
  idle,
  pending,
  running,
  succeeded,
  failed,
  cancelled;

  static SyncRunState parse(String? value) => switch (value) {
        'pending' => SyncRunState.pending,
        'running' => SyncRunState.running,
        'succeeded' => SyncRunState.succeeded,
        'failed' => SyncRunState.failed,
        'cancelled' => SyncRunState.cancelled,
        _ => SyncRunState.idle,
      };
}

/// The window of history already read, independent of any one run.
class SyncHistory {
  const SyncHistory({
    required this.hasHistory,
    this.fromDate,
    this.toDate,
    this.supportsIncremental = false,
    this.lastDeltaAt,
  });

  /// Whether the app can show figures at all. Drives the difference between a
  /// first-run screen and a progress card over real numbers.
  final bool hasHistory;
  final DateTime? fromDate;
  final DateTime? toDate;

  /// Whether this TallyPrime reports change ids. When it does, keeping up to
  /// date costs one tiny request; when it does not, a recent window is re-read
  /// instead. Surfaced because it is the honest answer to "why is this slow?".
  final bool supportsIncremental;
  final DateTime? lastDeltaAt;

  static const SyncHistory none = SyncHistory(hasHistory: false);

  factory SyncHistory.fromJson(Map<String, Object?>? json) {
    if (json == null) return none;
    return SyncHistory(
      hasHistory: (json['has_history'] as bool?) ?? false,
      fromDate: _date(json['from_date']),
      toDate: _date(json['to_date']),
      supportsIncremental: (json['supports_incremental'] as bool?) ?? false,
      lastDeltaAt: _date(json['last_delta_at'])?.toLocal(),
    );
  }
}

class SyncStatus {
  const SyncStatus({
    required this.state,
    required this.running,
    required this.progress,
    required this.completedChunks,
    required this.totalChunks,
    required this.history,
    this.phase,
    this.currentLabel,
    this.vouchersIngested = 0,
    this.elapsed = Duration.zero,
    this.eta,
    this.startedAt,
    this.finishedAt,
    this.error,
  });

  final SyncRunState state;
  final bool running;

  /// 0.0 - 1.0, counted in finished slices. Only ever moves forwards, which is
  /// the property that makes a determinate bar worth showing at all.
  final double progress;
  final int completedChunks;
  final int totalChunks;
  final SyncHistory history;
  final SyncPhase? phase;

  /// The months currently being read, as an owner would say them: "Oct 2024 -
  /// Mar 2025".
  final String? currentLabel;
  final int vouchersIngested;
  final Duration elapsed;

  /// Null until the first slice finishes. Rendered as "estimating", never as
  /// zero: an estimate projected from no completed work would be invented.
  final Duration? eta;
  final DateTime? startedAt;
  final DateTime? finishedAt;

  /// Already written for a business owner by the backend; shown verbatim.
  final String? error;

  static const SyncStatus unknown = SyncStatus(
    state: SyncRunState.idle,
    running: false,
    progress: 0,
    completedChunks: 0,
    totalChunks: 0,
    history: SyncHistory.none,
  );

  factory SyncStatus.fromJson(Map<String, Object?> json) {
    return SyncStatus(
      state: SyncRunState.parse(json['state'] as String?),
      running: (json['running'] as bool?) ?? false,
      progress: ((json['progress'] as num?) ?? 0).toDouble().clamp(0.0, 1.0),
      completedChunks: (json['completed_chunks'] as num?)?.toInt() ?? 0,
      totalChunks: (json['total_chunks'] as num?)?.toInt() ?? 0,
      history: SyncHistory.fromJson(json['history'] as Map<String, Object?>?),
      phase: SyncPhase.parse(json['phase'] as String?),
      currentLabel: json['current_label'] as String?,
      vouchersIngested: (json['vouchers_ingested'] as num?)?.toInt() ?? 0,
      elapsed: _seconds(json['elapsed_seconds']) ?? Duration.zero,
      eta: _seconds(json['eta_seconds']),
      startedAt: _date(json['started_at'])?.toLocal(),
      finishedAt: _date(json['finished_at'])?.toLocal(),
      error: json['error'] as String?,
    );
  }

  /// Whether the sync stopped without finishing and is worth offering a retry.
  bool get needsAttention =>
      state == SyncRunState.failed || state == SyncRunState.cancelled;

  /// Whether the sync should be the whole screen rather than a line above it.
  ///
  /// The distinction the dashboard turns on, and it is about *history*, not
  /// about the run: with nothing read there are no figures to put a strip over,
  /// so the sync is the screen. The moment the first slice lands -- which the
  /// newest-first backfill makes the first thing that happens -- the figures
  /// take over and the rest of the history fills in behind them.
  bool get ownsTheScreen => !history.hasHistory && (running || needsAttention);

  /// Whether the strip has anything to say. Complementary to [ownsTheScreen]:
  /// exactly one of them is true at a time, so the two never both appear.
  bool get isNoteworthy =>
      !ownsTheScreen && (running || (needsAttention && history.hasHistory));

  /// "Step 3 of 8" -- empty until the plan is known.
  String get stepLabel =>
      totalChunks <= 0 ? '' : 'Part ${completedChunks + 1} of $totalChunks';

  /// A time an owner would use, rounded to something they would say out loud.
  ///
  /// Sub-minute precision is false confidence here: each slice is a separate
  /// export from a desktop PC and one of them being slow moves the estimate by
  /// more than the seconds we would be printing.
  String? get etaLabel {
    final Duration? left = eta;
    if (left == null) return null;
    if (left.inSeconds < 45) return 'almost done';
    if (left.inMinutes < 1) return 'less than a minute left';
    if (left.inMinutes == 1) return 'about a minute left';
    if (left.inMinutes < 60) return 'about ${left.inMinutes} minutes left';
    final int hours = left.inHours;
    return 'about $hours ${hours == 1 ? 'hour' : 'hours'} left';
  }
}

Duration? _seconds(Object? value) {
  if (value is! num) return null;
  return Duration(milliseconds: (value * 1000).round());
}

DateTime? _date(Object? value) =>
    value is String ? DateTime.tryParse(value) : null;
