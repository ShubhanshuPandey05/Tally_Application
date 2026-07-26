import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/router.dart';
import '../../../../app/theme.dart';
import '../../../companies/application/company_providers.dart';
import '../../../companies/domain/company.dart';

/// The app-bar title: which books am I looking at?
///
/// Made a tappable control rather than plain text because an accountant with
/// four companies must be able to see, at a glance and without doubt, whose
/// figures are on screen -- and switch without hunting through settings.
class CompanySwitcher extends ConsumerWidget {
  const CompanySwitcher({super.key, this.subtitle});

  final String? subtitle;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final Company? company = ref.watch(activeCompanyProvider).valueOrNull;
    final int count = ref.watch(companiesProvider).valueOrNull?.length ?? 0;

    return InkWell(
      onTap: count > 1 ? () => context.push(Routes.companies) : null,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Flexible(
                  child: Text(
                    company?.name ?? 'TallyFlow',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                if (count > 1)
                  Icon(Icons.expand_more, size: 20, color: context.mutedColor),
              ],
            ),
            if (subtitle != null)
              Text(
                subtitle!,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              ),
          ],
        ),
      ),
    );
  }
}
