import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../../app/theme.dart';
import '../../../core/network/api_exception.dart';
import '../../companies/application/company_providers.dart';
import '../application/connector_providers.dart';
import '../data/connector_repository.dart';
import '../domain/connector.dart';

/// Pairing a Tally PC by pointing the camera at it.
///
/// This replaces the step every new customer got stuck on: reading a connector
/// id and a 43-character secret off this screen and typing both into a Windows
/// keyboard across the room. Now the PC shows a code, the phone reads it, and
/// the credential travels between the two over TLS without a person in the
/// middle of it.
///
/// Two jobs, chosen by [connectorId]: null adopts the machine as a new Tally
/// PC; a value re-points an existing one at it. The second is the important
/// one to keep as *this* screen rather than "add a PC again" -- companies
/// belong to one connector, so a second row for the same books gives the shop
/// two half-synced copies of every company.
class ScanConnectorScreen extends ConsumerStatefulWidget {
  const ScanConnectorScreen({
    super.key,
    this.connectorId,
    this.suggestedName = 'Shop PC',
  });

  /// The connector being re-paired, or null when adopting a new machine.
  final String? connectorId;
  final String suggestedName;

  @override
  ConsumerState<ScanConnectorScreen> createState() => _ScanConnectorScreenState();
}

class _ScanConnectorScreenState extends ConsumerState<ScanConnectorScreen> {
  final MobileScannerController _controller = MobileScannerController(
    // Only what we are looking for. A camera pointed at a shop counter finds
    // barcodes on stock, and decoding formats we can never act on is work that
    // only produces things to ignore.
    formats: const <BarcodeFormat>[BarcodeFormat.qrCode],
    detectionSpeed: DetectionSpeed.noDuplicates,
  );

  /// Set the moment a code is accepted, and never cleared while a claim is in
  /// flight. The detector fires many times a second; without this the same
  /// code would be claimed repeatedly, and every attempt after the first would
  /// fail with "that code is no longer valid" over a pairing that worked.
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (_busy) return;

    for (final Barcode barcode in capture.barcodes) {
      final PairingCode? scanned = PairingCode.tryParse(barcode.rawValue ?? '');
      // Silently ignored, on purpose. The camera sees every barcode in front of
      // it, and an error toast for each packet of biscuits behind the monitor
      // would bury the one message that matters.
      if (scanned == null) continue;

      setState(() {
        _busy = true;
        _error = null;
      });
      await _claim(scanned);
      return;
    }
  }

  Future<void> _claim(PairingCode scanned) async {
    final ConnectorRepository repository = ref.read(connectorRepositoryProvider);
    try {
      final ClaimPreview preview = await repository.previewClaim(scanned.code);
      if (!mounted) return;

      // Stopped rather than left running behind the sheet: a camera preview
      // under a modal is a torch pointed at the ceiling and a flat battery.
      await _controller.stop();
      final bool confirmed = await _confirm(preview) ?? false;
      if (!mounted) return;
      if (!confirmed) {
        setState(() => _busy = false);
        await _controller.start();
        return;
      }

      final Connector connector = widget.connectorId == null
          ? await repository.claimNew(scanned.code, name: preview.hostname.isEmpty
              ? widget.suggestedName
              : preview.hostname)
          : await repository.claimExisting(widget.connectorId!, scanned.code);

      ref.invalidate(connectorsProvider);
      ref.invalidate(companiesProvider);
      if (mounted) Navigator.of(context).pop(connector);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.message;
      });
      // Back to scanning: the usual cause is a code that expired while the
      // owner walked across the shop, and the PC has already drawn a new one.
      await _controller.start();
    }
  }

  Future<bool?> _confirm(ClaimPreview preview) {
    return showModalBottomSheet<bool>(
      context: context,
      isDismissible: false,
      enableDrag: false,
      builder: (BuildContext context) => _ConfirmSheet(
        preview: preview,
        repairing: widget.connectorId != null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.connectorId == null ? 'Scan your Tally PC' : 'Pair again'),
        actions: <Widget>[
          IconButton(
            tooltip: 'Torch',
            onPressed: () => _controller.toggleTorch(),
            icon: const Icon(Icons.flashlight_on_outlined),
          ),
        ],
      ),
      body: Column(
        children: <Widget>[
          Expanded(
            child: Stack(
              fit: StackFit.expand,
              children: <Widget>[
                MobileScanner(
                  controller: _controller,
                  onDetect: _onDetect,
                  // Shown when the camera itself cannot run -- permission
                  // refused, or a device without one. The fallback is real, so
                  // this is a signpost rather than a dead end.
                  errorBuilder: (BuildContext context, MobileScannerException error,
                          Widget? child) =>
                      _CameraUnavailable(error: error),
                ),
                const _ScanFrame(),
                if (_busy)
                  const ColoredBox(
                    color: Color(0x66000000),
                    child: Center(child: CircularProgressIndicator()),
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 26),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Text(
                  'On the Tally PC, open TallyFlow Connector',
                  style: theme.textTheme.titleSmall,
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 6),
                Text(
                  'Point the camera at the code on that screen. Nothing is typed '
                  'and no key is shown to anybody.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: context.mutedColor),
                  textAlign: TextAlign.center,
                ),
                if (_error != null) ...<Widget>[
                  const SizedBox(height: 12),
                  Text(
                    _error!,
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(color: context.negativeColor),
                    textAlign: TextAlign.center,
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The confirmation between seeing a code and granting a machine access.
class _ConfirmSheet extends StatelessWidget {
  const _ConfirmSheet({required this.preview, required this.repairing});

  final ClaimPreview preview;
  final bool repairing;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(Icons.computer, color: theme.colorScheme.primary),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(preview.label, style: theme.textTheme.titleMedium),
                      if (preview.os.isNotEmpty)
                        Text(
                          preview.connectorVersion.isEmpty
                              ? preview.os
                              : '${preview.os} · connector ${preview.connectorVersion}',
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: context.mutedColor),
                        ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 18),
            Text(
              repairing
                  ? 'This computer will take over the connection, keeping its '
                      'companies and everything already synced.'
                  : 'This computer will be able to read the companies you '
                      'connect to it. TallyFlow never writes to your books.',
              style: theme.textTheme.bodyMedium
                  ?.copyWith(color: context.mutedColor),
            ),
            const SizedBox(height: 22),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(repairing ? 'Pair this computer again' : 'Connect this computer'),
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('Not this one'),
            ),
          ],
        ),
      ),
    );
  }
}

/// A cut-out to aim with. Purely to say where to point the phone -- detection
/// is not restricted to it, because a code half outside a drawn box that the
/// camera can read anyway should just work.
class _ScanFrame extends StatelessWidget {
  const _ScanFrame();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        width: 240,
        height: 240,
        decoration: BoxDecoration(
          border: Border.all(color: Colors.white70, width: 2),
          borderRadius: BorderRadius.circular(20),
        ),
      ),
    );
  }
}

class _CameraUnavailable extends StatelessWidget {
  const _CameraUnavailable({required this.error});

  final MobileScannerException error;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool denied =
        error.errorCode == MobileScannerErrorCode.permissionDenied;
    return ColoredBox(
      color: theme.colorScheme.surface,
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            Icon(Icons.no_photography_outlined, size: 40, color: context.mutedColor),
            const SizedBox(height: 14),
            Text(
              denied ? 'TallyFlow cannot use the camera' : 'The camera is not available',
              style: theme.textTheme.titleSmall,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              denied
                  ? 'Allow camera access in your phone settings, or go back and '
                      'enter the pairing details by hand instead.'
                  : 'Go back and enter the pairing details by hand instead.',
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}
