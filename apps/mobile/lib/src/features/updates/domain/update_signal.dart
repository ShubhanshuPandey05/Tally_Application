import 'package:flutter/foundation.dart';

import 'app_release.dart';

/// What the backend last said about versions, and a nudge when that changes.
///
/// Every API response carries the newest published version and the floor, so
/// this is written to constantly -- on every dashboard load, every report, every
/// token refresh. It therefore only notifies when the answer actually *changes*,
/// which for a current app is never. Without that, the whole provider tree that
/// depends on update state would rebuild on every HTTP response in the app.
///
/// Lives in the domain layer because two very different sources write to it --
/// response headers and, as a cold-start fallback, the published manifest -- and
/// both have to converge on one answer. A widget reading two sources and
/// deciding for itself is how the banner and the blocking gate end up disagreeing
/// about whether an update exists.
///
/// The latest reading always wins, including one that *withdraws* a `required`
/// verdict. The backend is the authority here, so a floor that turns out to be
/// wrong and gets corrected has to be able to unblock a running app -- making
/// `required` sticky would mean every affected user had to restart before they
/// could work again, which is the opposite of what a rollback is for.
class UpdateSignal extends ChangeNotifier {
  UpdateObservation? _current;

  /// The most recent thing the server said, or null if it has not said anything.
  UpdateObservation? get current => _current;

  /// Called from the HTTP interceptor for every response that carried the
  /// advisory headers.
  void reportHeaders({
    required String latestVersion,
    required String minVersion,
    required String action,
  }) {
    _apply(
      UpdateObservation(
        latestVersion: latestVersion,
        minVersion: minVersion,
        action: _parseAction(action),
        // Header-only: the version is known, the download is not. Whoever
        // consumes this fills in the rest from the manifest, once, rather than
        // the headers trying to carry a URL and a checksum on every response.
        release: _current?.release?.version == latestVersion ? _current?.release : null,
      ),
    );
  }

  /// Called when a request was refused with 426. The body carries the full
  /// release, so this is the one path that needs no follow-up fetch.
  void reportRequired(AppRelease? release) {
    _apply(
      UpdateObservation(
        latestVersion: release?.version ?? _current?.latestVersion ?? '',
        minVersion: release?.minSupportedVersion ?? _current?.minVersion ?? '',
        action: UpdateAction.required,
        release: release ?? _current?.release,
      ),
    );
  }

  void _apply(UpdateObservation next) {
    final UpdateObservation? previous = _current;
    _current = next;
    // Notify only on a real change -- these arrive on every response, and the
    // answer for an up-to-date app is the same every time.
    if (previous != null && previous.sameAs(next)) return;
    notifyListeners();
  }

  static UpdateAction _parseAction(String value) {
    switch (value) {
      case 'required':
        return UpdateAction.required;
      case 'optional':
        return UpdateAction.optional;
      default:
        return UpdateAction.none;
    }
  }
}
