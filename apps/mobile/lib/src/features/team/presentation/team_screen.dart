import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_exception.dart';
import '../../../app/theme.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/application/company_providers.dart';
import '../../companies/domain/company.dart';
import '../../subscription/presentation/subscription_notice.dart';
import '../application/team_providers.dart';
import '../data/team_repository.dart';
import '../domain/team_member.dart';
import 'member_editor_sheet.dart';
import 'temporary_password_dialog.dart';

/// Who can open this business's books, and which parts of it.
///
/// Admin-only, and the backend enforces that independently — this screen is
/// simply not reachable for staff, so they never meet a control that fails.
class TeamScreen extends ConsumerWidget {
  const TeamScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<TeamMember>> members = ref.watch(teamMembersProvider);
    final AppUser? me = ref.watch(authControllerProvider).user;
    // The list stays readable when the subscription is not live -- reviewing
    // who has access is exactly what an admin should still be able to do while
    // sorting out a suspension. Only adding is held back.
    final bool canAdd = me?.canManageTeam ?? false;

    return Scaffold(
      appBar: AppBar(title: const Text('People')),
      floatingActionButton: canAdd
          ? FloatingActionButton.extended(
              onPressed: () => _addMember(context, ref),
              icon: const Icon(Icons.person_add_alt_1),
              label: const Text('Add person'),
            )
          : null,
      body: members.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (Object error, StackTrace _) => _TeamError(error: error, ref: ref),
        data: (List<TeamMember> rows) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(teamMembersProvider),
          child: ListView.separated(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
            itemCount: rows.length + 1,
            separatorBuilder: (_, int index) =>
                index == 0 ? const SizedBox(height: 12) : const SizedBox(height: 8),
            itemBuilder: (BuildContext context, int index) {
              if (index == 0) {
                return const Column(
                  children: <Widget>[
                    SubscriptionNotice(compact: true),
                    _Explainer(),
                  ],
                );
              }
              final TeamMember member = rows[index - 1];
              return _MemberCard(
                member: member,
                isSelf: member.id == me?.id,
                onTap: () => _editMember(context, ref, member),
              );
            },
          ),
        ),
      ),
    );
  }

  Future<void> _addMember(BuildContext context, WidgetRef ref) async {
    final List<Company> companies =
        ref.read(companiesProvider).valueOrNull ?? const <Company>[];

    final MemberDraft? draft = await showMemberEditor(
      context,
      companies: companies,
      title: 'Add a person',
    );
    if (draft == null || !context.mounted) return;

    try {
      final NewTeamMember created = await ref.read(teamRepositoryProvider).add(
            email: draft.email,
            fullName: draft.fullName,
            role: draft.role,
            companyIds: draft.companyIds,
          );
      ref.invalidate(teamMembersProvider);
      if (!context.mounted) return;
      await showTemporaryPasswordDialog(
        context,
        name: created.member.displayName,
        email: created.member.email,
        password: created.temporaryPassword,
      );
    } on ApiException catch (error) {
      if (context.mounted) _toast(context, error.message, isError: true);
    }
  }

  Future<void> _editMember(
    BuildContext context,
    WidgetRef ref,
    TeamMember member,
  ) async {
    final List<Company> companies =
        ref.read(companiesProvider).valueOrNull ?? const <Company>[];

    final MemberDraft? draft = await showMemberEditor(
      context,
      companies: companies,
      title: member.displayName,
      existing: member,
    );
    if (draft == null || !context.mounted) return;

    try {
      final TeamRepository repository = ref.read(teamRepositoryProvider);
      switch (draft.action) {
        case MemberAction.save:
          await repository.update(
            member.id,
            role: draft.role,
            fullName: draft.fullName,
            // Admins have no grant list; sending one would be meaningless and
            // the server clears it anyway.
            companyIds: draft.role.isAdmin ? null : draft.companyIds,
          );
        case MemberAction.resetPassword:
          final String password = await repository.resetPassword(member.id);
          if (!context.mounted) return;
          await showTemporaryPasswordDialog(
            context,
            name: member.displayName,
            email: member.email,
            password: password,
            isReset: true,
          );
        case MemberAction.remove:
          await repository.remove(member.id);
      }
      ref.invalidate(teamMembersProvider);
    } on ApiException catch (error) {
      if (context.mounted) _toast(context, error.message, isError: true);
    }
  }
}

void _toast(BuildContext context, String message, {bool isError = false}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: isError ? Theme.of(context).colorScheme.error : null,
      ),
    );
}

class _Explainer extends StatelessWidget {
  const _Explainer();

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.surfaceContainerHighest,
      elevation: 0,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(Icons.info_outline, size: 18, color: context.mutedColor),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                'Staff see only the companies you tick. Admins can add Tally PCs, '
                'connect companies and manage people.',
                style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _MemberCard extends StatelessWidget {
  const _MemberCard({
    required this.member,
    required this.isSelf,
    required this.onTap,
  });

  final TeamMember member;
  final bool isSelf;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool admin = member.role.isAdmin;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: ListTile(
        onTap: onTap,
        contentPadding: const EdgeInsets.fromLTRB(14, 8, 10, 8),
        leading: CircleAvatar(
          backgroundColor: admin
              ? theme.colorScheme.primaryContainer
              : theme.colorScheme.surfaceContainerHighest,
          foregroundColor: admin
              ? theme.colorScheme.onPrimaryContainer
              : theme.colorScheme.onSurfaceVariant,
          child: Text(
            member.initials,
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        ),
        title: Row(
          children: <Widget>[
            Flexible(
              child: Text(
                member.displayName,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.titleSmall,
              ),
            ),
            if (isSelf) ...<Widget>[
              const SizedBox(width: 6),
              Text('· you', style: theme.textTheme.labelSmall
                  ?.copyWith(color: context.mutedColor)),
            ],
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                member.email,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: <Widget>[
                  _Chip(
                    label: member.role.label,
                    tone: admin ? _Tone.accent : _Tone.neutral,
                  ),
                  if (!admin)
                    _Chip(
                      label: switch (member.companyIds.length) {
                        0 => 'No companies',
                        1 => '1 company',
                        final int n => '$n companies',
                      },
                      tone: member.companyIds.isEmpty ? _Tone.caution : _Tone.neutral,
                    ),
                  if (!member.isActive)
                    const _Chip(label: 'Switched off', tone: _Tone.negative),
                  if (member.mustChangePassword)
                    const _Chip(label: 'Not signed in yet', tone: _Tone.caution),
                ],
              ),
            ],
          ),
        ),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}

enum _Tone { neutral, accent, caution, negative }

class _Chip extends StatelessWidget {
  const _Chip({required this.label, this.tone = _Tone.neutral});

  final String label;
  final _Tone tone;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final (Color fg, Color bg) = switch (tone) {
      _Tone.accent => (theme.colorScheme.onPrimaryContainer, theme.colorScheme.primaryContainer),
      _Tone.caution => (context.cautionColor, context.cautionColor.withOpacity(0.12)),
      _Tone.negative => (context.negativeColor, context.negativeColor.withOpacity(0.12)),
      _Tone.neutral => (context.mutedColor, theme.colorScheme.surfaceContainerHighest),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(6)),
      child: Text(
        label,
        style: theme.textTheme.labelSmall?.copyWith(
          color: fg,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class _TeamError extends StatelessWidget {
  const _TeamError({required this.error, required this.ref});

  final Object error;
  final WidgetRef ref;

  @override
  Widget build(BuildContext context) {
    final String message = error is ApiException
        ? (error as ApiException).message
        : 'Could not load your team.';
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.people_outline, size: 44, color: context.mutedColor),
            const SizedBox(height: 14),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.tonal(
              onPressed: () => ref.invalidate(teamMembersProvider),
              child: const Text('Try again'),
            ),
          ],
        ),
      ),
    );
  }
}
