/// What a member may do inside their organisation.
///
/// Mirrors the backend enum. The app uses it only to hide controls the server
/// would refuse anyway -- authorisation is enforced there, and a role held in a
/// phone's memory is a hint, never a permission.
enum UserRole {
  owner,
  accountant,
  viewer;

  static UserRole parse(String? raw) => switch (raw) {
        'owner' => UserRole.owner,
        'accountant' => UserRole.accountant,
        _ => UserRole.viewer,
      };

  bool get canManageConnectors => this == UserRole.owner;
  bool get canLinkCompanies => this == UserRole.owner || this == UserRole.accountant;

  String get label => switch (this) {
        UserRole.owner => 'Owner',
        UserRole.accountant => 'Accountant',
        UserRole.viewer => 'Viewer',
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
  });

  final String id;
  final String email;
  final String orgId;
  final String orgName;
  final UserRole role;
  final String? fullName;

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
      );
}

extension on String {
  String characters() => isEmpty ? '' : substring(0, 1);
}
