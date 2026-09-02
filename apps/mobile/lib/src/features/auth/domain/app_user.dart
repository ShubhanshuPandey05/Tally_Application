import '../../subscription/domain/subscription.dart';

/// What a member may do inside their organisation.
///
/// Mirrors the backend enum. The app uses it only to hide controls the server
/// would refuse anyway -- authorisation is enforced there, and a role held in a
/// phone's memory is a hint, never a permission.
///
/// The line between the two is "may this person change the shape of the
/// account?" -- add a PC, link a company, add a colleague. Everything else,
/// including reading every report, is available to both.
enum UserRole {
  staff,
  admin;

  /// Anything unrecognised is staff, never admin. A future role this build
  /// predates must not fall through into showing administrative controls.
  static UserRole parse(String? raw) => switch (raw) {
        'admin' => UserRole.admin,
        _ => UserRole.staff,
      };

  bool get isAdmin => this == UserRole.admin;
  bool get canManageConnectors => isAdmin;
  bool get canLinkCompanies => isAdmin;
  bool get canManageTeam => isAdmin;

  String get label => switch (this) {
        UserRole.admin => 'Admin',
        UserRole.staff => 'Staff',
      };

  String get description => switch (this) {
        UserRole.admin =>
          'Can add Tally PCs, connect companies, and manage people.',
        UserRole.staff => 'Can only view the companies you choose.',
      };
}

class AppUser {
  const AppUser({
    required this.id,
    required this.email,
    required this.orgId,
    required this.orgName,
    required this.role,
    this.fullName,
    this.mustChangePassword = false,
    this.subscription = OrgSubscription.unknown,
  });

  final String id;
  final String email;
  final String orgId;
  final String orgName;
  final UserRole role;
  final String? fullName;

  /// The account is still on a password an admin handed over. Until it is
  /// changed the app shows nothing but the "choose a password" screen.
  final bool mustChangePassword;

  /// What the business is entitled to. Arrives with the user because the app
  /// needs both at the same moment — on cold start, before it decides which
  /// controls to draw.
  final OrgSubscription subscription;

  /// Whether to draw an administrative control at all.
  ///
  /// Two conditions, and they are genuinely different questions: *is this
  /// person allowed?* and *is this account allowed?* Combining them here means
  /// every screen asks once and cannot accidentally check only one — which
  /// would show a shop owner an "Add a PC" button that 402s when tapped.
  bool get canManageConnectors =>
      role.canManageConnectors && subscription.allowsChanges;
  bool get canLinkCompanies => role.canLinkCompanies && subscription.allowsChanges;
  bool get canManageTeam => role.canManageTeam && subscription.allowsChanges;

  /// Whether to offer removing something the account already holds -- a
  /// company, a Tally PC.
  ///
  /// Deliberately *not* gated on [OrgSubscription.allowsChanges], which is the
  /// rule for growing: a customer whose subscription has lapsed is still
  /// entitled to unlink their own books, and hiding that would be punitive
  /// rather than commercial. The demo is the one account that may not, because
  /// everyone who signs in shares it. Mirrors the server's own
  /// `require_mutable`.
  bool get canRemoveCompanies => role.isAdmin && !subscription.isDemo;

  /// The shared demo account, which the app labels wherever a figure could be
  /// mistaken for somebody's real books.
  bool get isDemo => subscription.isDemo;

  /// Show the subscription notice rather than a screen full of disabled
  /// buttons. Only admins see it: telling a cashier the business is behind on
  /// its subscription is neither their problem nor their information.
  bool get shouldExplainSubscription =>
      role.isAdmin && !subscription.allowsChanges;

  String get displayName {
    final String? name = fullName;
    if (name != null && name.trim().isNotEmpty) return name.trim();
    return email.split('@').first;
  }

  String get initials {
    final List<String> parts = displayName.split(RegExp(r'[\s.]+'))
      ..removeWhere((String part) => part.isEmpty);
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts.first.characters().toUpperCase();
    return (parts.first.characters() + parts[1].characters()).toUpperCase();
  }

  factory AppUser.fromJson(Map<String, Object?> json) => AppUser(
        id: json['id'] as String? ?? '',
        email: json['email'] as String? ?? '',
        orgId: json['org_id'] as String? ?? '',
        orgName: json['org_name'] as String? ?? '',
        role: UserRole.parse(json['role'] as String?),
        fullName: json['full_name'] as String?,
        mustChangePassword: json['must_change_password'] as bool? ?? false,
        subscription: json['subscription'] is Map
            ? OrgSubscription.fromJson(
                Map<String, Object?>.from(json['subscription'] as Map))
            : OrgSubscription.unknown,
      );
}

extension on String {
  String characters() => isEmpty ? '' : substring(0, 1);
}
