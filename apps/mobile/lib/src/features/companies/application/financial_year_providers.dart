import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/model/financial_year.dart';
import '../domain/company.dart';
import 'company_providers.dart';

/// Which financial years this company's books cover, newest first.
///
/// Derived from Tally's own `BOOKSFROM` rather than from a fixed number of
/// years back: offering an owner four years of reports on a company that
/// opened last April is offering three years of empty screens.
final Provider<List<FinancialYear>> financialYearsProvider =
    Provider<List<FinancialYear>>((Ref ref) {
  final Company? company = ref.watch(activeCompanyProvider).valueOrNull;
  return FinancialYear.since(company?.booksFrom ?? company?.financialYearFrom);
});

/// The year the user picked, as the year it starts in. Null means "the current
/// one", which is what everybody wants nearly all of the time.
///
/// Stored as an int rather than a [FinancialYear] so that the default keeps
/// meaning *current* rather than freezing to whichever year was current when
/// the app was opened -- an app left running overnight on 31 March would
/// otherwise still be showing last year in the morning.
///
/// Kept per company: an accountant with four sets of books may reasonably be
/// looking at 2024-25 in one of them and today in another, and resetting the
/// year on every switch would make that unusable.
///
/// Not persisted, deliberately. Opening the app should show the year you are
/// in; a stored choice from a fortnight ago is a figure that looks wrong for
/// no reason the owner can see.
final NotifierProviderFamily<FinancialYearController, int?, String>
    selectedFinancialYearProvider =
    NotifierProvider.family<FinancialYearController, int?, String>(
  FinancialYearController.new,
);

class FinancialYearController extends FamilyNotifier<int?, String> {
  @override
  // ignore: avoid_renaming_method_parameters
  int? build(String companyId) => null;

  void select(FinancialYear year) => state = year.isCurrent ? null : year.startYear;
}

/// The financial year every date filter in the app is confined to.
///
/// Resolved against the years the company actually has, so a stored choice can
/// never outlive the books it pointed at.
final Provider<FinancialYear> activeFinancialYearProvider =
    Provider<FinancialYear>((Ref ref) {
  final List<FinancialYear> years = ref.watch(financialYearsProvider);
  final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
  final int? chosen =
      companyId == null ? null : ref.watch(selectedFinancialYearProvider(companyId));

  if (chosen == null) return years.isEmpty ? FinancialYear.current() : years.first;
  for (final FinancialYear year in years) {
    if (year.startYear == chosen) return year;
  }
  return years.isEmpty ? FinancialYear.current() : years.first;
});
