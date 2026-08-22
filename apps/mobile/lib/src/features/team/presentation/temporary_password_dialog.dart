import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../app/theme.dart';

/// Shows a newly issued password once, and says plainly that it is the only time.
///
/// There is no email delivery, so this password gets to the person by being read
/// out or written down. That makes the dialog the entire handover step, and it
/// has to be impossible to dismiss by accident — hence `barrierDismissible:
/// false` and a single explicit button.
///
/// The same contract as the connector pairing secret, for the same reason: the
/// server stores only a hash, so nothing can recover it afterwards. An admin who
/// closes this without copying it has to issue a new one, which is a mild
/// annoyance and a much better failure than a recoverable password would be.
Future<void> showTemporaryPasswordDialog(
  BuildContext context, {
  required String name,
  required String email,
  required String password,
  bool isReset = false,
}) {
  return showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (BuildContext context) => _TemporaryPasswordDialog(
      name: name,
      email: email,
      password: password,
      isReset: isReset,
    ),
  );
}

class _TemporaryPasswordDialog extends StatefulWidget {
  const _TemporaryPasswordDialog({
    required this.name,
    required this.email,
    required this.password,
    required this.isReset,
  });

  final String name;
  final String email;
  final String password;
  final bool isReset;

  @override
  State<_TemporaryPasswordDialog> createState() => _TemporaryPasswordDialogState();
}

class _TemporaryPasswordDialogState extends State<_TemporaryPasswordDialog> {
  bool _copied = false;

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.password));
    if (mounted) setState(() => _copied = true);
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return AlertDialog(
      icon: Icon(Icons.key, color: theme.colorScheme.primary),
      title: Text(widget.isReset ? 'New password' : '${widget.name} is ready'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(
            'Give ${widget.name} these two things. They will be asked to choose '
            'their own password when they first sign in.',
            style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
          ),
          const SizedBox(height: 18),

          _Field(label: 'Email', value: widget.email),
          const SizedBox(height: 10),
          _Field(label: 'Temporary password', value: widget.password, emphasise: true),

          const SizedBox(height: 14),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Icon(Icons.warning_amber_rounded,
                  size: 16, color: context.cautionColor),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'This password is shown only now. If you lose it, you can issue '
                  'a new one from their profile.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: context.cautionColor),
                ),
              ),
            ],
          ),
        ],
      ),
      actions: <Widget>[
        TextButton.icon(
          onPressed: _copy,
          icon: Icon(_copied ? Icons.check : Icons.copy, size: 18),
          label: Text(_copied ? 'Copied' : 'Copy password'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Done'),
        ),
      ],
    );
  }
}

class _Field extends StatelessWidget {
  const _Field({required this.label, required this.value, this.emphasise = false});

  final String label;
  final String value;
  final bool emphasise;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          label,
          style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
        ),
        const SizedBox(height: 4),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(8),
          ),
          child: SelectableText(
            value,
            style: emphasise
                // Monospace so a hand-written copy cannot confuse similar
                // glyphs. The generator already excludes the worst offenders.
                ? theme.textTheme.titleMedium?.copyWith(
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w600,
                    letterSpacing: 1.2,
                  )
                : theme.textTheme.bodyMedium,
          ),
        ),
      ],
    );
  }
}
