import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../application/auth_controller.dart';

/// "Forgot password?": an emailed six-digit code, then a new password.
///
/// One screen with two steps rather than two routes, so going back from the
/// code step keeps the address that was typed. On success the person is
/// signed straight in with the password they just chose -- the server has
/// signed out every other device, which is the point if somebody else had it.
class ForgotPasswordScreen extends ConsumerStatefulWidget {
  const ForgotPasswordScreen({super.key, this.email});

  /// Carried over from the sign-in form, so it is not typed twice.
  final String? email;

  @override
  ConsumerState<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends ConsumerState<ForgotPasswordScreen> {
  final GlobalKey<FormState> _form = GlobalKey<FormState>();
  late final TextEditingController _email = TextEditingController(text: widget.email);
  final TextEditingController _code = TextEditingController();
  final TextEditingController _password = TextEditingController();
  bool _codeSent = false;
  bool _busy = false;
  bool _obscure = true;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _code.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _sendCode() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    await _run(() async {
      await ref.read(authRepositoryProvider).requestPasswordReset(_email.text);
      setState(() => _codeSent = true);
    });
  }

  Future<void> _reset() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    await _run(() async {
      await ref.read(authRepositoryProvider).resetPassword(
            email: _email.text,
            code: _code.text,
            newPassword: _password.text,
          );
      // The router takes over once the session exists.
      await ref.read(authControllerProvider.notifier).signIn(
            email: _email.text,
            password: _password.text,
          );
    });
  }

  Future<void> _run(Future<void> Function() step) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await step();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: _codeSent && !_busy
              ? () => setState(() {
                    _codeSent = false;
                    _error = null;
                  })
              : () => context.go(Routes.signIn),
        ),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Form(
                key: _form,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Text('Reset your password', style: theme.textTheme.headlineSmall),
                    const SizedBox(height: 6),
                    Text(
                      _codeSent
                          // Worded so it is true whether or not the address has
                          // an account -- the server will not say, so neither
                          // can this screen.
                          ? 'If ${_email.text.trim()} has a Prime Analyst account, we '
                              'have emailed it a 6-digit code. Check your spam folder '
                              'if it does not arrive in a minute.'
                          : 'Enter the email you sign in with and we will send you a code.',
                      style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
                    ),
                    const SizedBox(height: 24),
                    TextFormField(
                      controller: _email,
                      enabled: !_codeSent,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const <String>[AutofillHints.email],
                      decoration: const InputDecoration(
                        labelText: 'Email',
                        prefixIcon: Icon(Icons.alternate_email),
                      ),
                      validator: (String? value) =>
                          (value ?? '').contains('@') ? null : 'Enter your email address',
                    ),
                    if (_codeSent) ...<Widget>[
                      const SizedBox(height: 14),
                      TextFormField(
                        controller: _code,
                        autofocus: true,
                        keyboardType: TextInputType.number,
                        autofillHints: const <String>[AutofillHints.oneTimeCode],
                        inputFormatters: <TextInputFormatter>[
                          FilteringTextInputFormatter.digitsOnly,
                          LengthLimitingTextInputFormatter(6),
                        ],
                        decoration: const InputDecoration(
                          labelText: 'Code from the email',
                          prefixIcon: Icon(Icons.pin_outlined),
                        ),
                        validator: (String? value) =>
                            (value ?? '').length == 6 ? null : 'Enter the 6-digit code',
                      ),
                      const SizedBox(height: 14),
                      TextFormField(
                        controller: _password,
                        obscureText: _obscure,
                        autofillHints: const <String>[AutofillHints.newPassword],
                        onFieldSubmitted: (_) => _reset(),
                        decoration: InputDecoration(
                          labelText: 'New password',
                          helperText: 'At least 10 characters',
                          prefixIcon: const Icon(Icons.lock_outline),
                          suffixIcon: IconButton(
                            onPressed: () => setState(() => _obscure = !_obscure),
                            icon: Icon(
                              _obscure
                                  ? Icons.visibility_outlined
                                  : Icons.visibility_off_outlined,
                            ),
                          ),
                        ),
                        validator: (String? value) => (value ?? '').length < 10
                            ? 'Use at least 10 characters'
                            : null,
                      ),
                    ],
                    if (_error != null) ...<Widget>[
                      const SizedBox(height: 16),
                      Text(
                        _error!,
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: context.negativeColor),
                      ),
                    ],
                    const SizedBox(height: 24),
                    FilledButton(
                      onPressed: _busy ? null : (_codeSent ? _reset : _sendCode),
                      child: _busy
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2.2),
                            )
                          : Text(_codeSent ? 'Set new password' : 'Send code'),
                    ),
                    if (_codeSent) ...<Widget>[
                      const SizedBox(height: 8),
                      TextButton(
                        onPressed: _busy ? null : _sendCode,
                        child: const Text('Send a new code'),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
