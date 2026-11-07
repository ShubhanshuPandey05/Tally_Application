import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../auth/domain/app_user.dart';
import '../../companies/domain/company.dart';
import '../domain/access_policy.dart';
import '../domain/team_member.dart';
import 'member_access_screen.dart';

/// What the editor was asked to do when it closed.
enum MemberAction { save, resetPassword, remove }

/// The result of the sheet. Null is returned when it was dismissed.
class MemberDraft {
  const MemberDraft({
    required this.action,
    required this.email,
    required this.role,
    required this.companyIds,
    this.fullName,
  });

  final MemberAction action;
  final String email;
  final UserRole role;
  final List<String> companyIds;
  final String? fullName;
}

/// Add a colleague, or change one.
///
/// A bottom sheet rather than a route: it is a short form, it is reached from a
/// list the admin will use several times in a row, and a sheet keeps that list
/// visible behind it.
Future<MemberDraft?> showMemberEditor(
  BuildContext context, {
  required List<Company> companies,
  required String title,
  TeamMember? existing,
}) {
  return showModalBottomSheet<MemberDraft>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (BuildContext context) => _MemberEditor(
      companies: companies,
      title: title,
      existing: existing,
    ),
  );
}

class _MemberEditor extends StatefulWidget {
  const _MemberEditor({
    required this.companies,
    required this.title,
    this.existing,
  });

  final List<Company> companies;
  final String title;
  final TeamMember? existing;

  @override
  State<_MemberEditor> createState() => _MemberEditorState();
}

class _MemberEditorState extends State<_MemberEditor> {
  late final TextEditingController _email =
      TextEditingController(text: widget.existing?.email ?? '');
  late final TextEditingController _name =
      TextEditingController(text: widget.existing?.fullName ?? '');

  late UserRole _role = widget.existing?.role ?? UserRole.staff;
  late Set<String> _granted = <String>{...?widget.existing?.companyIds};
  late final Set<String> _restricted = <String>{...?widget.existing?.restrictedCompanyIds};

  String? _emailError;

  bool get _isNew => widget.existing == null;

  @override
  void dispose() {
    _email.dispose();
    _name.dispose();
    super.dispose();
  }

  void _submit() {
    final String email = _email.text.trim();
    if (_isNew && !email.contains('@')) {
      setState(() => _emailError = 'Enter a valid email address');
      return;
    }
    Navigator.of(context).pop(
      MemberDraft(
        action: MemberAction.save,
        email: email,
        fullName: _name.text.trim(),
        role: _role,
        companyIds: _granted.toList(growable: false),
      ),
    );
  }

  void _close(MemberAction action) {
    Navigator.of(context).pop(
      MemberDraft(
        action: action,
        email: _email.text.trim(),
        fullName: _name.text.trim(),
        role: _role,
        companyIds: _granted.toList(growable: false),
      ),
    );
  }

  /// Only a saved staff member has grant rows for a policy to live on.
  bool get _customisable => widget.existing != null && !widget.existing!.role.isAdmin;

  /// Which reports, ledger groups and stock this person sees in [company].
  ///
  /// Only for a company that is already granted on the server: the policy is
  /// stored on that grant. A company switched on in this sheet but not saved
  /// says so instead of opening.
  Future<void> _customise(Company company) async {
    final Object? saved = await Navigator.of(context).push<Object?>(
      MaterialPageRoute<Object?>(
        builder: (_) => MemberAccessScreen(member: widget.existing!, company: company),
      ),
    );
    if (saved is AccessPolicy && mounted) {
      setState(() {
        saved.isUnrestricted ? _restricted.remove(company.id) : _restricted.add(company.id);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // Keeps the submit button above the keyboard on a small phone, which is
    // where this is actually used.
    final double bottomInset = MediaQuery.viewInsetsOf(context).bottom;

    return Padding(
      padding: EdgeInsets.fromLTRB(20, 4, 20, 20 + bottomInset),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Text(widget.title, style: theme.textTheme.titleLarge),
            const SizedBox(height: 18),

            TextField(
              controller: _email,
              enabled: _isNew,
              keyboardType: TextInputType.emailAddress,
              autocorrect: false,
              decoration: InputDecoration(
                labelText: 'Email',
                errorText: _emailError,
                // An email change would move the account itself, so it is fixed
                // once created rather than silently ignored on save.
                helperText: _isNew ? 'They sign in with this' : 'Cannot be changed',
                prefixIcon: const Icon(Icons.alternate_email),
              ),
              onChanged: (_) {
                if (_emailError != null) setState(() => _emailError = null);
              },
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _name,
              textCapitalization: TextCapitalization.words,
              decoration: const InputDecoration(
                labelText: 'Name',
                prefixIcon: Icon(Icons.person_outline),
              ),
            ),

            const SizedBox(height: 22),
            Text('Role', style: theme.textTheme.titleSmall),
            const SizedBox(height: 8),
            ...UserRole.values.map(
              (UserRole role) => RadioListTile<UserRole>(
                value: role,
                groupValue: _role,
                onChanged: (UserRole? picked) =>
                    setState(() => _role = picked ?? _role),
                contentPadding: EdgeInsets.zero,
                title: Text(role.label),
                subtitle: Text(
                  role.description,
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: context.mutedColor),
                ),
              ),
            ),

            // Only staff have a grant list. An admin's access comes from the
            // role, so showing tick boxes there would imply a choice that does
            // not exist.
            if (!_role.isAdmin) ...<Widget>[
              const SizedBox(height: 14),
              _CompanyPicker(
                companies: widget.companies,
                granted: _granted,
                restricted: _restricted,
                saved: <String>{...?widget.existing?.companyIds},
                onChanged: (Set<String> next) => setState(() => _granted = next),
                onCustomise: _customisable ? _customise : null,
              ),
            ],

            const SizedBox(height: 24),
            FilledButton(
              onPressed: _submit,
              child: Text(_isNew ? 'Add person' : 'Save changes'),
            ),

            if (!_isNew) ...<Widget>[
              const SizedBox(height: 6),
              TextButton.icon(
                onPressed: () => _close(MemberAction.resetPassword),
                icon: const Icon(Icons.key_outlined, size: 18),
                label: const Text('Reset their password'),
              ),
              TextButton.icon(
                onPressed: () => _close(MemberAction.remove),
                icon: Icon(Icons.person_remove_outlined,
                    size: 18, color: context.negativeColor),
                label: Text(
                  'Remove from this business',
                  style: TextStyle(color: context.negativeColor),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _CompanyPicker extends StatelessWidget {
  const _CompanyPicker({
    required this.companies,
    required this.granted,
    required this.restricted,
    required this.saved,
    required this.onChanged,
    this.onCustomise,
  });

  /// Companies granted on the server. A policy lives on that grant, so only
  /// these can be opened.
  final Set<String> saved;

  final List<Company> companies;
  final Set<String> granted;

  /// Granted companies where something inside is switched off.
  final Set<String> restricted;
  final ValueChanged<Set<String>> onChanged;

  /// Opens what they see inside a company. Null while the person is not saved
  /// yet, or is an admin, who always sees everything.
  final ValueChanged<Company>? onCustomise;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    if (companies.isEmpty) {
      return Card(
        elevation: 0,
        color: theme.colorScheme.surfaceContainerHighest,
        child: ListTile(
          leading: Icon(Icons.folder_off_outlined, color: context.mutedColor),
          title: const Text('No companies connected yet'),
          subtitle: const Text('Connect one first, then choose who can see it'),
        ),
      );
    }

    final bool all = granted.length == companies.length;

    String subtitle(Company company) {
      if (!granted.contains(company.id)) return 'Hidden';
      if (onCustomise == null || !saved.contains(company.id)) {
        return 'Everything · save to choose inside';
      }
      return restricted.contains(company.id)
          ? 'Limited · tap to change'
          : 'Everything · tap to limit';
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text('Companies they can see', style: theme.textTheme.titleSmall),
            TextButton(
              onPressed: () => onChanged(
                all
                    ? <String>{}
                    : companies.map((Company c) => c.id).toSet(),
              ),
              child: Text(all ? 'Clear all' : 'Select all'),
            ),
          ],
        ),
        // Named rather than left implicit: an empty selection is a legitimate
        // state (create the account now, decide access with the person later),
        // and it must not look like the form is unfinished.
        if (granted.isEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Text(
              'They will not see any company until you switch one on.',
              style: theme.textTheme.bodySmall?.copyWith(color: context.cautionColor),
            ),
          ),
        Card(
          elevation: 0,
          clipBehavior: Clip.antiAlias,
          color: theme.colorScheme.surfaceContainerHighest,
          child: Column(
            children: <Widget>[
              for (final Company company in companies)
                ToggleRow(
                  title: company.name,
                  subtitle: subtitle(company),
                  value: granted.contains(company.id),
                  onChanged: (bool on) {
                    final Set<String> next = <String>{...granted};
                    if (on) {
                      next.add(company.id);
                    } else {
                      next.remove(company.id);
                    }
                    onChanged(next);
                  },
                  onOpen: onCustomise == null || !saved.contains(company.id)
                      ? null
                      : () => onCustomise!(company),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
