import 'dart:collection';
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:hive_ce/hive.dart';
import 'package:path_provider/path_provider.dart';

/// One response as this phone last received it.
class SavedRead {
  const SavedRead(this.body, this.savedAt);

  /// The JSON exactly as the server sent it -- a map or a list.
  final Object? body;

  /// When the *phone* received it. Not when the server read it from Tally:
  /// that is `refreshed_at` inside the body, and the two answer different
  /// questions ("how old is this copy?" versus "how old are these figures?").
  final DateTime savedAt;

  Duration get age => DateTime.now().difference(savedAt);
}

/// The phone's copy of every read it has been shown.
///
/// It exists for two moments. Opening the app with no signal must show the
/// last figures rather than an error, and opening a report somebody looked at
/// a minute ago must not flash a skeleton while the same figures come back.
///
/// Stores raw JSON rather than domain objects, so a new build with a changed
/// mapper reads old copies through the new mapper instead of through a
/// serialiser that has to be kept in step with every model.
///
/// **Never shared between people.** Cleared on every sign-in, sign-out and
/// expired session, so one person's receivables cannot reach the next
/// person's screen on a shared counter phone.
abstract class ReadCache {
  Future<SavedRead?> read(String key);

  Future<void> write(String key, Object? body);

  Future<void> clear();

  /// Identity of a read: the path and every query parameter except `mode`.
  ///
  /// `mode` is excluded on purpose. A pull-to-refresh asks for the same report
  /// with `mode=live`, and its answer must replace the copy the screen reads
  /// with `mode=auto` -- otherwise refreshing would update a copy no screen
  /// ever looks at.
  static String keyFor(String path, Map<String, Object?>? query) {
    final List<String> parts = <String>[
      for (final MapEntry<String, Object?> entry in (query ?? <String, Object?>{})
          .entries
          .where((MapEntry<String, Object?> e) => e.key != 'mode' && e.value != null)
          .toList()
        ..sort((MapEntry<String, Object?> a, MapEntry<String, Object?> b) =>
            a.key.compareTo(b.key)))
        '${entry.key}=${entry.value}',
    ];
    return parts.isEmpty ? path : '$path?${parts.join('&')}';
  }
}

/// Held in memory only. Used by tests, and as the fallback when the on-disk
/// store cannot be opened -- a broken cache must cost the offline copy, never
/// the app.
class MemoryReadCache implements ReadCache {
  final Map<String, SavedRead> _entries = <String, SavedRead>{};

  @override
  Future<SavedRead?> read(String key) async => _entries[key];

  @override
  Future<void> write(String key, Object? body) async {
    _entries[key] = SavedRead(body, DateTime.now());
  }

  @override
  Future<void> clear() async => _entries.clear();
}

/// The on-disk store: an encrypted Hive box, with a small index beside it.
///
/// A *lazy* box, so launching the app reads the keys and not a year of day
/// books into memory. The index box -- key to save time -- is what eviction
/// runs on without opening every payload to find the oldest.
class HiveReadCache implements ReadCache {
  HiveReadCache._(this._bodies, this._index);

  final LazyBox<String> _bodies;
  final Box<String> _index;

  /// The last few decoded reads, so going back and forth between a list and a
  /// report neither decrypts nor parses the same JSON twice.
  final LinkedHashMap<String, SavedRead> _recent = LinkedHashMap<String, SavedRead>();

  /// Enough for every report of a handful of companies plus the drill-downs
  /// somebody walked through recently. Past this the oldest go first.
  static const int _maxEntries = 250;

  /// A response larger than this is not kept. A 400-day day book of a busy
  /// shop can run to megabytes, and one such copy is not worth crowding out
  /// fifty ordinary ones.
  static const int _maxBodyChars = 3 * 1024 * 1024;

  static const int _recentCapacity = 24;

  static const String _bodiesBox = 'tallyflow_reads';
  static const String _indexBox = 'tallyflow_reads_index';
  static const String _keyName = 'tallyflow.read_cache_key';

  /// Opens the store, or falls back to memory.
  ///
  /// A key that no longer opens the box -- secure storage wiped by an OS
  /// restore, say -- means the copy on disk is unreadable. It is deleted and
  /// started again rather than retried: it held nothing that the next read
  /// will not bring back.
  static Future<ReadCache> open({FlutterSecureStorage? secure}) async {
    final FlutterSecureStorage storage = secure ??
        const FlutterSecureStorage(
          aOptions: AndroidOptions(encryptedSharedPreferences: true),
          iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
        );
    try {
      if (!kIsWeb) {
        Hive.init((await getApplicationSupportDirectory()).path);
      }
      final HiveAesCipher cipher = HiveAesCipher(await _cipherKey(storage));
      try {
        return await _openBoxes(cipher);
      } catch (_) {
        await Hive.deleteBoxFromDisk(_bodiesBox);
        await Hive.deleteBoxFromDisk(_indexBox);
        return await _openBoxes(cipher);
      }
    } catch (error) {
      debugPrint('read cache unavailable, keeping reads in memory: $error');
      return MemoryReadCache();
    }
  }

  static Future<HiveReadCache> _openBoxes(HiveAesCipher cipher) async =>
      HiveReadCache._(
        await Hive.openLazyBox<String>(_bodiesBox, encryptionCipher: cipher),
        await Hive.openBox<String>(_indexBox, encryptionCipher: cipher),
      );

  static Future<List<int>> _cipherKey(FlutterSecureStorage storage) async {
    final String? stored = await storage.read(key: _keyName);
    if (stored != null) {
      final List<int> key = base64Decode(stored);
      if (key.length == 32) return key;
    }
    final List<int> key = Hive.generateSecureKey();
    await storage.write(key: _keyName, value: base64Encode(key));
    return key;
  }

  /// Hashed, because Hive caps a key at 255 ASCII characters and a statement
  /// query carries a ledger name that may be neither.
  static String _slot(String key) => sha256.convert(utf8.encode(key)).toString();

  @override
  Future<SavedRead?> read(String key) async {
    final String slot = _slot(key);
    final SavedRead? recent = _recent.remove(slot);
    if (recent != null) {
      _recent[slot] = recent;
      return recent;
    }
    try {
      final String? raw = await _bodies.get(slot);
      final DateTime? savedAt = DateTime.tryParse(_index.get(slot) ?? '');
      if (raw == null || savedAt == null) return null;
      return _remember(slot, SavedRead(jsonDecode(raw), savedAt));
    } catch (_) {
      // A corrupt entry is a missing entry. The next read replaces it.
      return null;
    }
  }

  @override
  Future<void> write(String key, Object? body) async {
    final String slot = _slot(key);
    final SavedRead saved = SavedRead(body, DateTime.now());
    try {
      final String raw = jsonEncode(body);
      if (raw.length > _maxBodyChars) {
        // Too big to keep, but the copy already held is now out of date and
        // must not be served as though it were the latest.
        await _forget(slot);
        return;
      }
      _remember(slot, saved);
      await _bodies.put(slot, raw);
      await _index.put(slot, saved.savedAt.toIso8601String());
      await _evict();
    } catch (error) {
      debugPrint('could not keep a read on the phone: $error');
    }
  }

  @override
  Future<void> clear() async {
    _recent.clear();
    try {
      await _bodies.clear();
      await _index.clear();
    } catch (error) {
      debugPrint('could not clear the read cache: $error');
    }
  }

  SavedRead _remember(String slot, SavedRead saved) {
    _recent.remove(slot);
    _recent[slot] = saved;
    while (_recent.length > _recentCapacity) {
      _recent.remove(_recent.keys.first);
    }
    return saved;
  }

  Future<void> _forget(String slot) async {
    _recent.remove(slot);
    await _bodies.delete(slot);
    await _index.delete(slot);
  }

  Future<void> _evict() async {
    if (_index.length <= _maxEntries) return;
    final List<MapEntry<dynamic, String>> byAge = _index.toMap().entries.toList()
      ..sort((MapEntry<dynamic, String> a, MapEntry<dynamic, String> b) =>
          a.value.compareTo(b.value));
    for (final MapEntry<dynamic, String> entry
        in byAge.take(_index.length - _maxEntries)) {
      await _forget(entry.key as String);
    }
  }
}
