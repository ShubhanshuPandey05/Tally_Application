import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/features/sync/application/sync_providers.dart';
import 'package:tallyflow/src/features/sync/domain/sync_status.dart';
import 'package:tallyflow/src/features/sync/presentation/widgets/sync_progress.dart';

import 'support/fixtures.dart';

/// The progress UI for a job that takes minutes.
///
/// The claims worth testing are not "does a bar appear" but the ones a
/// shopkeeper would notice being wrong: that the bar is *determinate* (a
/// spinner for four minutes reads as a hang), that an estimate is never
/// invented before there is anything to project from, and that a finished sync
/// leaves no furniture behind on the dashboard.

const String _companyId = 'company-1';

Future<void> _pump(WidgetTester tester, Widget widget, SyncStatus status) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        // The family as a whole: Riverpod 2.x cannot override a single
        // AsyncNotifier family element, and the test only ever asks for one.
        syncStatusProvider.overrideWith(() => _StubSyncController(status)),
      ],
      child: MaterialApp(
        theme: AppTheme.light(),
        home: Scaffold(body: widget),
      ),
    ),
  );
  await tester.pump();
}

class _StubSyncController extends SyncController {
  _StubSyncController(this._status);

  final SyncStatus _status;

  @override
  Future<SyncStatus> build(String companyId) async => _status;
}

void main() {
  group('decoding the wire', () {
    test('a running backfill decodes into a determinate progress', () {
      final SyncStatus status = SyncStatus.fromJson(fixture('sync_running'));

      expect(status.running, isTrue);
      expect(status.state, SyncRunState.running);
      expect(status.phase, SyncPhase.backfill);
      expect(status.progress, closeTo(0.375, 0.0001));
      expect(status.completedChunks, 3);
      expect(status.totalChunks, 8);
      expect(status.currentLabel, 'Oct 2024 - Mar 2025');
      expect(status.vouchersIngested, 14820);
      expect(status.history.hasHistory, isTrue);
      expect(status.history.supportsIncremental, isTrue);
    });

    test('an idle company with no history is not mistaken for a finished one', () {
      final SyncStatus status = SyncStatus.fromJson(fixture('sync_idle'));

      expect(status.running, isFalse);
      expect(status.progress, 0);
      expect(status.history.hasHistory, isFalse);
      // No run has ever completed, so there is nothing to estimate from.
      expect(status.eta, isNull);
      expect(status.etaLabel, isNull);
    });

    test('a partly-read company shows figures, not a progress screen', () {
      // The point of reading newest months first: as soon as one slice lands
      // the dashboard is worth looking at, and the sync moves out of its way.
      final SyncStatus status = SyncStatus.fromJson(fixture('sync_running'));

      expect(status.ownsTheScreen, isFalse);
      expect(status.isNoteworthy, isTrue);
    });

    test('the screen belongs to the sync only while there is nothing to show', () {
      const SyncStatus first = SyncStatus(
        state: SyncRunState.running,
        running: true,
        progress: 0,
        completedChunks: 0,
        totalChunks: 8,
        history: SyncHistory.none,
      );

      expect(first.ownsTheScreen, isTrue);
      // Never both: one place on screen talks about the sync at a time.
      expect(first.isNoteworthy, isFalse);
    });
  });

  group('estimates', () {
    SyncStatus withEta(Duration? eta) => SyncStatus(
          state: SyncRunState.running,
          running: true,
          progress: 0.5,
          completedChunks: 4,
          totalChunks: 8,
          history: const SyncHistory(hasHistory: true),
          eta: eta,
        );

    test('no estimate is offered rather than a fabricated one', () {
      expect(withEta(null).etaLabel, isNull);
    });

    test('estimates are rounded to something a person would say', () {
      expect(withEta(const Duration(seconds: 20)).etaLabel, 'almost done');
      expect(withEta(const Duration(minutes: 1)).etaLabel, 'about a minute left');
      expect(
        withEta(const Duration(minutes: 4, seconds: 37)).etaLabel,
        'about 4 minutes left',
      );
      expect(withEta(const Duration(hours: 2)).etaLabel, 'about 2 hours left');
    });
  });

  group('the first-run panel', () {
    testWidgets('shows a determinate bar, not a spinner', (WidgetTester tester) async {
      await _pump(
        tester,
        const SyncProgressPanel(companyId: _companyId),
        const SyncStatus(
          state: SyncRunState.running,
          running: true,
          progress: 0.25,
          completedChunks: 2,
          totalChunks: 8,
          currentLabel: 'Apr - Sep 2025',
          vouchersIngested: 14820,
          eta: Duration(minutes: 3),
          history: SyncHistory.none,
        ),
      );

      final LinearProgressIndicator bar = tester.widget<LinearProgressIndicator>(
        find.byType(LinearProgressIndicator),
      );
      expect(bar.value, closeTo(0.25, 0.0001),
          reason: 'a null value renders as an indeterminate spinner');

      expect(find.text('Apr - Sep 2025'), findsOneWidget);
      expect(find.text('Part 3 of 8'), findsOneWidget);
      expect(find.textContaining('14,820 transactions read'), findsOneWidget);
      expect(find.textContaining('about 3 minutes left'), findsOneWidget);
    });

    testWidgets('says why it takes minutes', (WidgetTester tester) async {
      await _pump(
        tester,
        const SyncProgressPanel(companyId: _companyId),
        const SyncStatus(
          state: SyncRunState.running,
          running: true,
          progress: 0,
          completedChunks: 0,
          totalChunks: 8,
          history: SyncHistory.none,
        ),
      );

      expect(find.textContaining('a few months at a time'), findsOneWidget);
      expect(find.textContaining('Estimating'), findsOneWidget,
          reason: 'no completed slice means no honest estimate');
    });

    testWidgets('a stopped sync offers to carry on', (WidgetTester tester) async {
      await _pump(
        tester,
        const SyncProgressPanel(companyId: _companyId),
        const SyncStatus(
          state: SyncRunState.failed,
          running: false,
          progress: 0.25,
          completedChunks: 2,
          totalChunks: 8,
          history: SyncHistory.none,
          error: 'Your Tally PC is offline.',
        ),
      );

      expect(find.text('Your Tally PC is offline.'), findsOneWidget);
      expect(find.text('Continue reading'), findsOneWidget);
    });
  });

  group('the inline strip', () {
    testWidgets('stays out of the way once the sync is done',
        (WidgetTester tester) async {
      await _pump(
        tester,
        const SyncProgressStrip(companyId: _companyId),
        const SyncStatus(
          state: SyncRunState.succeeded,
          running: false,
          progress: 1,
          completedChunks: 8,
          totalChunks: 8,
          history: SyncHistory(hasHistory: true),
        ),
      );

      expect(find.byType(LinearProgressIndicator), findsNothing);
      expect(find.textContaining('history'), findsNothing);
    });

    testWidgets('reports where it has got to while figures are on screen',
        (WidgetTester tester) async {
      await _pump(
        tester,
        const SyncProgressStrip(companyId: _companyId),
        const SyncStatus(
          state: SyncRunState.running,
          running: true,
          progress: 0.5,
          completedChunks: 4,
          totalChunks: 8,
          currentLabel: 'Oct 2024 - Mar 2025',
          eta: Duration(minutes: 2),
          history: SyncHistory(hasHistory: true),
        ),
      );

      expect(find.text('Still reading your older history'), findsOneWidget);
      expect(
        find.text('Oct 2024 - Mar 2025 · Part 5 of 8 · about 2 minutes left'),
        findsOneWidget,
      );
    });
  });
}
