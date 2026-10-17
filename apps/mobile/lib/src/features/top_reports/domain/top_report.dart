import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../../core/money/money.dart';
import '../../../core/money/money_format.dart';

/// How a column's values are read and written.
///
/// Every Top Prime report arrives in one table shape, so the app needs one
/// renderer and this one list of cell kinds -- not a screen per report that
/// drifts from the others a fix at a time.
enum TopCellType {
  text,
  date,
  count,
  days,
  quantity,
  money,
  balance,
  rate,
  percent;

  static TopCellType parse(String? raw) => TopCellType.values.firstWhere(
        (TopCellType type) => type.name == raw,
        orElse: () => TopCellType.text,
      );

  bool get isNumeric => this != text && this != date;
}

class TopColumn {
  const TopColumn({
    required this.key,
    required this.label,
    required this.type,
    required this.totalled,
  });

  final String key;
  final String label;
  final TopCellType type;
  final bool totalled;

  factory TopColumn.fromJson(Map<String, Object?> json) => TopColumn(
        key: json['key'] as String? ?? '',
        label: json['label'] as String? ?? '',
        type: TopCellType.parse(json['type'] as String?),
        totalled: json['total'] as bool? ?? false,
      );

  static final DateFormat _date = DateFormat('d MMM yy');

  /// The cell as text. `--` wherever there is no value: a blank date is "never"
  /// and a blank amount is "not counted", and neither is a zero.
  String format(Object? value) {
    if (value == null) return '--';
    switch (type) {
      case TopCellType.text:
        return value.toString();
      case TopCellType.date:
        final DateTime? when = DateTime.tryParse(value.toString());
        return when == null ? '--' : _date.format(when);
      case TopCellType.count:
        return value.toString();
      case TopCellType.days:
        final int days = (value as num).round();
        // Overdue days come out negative for a bill not yet due. "-4 days
        // overdue" is a sum, not a sentence.
        return days < 0 ? 'due in ${-days} d' : '$days d';
      case TopCellType.quantity:
        return MoneyFormat.quantity((value as num).toDouble(), null);
      case TopCellType.money:
      case TopCellType.rate:
        return MoneyFormat.full(Money.fromJson(value));
      case TopCellType.balance:
        return MoneyFormat.withSide(Money.fromJson(value));
      case TopCellType.percent:
        return '$value%';
    }
  }
}

class TopSort {
  const TopSort({required this.key, required this.label, required this.column});

  final String key;
  final String label;

  /// The column this order ranks on, which each row leads with.
  final String column;

  factory TopSort.fromJson(Map<String, Object?> json) => TopSort(
        key: json['key'] as String? ?? '',
        label: json['label'] as String? ?? '',
        column: json['column'] as String? ?? json['key'] as String? ?? '',
      );
}

/// Where tapping a row goes.
class TopDrill {
  const TopDrill({required this.type, required this.name, this.kind});

  /// `ledger`, `item` or `outstanding`.
  final String type;
  final String name;

  /// For `outstanding`: `receivable` or `payable`.
  final String? kind;

  static TopDrill? fromJson(Object? json) {
    if (json is! Map) return null;
    final String? type = json['type'] as String?;
    final String? name = json['name'] as String?;
    if (type == null || name == null || name.isEmpty) return null;
    return TopDrill(type: type, name: name, kind: json['kind'] as String?);
  }
}

class TopRow {
  const TopRow({required this.rank, required this.name, required this.cells, this.drill});

  final int rank;
  final String name;
  final Map<String, Object?> cells;
  final TopDrill? drill;

  factory TopRow.fromJson(Map<String, Object?> json) => TopRow(
        rank: (json['rank'] as num?)?.toInt() ?? 0,
        name: json['name'] as String? ?? '',
        cells: (json['cells'] as Map?)?.cast<String, Object?>() ?? const <String, Object?>{},
        drill: TopDrill.fromJson(json['drill']),
      );
}

class TopReport {
  const TopReport({
    required this.key,
    required this.title,
    required this.sort,
    required this.sorts,
    required this.limit,
    required this.rowCount,
    required this.columns,
    required this.rows,
    required this.total,
    required this.notes,
  });

  final String key;
  final String title;
  final String sort;
  final List<TopSort> sorts;
  final int limit;

  /// Every row that qualified, before the top-N cut. The list below shows
  /// [rows]; this says how many more there were.
  final int rowCount;
  final List<TopColumn> columns;
  final List<TopRow> rows;

  /// The TOTAL row: over the rows shown only, as Tally's own report totals.
  final Map<String, Object?> total;

  /// What this report cannot see, said under it rather than implied.
  final List<String> notes;

  TopSort? get activeSort {
    for (final TopSort option in sorts) {
      if (option.key == sort) return option;
    }
    return sorts.isEmpty ? null : sorts.first;
  }

  factory TopReport.fromJson(Map<String, Object?> json) {
    List<Map<String, Object?>> maps(Object? raw) => raw is List
        ? raw
            .whereType<Map<Object?, Object?>>()
            .map((Map<Object?, Object?> m) => m.cast<String, Object?>())
            .toList()
        : const <Map<String, Object?>>[];
    return TopReport(
      key: json['key'] as String? ?? '',
      title: json['title'] as String? ?? '',
      sort: json['sort'] as String? ?? '',
      sorts: maps(json['sorts']).map(TopSort.fromJson).toList(),
      limit: (json['limit'] as num?)?.toInt() ?? 10,
      rowCount: (json['row_count'] as num?)?.toInt() ?? 0,
      columns: maps(json['columns']).map(TopColumn.fromJson).toList(),
      rows: maps(json['rows']).map(TopRow.fromJson).toList(),
      total: (json['total'] as Map?)?.cast<String, Object?>() ?? const <String, Object?>{},
      notes: (json['notes'] as List?)?.whereType<String>().toList() ?? const <String>[],
    );
  }
}

/// One report in the Top Prime Reports list.
class TopReportEntry {
  const TopReportEntry({
    required this.key,
    required this.title,
    required this.question,
    required this.icon,
    this.unavailable,
  });

  final String key;
  final String title;

  /// What it answers, in the owner's words.
  final String question;
  final IconData icon;

  /// Why it cannot be opened yet, or null when it can.
  final String? unavailable;
}

class TopReportGroup {
  const TopReportGroup(this.heading, this.entries);

  final String heading;
  final List<TopReportEntry> entries;
}

const String _ordersNotYet =
    'Needs order tracking from TallyPrime, which the connector does not read yet';

/// The twenty-three reports of the Top Prime set, grouped by who or what they
/// rank. Keys match the backend's `top_reports.REPORT_KEYS`.
const List<TopReportGroup> topReportCatalog = <TopReportGroup>[
  TopReportGroup('Customers and suppliers', <TopReportEntry>[
    TopReportEntry(
      key: 'customers',
      title: 'Top Prime Customers',
      question: 'Who buys the most from us',
      icon: Icons.groups_outlined,
    ),
    TopReportEntry(
      key: 'suppliers',
      title: 'Top Prime Suppliers',
      question: 'From whom we buy the most',
      icon: Icons.local_shipping_outlined,
    ),
    TopReportEntry(
      key: 'inactive_customers',
      title: 'Top Inactive Customers',
      question: 'Customers who have not bought for 180+ days',
      icon: Icons.person_off_outlined,
    ),
    TopReportEntry(
      key: 'sales_returns',
      title: 'Top Sales Return Customers',
      question: 'Which customers return the most',
      icon: Icons.assignment_return_outlined,
    ),
    TopReportEntry(
      key: 'purchase_returns',
      title: 'Top Purchase Return Suppliers',
      question: 'To which suppliers we return the most',
      icon: Icons.keyboard_return,
    ),
  ]),
  TopReportGroup('Products', <TopReportEntry>[
    TopReportEntry(
      key: 'selling_products',
      title: 'Top Selling Products',
      question: 'Which items sell the most',
      icon: Icons.trending_up,
    ),
    TopReportEntry(
      key: 'buying_products',
      title: 'Top Buying Products',
      question: 'Which items we buy the most',
      icon: Icons.shopping_cart_outlined,
    ),
    TopReportEntry(
      key: 'fast_moving',
      title: 'Top Fast Moving Products',
      question: 'Which items sell fastest per day, week and month',
      icon: Icons.speed,
    ),
    TopReportEntry(
      key: 'non_moving',
      title: 'Top Non-Moving Products',
      question: 'Stock on hand with no sale for 180+ days',
      icon: Icons.hourglass_bottom,
    ),
    TopReportEntry(
      key: 'negative_stock',
      title: 'Top Negative Stock Products',
      question: 'Items the books say we sold more of than we had',
      icon: Icons.error_outline,
    ),
    TopReportEntry(
      key: 'rate_variance',
      title: 'Top Lower/Higher Rate Item Suppliers',
      question: 'Items bought at different rates from different suppliers',
      icon: Icons.compare_arrows,
    ),
  ]),
  TopReportGroup('Profit', <TopReportEntry>[
    TopReportEntry(
      key: 'profitable_products',
      title: 'Top Profitable Products',
      question: 'Which items earn the most gross profit',
      icon: Icons.savings_outlined,
    ),
    TopReportEntry(
      key: 'loss_products',
      title: 'Top Non-Profitable Products',
      question: 'Which items are sold at a loss',
      icon: Icons.money_off,
    ),
    TopReportEntry(
      key: 'profitable_customers',
      title: 'Top Profitable Customers',
      question: 'Which customers give the most gross profit',
      icon: Icons.thumb_up_alt_outlined,
    ),
    TopReportEntry(
      key: 'loss_customers',
      title: 'Top Non-Profitable Customers',
      question: 'To which customers we sell at a loss',
      icon: Icons.thumb_down_alt_outlined,
    ),
    TopReportEntry(
      key: 'expenses',
      title: 'Top Expense Accounts',
      question: 'Where the money is spent',
      icon: Icons.receipt_outlined,
    ),
  ]),
  TopReportGroup('Outstanding and payments', <TopReportEntry>[
    TopReportEntry(
      key: 'outstanding_customers',
      title: 'Top Outstanding Customers',
      question: 'Which customers owe us the longest and the most',
      icon: Icons.call_received,
    ),
    TopReportEntry(
      key: 'outstanding_suppliers',
      title: 'Top Outstanding Suppliers',
      question: 'Whom we owe the longest and the most',
      icon: Icons.call_made,
    ),
    TopReportEntry(
      key: 'payment_performance',
      title: 'Top Customers Payment Performance',
      question: 'Which customers pay fastest and slowest',
      icon: Icons.timer_outlined,
    ),
  ]),
  TopReportGroup('Orders', <TopReportEntry>[
    TopReportEntry(
      key: 'so_customers',
      title: 'Top Sales Order Outstanding Customers',
      question: 'Customers with the most pending sales orders',
      icon: Icons.pending_actions_outlined,
      unavailable: _ordersNotYet,
    ),
    TopReportEntry(
      key: 'po_suppliers',
      title: 'Top Purchase Order Outstanding Suppliers',
      question: 'Suppliers with the most pending purchase orders',
      icon: Icons.pending_outlined,
      unavailable: _ordersNotYet,
    ),
    TopReportEntry(
      key: 'so_items',
      title: 'Top Sales Order Item Outstanding',
      question: 'Items with the most pending sales orders',
      icon: Icons.inventory_outlined,
      unavailable: _ordersNotYet,
    ),
    TopReportEntry(
      key: 'po_items',
      title: 'Top Purchase Order Item Outstanding',
      question: 'Items with the most pending purchase orders',
      icon: Icons.inventory_2_outlined,
      unavailable: _ordersNotYet,
    ),
  ]),
];
