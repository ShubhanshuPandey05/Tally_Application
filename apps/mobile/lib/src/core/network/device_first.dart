import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'api_client.dart';

/// How long a report stays in memory after its screen is closed.
///
/// Going back from a report and tapping it again is the gesture this is for:
/// within this window the screen reappears exactly as it was, with no frame of
/// skeleton at all. Past it, the phone's saved copy still answers first -- one
/// frame later, which nobody sees.
const Duration keepWarmFor = Duration(minutes: 5);

/// The cache policy for a provider that opens a screen.
///
/// **The first build answers from the phone's copy** when there is one, then
/// checks the server behind it and rebuilds this provider once something
/// newer has been kept. That rebuild is a refresh, not a reload: screens render
/// with `skipLoadingOnRefresh`, so the figures change in place.
///
/// **Any other rebuild goes to the server.** A provider is invalidated from
/// outside because something changed -- a company was linked, a sync
/// finished -- and answering that from the copy taken before the change would
/// quietly undo it. The server is still backed by the copy if the phone has no
/// signal.
///
/// Also keeps an auto-disposed provider alive for [keepWarmFor] after its last
/// listener goes, which is what makes "back, then tap it again" instant.
CachePolicy deviceFirst(Ref ref) {
  final _Visit visit = _visits[ref] ??= _Visit();
  final bool opening = !visit.built;
  final bool ourOwnCheck = visit.checked;
  visit
    ..built = true
    ..checked = false;

  // A no-op on a provider that is not auto-disposed, like the dashboard's.
  visit.keepWarm(ref);

  if (!opening && !ourOwnCheck) return CachePolicy.keep;

  // Runs on every rebuild as well as on disposal, which is exactly right: a
  // check started by an earlier build has been overtaken and must not trigger
  // another.
  bool current = true;
  ref.onDispose(() => current = false);
  return CachePolicy.deviceFirst(
    onNewer: () {
      if (!current) return;
      visit.checked = true;
      ref.invalidateSelf();
    },
  );
}

/// Per provider element, not per build: Riverpod hands every build of one
/// element the same `Ref`, and forgets its listeners -- but not its keep-alive
/// links -- in between. So the link and its release timer are held here, once,
/// rather than taken again on each build and never let go.
final Expando<_Visit> _visits = Expando<_Visit>('deviceFirst');

class _Visit {
  bool built = false;

  /// The next build was caused by this helper's own background check.
  bool checked = false;

  KeepAliveLink? _link;
  Timer? _release;

  void keepWarm(Ref ref) {
    _link ??= ref.keepAlive();
    // Listeners are cleared on every rebuild, so these are registered on every
    // build; the timer they drive is not.
    ref.onCancel(() {
      _release?.cancel();
      _release = Timer(keepWarmFor, () {
        _link?.close();
        _link = null;
      });
    });
    ref.onResume(() => _release?.cancel());
    // Riverpod runs this on a rebuild as well as on disposal, and cannot say
    // which. Letting go early is right for both: disposed, there is nothing
    // left to keep; rebuilt while nobody is watching, losing a few minutes of
    // warmth is better than a timer nothing will ever cancel -- or, if the
    // timer were simply dropped, a link nothing will ever close.
    ref.onDispose(() {
      if (_release?.isActive ?? false) {
        _release!.cancel();
        _link?.close();
        _link = null;
      }
    });
  }
}
