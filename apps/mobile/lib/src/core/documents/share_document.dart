import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

import '../widgets/states.dart';

/// Shows a built document, and hands it to the platform's share sheet from
/// there.
///
/// One helper rather than a call at each site, because the failure handling is
/// the part worth sharing: building a PDF touches platform code, and on a
/// device where that goes wrong the owner must get a line of text rather than a
/// button that silently does nothing. Same rule as everywhere else in this app
/// -- fail soft, and say what happened.
///
/// The preview comes first. Sharing used to go straight to the share sheet,
/// which meant the first time anybody saw the document was after it had
/// reached the customer -- and a bill is the one thing worth reading before it
/// is sent.
class ShareDocument {
  const ShareDocument._();

  static Future<void> preview(
    BuildContext context, {
    required Future<List<int>> Function() build,
    required String fileName,
    String? subject,
  }) =>
      Navigator.of(context, rootNavigator: true).push<void>(
        MaterialPageRoute<void>(
          builder: (BuildContext context) => _DocumentPreview(
            build: build,
            fileName: fileName,
            subject: subject,
          ),
        ),
      );
}

class _DocumentPreview extends StatefulWidget {
  const _DocumentPreview({
    required this.build,
    required this.fileName,
    this.subject,
  });

  final Future<List<int>> Function() build;
  final String fileName;
  final String? subject;

  @override
  State<_DocumentPreview> createState() => _DocumentPreviewState();
}

class _DocumentPreviewState extends State<_DocumentPreview> {
  /// Built once. What is shared must be the bytes that were looked at, not a
  /// second build that happens to come out the same.
  late final Future<Uint8List> _bytes =
      widget.build().then((List<int> bytes) => Uint8List.fromList(bytes));

  Future<void> _share(Uint8List bytes) async {
    final ScaffoldMessengerState messenger = ScaffoldMessenger.of(context);
    try {
      await Printing.sharePdf(
        bytes: bytes,
        filename: '${widget.fileName}.pdf',
        subject: widget.subject,
      );
    } catch (error) {
      messenger.showSnackBar(
        const SnackBar(content: Text('Could not share the document.')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Uint8List>(
      future: _bytes,
      builder: (BuildContext context, AsyncSnapshot<Uint8List> snapshot) {
        final Uint8List? bytes = snapshot.data;
        return Scaffold(
          appBar: AppBar(
            title: const Text('Preview'),
            actions: <Widget>[
              // Only once there is a document: a share button over a spinner
              // sends nothing.
              if (bytes != null)
                IconButton(
                  icon: const Icon(Icons.ios_share),
                  tooltip: 'Share',
                  onPressed: () => _share(bytes),
                ),
            ],
          ),
          body: snapshot.hasError
              ? const EmptyState(
                  icon: Icons.error_outline,
                  title: 'Could not prepare the document',
                  message: 'Go back and try again.',
                )
              : bytes == null
                  ? const Center(child: CircularProgressIndicator())
                  : PdfPreview(
                      build: (_) => bytes,
                      // The app bar carries the one action there is. The
                      // widget's own bar would add paper-size and orientation
                      // switches to a document whose layout is already fixed.
                      useActions: false,
                      canChangePageFormat: false,
                      canChangeOrientation: false,
                      canDebug: false,
                      pdfFileName: '${widget.fileName}.pdf',
                    ),
        );
      },
    );
  }
}
