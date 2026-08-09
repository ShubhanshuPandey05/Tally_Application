import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../../companies/application/company_providers.dart';
import '../application/connector_providers.dart';
import '../domain/connector.dart';

/// The pairing wizard.
///
/// Three steps, in this order for a reason: the credentials are issued first
/// and shown exactly once, then the user walks to their PC, then the app waits
/// for that PC to call in. The wait is real -- someone is physically installing
/// software -- so the screen has to keep saying something useful throughout
/// rather than sitting on a spinner.
class PairConnectorScreen extends ConsumerStatefulWidget {
  const PairConnectorScreen({super.key});

  @override
  ConsumerState<PairConnectorScreen> createState() => _PairConnectorScreenState();
}

class _PairConnectorScreenState extends ConsumerState<PairConnectorScreen> {
  final TextEditingController _name = TextEditingController(text: 'Shop PC');
  ConnectorPairing? _pairing;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final ConnectorPairing pairing = await ref
          .read(connectorRepositoryProvider)
          .create(name: _name.text.trim().isEmpty ? 'Tally PC' : _name.text.trim());
      ref.invalidate(connectorsProvider);
      if (mounted) setState(() => _pairing = pairing);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ConnectorPairing? pairing = _pairing;
    return Scaffold(
      appBar: AppBar(title: const Text('Connect a Tally PC')),
      body: pairing == null ? _buildNameStep(context) : _WaitingStep(pairing: pairing),
    );
  }

  Widget _buildNameStep(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(20),
      children: <Widget>[
        Text('What should we call this computer?',
            style: theme.textTheme.titleMedium),
        const SizedBox(height: 6),
        Text(
          'Give it a name you will recognise later -- "Shop PC", "Back office", '
          '"Warehouse".',
          style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
        ),
        const SizedBox(height: 20),
        TextField(
          controller: _name,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(
            labelText: 'Computer name',
            prefixIcon: Icon(Icons.computer),
          ),
        ),
        const SizedBox(height: 24),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Icon(Icons.shield_outlined,
                        size: 18, color: theme.colorScheme.primary),
                    const SizedBox(width: 8),
                    Text('How this works', style: theme.textTheme.titleSmall),
                  ],
                ),
                const SizedBox(height: 10),
                // Worth stating plainly: the usual objection to a cloud Tally
                // product is "you want me to open my accounts to the internet".
                // The connector dials out; nothing is ever opened inward.
                Text(
                  'The connector runs on your PC and calls out to TallyFlow. '
                  'Nothing is opened up on your computer, and TallyPrime is '
                  'never exposed to the internet.\n\n'
                  'TallyFlow only reads. It cannot create, edit or delete '
                  'anything in your books.',
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: context.mutedColor),
                ),
              ],
            ),
          ),
        ),
        if (_error != null) ...<Widget>[
          const SizedBox(height: 16),
          Text(_error!, style: TextStyle(color: context.negativeColor)),
        ],
        const SizedBox(height: 24),
        FilledButton(
          onPressed: _busy ? null : _create,
          child: _busy
              ? const SizedBox(
                  width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.2))
              : const Text('Create connection'),
        ),
      ],
    );
  }
}

class _WaitingStep extends ConsumerWidget {
  const _WaitingStep({required this.pairing});

  final ConnectorPairing pairing;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final AsyncValue<Connector> status =
        ref.watch(connectorStatusProvider(pairing.connectorId));
    final Connector? connector = status.valueOrNull;
    final bool connected = connector?.online ?? false;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: <Widget>[
        _StatusHeader(connector: connector),
        const SizedBox(height: 20),
        Text('1. Install the connector', style: theme.textTheme.titleSmall),
        const SizedBox(height: 6),
        Text(
          'Download TallyFlow Connector on the PC running TallyPrime and run '
          'the installer.',
          style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
        ),
        const SizedBox(height: 20),
        Text('2. Enter these details', style: theme.textTheme.titleSmall),
        const SizedBox(height: 10),
        CopyField(label: 'Connector ID', value: pairing.connectorId),
        const SizedBox(height: 10),
        CopyField(label: 'Pairing code', value: pairing.pairingCode),
        const SizedBox(height: 10),
        CopyField(label: 'Secret key', value: pairing.secret, sensitive: true),
        const SizedBox(height: 10),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: context.cautionColor.withOpacity(0.08),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: context.cautionColor.withOpacity(0.25)),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Icon(Icons.warning_amber_rounded, size: 18, color: context.cautionColor),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  // True, and the reason it is true is worth the sentence: the
                  // server encrypts this secret rather than storing it in the
                  // clear, and cannot hand it back.
                  'The secret key is shown once and cannot be retrieved again. '
                  'Copy it to the PC now. If you lose it, create a new '
                  'connection instead.',
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 24),
        Text('3. Open your company in TallyPrime', style: theme.textTheme.titleSmall),
        const SizedBox(height: 6),
        Text(
          'Only companies that are open in Tally can be connected. That is what '
          'keeps you in control of what this app can see.',
          style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
        ),
        const SizedBox(height: 28),
        FilledButton(
          onPressed: connected
              ? () {
                  ref.invalidate(connectorsProvider);
                  ref.invalidate(companiesProvider);
                  context.pushReplacement(
                    '${Routes.connectors}/${pairing.connectorId}/link',
                  );
                }
              : null,
          child: Text(connected ? 'Choose companies' : 'Waiting for the PC...'),
        ),
        const SizedBox(height: 8),
        TextButton(
          onPressed: () => context.go(Routes.dashboard),
          child: const Text('I will finish this later'),
        ),
      ],
    );
  }
}

class _StatusHeader extends StatelessWidget {
  const _StatusHeader({required this.connector});

  final Connector? connector;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool online = connector?.online ?? false;
    final Color colour = online ? context.positiveColor : context.cautionColor;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colour.withOpacity(0.08),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: colour.withOpacity(0.25)),
      ),
      child: Row(
        children: <Widget>[
          if (online)
            Icon(Icons.check_circle, color: colour, size: 26)
          else
            SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.4, color: colour),
            ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  online ? 'Connected' : 'Waiting for your PC',
                  style: theme.textTheme.titleSmall?.copyWith(color: colour),
                ),
                const SizedBox(height: 2),
                Text(
                  online
                      ? 'Your PC is talking to TallyFlow.'
                      : 'This screen updates on its own once the connector '
                          'starts on that computer.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: context.mutedColor),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// A labelled value with a copy button, and a reveal toggle when sensitive.
///
/// Public because re-pairing shows the same two fields from a different
/// screen, and a second copy of this would be a second place for the
/// "reveal the secret" behaviour to drift.
class CopyField extends StatefulWidget {
  const CopyField({
    super.key,
    required this.label,
    required this.value,
    this.sensitive = false,
  });

  final String label;
  final String value;
  final bool sensitive;

  @override
  State<CopyField> createState() => _CopyFieldState();
}

class _CopyFieldState extends State<CopyField> {
  bool _revealed = false;

  @override
  void initState() {
    super.initState();
    _revealed = !widget.sensitive;
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: theme.colorScheme.outlineVariant),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  widget.label,
                  style: theme.textTheme.labelSmall?.copyWith(color: context.mutedColor),
                ),
                const SizedBox(height: 3),
                Text(
                  _revealed ? widget.value : '•' * 24,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    fontFamily: 'monospace',
                    letterSpacing: 0.3,
                  ),
                ),
              ],
            ),
          ),
          if (widget.sensitive)
            IconButton(
              tooltip: _revealed ? 'Hide' : 'Show',
              onPressed: () => setState(() => _revealed = !_revealed),
              icon: Icon(
                _revealed ? Icons.visibility_off_outlined : Icons.visibility_outlined,
                size: 20,
              ),
            ),
          IconButton(
            tooltip: 'Copy',
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: widget.value));
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('${widget.label} copied')),
                );
              }
            },
            icon: const Icon(Icons.copy, size: 20),
          ),
        ],
      ),
    );
  }
}
