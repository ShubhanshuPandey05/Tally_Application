import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../data/team_repository.dart';
import '../domain/access_policy.dart';
import '../domain/team_member.dart';

final Provider<TeamRepository> teamRepositoryProvider =
    Provider<TeamRepository>((Ref ref) => TeamRepository(ref.watch(apiClientProvider)));

/// Everyone in the organisation.
///
/// `autoDispose` because this is admin-only and rarely open: keeping a cached
/// team list alive for the whole session would mean a role change made on
/// another device stayed invisible until the app was restarted.
final AutoDisposeFutureProvider<List<TeamMember>> teamMembersProvider =
    FutureProvider.autoDispose<List<TeamMember>>((Ref ref) {
  return ref.watch(teamRepositoryProvider).members();
});

/// What the signed-in person may see in one company.
///
/// Fails open on purpose: if this cannot be read, every menu shows as it always
/// has, and the server still refuses whatever is switched off. Hiding the
/// whole Reports screen because one request failed would be the worse mistake.
final AutoDisposeFutureProviderFamily<AccessPolicy, String> myAccessProvider =
    FutureProvider.autoDispose.family<AccessPolicy, String>((Ref ref, String companyId) async {
  try {
    return await ref.watch(teamRepositoryProvider).myAccess(companyId);
  } on Object {
    return AccessPolicy.all;
  }
});
