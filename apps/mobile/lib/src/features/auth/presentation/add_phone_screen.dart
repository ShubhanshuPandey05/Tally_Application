import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../application/auth_controller.dart';
import '../domain/app_user.dart';
import '../domain/phone.dart';
import 'partner_code_field.dart';

/// "Add your mobile number", for an account made before signup asked for one.
///
/// The router holds the person here until the number is saved, the same way it
/// holds someone on an admin-issued password. For an admin of a business still
/// waiting for approval this is also the step that starts the free trial, so
/// the screen says so -- it is the reason to fill it in.
///
/// The partner-code box is offered to admins only, and only while the
/// business has no partner: the server ignores it otherwise, and a box that
/// silently does nothing is worse than no box.
class AddPhoneScreen extends ConsumerStatefulWidget {
  const AddPhoneScreen({super.key});

  @override
  ConsumerState<AddPhoneScreen> createState() => _AddPhoneScreenState();
}

class _AddPhoneScreenState extends ConsumerState<AddPhoneScreen> {
  final GlobalKey<FormState> _form = GlobalKey<FormState>();
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _partnerCode = TextEditingController();
  PartnerCodeState _codeState = PartnerCodeState.empty;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _phone.dispose();
    _partnerCode.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    if (!_codeState.allowsSubmit) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(authControllerProvider.notifier).addPhone(
            phone: _phone.text,
            partnerCode: _partnerCode.text,
          );
      // The router moves on by itself once `needsPhone` clears.
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
    final bool admin = user?.role.isAdmin ?? false;
    final bool waiting = user?.subscription.isAwaitingApproval ?? false;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Add your mobile number'),
        automaticallyImplyLeading: false,
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 32),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Form(
                key: _form,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Icon(Icons.phone_iphone, size: 44, color: theme.colorScheme.primary),
                    const SizedBox(height: 14),
                    Text(
                      admin && waiting
                          ? 'Add your mobile number to start your free trial. '
                              'It starts counting when you connect your first company.'
                          : 'Prime Analyst now asks every account for a mobile '
                              'number, so your partner can reach you if anything '
                              'needs setting up.',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
                    ),
                    const SizedBox(height: 26),
                    TextFormField(
                      controller: _phone,
                      autofocus: true,
                      keyboardType: TextInputType.phone,
                      textInputAction: TextInputAction.next,
                      autofillHints: const <String>[AutofillHints.telephoneNumber],
                      decoration: const InputDecoration(
                        labelText: 'Mobile number',
                        prefixIcon: Icon(Icons.phone_iphone),
                      ),
                      validator: validatePhone,
                    ),
                    if (admin) ...<Widget>[
                      const SizedBox(height: 14),
                      PartnerCodeField(
                        controller: _partnerCode,
                        onStateChanged: (PartnerCodeState state) =>
                            setState(() => _codeState = state),
                      ),
                    ],
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
                      onPressed: _busy || !_codeState.allowsSubmit ? null : _submit,
                      child: _busy
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Text('Continue'),
                    ),
                    const SizedBox(height: 10),
                    TextButton(
                      onPressed: () => ref.read(authControllerProvider.notifier).signOut(),
                      child: const Text('Sign out instead'),
                    ),
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
