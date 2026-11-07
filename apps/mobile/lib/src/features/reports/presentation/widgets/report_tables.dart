import '../../../../core/documents/document_style.dart';
import '../../../../core/documents/report_table_document.dart';
import '../../../../core/model/figures.dart';
import '../../../../core/money/money.dart';
import '../../../../core/money/money_format.dart';
import '../../../top_reports/domain/top_report.dart';
import '../../domain/drilldown.dart';
import '../../domain/reports.dart';

/// The printable form of each report, built from the same data the screen is
/// showing. One place, so that every shared report reads the same way: dates
/// as Tally prints them, amounts to the paisa, balances with Dr or Cr.

String _date(DateTime? value) => value == null ? '' : DocumentStyle.shortDate(value);

String _amount(Money value) => ReportTableDocument.amount(value);

String _days(int days) => days <= 0 ? '' : '$days';

List<String> _voucherRow(TransactionLine line) => <String>[
      _date(line.date),
      line.voucherType ?? line.kind,
      line.voucherNumber ?? '',
      line.party ?? line.narration ?? '',
      _amount(line.amount),
    ];

const List<ReportColumn> _voucherColumns = <ReportColumn>[
  ReportColumn('Date', flex: 1.6),
  ReportColumn('Type', flex: 2),
  ReportColumn('No.', flex: 1.4),
  ReportColumn('Party', flex: 4),
  ReportColumn('Amount', numeric: true, flex: 2.2),
];

ReportTable daybookTable(DaybookReport report, {required String period}) => ReportTable(
      title: 'Day book',
      subtitle: period,
      columns: _voucherColumns,
      rows: <List<String>>[for (final TransactionLine line in report.vouchers) _voucherRow(line)],
      total: <String>['Total', '', '', '${report.voucherCount} vouchers', _amount(report.total)],
    );

ReportTable registerTable(RegisterReport report, {required String title, required String period}) =>
    ReportTable(
      title: title,
      subtitle: period,
      columns: _voucherColumns,
      rows: <List<String>>[for (final TransactionLine line in report.vouchers) _voucherRow(line)],
      total: <String>['Total', '', '', '${report.voucherCount} vouchers', _amount(report.total)],
      notes: <String>[
        if (report.truncated)
          'Showing ${report.vouchers.length} of ${report.voucherCount} vouchers. '
              'The total covers all ${report.voucherCount}.',
      ],
    );

ReportTable outstandingTable(OutstandingReport report, {required String subtitle}) => ReportTable(
      title: report.kind.title,
      subtitle: subtitle,
      columns: const <ReportColumn>[
        ReportColumn('Party', flex: 3.6),
        ReportColumn('Bill', flex: 2),
        ReportColumn('Bill date', flex: 1.6),
        ReportColumn('Due date', flex: 1.6),
        ReportColumn('Overdue days', numeric: true, flex: 1.4),
        ReportColumn('Amount', numeric: true, flex: 2.2),
      ],
      rows: <List<String>>[
        for (final PartyBills party in report.byParty)
          for (final OutstandingBill bill in party.bills)
            <String>[
              bill.party,
              bill.isAdvance ? 'Advance' : (bill.billName ?? ''),
              _date(bill.billDate),
              _date(bill.dueDate),
              _days(bill.daysOverdue),
              _amount(bill.amount),
            ],
      ],
      total: <String>['Total', '${report.billCount} bills', '', '', '', _amount(report.total)],
    );

ReportTable groupOutstandingTable(GroupOutstandingReport report, {required String subtitle}) =>
    ReportTable(
      title: report.group,
      subtitle: subtitle,
      columns: const <ReportColumn>[
        ReportColumn('Party', flex: 4),
        ReportColumn('Bills', numeric: true, flex: 1),
        ReportColumn('Overdue days', numeric: true, flex: 1.4),
        ReportColumn('Outstanding', numeric: true, flex: 2.2),
        ReportColumn('Advances', numeric: true, flex: 2),
        ReportColumn('Net', numeric: true, flex: 2.2),
      ],
      rows: <List<String>>[
        for (final GroupParty party in report.parties)
          <String>[
            party.party,
            '${party.billCount}',
            _days(party.daysOverdue),
            _amount(party.total),
            _amount(party.advances),
            _amount(party.net),
          ],
      ],
      total: <String>[
        'Total',
        '${report.billCount}',
        '',
        _amount(report.total),
        _amount(report.advances),
        _amount(report.net),
      ],
    );

ReportTable slowMovingTable(SlowMovingReport report) => ReportTable(
      title: 'Slow movers',
      subtitle: 'No sales in ${report.windowDays} days',
      columns: const <ReportColumn>[
        ReportColumn('Item', flex: 4),
        ReportColumn('Group', flex: 2.4),
        ReportColumn('Quantity', numeric: true, flex: 2),
        ReportColumn('Value', numeric: true, flex: 2.2),
      ],
      rows: <List<String>>[
        for (final StockLine item in report.items)
          <String>[
            item.name,
            item.group ?? '',
            MoneyFormat.quantity(item.quantity, item.unit),
            _amount(item.value),
          ],
      ],
      total: <String>[
        'Total',
        '${report.items.length} items',
        '',
        _amount(report.items.fold(Money.zero, (Money sum, StockLine item) => sum + item.value)),
      ],
    );

ReportTable itemMovementTable(ItemMovementReport report, {required String period}) {
  String qty(double value) => MoneyFormat.quantity(value, report.unit);
  return ReportTable(
    title: report.item,
    subtitle: period,
    columns: const <ReportColumn>[
      ReportColumn('Date', flex: 1.6),
      ReportColumn('Type', flex: 2),
      ReportColumn('No.', flex: 1.4),
      ReportColumn('Party', flex: 3.4),
      ReportColumn('In', numeric: true, flex: 1.8),
      ReportColumn('Out', numeric: true, flex: 1.8),
      ReportColumn('Value', numeric: true, flex: 2.2),
    ],
    rows: <List<String>>[
      for (final ItemMovementLine move in report.movements)
        <String>[
          _date(move.line.date),
          move.line.voucherType ?? move.line.kind,
          move.line.voucherNumber ?? '',
          move.line.party ?? '',
          move.direction == MovementDirection.inward ? qty(move.quantity) : '',
          move.direction == MovementDirection.outward ? qty(move.quantity) : '',
          _amount(move.value),
        ],
    ],
    total: <String>['Total', '', '', '', qty(report.quantityIn), qty(report.quantityOut), ''],
  );
}

ReportTable godownTable(GodownReport report, {String? only}) {
  final Iterable<GodownHolding> godowns =
      only == null ? report.godowns : report.godowns.where((GodownHolding g) => g.name == only);
  return ReportTable(
    title: only ?? 'Godown summary',
    subtitle: 'Closing stock, as it stands now',
    columns: const <ReportColumn>[
      ReportColumn('Godown', flex: 2.6),
      ReportColumn('Item', flex: 4),
      ReportColumn('Quantity', numeric: true, flex: 2),
      ReportColumn('Value', numeric: true, flex: 2.2),
    ],
    rows: <List<String>>[
      for (final GodownHolding godown in godowns)
        for (final StockLine item in godown.items)
          <String>[
            godown.name,
            item.name,
            MoneyFormat.quantity(item.quantity, item.unit),
            _amount(item.value),
          ],
    ],
    total: <String>[
      'Total',
      '',
      '',
      _amount(godowns.fold(Money.zero, (Money sum, GodownHolding g) => sum + g.value)),
    ],
  );
}

ReportTable topReportTable(TopReport report, {String? subtitle}) {
  final bool anyTotal = report.columns.any((TopColumn c) => c.totalled);
  return ReportTable(
    title: report.title,
    subtitle: subtitle,
    columns: <ReportColumn>[
      const ReportColumn('#', numeric: true, flex: 0.7),
      const ReportColumn('Name', flex: 4),
      for (final TopColumn column in report.columns)
        ReportColumn(column.label, numeric: column.type.isNumeric, flex: 2),
    ],
    rows: <List<String>>[
      for (final TopRow row in report.rows)
        <String>[
          '${row.rank}',
          row.name,
          for (final TopColumn column in report.columns) column.format(row.cells[column.key]),
        ],
    ],
    total: anyTotal
        ? <String>[
            '',
            'Total',
            for (final TopColumn column in report.columns)
              column.totalled ? column.format(report.total[column.key]) : '',
          ]
        : null,
    notes: report.notes,
  );
}
