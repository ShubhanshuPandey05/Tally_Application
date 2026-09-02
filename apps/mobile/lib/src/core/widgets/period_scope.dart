import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/companies/application/financial_year_providers.dart';
import '../model/date_range.dart';
import '../model/financial_year.dart';
import 'period_picker.dart';

/// A report screen's own date window, kept inside the financial year chosen at
/// the top of the app.
///
/// Every report used to hold its window entirely to itself, which was fine when
/// there was only one year to be in. Once the year became a choice, four
/// screens each had to answer the same three questions -- what do I open on,
/// what happens when the year changes under me, and what may the picker offer
/// -- and four separate answers is how one report ends up quietly reporting on
/// a year the header says it is not.
///
/// Screens implement [initialPeriod] and call [watchFinancialYear] from
/// `build`.
mixin YearScopedPeriod<T extends ConsumerStatefulWidget> on ConsumerState<T> {
  /// What this screen opens on inside [year]. A closed year has no "today" and
  /// no "last 30 days", so most screens answer with the whole year there.
  PeriodSelection initialPeriod(FinancialYear year);

  FinancialYear? _year;
  PeriodSelection? _period;

  FinancialYear get year {
    final FinancialYear? held = _year;
    if (held != null) return held;
    final FinancialYear resolved = ref.read(activeFinancialYearProvider);
    _year = resolved;
    return resolved;
  }

  PeriodSelection get period {
    return _period ??= initialPeriod(year);
  }

  DateRange get range => period.range;

  String get periodLabel => period.label;

  /// Re-anchors the window when the year changes. Called from `build` rather
  /// than through a listener so that the first frame after a switch already
  /// carries the new window -- a frame drawn with last year's dates under this
  /// year's heading is a screenshot somebody will send back as a bug.
  void watchFinancialYear() {
    final FinancialYear current = ref.watch(activeFinancialYearProvider);
    if (_year == current) return;
    _year = current;
    _period = initialPeriod(current);
  }

  Future<void> pickPeriod({int maxDays = 400}) async {
    final PeriodSelection? picked = await showPeriodPicker(
      context,
      current: range,
      year: year,
      maxDays: maxDays,
    );
    if (picked == null || !mounted) return;
    setState(() => _period = picked);
  }
}
