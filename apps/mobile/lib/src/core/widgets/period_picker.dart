import 'package:flutter/material.dart';

import '../model/date_range.dart';

/// A named way of computing a [DateRange], relative to "now" at the moment it
/// is chosen -- "This month" picked on the 3rd and picked again on the 28th
/// point at different windows, which is the whole reason presets are
/// functions and not precomputed ranges.
class PeriodPreset {
  const PeriodPreset(this.label, this.range);

  final String label;
  final DateRange Function() range;
}

/// The standard set offered everywhere a report accepts a date range. Ordered
/// shortest-to-longest, the way an owner thinks about "how far back."
const List<PeriodPreset> periodPresets = <PeriodPreset>[
  PeriodPreset('Today', DateRange.today),
  PeriodPreset('Yesterday', DateRange.yesterday),
  PeriodPreset('This week', DateRange.thisWeek),
  PeriodPreset('Last 7 days', _last7),
  PeriodPreset('This month', DateRange.thisMonth),
  PeriodPreset('Last month', DateRange.lastMonth),
  PeriodPreset('This quarter', DateRange.thisQuarter),
  PeriodPreset('This year', DateRange.thisYear),
];

DateRange _last7() => DateRange.lastDays(7);

/// A chosen period plus the label it should be shown under. Kept together so
/// a custom range picked once does not need its label re-derived from the
/// dates every time it is displayed.
class PeriodSelection {
  const PeriodSelection(this.range, this.label);

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
  DateTime? firstDate,
  int maxDays = 400,
}) {
  return showModalBottomSheet<PeriodSelection>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (BuildContext sheetContext) => _PeriodSheet(
      current: current,
      firstDate: firstDate ?? DateTime.now().subtract(Duration(days: maxDays * 6)),
      maxDays: maxDays,
    ),
  );
}

class _PeriodSheet extends StatelessWidget {
  const _PeriodSheet({
    required this.current,
    required this.firstDate,
    required this.maxDays,
  });

  final DateRange current;
  final DateTime firstDate;
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
                ],
              ),
            ),
            for (final PeriodPreset preset in periodPresets)
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
                  firstDate: firstDate,
                  lastDate: DateTime.now(),
                  initialDateRange: DateTimeRange(start: current.from, end: current.to),
                );
                if (picked == null || !context.mounted) return;

                DateRange range = DateRange(picked.start, picked.end);
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
