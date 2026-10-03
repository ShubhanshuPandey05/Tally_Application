import 'package:flutter/material.dart';

import '../../../../app/theme.dart';
import '../../../../core/model/figures.dart';
import '../../../../core/money/money_format.dart';

/// One outstanding bill: its name, its dates, how late it is, and what is left
/// on it. Shared by the party cards and a party's own page, so a bill reads the
/// same wherever somebody meets it.
class BillRow extends StatelessWidget {
  const BillRow({super.key, required this.bill});

  final OutstandingBill bill;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  bill.billName ?? 'Bill',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium,
                ),
                if (_dates(bill) case final String dates)
                  Text(
                    dates,
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                  ),
                if (bill.isOverdue)
                  Text(
                    '${bill.daysOverdue} days overdue',
                    style: theme.textTheme.bodySmall?.copyWith(color: context.negativeColor),
                  )
                else if (_dates(bill) == null)
                  Text(
                    ageingLabel(bill.ageingBucket),
                    style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Text(
                MoneyFormat.full(bill.amount),
                style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
              ),
              if (bill.isAdvance)
                Text(
                  'Advance',
                  style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                ),
            ],
          ),
        ],
      ),
    );
  }

  static String? _dates(OutstandingBill bill) {
    String dmy(DateTime date) => '${date.day}/${date.month}/${date.year}';
    final List<String> parts = <String>[
      if (bill.billDate != null) 'bill ${dmy(bill.billDate!)}',
      if (bill.dueDate != null) 'due ${dmy(bill.dueDate!)}',
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }
}
