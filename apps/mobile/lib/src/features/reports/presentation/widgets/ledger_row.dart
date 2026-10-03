import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/router.dart';
import '../../../../app/theme.dart';
import '../../../../core/model/figures.dart';
import '../../../../core/money/money_format.dart';

/// One account, and the way into everything that went through it.
///
/// A balance on its own answers "how much"; the statement behind it answers
/// "why", which is the next question every time the first one surprises
/// somebody. Shared by every list of ledgers so the tap exists in all of them
/// -- a drill-down that only works in one of two views is a bug report.
class LedgerRow extends StatelessWidget {
  const LedgerRow({super.key, required this.line, this.subtitle});

  final LedgerLine line;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return InkWell(
      onTap: () => context.push(
        '${Routes.ledgerStatement}?ledger=${Uri.encodeQueryComponent(line.name)}',
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    line.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium,
                  ),
                  if (subtitle != null)
                    Text(
                      subtitle!,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelSmall
                          ?.copyWith(color: context.mutedColor),
                    ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            Text(
              MoneyFormat.withSide(line.closing),
              style: theme.textTheme.bodyMedium?.merge(AppTheme.amount),
            ),
            Icon(Icons.chevron_right, size: 18, color: context.mutedColor),
          ],
        ),
      ),
    );
  }
}
