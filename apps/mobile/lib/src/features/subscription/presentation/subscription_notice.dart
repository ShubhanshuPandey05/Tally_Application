import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../domain/subscription.dart';

/// Why the "add" buttons are missing.
///
/// A control that exists and refuses reads as broken software; a control that
/// is simply absent reads as "not yet". This is the piece that turns the second
/// one into an explanation instead of a mystery, and it is the reason every
/// admin-only button in the app can be hidden rather than disabled.
///
/// Shown to admins only. A cashier cannot approve anything, cannot pay for
/// anything, and being told the business is behind on its subscription is
/// neither their problem nor their information.
///
/// The wording comes from the server. The same sentence then appears here, in
/// a 402 body, and in the portal's own copy — one source, so the app cannot
/// tell the owner a different story from the one their partner is looking at.
class SubscriptionNotice extends ConsumerWidget {
  const SubscriptionNotice({super.key, this.compact = false});

  /// A single line for the top of a busy screen, rather than the full card.
  final bool compact;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppUser? user = ref.watch(authControllerProvider).user;
    if (user == null || !user.shouldExplainSubscription) {
      return const SizedBox.shrink();
    }

    final OrgSubscription subscription = user.subscription;
    final ThemeData theme = Theme.of(context);

    // Waiting is not a failure, so it is not painted like one. Stopped is --
    // an owner who skims past a suspension notice finds out when a report
    // stops loading.
    final bool waiting = subscription.isAwaitingApproval;
    final Color accent = waiting ? context.cautionColor : context.negativeColor;
    final IconData icon = waiting ? Icons.hourglass_top_rounded : Icons.lock_outline;

    if (compact) {
      return Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: accent.withOpacity(0.10),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          children: <Widget>[
            Icon(icon, size: 17, color: accent),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                subscription.message,
                style: theme.textTheme.bodySmall?.copyWith(color: accent),
              ),
            ),
          ],
        ),
      );
    }

    return Card(
      color: accent.withOpacity(0.08),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: accent.withOpacity(0.35)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(icon, size: 19, color: accent),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    subscription.status.label,
                    style: theme.textTheme.titleSmall?.copyWith(color: accent),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              subscription.message,
              style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
            ),
          ],
        ),
      ),
    );
  }
}

/// What the plan covers, on the account screen.
///
/// Deliberately visible before anything is refused. Being told a limit exists
/// at the moment you hit it is the worst possible time to learn about it, and
/// "3 of 3 companies" a week earlier is what turns a blocked action into a
/// planned phone call.
class SubscriptionCard extends ConsumerWidget {
  const SubscriptionCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppUser? user = ref.watch(authControllerProvider).user;
    // Staff have no view of the commercial side of the account, and no way to
    // act on it either.
    if (user == null || !user.role.isAdmin) return const SizedBox.shrink();

    final OrgSubscription subscription = user.subscription;
    final ThemeData theme = Theme.of(context);
    final bool live = subscription.allowsChanges;
    final Color accent = live
        ? context.positiveColor
        : subscription.isAwaitingApproval
            ? context.cautionColor
            : context.negativeColor;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(Icons.workspace_premium_outlined, size: 18, color: accent),
                const SizedBox(width: 8),
                Text('Subscription', style: theme.textTheme.titleSmall),
                const Spacer(),
                Text(
                  subscription.isExpired ? 'Expired' : subscription.status.label,
                  style: theme.textTheme.labelMedium?.copyWith(color: accent),
                ),
              ],
            ),
            const SizedBox(height: 14),
            _Allowance(
              label: 'Companies',
              used: subscription.companiesUsed,
              cap: subscription.maxCompanies,
            ),
            const SizedBox(height: 10),
            _Allowance(
              label: 'People',
              used: subscription.usersUsed,
              cap: subscription.maxUsers,
            ),
            if (subscription.expiresAt != null) ...<Widget>[
              const SizedBox(height: 12),
              Text(
                subscription.isExpired
                    ? 'Ended ${_date(subscription.expiresAt!)}'
                    : 'Renews on ${_date(subscription.expiresAt!)}',
                style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              ),
            ],
            // The explanation deliberately lives only in [SubscriptionNotice],
            // which sits directly above this on the account screen. Repeating
            // it here printed the same sentence twice on one page.
          ],
        ),
      ),
    );
  }

  static String _date(DateTime value) {
    const List<String> months = <String>[
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    return '${value.day} ${months[value.month - 1]} ${value.year}';
  }
}

class _Allowance extends StatelessWidget {
  const _Allowance({required this.label, required this.used, required this.cap});

  final String label;
  final int used;
  final int cap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // An unapproved account has a cap of zero, and "0 of 0" with a full red bar
    // would read as "you have used everything up" rather than "nothing agreed
    // yet". The two are not the same message.
    final bool agreed = cap > 0;
    final double fraction = agreed ? (used / cap).clamp(0.0, 1.0) : 0.0;
    final bool atLimit = agreed && used >= cap;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          children: <Widget>[
            Text(label, style: theme.textTheme.bodyMedium),
            const Spacer(),
            Text(
              agreed ? '$used of $cap' : 'not set yet',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: atLimit ? context.cautionColor : context.mutedColor,
                fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
              ),
            ),
          ],
        ),
        const SizedBox(height: 5),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: LinearProgressIndicator(
            value: fraction,
            minHeight: 5,
            backgroundColor: context.mutedColor.withOpacity(0.18),
            valueColor: AlwaysStoppedAnimation<Color>(
              atLimit ? context.cautionColor : Theme.of(context).colorScheme.primary,
            ),
          ),
        ),
      ],
    );
  }
}
