/// What one staff member may see inside one company.
///
/// Mirrors the backend's `AccessPolicy` field for field. It records only the
/// *exceptions*: a fresh grant is [AccessPolicy.all], and every switch an admin
/// turns off adds a name to one of the lists. A row under a switched-off parent
/// is hidden whatever its own entry says, so turning the parent back on brings
/// the children back exactly as they were left.
///
/// The server enforces all of it. The app reads it only to leave switched-off
/// reports and entry kinds out of the menus, so nobody taps into a screen that
/// will refuse them.
class AccessPolicy {
  const AccessPolicy({
    this.reports = true,
    this.standardReports = true,
    this.primeReports = true,
    this.deniedReports = const <String>{},
    this.ledgers = true,
    this.deniedLedgerGroups = const <String>{},
    this.deniedLedgers = const <String>{},
    this.stock = true,
    this.deniedStockGroups = const <String>{},
    this.deniedStockItems = const <String>{},
    this.entries = true,
    this.deniedEntries = const <String>{},
  });

  static const AccessPolicy all = AccessPolicy();

  /// "Reports Master": off hides every report.
  final bool reports;
  final bool standardReports;
  final bool primeReports;

  /// Report keys switched off. Top Prime Reports are `top:<key>`.
  final Set<String> deniedReports;

  /// "Ledger Groups": off hides every ledger, and every entry that uses one.
  final bool ledgers;
  final Set<String> deniedLedgerGroups;
  final Set<String> deniedLedgers;

  /// "Stock Group": off hides every stock item.
  final bool stock;
  final Set<String> deniedStockGroups;
  final Set<String> deniedStockItems;

  /// "Entries": off means no new entry of any kind.
  final bool entries;

  /// Entry kinds switched off, by the wire name `POST /vouchers` takes
  /// (`EntryKind.wire`).
  final Set<String> deniedEntries;

  static const String primePrefix = 'top:';

  bool allowsEntry(String kind) => entries && !deniedEntries.contains(kind);

  bool allowsReport(String key) {
    if (!reports) return false;
    final bool section = key.startsWith(primePrefix) ? primeReports : standardReports;
    return section && !deniedReports.contains(key);
  }

  bool allowsPrimeReport(String key) => allowsReport('$primePrefix$key');

  bool get isUnrestricted =>
      reports &&
      standardReports &&
      primeReports &&
      deniedReports.isEmpty &&
      ledgers &&
      deniedLedgerGroups.isEmpty &&
      deniedLedgers.isEmpty &&
      stock &&
      deniedStockGroups.isEmpty &&
      deniedStockItems.isEmpty &&
      entries &&
      deniedEntries.isEmpty;

  AccessPolicy copyWith({
    bool? reports,
    bool? standardReports,
    bool? primeReports,
    Set<String>? deniedReports,
    bool? ledgers,
    Set<String>? deniedLedgerGroups,
    Set<String>? deniedLedgers,
    bool? stock,
    Set<String>? deniedStockGroups,
    Set<String>? deniedStockItems,
    bool? entries,
    Set<String>? deniedEntries,
  }) =>
      AccessPolicy(
        reports: reports ?? this.reports,
        standardReports: standardReports ?? this.standardReports,
        primeReports: primeReports ?? this.primeReports,
        deniedReports: deniedReports ?? this.deniedReports,
        ledgers: ledgers ?? this.ledgers,
        deniedLedgerGroups: deniedLedgerGroups ?? this.deniedLedgerGroups,
        deniedLedgers: deniedLedgers ?? this.deniedLedgers,
        stock: stock ?? this.stock,
        deniedStockGroups: deniedStockGroups ?? this.deniedStockGroups,
        deniedStockItems: deniedStockItems ?? this.deniedStockItems,
        entries: entries ?? this.entries,
        deniedEntries: deniedEntries ?? this.deniedEntries,
      );

  static Set<String> _names(Object? raw) =>
      (raw as List<Object?>? ?? const <Object?>[]).whereType<String>().toSet();

  factory AccessPolicy.fromJson(Map<String, Object?> json) => AccessPolicy(
        reports: json['reports'] as bool? ?? true,
        standardReports: json['standard_reports'] as bool? ?? true,
        primeReports: json['prime_reports'] as bool? ?? true,
        deniedReports: _names(json['denied_reports']),
        ledgers: json['ledgers'] as bool? ?? true,
        deniedLedgerGroups: _names(json['denied_ledger_groups']),
        deniedLedgers: _names(json['denied_ledgers']),
        stock: json['stock'] as bool? ?? true,
        deniedStockGroups: _names(json['denied_stock_groups']),
        deniedStockItems: _names(json['denied_stock_items']),
        entries: json['entries'] as bool? ?? true,
        deniedEntries: _names(json['denied_entries']),
      );

  Map<String, Object?> toJson() => <String, Object?>{
        'reports': reports,
        'standard_reports': standardReports,
        'prime_reports': primeReports,
        'denied_reports': deniedReports.toList()..sort(),
        'ledgers': ledgers,
        'denied_ledger_groups': deniedLedgerGroups.toList()..sort(),
        'denied_ledgers': deniedLedgers.toList()..sort(),
        'stock': stock,
        'denied_stock_groups': deniedStockGroups.toList()..sort(),
        'denied_stock_items': deniedStockItems.toList()..sort(),
        'entries': entries,
        'denied_entries': deniedEntries.toList()..sort(),
      };
}

/// One named node an admin can switch: a group, a ledger, a stock group or item.
class AccessNode {
  const AccessNode({required this.name, this.parent});

  final String name;

  /// The group it sits under, or null at the top of its tree.
  final String? parent;
}

/// Everything an admin can switch off in one company, by name. Never a figure.
class AccessCatalog {
  const AccessCatalog({
    required this.mastersRead,
    required this.ledgerGroups,
    required this.ledgers,
    required this.stockGroups,
    required this.stockItems,
  });

  /// False until the company's masters have been read from Tally. The trees
  /// are then empty because nobody knows them yet, not because there are none.
  final bool mastersRead;
  final List<AccessNode> ledgerGroups;
  final List<AccessNode> ledgers;
  final List<AccessNode> stockGroups;
  final List<AccessNode> stockItems;

  static List<AccessNode> _nodes(Object? raw, String parentKey) =>
      (raw as List<Object?>? ?? const <Object?>[])
          .whereType<Map<Object?, Object?>>()
          .map(
            (Map<Object?, Object?> row) => AccessNode(
              name: '${row['name'] ?? ''}',
              parent: switch (row[parentKey]) {
                final String p when p.trim().isNotEmpty => p,
                _ => null,
              },
            ),
          )
          .where((AccessNode n) => n.name.isNotEmpty)
          .toList(growable: false);

  factory AccessCatalog.fromJson(Map<String, Object?> json) => AccessCatalog(
        mastersRead: json['masters_read'] as bool? ?? false,
        ledgerGroups: _nodes(json['ledger_groups'], 'parent'),
        ledgers: _nodes(json['ledgers'], 'group'),
        stockGroups: _nodes(json['stock_groups'], 'parent'),
        stockItems: _nodes(json['stock_items'], 'group'),
      );
}
