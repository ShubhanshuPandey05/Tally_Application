import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/providers.dart';
import '../data/update_repository.dart';
import '../domain/app_release.dart';
import '../domain/update_signal.dart';

/// Written by the HTTP interceptor on every response; read here.
///
/// A `ChangeNotifierProvider` so that watching it re-runs [updateStatusProvider]
/// when -- and only when -- the server's answer changes. [UpdateSignal] does the
/// deduplication, which matters: the headers arrive on every single response,
/// and rebuilding the update state that often would be a rebuild per API call.
final ChangeNotifierProvider<UpdateSignal> updateSignalProvider =
    ChangeNotifierProvider<UpdateSignal>((Ref ref) => UpdateSignal());

final Provider<UpdateRepository> updateRepositoryProvider =
    Provider<UpdateRepository>((Ref ref) {
  return UpdateRepository(baseUrl: ref.watch(appConfigProvider).baseUrl);
});

/// The running build's version, from the platform rather than from a constant.
///
/// Read from the package metadata so it cannot drift from the artefact: a
/// hard-coded string here would be one more place to forget on release day,
/// and the symptom -- an app that believes it is permanently out of date, or
/// permanently current -- is confusing enough to lose a day to.
final FutureProvider<String> currentVersionProvider =
    FutureProvider<String>((Ref ref) async {
  final PackageInfo info = await PackageInfo.fromPlatform();
  return info.version;
});

/// Whether an update exists and how insistent to be about it.
///
/// Driven by the response headers on ordinary API traffic, not by a poll. The
/// dashboard the user just opened is what tells the app a release exists, so the
/// delay between publishing and noticing is one request rather than one app
/// restart.
///
/// The manifest is still read, but only in the two cases where headers cannot
/// answer:
///
///  * **Nothing has been requested yet.** A cold start that has not yet hit the
///    API -- or one whose every request failed offline -- has no headers to read.
///  * **Headers detected an update but cannot describe it.** They carry versions,
///    not a URL or a size. One fetch fills that in, and only when there is
///    genuinely something to show.
///
/// Every failure resolves to "up to date" rather than to an error, because a
/// manifest that cannot be reached must never be the reason someone cannot see
/// today's sales.
final FutureProvider<UpdateStatus> updateStatusProvider =
    FutureProvider<UpdateStatus>((Ref ref) async {
  final String current = await ref.watch(currentVersionProvider.future);
  final UpdateSignal signal = ref.watch(updateSignalProvider);
  final UpdateObservation? observed = signal.current;

  // Nothing observed yet: fall back to the manifest so a phone that has just
  // launched still learns about a release before its first request lands.
  if (observed == null) {
    final AppRelease? release = await ref.watch(updateRepositoryProvider).fetch();
    return UpdateStatus.evaluate(currentVersion: current, release: release);
  }

  if (!observed.hasUpdate) {
    return UpdateStatus.upToDate(current);
  }

  // Detected by headers, described by the manifest. A failure here still leaves
  // a usable verdict below -- the app can say "update to 0.2.0" and link out even
  // without the size and notes.
  final AppRelease? release =
      observed.release ?? await ref.watch(updateRepositoryProvider).fetch();

  if (release != null && release.version == observed.latestVersion) {
    return UpdateStatus.evaluate(currentVersion: current, release: release);
  }

  // Either the manifest could not be read, or it disagrees with the headers --
  // which happens for a few minutes after a deploy, since `/downloads` is served
  // with a five-minute cache. The headers come straight from the backend and are
  // the fresher of the two, so they win.
  return UpdateStatus.fromObservation(currentVersion: current, observation: observed);
});

/// Hands the download to the platform.
///
/// The APK goes to the browser rather than being fetched in-app. Android will
/// not install a package straight from another app's storage without
/// `REQUEST_INSTALL_PACKAGES` and a FileProvider -- a permission that also draws
/// Play Store scrutiny on the appbundle we ship alongside the APK. The browser
/// already owns this flow: it downloads, Android verifies the signing key on
/// install, and the user sees the package-installer prompt they recognise.
///
/// The install prompt itself cannot be skipped by any app without device-owner
/// privileges, so the automation worth having is starting the download without
/// being asked -- which [UpdateGate] does -- not eliminating the confirmation.
final Provider<Future<bool> Function(AppRelease)> startUpdateProvider =
    Provider<Future<bool> Function(AppRelease)>((Ref ref) {
  final UpdateRepository repository = ref.watch(updateRepositoryProvider);
  return (AppRelease release) async {
    return launchUrl(
      repository.downloadUri(release),
      mode: LaunchMode.externalApplication,
    );
  };
});
