import '../../auth/domain/app_user.dart';

/// One person in the organisation, as the team screen shows them.
class TeamMember {
  const TeamMember({
    required this.id,
    required this.email,
    required this.role,
    required this.isActive,
    required this.mustChangePassword,
    required this.companyIds,
    this.fullName,
    this.lastLoginAt,
  });

  final String id;
  final String email;
  final UserRole role;
  final bool isActive;

  /// Still on the password an admin handed over — they have not signed in and
  /// chosen their own yet. Worth surfacing: it usually means the handover never
  /// actually happened.
  final bool mustChangePassword;

  /// For an admin this is every company in the organisation. The backend sends
  /// their real reach rather than an empty list, so the app never has to encode
  /// "admin means all" a second time and get it subtly different.
  final List<String> companyIds;

  final String? fullName;
  final DateTime? lastLoginAt;

  String get displayName {
    final String? name = fullName;
    if (name != null && name.trim().isNotEmpty) return name.trim();
    return email.split('@').first;
  }

  String get initials {
    final List<String> parts = displayName.split(RegExp(r'[\s.]+'))
      ..removeWhere((String part) => part.isEmpty);
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts.first.substring(0, 1).toUpperCase();
    return (parts.first.substring(0, 1) + parts[1].substring(0, 1)).toUpperCase();
  }

  factory TeamMember.fromJson(Map<String, Object?> json) => TeamMember(
        id: json['id'] as String? ?? '',
        email: json['email'] as String? ?? '',
        role: UserRole.parse(json['role'] as String?),
        isActive: json['is_active'] as bool? ?? true,
        mustChangePassword: json['must_change_password'] as bool? ?? false,
        companyIds: (json['company_ids'] as List<Object?>? ?? <Object?>[])
            .whereType<String>()
            .toList(growable: false),
        fullName: json['full_name'] as String?,
        lastLoginAt: switch (json['last_login_at']) {
          final String raw when raw.isNotEmpty => DateTime.tryParse(raw)?.toLocal(),
          _ => null,
        },
      );
}

/// A newly created colleague, together with the one-time password.
///
/// There is no email delivery, so this password reaches the person by being read
/// out or written down. It is returned once and never recoverable — the same
/// contract as the connector pairing secret, for the same reason.
class NewTeamMember {
  const NewTeamMember({required this.member, required this.temporaryPassword});

  final TeamMember member;
  final String temporaryPassword;

  factory NewTeamMember.fromJson(Map<String, Object?> json) => NewTeamMember(
        member: TeamMember.fromJson(
          (json['member'] as Map<Object?, Object?>? ?? <Object?, Object?>{})
              .map((Object? k, Object? v) => MapEntry<String, Object?>('$k', v)),
        ),
        temporaryPassword: json['temporary_password'] as String? ?? '',
      );
}
