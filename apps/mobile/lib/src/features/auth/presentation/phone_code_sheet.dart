import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../application/auth_features.dart';
import '../data/phone_proof.dart';
import '../domain/phone.dart';

/// Prove [typed] with an SMS code before a form that names it is sent.
///
/// Returns the proof to send with the form, or null when there is nothing to
/// send -- this build has no Firebase, or the server does not check numbers.
/// Throws [PhoneProofCancelled] when the person closed the sheet, so the form
/// stays open rather than going ahead without the step they walked away from.
Future<String?> provePhoneIfNeeded(
  BuildContext context,
  WidgetRef ref,
  String typed,
) async {
  final AuthFeatures features = await ref.read(authFeaturesProvider.future);
  if (!features.checksPhone) return null;
  if (!ref.read(firebaseReadyProvider)) {
    if (features.requiresPhoneProof) {
      // Saying so here beats sending the form to be refused for a step this
      // build cannot show.
      throw const PhoneProofException(
        'This version of Prime Analyst cannot confirm mobile numbers. '
        'Please update the app.',
      );
    }
    return null;
  }
  final String? phone = normalisePhone(typed);
  if (phone == null || !context.mounted) return null;

  final String? token = await showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    isDismissible: false,
    builder: (BuildContext context) => _PhoneCodeSheet(phone: phone),
  );
  if (token == null) throw const PhoneProofCancelled();
  return token;
}

class PhoneProofCancelled implements Exception {
  const PhoneProofCancelled();
}

class _PhoneCodeSheet extends ConsumerStatefulWidget {
  const _PhoneCodeSheet({required this.phone});

  final String phone;

  @override
  ConsumerState<_PhoneCodeSheet> createState() => _PhoneCodeSheetState();
}

class _PhoneCodeSheetState extends ConsumerState<_PhoneCodeSheet> {
  /// Long enough that a slow SMS is not met with a second one on top of it,
  /// which only confuses which code is the right one.
  static const int _resendAfterSeconds = 30;

  final TextEditingController _code = TextEditingController();
  PhoneCodeSession? _session;
  String? _error;
  bool _sending = true;
  bool _checking = false;
  int _wait = _resendAfterSeconds;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _send();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final PhoneCodeSession session = await ref
          .read(phoneProofProvider)
          .sendCode(widget.phone, resendToken: _session?.resendToken);
      if (!mounted) return;
      if (session.provenToken != null) {
        Navigator.of(context).pop(session.provenToken);
        return;
      }
      setState(() {
        _session = session;
        _sending = false;
      });
      _startCountdown();
      // Android may read the SMS by itself while the box is still empty.
      unawaited(session.autoToken.then((String token) {
        if (mounted) Navigator.of(context).pop(token);
      }, onError: (_) {}));
    } on PhoneProofException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _sending = false;
      });
      _startCountdown();
    }
  }

  void _startCountdown() {
    _timer?.cancel();
    setState(() => _wait = _resendAfterSeconds);
    _timer = Timer.periodic(const Duration(seconds: 1), (Timer timer) {
      if (!mounted) return timer.cancel();
      setState(() => _wait -= 1);
      if (_wait <= 0) timer.cancel();
    });
  }

  Future<void> _confirm() async {
    final PhoneCodeSession? session = _session;
    if (session == null || _code.text.trim().length < 6) return;
    setState(() {
      _checking = true;
      _error = null;
    });
    try {
      final String token = await session.confirm(_code.text);
      if (mounted) Navigator.of(context).pop(token);
    } on PhoneProofException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(
        24,
        20,
        24,
        24 + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text('Confirm your mobile number', style: theme.textTheme.titleLarge),
          const SizedBox(height: 6),
          Text(
            _sending
                ? 'Sending a code to ${widget.phone}…'
                : 'Enter the 6-digit code we sent to ${widget.phone}.',
            style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
          ),
          const SizedBox(height: 20),
          TextField(
            controller: _code,
            autofocus: true,
            enabled: _session != null && !_checking,
            keyboardType: TextInputType.number,
            autofillHints: const <String>[AutofillHints.oneTimeCode],
            inputFormatters: <TextInputFormatter>[
              FilteringTextInputFormatter.digitsOnly,
              LengthLimitingTextInputFormatter(6),
            ],
            textAlign: TextAlign.center,
            style: theme.textTheme.headlineSmall?.copyWith(letterSpacing: 8),
            decoration: const InputDecoration(hintText: '••••••'),
            onChanged: (String value) {
              if (value.length == 6) _confirm();
            },
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: 12),
            Text(
              _error!,
              style: theme.textTheme.bodySmall?.copyWith(color: context.negativeColor),
            ),
          ],
          const SizedBox(height: 20),
          FilledButton(
            onPressed: _session == null || _checking ? null : _confirm,
            child: _checking
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Confirm'),
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: <Widget>[
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Change number'),
              ),
              TextButton(
                onPressed: _sending || _wait > 0 ? null : _send,
                child: Text(_wait > 0 ? 'Resend in ${_wait}s' : 'Resend code'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
