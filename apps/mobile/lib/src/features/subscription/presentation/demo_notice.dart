import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../auth/application/auth_controller.dart';

/// Says, on the screen carrying the figures, that the figures are invented.
///
/// The demo is deliberately indistinguishable from the product -- same
/// endpoints, same reports, same drill-downs -- which is exactly why it has to
/// label itself. An accounting app is sold on trust, and a visitor who works
/// out for themselves that the receivables are made up has spent the first
/// minute of the demo doubting the software rather than reading it.
///
/// Renders nothing at all on a real account, so it costs a signed-in customer
/// a single boolean.
class DemoNotice extends ConsumerWidget {
  const DemoNotice({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bool isDemo = ref.watch(authControllerProvider).user?.isDemo ?? false;
    if (!isDemo) return const SizedBox.shrink();

    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: theme.colorScheme.primary.withOpacity(0.08),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: theme.colorScheme.primary.withOpacity(0.22)),
        ),
        child: Row(
          children: <Widget>[
            Icon(Icons.science_outlined, size: 18, color: theme.colorScheme.primary),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                'Demo account — a sample business, invented figures. '
                'Nothing here is anyone\'s real books.',
                style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
