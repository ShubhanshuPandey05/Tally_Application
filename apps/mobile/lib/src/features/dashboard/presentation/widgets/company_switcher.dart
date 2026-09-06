import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/router.dart';
import '../../../../app/theme.dart';
import '../../../../core/model/financial_year.dart';
import '../../../companies/application/company_providers.dart';
import '../../../companies/application/financial_year_providers.dart';
import '../../../companies/domain/company.dart';

/// The app-bar title: which books am I looking at, and for which year?
///
/// Two questions, one control, stacked in the order they change the meaning of
/// the screen. The company name is the louder of the two because switching
/// company replaces every figure; the year sits under it in muted text because
/// it is nearly always the current one and only occasionally worth thinking
/// about -- but it is *always* worth being able to read, which is why it is
/// here rather than behind a filter icon.
///
/// Made tappable rather than printed because an accountant with four companies
/// must be able to see, at a glance and without doubt, whose figures are on
/// screen -- and switch without hunting through settings.
class CompanySwitcher extends ConsumerWidget {
  const CompanySwitcher({super.key, this.subtitle});

  final String? subtitle;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final Company? company = ref.watch(activeCompanyProvider).valueOrNull;
    final int count = ref.watch(companiesProvider).valueOrNull?.length ?? 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        InkWell(
          onTap: count > 1 ? () => context.push(Routes.companies) : null,
          borderRadius: BorderRadius.circular(8),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 2, horizontal: 4),
            child: Row(
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
          ),
        ),
        if (company != null) const FinancialYearLine(),
        if (company == null && subtitle != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Text(
              subtitle!,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
            ),
          ),
      ],
    );
  }
}

/// The muted line under the company name: which financial year everything on
/// screen is scoped to, and the way to change it.
class FinancialYearLine extends ConsumerWidget {
  const FinancialYearLine({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final FinancialYear active = ref.watch(activeFinancialYearProvider);
    final List<FinancialYear> years = ref.watch(financialYearsProvider);

    return InkWell(
      onTap: years.length > 1 ? () => _pick(context, ref, years, active) : null,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 1, horizontal: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(
              active.label,
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
            ),
            if (!active.isCurrent) ...<Widget>[
              const SizedBox(width: 6),
              // A closed year has to announce itself. Every figure below is
              // history, and a screen that looks exactly like today's is how
              // somebody reads last year's cash position as their own.
              Text(
                'closed',
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.primary,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
            if (years.length > 1)
              Icon(Icons.expand_more, size: 16, color: context.mutedColor),
          ],
        ),
      ),
    );
  }

  Future<void> _pick(
    BuildContext context,
    WidgetRef ref,
    List<FinancialYear> years,
    FinancialYear active,
  ) async {
    final String? companyId = ref.read(activeCompanyIdResolvedProvider);
    if (companyId == null) return;

    final FinancialYear? picked = await showModalBottomSheet<FinancialYear>(
      context: context,
      showDragHandle: true,
      builder: (BuildContext sheetContext) {
        final ThemeData theme = Theme.of(sheetContext);
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 4),
                child: Row(
                  children: <Widget>[
                    Text('Financial year', style: theme.textTheme.titleMedium),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        'Every report and date filter is read inside the year '
                        'you pick here.',
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: sheetContext.mutedColor),
                      ),
                    ),
                  ],
                ),
              ),
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  // Same reason as the dashboard's metric grid: a null padding
                  // here would take on the system insets and pad this sheet by
                  // the height of the navigation bar.
                  padding: EdgeInsets.zero,
                  children: <Widget>[
                    for (final FinancialYear year in years)
                      ListTile(
                        title: Text(year.label),
                        subtitle: Text(
                          year.isCurrent
                              ? '1 Apr ${year.startYear} – today'
                              : '1 Apr ${year.startYear} – 31 Mar ${year.startYear + 1}',
                        ),
                        trailing: year == active
                            ? Icon(Icons.check, color: theme.colorScheme.primary)
                            : null,
                        onTap: () => Navigator.of(sheetContext).pop(year),
                      ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );

    if (picked == null) return;
    ref.read(selectedFinancialYearProvider(companyId).notifier).select(picked);
  }
}
