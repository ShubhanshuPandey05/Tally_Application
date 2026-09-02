import 'package:flutter/material.dart';

import '../model/date_range.dart';
import '../model/financial_year.dart';

/// A named way of computing a [DateRange], relative to "now" at the moment it
/// is chosen -- "This month" picked on the 3rd and picked again on the 28th
/// point at different windows, which is the whole reason presets are
/// functions and not precomputed ranges.
class PeriodPreset {
  const PeriodPreset(this.label, this.range);

  final String label;
  final DateRange Function() range;
}

/// The set offered inside one financial year.
///
/// There is no "This year" here, and that is the point. The year is chosen
/// once, at the top of the home screen, and everything below it is a window
/// *within* that choice -- so on 2 September the longest option is 1 April to
/// today, not the last twelve months and not the calendar year. A filter that
/// silently reached back across 31 March would be mixing two sets of books in
/// one figure, which in an accounting product is a wrong number rather than an
/// untidy one.
///
/// A closed year gets different options for the same reason: "Today" and "This
/// month" mean nothing in a year that ended, so it is offered its quarters
/// instead. Anything else is the custom range, which the picker bounds to the
/// year as well.
List<PeriodPreset> periodPresetsFor(FinancialYear year) {
  if (!year.isCurrent) {
    return <PeriodPreset>[
      PeriodPreset('Full year', () => year.toDate),
      PeriodPreset('Apr – Jun', () => _quarter(year, 4)),
      PeriodPreset('Jul – Sep', () => _quarter(year, 7)),
      PeriodPreset('Oct – Dec', () => _quarter(year, 10)),
      PeriodPreset('Jan – Mar', () => _quarter(year, 1)),
    ];
  }

  final List<PeriodPreset> presets = <PeriodPreset>[
    const PeriodPreset('Today', DateRange.today),
    const PeriodPreset('Yesterday', DateRange.yesterday),
    const PeriodPreset('This week', DateRange.thisWeek),
    PeriodPreset('Last 7 days', () => DateRange.lastDays(7)),
    const PeriodPreset('This month', DateRange.thisMonth),
    const PeriodPreset('Last month', DateRange.lastMonth),
    const PeriodPreset('This quarter', DateRange.thisQuarter),
    PeriodPreset(year.label, () => year.toDate),
  ];

  // Early in April several of these lie entirely in the year that has just
  // closed. Dropping them beats offering a button that answers with a window
  // from the previous set of books.
  return <PeriodPreset>[
    for (final PeriodPreset preset in presets)
      if (year.overlaps(preset.range()))
        PeriodPreset(preset.label, () => year.confine(preset.range())),
  ];
}

/// The quarter of [year] beginning in [month]. January starts the fourth one,
/// which falls in the following calendar year.
DateRange _quarter(FinancialYear year, int month) {
  final int calendarYear = month >= 4 ? year.startYear : year.startYear + 1;
  final DateTime start = DateTime(calendarYear, month);
  final DateTime end = DateTime(calendarYear, month + 3, 0);
  return year.confine(DateRange(start, end));
}

/// A chosen period plus the label it should be shown under. Kept together so
/// a custom range picked once does not need its label re-derived from the
/// dates every time it is displayed.
class PeriodSelection {
  const PeriodSelection(this.range, this.label);

  /// The ordinary starting point for a screen in the year we are in.
  PeriodSelection.today() : this(DateRange.today(), 'Today');

  final DateRange range;
  final String label;
}

/// The pill that sits in a report's filter row and opens the full period
/// sheet. Shows the active period so switching back to the report later does
/// not require remembering which button was tapped.
class PeriodField extends StatelessWidget {
  const PeriodField({
    super.key,
    required this.label,
    required this.onTap,
  });

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ActionChip(
      avatar: Icon(Icons.calendar_month_outlined, size: 17, color: theme.colorScheme.primary),
      label: Text(label),
      onPressed: onTap,
    );
  }
}

/// Opens the period picker: presets first, a custom range at the bottom.
///
/// [maxDays] mirrors the backend's own window clamp (see `MAX_REPORT_DAYS`).
/// A custom range longer than that is narrowed here, with the user told why,
/// rather than silently narrowed by the server on the request that follows.
Future<PeriodSelection?> showPeriodPicker(
  BuildContext context, {
  required DateRange current,
  required FinancialYear year,
  int maxDays = 400,
}) {
  return showModalBottomSheet<PeriodSelection>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (BuildContext sheetContext) => _PeriodSheet(
      current: current,
      year: year,
      maxDays: maxDays,
    ),
  );
}

class _PeriodSheet extends StatelessWidget {
  const _PeriodSheet({
    required this.current,
    required this.year,
    required this.maxDays,
  });

  final DateRange current;
  final FinancialYear year;
  final int maxDays;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Row(
                children: <Widget>[
                  Text('Select period', style: theme.textTheme.titleMedium),
                  const Spacer(),
                  // Which year these windows sit inside. Without it a sheet
                  // offering "Jul - Sep" says nothing about which July.
                  Text(
                    year.label,
                    style: theme.textTheme.labelLarge?.copyWith(
                      color: theme.colorScheme.primary,
                    ),
                  ),
                ],
              ),
            ),
            for (final PeriodPreset preset in periodPresetsFor(year))
              ListTile(
                title: Text(preset.label),
                trailing: _isActive(preset.range()) ? Icon(Icons.check, color: theme.colorScheme.primary) : null,
                onTap: () => Navigator.of(context).pop(PeriodSelection(preset.range(), preset.label)),
              ),
            const Divider(height: 1),
            ListTile(
              leading: Icon(Icons.tune, color: theme.colorScheme.primary),
              title: const Text('Custom range'),
              onTap: () async {
                final DateTimeRange? picked = await showDateRangePicker(
                  context: context,
                  // Bounded by the chosen year on both sides: the calendar is
                  // the one place a custom range could otherwise walk out of
                  // the year the rest of the screen is reporting on.
                  firstDate: year.start,
                  lastDate: year.lastDay,
                  initialDateRange: DateTimeRange(
                    start: year.confine(current).from,
                    end: year.confine(current).to,
                  ),
                );
                if (picked == null || !context.mounted) return;

                DateRange range = year.confine(DateRange(picked.start, picked.end));
                final bool wasClamped = range.dayCount > maxDays;
                range = range.clampToMaxDays(maxDays);

                if (wasClamped && context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(
                        'Custom ranges are limited to $maxDays days. Showing the most '
                        'recent $maxDays days of what you picked.',
                      ),
                    ),
                  );
                }
                if (context.mounted) {
                  Navigator.of(context).pop(PeriodSelection(range, formatRangeLabel(range)));
                }
              },
            ),
          ],
        ),
      ),
    );
  }

  bool _isActive(DateRange preset) => preset == current;
}

const List<String> _months = <String>[
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// "12 Jan" or, spanning a year boundary or single day, whatever makes the
/// range unambiguous without repeating the year twice for one day.
String formatRangeLabel(DateRange range) {
  final DateTime now = DateTime.now();
  if (range.isSingleDay) {
    return _formatDay(range.from, includeYear: range.from.year != now.year);
  }
  final bool sameYear = range.from.year == range.to.year;
  final String from = _formatDay(range.from, includeYear: !sameYear);
  final String to = _formatDay(range.to, includeYear: true);
  return '$from – $to';
}

String _formatDay(DateTime day, {required bool includeYear}) {
  final String month = _months[day.month - 1];
  return includeYear ? '${day.day} $month ${day.year}' : '${day.day} $month';
}
