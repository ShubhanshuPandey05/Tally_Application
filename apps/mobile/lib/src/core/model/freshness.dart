/// How current a figure is, and whether the shop's PC is reachable.
///
/// The backend attaches this to every data response and the app renders it on
/// every screen. That is a product rule, not decoration: reads are served from
/// snapshots, so a number with no "as of" is a number an owner may act on
/// believing it is live. The two flags are independent -- data can be fresh
/// while the connector has since gone offline, and stale while it is online but
/// slow.
class Freshness {
  const Freshness({
    required this.available,
    this.refreshedAt,
    this.ageSeconds = 0,
    this.isStale = false,
    this.connectorOnline = false,
    this.fromSnapshot = true,
    this.error,
    this.sectionsUnavailable = 0,
  });

  final bool available;
  final DateTime? refreshedAt;
  final double ageSeconds;
  final bool isStale;
  final bool connectorOnline;
  final bool fromSnapshot;
  final String? error;
  final int sectionsUnavailable;

  static const Freshness unavailable = Freshness(available: false);

  factory Freshness.fromJson(Map<String, Object?>? json) {
    if (json == null || json.isEmpty) return unavailable;
    final Object? refreshed = json['refreshed_at'];
    return Freshness(
      // A meta block with a timestamp is data we have, even when the backend
      // did not spell out `available` (report envelopes omit it).
      available: (json['available'] as bool?) ?? refreshed != null,
      refreshedAt: refreshed is String ? DateTime.tryParse(refreshed)?.toLocal() : null,
      ageSeconds: (json['age_seconds'] as num?)?.toDouble() ?? 0,
      isStale: (json['is_stale'] as bool?) ?? false,
      connectorOnline: (json['connector_online'] as bool?) ?? false,
      fromSnapshot: (json['from_snapshot'] as bool?) ?? true,
      error: json['error'] as String?,
      sectionsUnavailable: (json['sections_unavailable'] as num?)?.toInt() ?? 0,
    );
  }

  /// The phrase shown under the company name. Written for a shop owner: no
  /// timestamps in ISO, no jargon, and never a claim we cannot support.
  String get label {
    if (!available) return 'No data yet';
    final DateTime? at = refreshedAt;
    if (at == null) return 'Updated recently';
    return 'Updated ${relativeTime(at)}';
  }

  /// Whether the app should warn the user rather than just inform them.
  bool get needsAttention => !available || isStale || !connectorOnline;

  static String relativeTime(DateTime time) {
    final Duration delta = DateTime.now().difference(time);
    if (delta.inSeconds < 60) return 'just now';
    if (delta.inMinutes < 60) {
      return '${delta.inMinutes} min ago';
    }
    if (delta.inHours < 24) {
      final int hours = delta.inHours;
      return '$hours ${hours == 1 ? 'hour' : 'hours'} ago';
    }
    final int days = delta.inDays;
    if (days < 30) return '$days ${days == 1 ? 'day' : 'days'} ago';
    return 'on ${time.day}/${time.month}/${time.year}';
  }
}

/// A payload plus its freshness. Every repository returns one of these so a
/// screen physically cannot render figures without having the "as of" in hand.
class Fresh<T> {
  const Fresh(this.data, this.freshness);

  final T data;
  final Freshness freshness;

  Fresh<R> map<R>(R Function(T value) transform) =>
      Fresh<R>(transform(data), freshness);
}
