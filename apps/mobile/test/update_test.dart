/// The update manager's decision logic.
///
/// Two failures are worth more than the rest put together, and both are quiet:
/// offering an update that is really a downgrade (the fleet then flaps between
/// two builds), and blocking someone out of their own books because a check
/// that could not be made was read as "you are too old".
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/updates/domain/app_release.dart';

AppRelease release({
  String version = '0.2.0',
  bool mandatory = false,
  String floor = '0.0.0',
}) {
  return AppRelease(
    version: version,
    url: '/downloads/TallyFlow-$version.apk',
    sha256: 'abc123',
    sizeBytes: 23583706,
    file: 'TallyFlow-$version.apk',
    mandatory: mandatory,
    minSupportedVersion: floor,
  );
}

void main() {
  group('version comparison', () {
    test('a higher version is newer', () {
      expect(isNewer('0.2.0', '0.1.0'), isTrue);
    });

    test('the same version is not newer', () {
      expect(isNewer('0.1.0', '0.1.0'), isFalse);
    });

    test('an older version is not newer', () {
      expect(isNewer('0.1.0', '0.2.0'), isFalse);
    });

    test('0.10.0 beats 0.9.0', () {
      // The release at which comparing as strings silently stops working:
      // "0.10.0" sorts before "0.9.0" as text.
      expect(isNewer('0.10.0', '0.9.0'), isTrue);
    });

    test('a release candidate matches its release', () {
      expect(isNewer('0.2.0-rc1', '0.2.0'), isFalse);
      expect(isNewer('0.2.0', '0.2.0-rc1'), isFalse);
    });

    test('missing segments count as zero', () {
      expect(compareVersions('1.0', '1.0.0'), 0);
      expect(isNewer('1.0.1', '1.0'), isTrue);
    });

    test('parses to numbers', () {
      expect(parseVersion('0.10.2'), <int>[0, 10, 2]);
    });
  });

  group('what to do about it', () {
    test('no manifest entry means nothing to do', () {
      final UpdateStatus status =
          UpdateStatus.evaluate(currentVersion: '0.1.0', release: null);
      expect(status.action, UpdateAction.none);
      expect(status.hasUpdate, isFalse);
    });

    test('the running version is never offered to itself', () {
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.2.0',
        release: release(version: '0.2.0'),
      );
      expect(status.action, UpdateAction.none);
    });

    test('a build ahead of the manifest is left alone', () {
      // A tester on an unreleased build must not be walked backwards.
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.3.0',
        release: release(version: '0.2.0'),
      );
      expect(status.action, UpdateAction.none);
    });

    test('a newer build is optional by default', () {
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.1.0',
        release: release(version: '0.2.0'),
      );
      expect(status.action, UpdateAction.optional);
      expect(status.isRequired, isFalse);
      expect(status.release?.version, '0.2.0');
    });

    test('a mandatory release is required', () {
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.1.0',
        release: release(version: '0.2.0', mandatory: true),
      );
      expect(status.action, UpdateAction.required);
    });

    test('a version below the floor is required', () {
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.1.0',
        release: release(version: '0.3.0', floor: '0.2.0'),
      );
      expect(status.action, UpdateAction.required);
    });

    test('a version on the floor is only optional', () {
      // The floor is "below this stops working", not "below this is old".
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.2.0',
        release: release(version: '0.3.0', floor: '0.2.0'),
      );
      expect(status.action, UpdateAction.optional);
    });

    test('a floor above the running version cannot block a current build', () {
      // The dangerous misconfiguration: a floor raised past what is published.
      // Nothing newer exists, so there is nothing to demand -- blocking here
      // would lock every user out with no way forward.
      final UpdateStatus status = UpdateStatus.evaluate(
        currentVersion: '0.2.0',
        release: release(version: '0.2.0', floor: '0.9.0'),
      );
      expect(status.action, UpdateAction.none);
    });
  });

  group('parsing the manifest', () {
    test('reads a full entry', () {
      final AppRelease parsed = AppRelease.fromJson(<String, Object?>{
        'version': '0.2.0',
        'file': 'TallyFlow-0.2.0.apk',
        'url': '/downloads/TallyFlow-0.2.0.apk',
        'sha256': 'ABC123',
        'size_bytes': 23583706,
        'build_number': 4,
        'mandatory': true,
        'min_supported_version': '0.1.0',
        'notes': 'Period picker.',
      });

      expect(parsed.version, '0.2.0');
      expect(parsed.buildNumber, 4);
      expect(parsed.mandatory, isTrue);
      expect(parsed.sha256, 'abc123', reason: 'hashes compare lower-case');
      expect(parsed.sizeLabel, '23.6 MB');
    });

    test('an entry without a version is refused rather than guessed', () {
      expect(
        () => AppRelease.fromJson(<String, Object?>{'url': '/x.apk'}),
        throwsA(isA<FormatException>()),
      );
    });

    test('optional fields fall back to safe defaults', () {
      final AppRelease parsed = AppRelease.fromJson(<String, Object?>{
        'version': '0.2.0',
        'url': '/downloads/TallyFlow-0.2.0.apk',
      });
      expect(parsed.mandatory, isFalse);
      expect(parsed.minSupportedVersion, '0.0.0');
      expect(parsed.notes, isEmpty);
    });
  });

  group('resolving the download', () {
    test('a relative url resolves against the manifest host', () {
      // Manifest urls are relative so one file serves any hostname.
      final Uri resolved = Uri.parse('https://uat.example.test/downloads/manifest.json')
          .resolve(release(version: '0.2.0').url);
      expect(resolved.toString(),
          'https://uat.example.test/downloads/TallyFlow-0.2.0.apk');
    });
  });
}
