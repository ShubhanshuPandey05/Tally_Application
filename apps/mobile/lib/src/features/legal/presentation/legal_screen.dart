import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../domain/legal_documents.dart';

/// One legal document, as plain readable text.
///
/// Reachable signed out, from the sign-up screen, so it depends on nothing
/// but the document it is given: no account, no company, no network.
class LegalScreen extends StatelessWidget {
  const LegalScreen({super.key, required this.document});

  final LegalDocument document;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? body = theme.textTheme.bodyMedium?.copyWith(height: 1.5);

    return Scaffold(
      appBar: AppBar(title: Text(document.title)),
      body: SafeArea(
        top: false,
        child: ContentPane(
          child: SelectionArea(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
              children: <Widget>[
                Text(
                  'Last updated ${document.updated}',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: context.mutedColor),
                ),
                const SizedBox(height: 12),
                Text(document.intro, style: body),
                for (final LegalSection section
                    in document.sections) ...<Widget>[
                  const SizedBox(height: 22),
                  Text(section.heading, style: theme.textTheme.titleSmall),
                  const SizedBox(height: 6),
                  for (final String paragraph in section.paragraphs)
                    _Paragraph(text: paragraph, style: body),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Paragraph extends StatelessWidget {
  const _Paragraph({required this.text, required this.style});

  final String text;
  final TextStyle? style;

  static const String _bullet = '• ';

  @override
  Widget build(BuildContext context) {
    if (!text.startsWith(_bullet)) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(text, style: style),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(left: 4, bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('•  ', style: style),
          Expanded(child: Text(text.substring(_bullet.length), style: style)),
        ],
      ),
    );
  }
}

/// "Terms of Use · Privacy Policy" as two small links, for the auth screens.
///
/// Pushed rather than gone to, so Back returns to a half-filled sign-up form
/// instead of throwing it away.
class LegalLinks extends StatelessWidget {
  const LegalLinks({super.key, this.lead});

  /// A line above the links, such as what creating an account agrees to.
  final String? lead;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? small =
        theme.textTheme.bodySmall?.copyWith(color: context.mutedColor);
    final ButtonStyle link = TextButton.styleFrom(
      visualDensity: VisualDensity.compact,
      padding: const EdgeInsets.symmetric(horizontal: 6),
      textStyle:
          theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600),
    );

    return Column(
      children: <Widget>[
        if (lead != null)
          Text(lead!, textAlign: TextAlign.center, style: small),
        Wrap(
          alignment: WrapAlignment.center,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: <Widget>[
            TextButton(
              style: link,
              onPressed: () => context.push(Routes.terms),
              child: const Text('Terms of Use'),
            ),
            Text('·', style: small),
            TextButton(
              style: link,
              onPressed: () => context.push(Routes.privacy),
              child: const Text('Privacy Policy'),
            ),
          ],
        ),
      ],
    );
  }
}
