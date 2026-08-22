import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../../auth/application/auth_controller.dart';
import '../../auth/domain/app_user.dart';
import '../application/team_providers.dart';

/// Choose a password.
///
/// Two ways in, and they are genuinely different:
///
///  * **Forced** — the account is still on a password an admin handed over. The
///    app shows nothing else until it is changed, there is no way back, and the
///    current password is not asked for: someone else chose it, so retyping it
///    proves nothing.
///  * **Voluntary** — from Settings. The current password is required, because
///    otherwise a borrowed unlocked phone is a permanent account takeover.
class ChangePasswordScreen extends ConsumerStatefulWidget {
  const ChangePasswordScreen({super.key});

  @override
  ConsumerState<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends ConsumerState<ChangePasswordScreen> {
  final TextEditingController _current = TextEditingController();
  final TextEditingController _next = TextEditingController();
  final TextEditingController _confirm = TextEditingController();

  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit({required bool forced}) async {
    final String next = _next.text;
    if (next.length < 10) {
      setState(() => _error = 'Use at least 10 characters.');
      return;
    }
    if (next != _confirm.text) {
      setState(() => _error = 'Those two passwords are not the same.');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      await ref.read(teamRepositoryProvider).changeOwnPassword(
            newPassword: next,
            currentPassword: forced ? null : _current.text,
          );
      // Re-read the account so `mustChangePassword` clears and the router lets
      // them through. Without this the gate below would hold them on this
      // screen with nothing left to do.
      await ref.read(authControllerProvider.notifier).refreshUser();
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(const SnackBar(content: Text('Password changed')));
      if (!forced && Navigator.of(context).canPop()) Navigator.of(context).pop();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AppUser? user = ref.watch(authControllerProvider).user;
    final bool forced = user?.mustChangePassword ?? false;

    return Scaffold(
      appBar: AppBar(
        title: Text(forced ? 'Choose a password' : 'Change password'),
        automaticallyImplyLeading: !forced,
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 32),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              if (forced) ...<Widget>[
                Icon(Icons.lock_reset, size: 44, color: theme.colorScheme.primary),
                const SizedBox(height: 14),
                Text(
                  'Your account was set up by an admin. Choose a password only '
                  'you know before you carry on.',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: context.mutedColor),
                ),
                const SizedBox(height: 26),
              ],

              if (!forced) ...<Widget>[
                TextField(
                  controller: _current,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'Current password',
                    prefixIcon: Icon(Icons.lock_outline),
                  ),
                ),
                const SizedBox(height: 14),
              ],

              TextField(
                controller: _next,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'New password',
                  helperText: 'At least 10 characters',
                  prefixIcon: Icon(Icons.password),
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _confirm,
                obscureText: true,
                onSubmitted: (_) => _busy ? null : _submit(forced: forced),
                decoration: const InputDecoration(
                  labelText: 'Repeat new password',
                  prefixIcon: Icon(Icons.password),
                ),
              ),

              if (_error != null) ...<Widget>[
                const SizedBox(height: 14),
                Text(
                  _error!,
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.error),
                ),
              ],

              const SizedBox(height: 26),
              FilledButton(
                onPressed: _busy ? null : () => _submit(forced: forced),
                child: _busy
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Save password'),
              ),

              if (forced) ...<Widget>[
                const SizedBox(height: 10),
                TextButton(
                  onPressed: () => ref.read(authControllerProvider.notifier).signOut(),
                  child: const Text('Sign out instead'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
