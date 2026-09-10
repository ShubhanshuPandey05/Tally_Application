import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

/// Hands a built document to the platform's own share sheet.
///
/// One helper rather than a call at each site, because the failure handling is
/// the part worth sharing: building a PDF touches platform code, and on a
/// device where that goes wrong the owner must get a line of text rather than a
/// button that silently does nothing. Same rule as everywhere else in this app
/// -- fail soft, and say what happened.
class ShareDocument {
  const ShareDocument._();

  static Future<void> share(
    BuildContext context, {
    required Future<List<int>> Function() build,
    required String fileName,
    String? subject,
  }) async {
    final ScaffoldMessengerState messenger = ScaffoldMessenger.of(context);
    try {
      final List<int> bytes = await build();
      await Printing.sharePdf(
        bytes: Uint8List.fromList(bytes),
        filename: '$fileName.pdf',
        subject: subject,
      );
    } catch (error) {
      messenger.showSnackBar(
        const SnackBar(
          content: Text('Could not prepare the document to share.'),
        ),
      );
    }
  }
}
