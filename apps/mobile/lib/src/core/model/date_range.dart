/// An inclusive span of days, and the named windows an owner actually asks for.
///
/// Lives in core rather than under a feature because the reports and the
/// dashboard both scope themselves by one, and the presets must mean the same
/// thing on every screen -- "This month" cannot start on the 1st in one place
/// and thirty days ago in another.
class DateRange {
  const DateRange(this.from, this.to);

  final DateTime from;
  final DateTime to;

  static DateRange today() {
    final DateTime now = DateTime.now();
    final DateTime day = DateTime(now.year, now.month, now.day);
    return DateRange(day, day);
  }

  static DateRange yesterday() {
    final DateTime now = DateTime.now();
    final DateTime day =
        DateTime(now.year, now.month, now.day).subtract(const Duration(days: 1));
    return DateRange(day, day);
  }

  /// Monday through today -- the week as an accountant marks it, not the last
  /// rolling seven days.
  static DateRange thisWeek() {
    final DateTime now = DateTime.now();
    final DateTime today = DateTime(now.year, now.month, now.day);
    final DateTime monday = today.subtract(Duration(days: today.weekday - 1));
    return DateRange(monday, today);
  }

  static DateRange thisMonth() {
    final DateTime now = DateTime.now();
    return DateRange(DateTime(now.year, now.month), DateTime(now.year, now.month, now.day));
  }

  static DateRange lastMonth() {
    final DateTime now = DateTime.now();
    final DateTime firstOfThisMonth = DateTime(now.year, now.month);
    final DateTime lastMonthEnd = firstOfThisMonth.subtract(const Duration(days: 1));
    return DateRange(DateTime(lastMonthEnd.year, lastMonthEnd.month), lastMonthEnd);
  }

  static DateRange thisQuarter() {
    final DateTime now = DateTime.now();
    final int quarterStartMonth = ((now.month - 1) ~/ 3) * 3 + 1;
    return DateRange(
      DateTime(now.year, quarterStartMonth),
      DateTime(now.year, now.month, now.day),
    );
  }

  static DateRange thisYear() {
    final DateTime now = DateTime.now();
    return DateRange(DateTime(now.year), DateTime(now.year, now.month, now.day));
  }

  static DateRange lastDays(int days) {
    final DateTime now = DateTime.now();
    final DateTime end = DateTime(now.year, now.month, now.day);
    return DateRange(end.subtract(Duration(days: days - 1)), end);
  }

  String get fromWire => _iso(from);
  String get toWire => _iso(to);

  bool get isSingleDay =>
      from.year == to.year && from.month == to.month && from.day == to.day;

  int get dayCount => to.difference(from).inDays + 1;

  /// Whether this span ends today. A period that does not is history, and
  /// several screens say so out loud.
  bool get endsToday {
    final DateTime now = DateTime.now();
    return to.year == now.year && to.month == now.month && to.day == now.day;
  }

  /// Mirrors the backend's own clamp (`MAX_REPORT_DAYS`) so a custom range the
  /// server would narrow anyway is narrowed before the request leaves the
  /// phone, with the user told why -- rather than quietly getting back fewer
  /// days than they picked.
  DateRange clampToMaxDays(int maxDays) {
    if (dayCount <= maxDays) return this;
    return DateRange(to.subtract(Duration(days: maxDays - 1)), to);
  }

  static String _iso(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';

  @override
  bool operator ==(Object other) =>
      other is DateRange && other.fromWire == fromWire && other.toWire == toWire;

  @override
  int get hashCode => Object.hash(fromWire, toWire);
}
