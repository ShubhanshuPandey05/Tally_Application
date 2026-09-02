/// What this business is entitled to, as the backend decided it.
///
/// Mirrors `SubscriptionResponse`. The app never works out for itself whether
/// an account may add a Tally PC: the server sends [allowsChanges] already
/// resolved, and the server is what enforces it. A phone re-deriving the rule
/// from status and expiry is a phone that will one day derive it differently
/// and show a button that only fails when tapped.
///
/// Everything here fails towards *locked*. An unrecognised status reads as
/// pending, a missing object reads as pending, and both hide the controls
/// rather than offering them — the same direction as [UserRole.parse], and for
/// the same reason.
library;

enum SubscriptionStatus {
  /// Signed up, waiting for someone to approve the account. Every customer
  /// spends their first few minutes here, so it is a first-class state with
  /// its own screen, not an error.
  pending,
  active,
  suspended,
  rejected;

  static SubscriptionStatus parse(String? raw) => switch (raw) {
        'active' => SubscriptionStatus.active,
        'suspended' => SubscriptionStatus.suspended,
        'rejected' => SubscriptionStatus.rejected,
        _ => SubscriptionStatus.pending,
      };

  String get label => switch (this) {
        SubscriptionStatus.pending => 'Waiting for approval',
        SubscriptionStatus.active => 'Active',
        SubscriptionStatus.suspended => 'Suspended',
        SubscriptionStatus.rejected => 'Not activated',
      };
}

class OrgSubscription {
  const OrgSubscription({
    this.status = SubscriptionStatus.pending,
    this.isExpired = false,
    this.allowsChanges = false,
    this.allowsData = true,
    this.maxUsers = 0,
    this.maxCompanies = 0,
    this.usersUsed = 0,
    this.companiesUsed = 0,
    this.expiresAt,
    this.message = '',
    this.isDemo = false,
  });

  final SubscriptionStatus status;
  final bool isExpired;

  /// May this account add a Tally PC, a company or a colleague?
  final bool allowsChanges;

  /// May it read its books at all? A pending account may — there is nothing
  /// there yet, and refusing would turn the first launch into an error page.
  final bool allowsData;

  final int maxUsers;
  final int maxCompanies;
  final int usersUsed;
  final int companiesUsed;
  final DateTime? expiresAt;

  /// Written by the backend for a shop owner, and always naming the way out.
  /// Empty when the account is live.
  final String message;

  /// The shared demo: real screens, invented books, and nobody's Tally PC
  /// behind them. The app says so out loud rather than letting a visitor read
  /// somebody's imaginary receivables as a product claim.
  final bool isDemo;

  /// True while the account is genuinely waiting on somebody, as opposed to
  /// having been switched off. The two need different words: one is "nearly
  /// there", the other is "ring your partner".
  bool get isAwaitingApproval =>
      status == SubscriptionStatus.pending && !isExpired;

  /// The account was live and is not any more. Suspension and a lapsed term
  /// are the same thing from the phone's side.
  bool get isStopped =>
      isExpired ||
      status == SubscriptionStatus.suspended ||
      status == SubscriptionStatus.rejected;

  bool get atCompanyLimit => maxCompanies > 0 && companiesUsed >= maxCompanies;
  bool get atUserLimit => maxUsers > 0 && usersUsed >= maxUsers;

  /// A one-line summary for the account screen: "2 of 3 companies".
  String get companiesLabel => '$companiesUsed of $maxCompanies';
  String get usersLabel => '$usersUsed of $maxUsers';

  /// The default for a response that predates this field. Locked, not open:
  /// an old build talking to a new backend must not draw controls the server
  /// will refuse, and one that cannot see its entitlement knows nothing.
  static const OrgSubscription unknown = OrgSubscription();

  factory OrgSubscription.fromJson(Map<String, Object?> json) => OrgSubscription(
        status: SubscriptionStatus.parse(json['status'] as String?),
        isExpired: json['is_expired'] as bool? ?? false,
        allowsChanges: json['allows_changes'] as bool? ?? false,
        allowsData: json['allows_data'] as bool? ?? true,
        maxUsers: (json['max_users'] as num?)?.toInt() ?? 0,
        maxCompanies: (json['max_companies'] as num?)?.toInt() ?? 0,
        usersUsed: (json['users_used'] as num?)?.toInt() ?? 0,
        companiesUsed: (json['companies_used'] as num?)?.toInt() ?? 0,
        expiresAt: json['expires_at'] == null
            ? null
            : DateTime.tryParse(json['expires_at'] as String)?.toLocal(),
        message: json['message'] as String? ?? '',
        isDemo: json['is_demo'] as bool? ?? false,
      );
}
