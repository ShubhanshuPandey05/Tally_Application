import 'dart:convert';
import 'dart:io';

/// Loads the JSON captured from the real backend.
///
/// These files are written by `apps/backend/tests/test_wire_contract.py` and
/// checked in. The app's decoders are tested against them rather than against
/// hand-written maps on purpose: a hand-written mock encodes what the app
/// *believes* the server sends, which is exactly the belief a wire change
/// invalidates. A renamed field fails here, in CI, instead of on a phone.
Map<String, Object?> fixture(String name) {
  final File file = File('test/fixtures/$name.json');
  if (!file.existsSync()) {
    throw StateError(
      'Missing wire fixture "$name". Regenerate with:\n'
      '  UPDATE_WIRE_FIXTURES=1 pytest apps/backend/tests/test_wire_contract.py',
    );
  }
  final Object? decoded = jsonDecode(file.readAsStringSync());
  if (decoded is! Map<String, Object?>) {
    throw StateError('Fixture "$name" is not a JSON object.');
  }
  return decoded;
}

List<Map<String, Object?>> fixtureList(String name) {
  final File file = File('test/fixtures/$name.json');
  final Object? decoded = jsonDecode(file.readAsStringSync());
  if (decoded is! List) {
    throw StateError('Fixture "$name" is not a JSON array.');
  }
  return decoded.cast<Map<String, Object?>>();
}
