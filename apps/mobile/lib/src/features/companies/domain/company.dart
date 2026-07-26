/// A set of books the user has linked from a connector.
class Company {
  const Company({
    required this.id,
    required this.name,
    required this.tallyName,
    required this.connectorId,
    required this.baseCurrency,
    required this.isActive,
    this.financialYearFrom,
  });

  final String id;
  final String name;

  /// The exact name inside TallyPrime. Shown when it differs from the display
  /// name, because a shop with "Bhatia Stores" and "Bhatia Stores (2024-25)"
  /// open at once must be able to tell which one they are looking at.
  final String tallyName;

  final String connectorId;
  final String baseCurrency;
  final bool isActive;
  final DateTime? financialYearFrom;

  factory Company.fromJson(Map<String, Object?> json) => Company(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        tallyName: json['tally_name'] as String? ?? '',
        connectorId: json['connector_id'] as String? ?? '',
        baseCurrency: json['base_currency'] as String? ?? 'INR',
        isActive: json['is_active'] as bool? ?? true,
        financialYearFrom: DateTime.tryParse(json['financial_year_from'] as String? ?? ''),
      );
}

/// A company found open in TallyPrime but not yet linked.
class DiscoveredCompany {
  const DiscoveredCompany({
    required this.tallyName,
    required this.linked,
    this.guid,
    this.companyId,
  });

  final String tallyName;
  final bool linked;
  final String? guid;
  final String? companyId;

  factory DiscoveredCompany.fromJson(Map<String, Object?> json) => DiscoveredCompany(
        tallyName: json['tally_name'] as String? ?? '',
        linked: json['linked'] as bool? ?? false,
        guid: json['guid'] as String?,
        companyId: json['company_id'] as String?,
      );
}
