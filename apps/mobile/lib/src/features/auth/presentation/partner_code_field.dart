import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../application/auth_controller.dart';
import '../data/auth_repository.dart';

/// Where the live check under the partner-code box has got to.
enum PartnerCodeState {
  /// Nothing typed. The box is optional, so this is fine to submit.
  empty,

  /// Waiting for the typing to pause, or for the server's answer.
  checking,

  /// The server knows this code.
  valid,

  /// The server does not know this code. Submitting would be refused, so the
  /// form holds the button until the box is fixed or cleared.
  invalid,

  /// The check itself failed -- no signal, or too many tries. Not a verdict on
  /// the code, so the form lets it through and the server decides.
  unknown;

  /// Whether the form may be submitted with the box as it stands.
  bool get allowsSubmit => this != invalid && this != checking;
}

/// The optional partner-code box, checked as it is typed.
///
/// Checked after the typing pauses rather than on every key, because each
/// check is a request in a rate-limited bucket and a shop owner reading a code
/// off a card types it a character at a time. Codes shorter than four
/// characters are never sent: none can be valid, and "not valid" under a half-
/// typed code reads as a mistake the person has not made yet.
class PartnerCodeField extends ConsumerStatefulWidget {
  const PartnerCodeField({
    super.key,
    required this.controller,
    required this.onStateChanged,
  });

  final TextEditingController controller;
  final ValueChanged<PartnerCodeState> onStateChanged;

  @override
  ConsumerState<PartnerCodeField> createState() => _PartnerCodeFieldState();
}

class _PartnerCodeFieldState extends ConsumerState<PartnerCodeField> {
  static const Duration _pause = Duration(milliseconds: 600);
  static const int _shortest = 4;

  Timer? _debounce;
  PartnerCodeState _state = PartnerCodeState.empty;
  String? _partnerName;

  /// The text the latest request was for. An answer for anything else arrived
  /// after the person kept typing, and is dropped rather than shown under a
  /// code it was not about.
  String _asked = '';

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  void _set(PartnerCodeState state, {String? partnerName}) {
    if (!mounted) return;
    setState(() {
      _state = state;
      _partnerName = partnerName;
    });
    widget.onStateChanged(state);
  }

  void _changed(String raw) {
    _debounce?.cancel();
    final String code = raw.trim().toUpperCase();
    if (code.isEmpty) {
      _asked = '';
      _set(PartnerCodeState.empty);
      return;
    }
    _set(PartnerCodeState.checking);
    _debounce = Timer(_pause, () => _check(code));
  }

  Future<void> _check(String code) async {
    _asked = code;
    if (code.length < _shortest) {
      _set(PartnerCodeState.invalid);
      return;
    }
    try {
      final PartnerCodeCheck answer =
          await ref.read(authRepositoryProvider).checkPartnerCode(code);
      if (_asked != code) return;
      _set(
        answer.valid ? PartnerCodeState.valid : PartnerCodeState.invalid,
        partnerName: answer.partnerName,
      );
    } on ApiException {
      if (_asked != code) return;
      _set(PartnerCodeState.unknown);
    }
  }

  @override
  Widget build(BuildContext context) {
    final String? name = _partnerName;
    final String? helper = switch (_state) {
      PartnerCodeState.empty => 'If a Prime Analyst partner gave you a code',
      PartnerCodeState.checking => 'Checking…',
      PartnerCodeState.valid =>
        name == null || name.isEmpty ? 'Valid partner code' : 'Partner: $name',
      PartnerCodeState.unknown => 'Could not check the code now. It is checked again '
          'when you continue.',
      PartnerCodeState.invalid => null,
    };

    return TextFormField(
      controller: widget.controller,
      textCapitalization: TextCapitalization.characters,
      textInputAction: TextInputAction.next,
      autocorrect: false,
      enableSuggestions: false,
      onChanged: _changed,
      decoration: InputDecoration(
        labelText: 'Partner code (optional)',
        prefixIcon: const Icon(Icons.handshake_outlined),
        helperText: helper,
        helperStyle: _state == PartnerCodeState.valid
            ? TextStyle(color: context.positiveColor)
            : null,
        // Inline, under the box, rather than in the form's error banner: it
        // is about this one field, and clearing the box is the other fix.
        errorText: _state == PartnerCodeState.invalid
            ? 'This partner code is not valid. Check it, or leave it empty.'
            : null,
        suffixIcon: switch (_state) {
          PartnerCodeState.checking => const Padding(
              padding: EdgeInsets.all(14),
              child: SizedBox.square(
                dimension: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
          PartnerCodeState.valid =>
            Icon(Icons.check_circle, color: context.positiveColor),
          _ => null,
        },
      ),
    );
  }
}
