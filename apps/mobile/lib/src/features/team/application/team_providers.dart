import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../data/team_repository.dart';
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
