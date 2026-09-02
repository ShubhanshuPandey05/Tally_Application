import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tallyflow/src/app/theme.dart';
import 'package:tallyflow/src/core/model/date_range.dart';
import 'package:tallyflow/src/core/model/financial_year.dart';
import 'package:tallyflow/src/core/providers.dart';
import 'package:tallyflow/src/core/widgets/period_picker.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/application/financial_year_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/dashboard/presentation/widgets/company_switcher.dart';

/// The financial year is the frame every figure in this app hangs off, and it
/// is not the calendar year. These pin the two things that go wrong: a window
/// that reaches across 31 March into another set of books, and a year list
/// offering an owner reports from before their business existed.
void main() {
  group('the year itself', () {
    test('runs April to March, and January belongs to the year before', () {
      expect(FinancialYear.containing(DateTime(2026, 9, 2)).startYear, 2026);
      expect(FinancialYear.containing(DateTime(2027, 1, 15)).startYear, 2026);
      expect(FinancialYear.containing(DateTime(2026, 3, 31)).startYear, 2025);
      expect(FinancialYear.containing(DateTime(2026, 4, 1)).startYear, 2026);
    });

    test('is labelled the way an accountant writes it', () {
      expect(const FinancialYear(2026).label, 'FY 2026-27');
      expect(const FinancialYear(2029).label, 'FY 2029-30');
      // The one that catches a naive `% 100`: 2009-10, not 2009-1.
      expect(const FinancialYear(2009).label, 'FY 2009-10');
    });

    test('a closed year ends on 31 March, the open one ends today', () {
      const FinancialYear closed = FinancialYear(2024);
      expect(closed.lastDay, DateTime(2025, 3, 31));

      final FinancialYear open = FinancialYear.current();
      final DateTime now = DateTime.now();
      expect(open.lastDay, DateTime(now.year, now.month, now.day));
    });

    test('confines a window that reaches outside it', () {
      const FinancialYear year = FinancialYear(2025);
      final DateRange straddling =
          DateRange(DateTime(2025, 3, 20), DateTime(2025, 4, 10));

      expect(year.confine(straddling).from, DateTime(2025, 4, 1));
      expect(year.confine(straddling).to, DateTime(2025, 4, 10));
    });

    test('a window with nothing in this year falls back to the whole year', () {
      const FinancialYear year = FinancialYear(2025);
      final DateRange elsewhere =
          DateRange(DateTime(2020, 5, 1), DateTime(2020, 6, 1));

      expect(year.confine(elsewhere), year.toDate);
    });

    test('offers only the years the books actually cover', () {
      final List<FinancialYear> years = FinancialYear.since(
        DateTime(2023, 7, 14),
        now: DateTime(2026, 9, 2),
      );

      expect(
        years.map((FinancialYear y) => y.startYear).toList(),
        <int>[2026, 2025, 2024, 2023],
      );
    });

    test('a company that has not synced is offered this year alone', () {
      // Inventing four years of history for a company we know nothing about
      // would be offering an owner four screens that can only come back empty.
      expect(FinancialYear.since(null).length, 1);
      expect(FinancialYear.since(null).single.isCurrent, isTrue);
    });
  });

  group('the windows offered inside a year', () {
    test('the open year offers today, and never anything before 1 April', () {
      final FinancialYear year = FinancialYear.current();
      final List<PeriodPreset> presets = periodPresetsFor(year);

      expect(presets.map((PeriodPreset p) => p.label), contains('Today'));
      for (final PeriodPreset preset in presets) {
        expect(preset.range().from.isBefore(year.start), isFalse,
            reason: '${preset.label} starts before the year does');
        expect(preset.range().to.isAfter(year.lastDay), isFalse,
            reason: '${preset.label} runs past today');
      }
    });

    test('a closed year offers its quarters instead of "today"', () {
      const FinancialYear year = FinancialYear(2024);
      final List<String> labels =
          periodPresetsFor(year).map((PeriodPreset p) => p.label).toList();

      expect(labels, <String>[
        'Full year',
        'Apr – Jun',
        'Jul – Sep',
        'Oct – Dec',
        'Jan – Mar',
      ]);
      // The fourth quarter of a financial year is in the *next* calendar year,
      // which is the arithmetic everybody gets wrong once.
      final DateRange q4 =
          periodPresetsFor(year).last.range();
      expect(q4.from, DateTime(2025, 1, 1));
      expect(q4.to, DateTime(2025, 3, 31));
    });
  });

  group('the picker under the company name', () {
    testWidgets('shows the year, and switching one is remembered per company',
        (WidgetTester tester) async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final SharedPreferences preferences = await SharedPreferences.getInstance();
      final FinancialYear thisYear = FinancialYear.current();
      final Company company = Company(
        id: 'company-1',
        name: 'Bhatia Supermarket',
        tallyName: 'Bhatia Supermarket',
        connectorId: 'connector-1',
        baseCurrency: 'INR',
        isActive: true,
        booksFrom: DateTime(thisYear.startYear - 2, 4, 1),
      );

      final ProviderContainer container = ProviderContainer(
        overrides: <Override>[
          sharedPreferencesProvider.overrideWithValue(preferences),
          companiesProvider.overrideWith((Ref ref) async => <Company>[company]),
        ],
      );
      addTearDown(container.dispose);

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            theme: AppTheme.light(),
            home: const Scaffold(body: CompanySwitcher()),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // The year is on screen without anyone opening anything -- that is the
      // whole point of putting it under the name rather than behind an icon.
      expect(find.text(thisYear.label), findsOneWidget);
      expect(container.read(activeFinancialYearProvider), thisYear);

      // Three years of books, so three to choose from.
      expect(container.read(financialYearsProvider).length, 3);

      final FinancialYear lastYear = FinancialYear(thisYear.startYear - 1);
      container
          .read(selectedFinancialYearProvider(company.id).notifier)
          .select(lastYear);
      await tester.pumpAndSettle();

      expect(container.read(activeFinancialYearProvider), lastYear);
      expect(find.text(lastYear.label), findsOneWidget);
      // A year that has ended must say so: every figure under it is history,
      // and a screen that looks like today's is how somebody reads last year's
      // cash position as their own.
      expect(find.text('closed'), findsOneWidget);
    });
  });
}
