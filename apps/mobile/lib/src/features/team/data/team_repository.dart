import '../../../core/network/api_client.dart';
import '../../auth/domain/app_user.dart';
import '../domain/team_member.dart';

/// Everything the team screen asks the backend for.
///
/// Thin on purpose: authorisation lives on the server, so there is nothing to
/// decide here. A staff member calling any of these gets a 403 and the app's
/// existing error envelope explains it — the role checks in the UI exist to keep
/// people out of dead ends, not to enforce anything.
class TeamRepository {
  TeamRepository(this._api);

  final ApiClient _api;

  Future<List<TeamMember>> members() async {
    final List<Map<String, Object?>> rows = await _api.getList('/v1/team');
    return rows.map(TeamMember.fromJson).toList(growable: false);
  }

  Future<NewTeamMember> add({
    required String email,
    required UserRole role,
    String? fullName,
    List<String> companyIds = const <String>[],
  }) async {
    final Map<String, Object?> body = await _api.postJson(
      '/v1/team',
      body: <String, Object?>{
        'email': email.trim(),
        'full_name': fullName?.trim().isEmpty ?? true ? null : fullName!.trim(),
        'role': role.name,
        'company_ids': companyIds,
      },
    );
    return NewTeamMember.fromJson(body);
  }

  /// Only the fields passed are changed. `companyIds` replaces the whole grant
  /// list, because the screen sends every ticked box — a merge would make
  /// un-ticking impossible.
  Future<TeamMember> update(
    String memberId, {
    UserRole? role,
    bool? isActive,
    String? fullName,
    List<String>? companyIds,
  }) async {
    final Map<String, Object?> body = <String, Object?>{
      if (role != null) 'role': role.name,
      if (isActive != null) 'is_active': isActive,
      if (fullName != null) 'full_name': fullName,
      if (companyIds != null) 'company_ids': companyIds,
    };
    return TeamMember.fromJson(await _api.patchJson('/v1/team/$memberId', body: body));
  }

  Future<String> resetPassword(String memberId) async {
    final Map<String, Object?> body =
        await _api.postJson('/v1/team/$memberId/password');
    return body['temporary_password'] as String? ?? '';
  }

  Future<void> remove(String memberId) => _api.delete('/v1/team/$memberId');

  /// Change your own password. [currentPassword] is not required while the
  /// account is still on an admin-issued one.
  Future<void> changeOwnPassword({
    required String newPassword,
    String? currentPassword,
  }) {
    return _api.post(
      '/v1/team/me/password',
      body: <String, Object?>{
        if (currentPassword != null) 'current_password': currentPassword,
        'new_password': newPassword,
      },
    );
  }
}
