/// The Share button on reports, and the document it sends.
///
/// The button is the whole of the Sharing switch -- the server has nothing to
/// refuse, because the document is drawn here from rows already on screen --
/// so what is pinned is that it shows only for someone allowed to share, and
/// only once there is something to share.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/core/documents/report_table_document.dart';
import 'package:tallyflow/src/core/model/freshness.dart';
import 'package:tallyflow/src/features/companies/application/company_providers.dart';
import 'package:tallyflow/src/features/companies/domain/company.dart';
import 'package:tallyflow/src/features/reports/presentation/widgets/report_scaffold.dart';
import 'package:tallyflow/src/features/team/application/team_providers.dart';
import 'package:tallyflow/src/features/team/domain/access_policy.dart';

const ValueKey<String> _share = ValueKey<String>('report-share');

ReportTable _table(List<String> names) => ReportTable(
      title: 'Ledgers',
      columns: const <ReportColumn>[ReportColumn('Ledger'), ReportColumn('Balance', numeric: true)],
      rows: <List<String>>[
        for (final String name in names) <String>[name, '₹1,000.00'],
      ],
    );

Future<void> _pump(
  WidgetTester tester, {
  required AsyncValue<Fresh<List<String>>> state,
  required AccessPolicy policy,
}) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: <Override>[
        activeCompanyIdResolvedProvider.overrideWithValue('company-1'),
        myAccessProvider('company-1').overrideWith((Ref ref) async => policy),
        activeCompanyProvider.overrideWithValue(const AsyncData<Company?>(null)),
      ],
      child: MaterialApp(
        home: ReportScaffold<List<String>>(
          title: 'Ledgers',
          state: state,
          onRefresh: () async {},
          share: _table,
          builder: (BuildContext context, List<String> names) =>
              <Widget>[for (final String name in names) Text(name)],
        ),
      ),
    ),
  );
  await tester.pump();
  await tester.pump();
}

void main() {
  const Fresh<List<String>> loaded = Fresh<List<String>>(
    <String>['Reliance Retail'],
    Freshness.unavailable,
  );

  testWidgets('shows at the top right for someone allowed to share', (WidgetTester tester) async {
    await _pump(tester, state: const AsyncData<Fresh<List<String>>>(loaded), policy: AccessPolicy.all);
    expect(
      find.descendant(of: find.byType(AppBar), matching: find.byKey(_share)),
      findsOneWidget,
    );
  });

  testWidgets('is not there when their admin switched sharing off', (WidgetTester tester) async {
    await _pump(
      tester,
      state: const AsyncData<Fresh<List<String>>>(loaded),
      policy: const AccessPolicy(share: false),
    );
    expect(find.byKey(_share), findsNothing);
    expect(find.text('Reliance Retail'), findsOneWidget);
  });

  testWidgets('waits for the report: a share over a skeleton sends nothing',
      (WidgetTester tester) async {
    await _pump(
      tester,
      state: const AsyncLoading<Fresh<List<String>>>(),
      policy: AccessPolicy.all,
    );
    expect(find.byKey(_share), findsNothing);
  });

  test('the document is a PDF, and drops the rupee sign its fonts cannot draw', () async {
    final ReportTableDocument document = ReportTableDocument(
      table: _table(<String>['Reliance Retail', 'Sharma & Co.']),
      companyName: 'Bhatia Supermarket',
      generatedAt: DateTime(2026, 11, 7),
    );
    final List<int> bytes = await document.build();
    expect(String.fromCharCodes(bytes.take(4)), '%PDF');
    expect(ReportTableDocument.clean('₹1,000.00'), '1,000.00');
    expect(document.fileName, 'Ledgers-07-Nov-2026');
  });

  test('an empty report still prints, saying so', () async {
    final List<int> bytes = await ReportTableDocument(
      table: _table(const <String>[]),
      companyName: 'Bhatia Supermarket',
      generatedAt: DateTime(2026, 11, 7),
    ).build();
    expect(bytes, isNotEmpty);
  });
}
