import 'date_range.dart';

/// One Indian financial year: 1 April to 31 March.
///
/// This is the frame every figure in the app hangs off, and it is not the
/// calendar year. An owner asking "how did we do this year?" means since April,
/// their books close in March, their filings run to March, and a report labelled
/// "This year" that starts in January answers a question nobody asked. So the
/// year is chosen once, near the company name, and every date filter underneath
/// it is confined to what that choice already decided.
///
/// Identified by the year it *starts* in, because that is the half that does
/// not move: FY 2026-27 begins on 1 April 2026 whichever of its two calendar
/// years you happen to be standing in.
class FinancialYear implements Comparable<FinancialYear> {
  const FinancialYear(this.startYear);

  /// The year containing [day] -- January to March belongs to the year before.
  factory FinancialYear.containing(DateTime day) =>
      FinancialYear(day.month >= 4 ? day.year : day.year - 1);

  static FinancialYear current() => FinancialYear.containing(DateTime.now());

  final int startYear;

  DateTime get start => DateTime(startYear, 4, 1);

  DateTime get end => DateTime(startYear + 1, 3, 31);

  bool get isCurrent => startYear == FinancialYear.current().startYear;

  /// The last day there can be data for: today in the open year, 31 March in a
  /// closed one. Offering an owner a window that runs into next week would be
  /// offering them a report that is guaranteed to be empty at one end.
  DateTime get lastDay {
    if (!isCurrent) return end;
    final DateTime now = DateTime.now();
    return DateTime(now.year, now.month, now.day);
  }

  /// The whole year so far.
  DateRange get toDate => DateRange(start, lastDay);

  /// "FY 2026-27". The form every Indian accountant writes.
  String get label => 'FY $startYear-${((startYear + 1) % 100).toString().padLeft(2, '0')}';

  bool contains(DateTime day) =>
      !day.isBefore(start) && !day.isAfter(DateTime(end.year, end.month, end.day, 23, 59));

  /// Whether any part of [range] falls inside this year.
  bool overlaps(DateRange range) => !range.to.isBefore(start) && !range.from.isAfter(lastDay);

  /// [range] cut down to this year.
  ///
  /// Clamping rather than refusing: an owner who picks "this week" on the 2nd
  /// of April means the part of it that is in this year's books, and showing
  /// them nothing would be a worse answer than showing them Tuesday onwards.
  DateRange confine(DateRange range) {
    if (!overlaps(range)) return toDate;
    final DateTime from = range.from.isBefore(start) ? start : range.from;
    final DateTime to = range.to.isAfter(lastDay) ? lastDay : range.to;
    return DateRange(from, to);
  }

  /// Every year from the one the books begin in, newest first.
  ///
  /// [booksFrom] is Tally's own `BOOKSFROM` for the company, so the list is the
  /// years this business has actually existed for. Null when a company has not
  /// synced yet, and then the list is just the year we are in -- an invented
  /// range of past years would offer an owner reports that can only come back
  /// empty.
  static List<FinancialYear> since(DateTime? booksFrom, {DateTime? now}) {
    final FinancialYear latest =
        now == null ? FinancialYear.current() : FinancialYear.containing(now);
    if (booksFrom == null) return <FinancialYear>[latest];

    final int first = FinancialYear.containing(booksFrom).startYear;
    if (first >= latest.startYear) return <FinancialYear>[latest];
    return <FinancialYear>[
      for (int year = latest.startYear; year >= first; year--) FinancialYear(year),
    ];
  }

  @override
  int compareTo(FinancialYear other) => startYear.compareTo(other.startYear);

  @override
  bool operator ==(Object other) =>
      other is FinancialYear && other.startYear == startYear;

  @override
  int get hashCode => startYear.hashCode;

  @override
  String toString() => label;
}
