/// What the update manifest says, and what the app should do about it.
///
/// The manifest is a static JSON file written by `python run.py publish`, served
/// from the same host as the API at `/downloads/manifest.json`. It is generated
/// from the artefacts actually staged for download, so the version and checksum
/// here always describe a file that exists.
library;

import 'package:flutter/foundation.dart';

/// Which manifest key describes the build this app was compiled as.
///
/// Only Android publishes a downloadable artefact: iOS cannot be updated outside
/// the App Store, and web reloads itself. Null for those, and null is
/// load-bearing in both places it is read -- it stops the app advertising an APK
/// to a platform that could not install it, and stops the backend judging such a
/// build against the Android floor and refusing it with 426.
///
/// Derived from [defaultTargetPlatform] rather than `dart:io`'s `Platform`
/// because this is reached from the core HTTP client, and `dart:io` does not
/// exist on web -- `run.py release` builds a web target, so importing it would
/// break that build.
///
/// Deliberately the only implementation of this question. It was briefly two,
/// and two implementations of "which platform am I?" that can disagree is how the
/// version the backend judges stops matching the artefact the app would fetch.
String? get currentPlatformKey {
  if (kIsWeb) return null;
  return defaultTargetPlatform == TargetPlatform.android ? 'android' : null;
}

/// Turns `"0.10.2"` into `[0, 10, 2]` for comparison.
///
/// Comparing versions as strings is wrong in a way that stays invisible for a
/// year: `"0.10.0"` sorts *before* `"0.9.0"`, so the first release past x.9
/// silently stops being offered to anybody.
///
/// Non-numeric suffixes are dropped, so `0.2.0-rc1` compares equal to `0.2.0`.
/// That is the intended reading of "am I out of date?" -- a release candidate
/// and its release are the same build to a user.
List<int> parseVersion(String value) {
  return value.split('.').map((String part) {
    final StringBuffer digits = StringBuffer();
    for (final String char in part.split('')) {
      if (int.tryParse(char) == null) break;
      digits.write(char);
    }
    return int.tryParse(digits.toString()) ?? 0;
  }).toList();
}

/// Whether [candidate] is a strictly higher version than [current].
bool isNewer(String candidate, String current) => compareVersions(candidate, current) > 0;

/// Standard comparator contract: negative, zero, or positive.
int compareVersions(String a, String b) {
  final List<int> left = parseVersion(a);
  final List<int> right = parseVersion(b);
  final int length = left.length > right.length ? left.length : right.length;
  for (int i = 0; i < length; i++) {
    final int x = i < left.length ? left[i] : 0;
    final int y = i < right.length ? right[i] : 0;
    if (x != y) return x.compareTo(y);
  }
  return 0;
}

/// One platform's published build.
class AppRelease {
  const AppRelease({
    required this.version,
    required this.url,
    required this.sha256,
    required this.sizeBytes,
    required this.file,
    this.buildNumber,
    this.mandatory = false,
    this.minSupportedVersion = '0.0.0',
    this.notes = '',
  });

  final String version;

  /// Relative to the manifest (`/downloads/TallyFlow-0.2.0.apk`), so one
  /// manifest works behind whatever hostname a deployment happens to use.
  final String url;
  final String sha256;
  final int sizeBytes;
  final String file;
  final int? buildNumber;

  /// The update installs without asking (connector) or blocks the app until
  /// taken (here). Reserved for security fixes and wire breaks.
  final bool mandatory;

  /// The floor. Below this the app cannot talk to the backend correctly and
  /// says so, rather than failing in whatever way the change happens to break.
  final String minSupportedVersion;
  final String notes;

  factory AppRelease.fromJson(Map<String, Object?> json) {
    final Object? version = json['version'];
    final Object? url = json['url'];
    if (version is! String || url is! String) {
      throw const FormatException('manifest entry is missing version or url');
    }
    return AppRelease(
      version: version,
      url: url,
      sha256: (json['sha256'] as String? ?? '').toLowerCase(),
      sizeBytes: (json['size_bytes'] as num?)?.toInt() ?? 0,
      file: json['file'] as String? ?? '',
      buildNumber: (json['build_number'] as num?)?.toInt(),
      mandatory: json['mandatory'] as bool? ?? false,
      minSupportedVersion: json['min_supported_version'] as String? ?? '0.0.0',
      notes: json['notes'] as String? ?? '',
    );
  }

  String get sizeLabel => '${(sizeBytes / 1e6).toStringAsFixed(1)} MB';

  /// Whether enough is known to actually fetch this build.
  ///
  /// False for a release assembled from response headers alone. The UI uses it
  /// to choose between offering a download button and telling the user where to
  /// go -- an "Install" button that cannot resolve a URL is worse than no button.
  bool get isDownloadable => url.isNotEmpty;

  /// A release known only by version, from the advisory response headers.
  ///
  /// Exists so the app can act on "you are out of date" even when the manifest
  /// is unreachable. That is not a hypothetical: the backend refusing an old
  /// build with 426 and `/downloads` being briefly unreachable are both symptoms
  /// of the same deploy, and blocking without saying what to do would be the
  /// worst possible moment to have nothing to show.
  factory AppRelease.fromVersion({
    required String version,
    String minSupportedVersion = '0.0.0',
    bool mandatory = false,
  }) {
    return AppRelease(
      version: version,
      url: '',
      sha256: '',
      sizeBytes: 0,
      file: '',
      mandatory: mandatory,
      minSupportedVersion: minSupportedVersion,
    );
  }
}

/// What the app should do, given a release and the version it is running.
enum UpdateAction {
  /// Nothing to do.
  none,

  /// A newer build exists. Mention it; do not interrupt anyone.
  optional,

  /// The running version is below the published floor, or the release is
  /// marked mandatory. The app must not carry on as though nothing is wrong.
  required,
}

/// One reading of the backend's opinion about this build.
///
/// Produced by the HTTP interceptor from the advisory response headers, which
/// ride on every response. Lives here beside [UpdateAction] rather than with
/// [UpdateSignal] so the domain has no import cycle.
@immutable
class UpdateObservation {
  const UpdateObservation({
    required this.latestVersion,
    required this.minVersion,
    required this.action,
    this.release,
  });

  final String latestVersion;
  final String minVersion;
  final UpdateAction action;

  /// The full release entry, when we have it. Null when only headers have been
  /// seen -- enough to know an update exists, not enough to download it.
  final AppRelease? release;

  bool get hasUpdate => action != UpdateAction.none;

  /// Whether the release details still need fetching from the manifest.
  bool get needsDetail => hasUpdate && release == null;

  bool sameAs(UpdateObservation other) =>
      latestVersion == other.latestVersion &&
      minVersion == other.minVersion &&
      action == other.action &&
      release?.version == other.release?.version;
}

/// The decision, and everything the UI needs to explain it.
class UpdateStatus {
  const UpdateStatus({
    required this.action,
    required this.currentVersion,
    this.release,
  });

  const UpdateStatus.upToDate(this.currentVersion)
      : action = UpdateAction.none,
        release = null;

  final UpdateAction action;
  final String currentVersion;
  final AppRelease? release;

  bool get isRequired => action == UpdateAction.required;
  bool get hasUpdate => action != UpdateAction.none;

  /// Decide from what the backend said on a response, rather than from the
  /// manifest.
  ///
  /// The backend has already applied the policy -- it compared this build against
  /// the published floor to produce `action` -- so this trusts that verdict
  /// instead of recomputing it. Recomputing would need the release details the
  /// headers do not carry, and would mean two implementations of the same rule
  /// that could disagree while the app is being refused service.
  ///
  /// A version-only [AppRelease] stands in when the manifest could not fill in
  /// the details, so the UI always has something to name.
  factory UpdateStatus.fromObservation({
    required String currentVersion,
    required UpdateObservation observation,
  }) {
    if (!observation.hasUpdate) return UpdateStatus.upToDate(currentVersion);

    return UpdateStatus(
      action: observation.action,
      currentVersion: currentVersion,
      release: observation.release ??
          AppRelease.fromVersion(
            version: observation.latestVersion,
            minSupportedVersion:
                observation.minVersion.isEmpty ? '0.0.0' : observation.minVersion,
            mandatory: observation.action == UpdateAction.required,
          ),
    );
  }

  /// Decide, from a release and the running version.
  ///
  /// The floor is checked independently of whether a newer build exists,
  /// because those are different failures: `mandatory` says "take this one",
  /// while `min_supported_version` says "the one you have no longer works".
  factory UpdateStatus.evaluate({
    required String currentVersion,
    required AppRelease? release,
  }) {
    if (release == null) return UpdateStatus.upToDate(currentVersion);

    final bool belowFloor = compareVersions(currentVersion, release.minSupportedVersion) < 0;
    if (!isNewer(release.version, currentVersion)) {
      // Already on the published build, or ahead of it (a tester's build).
      // Never report an update in either case -- offering a downgrade would
      // put the fleet in a loop between two versions.
      return UpdateStatus.upToDate(currentVersion);
    }

    return UpdateStatus(
      action: belowFloor || release.mandatory
          ? UpdateAction.required
          : UpdateAction.optional,
      currentVersion: currentVersion,
      release: release,
    );
  }
}
