import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/documents/report_table_document.dart';
import '../../../../core/documents/share_document.dart';
import '../../../companies/application/company_providers.dart';
import '../../../companies/domain/company.dart';
import '../../../team/application/team_providers.dart';
import '../../../team/domain/access_policy.dart';

/// Whether the signed-in person may share reports from the open company.
///
/// Hidden until their access has loaded, so a person whose admin switched
/// sharing off never sees the button flash up first. If the read fails,
/// [myAccessProvider] answers "everything", the same as every other menu.
bool shareAllowed(WidgetRef ref) {
  final String? companyId = ref.watch(activeCompanyIdResolvedProvider);
  if (companyId == null) return false;
  final AccessPolicy? policy = ref.watch(myAccessProvider(companyId)).valueOrNull;
  return policy?.share ?? false;
}

/// The Share button at the top right of a report.
///
/// [table] is called on tap, not on every rebuild: a day book can hold
/// thousands of rows, and formatting them each time the screen repaints would
/// be work nobody asked for.
class ReportShareButton extends ConsumerWidget {
  const ReportShareButton({super.key, required this.table});

  final ReportTable Function() table;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (!shareAllowed(ref)) return const SizedBox.shrink();
    final Company? company = ref.watch(activeCompanyProvider).valueOrNull;
    return IconButton(
      key: const ValueKey<String>('report-share'),
      icon: const Icon(Icons.ios_share),
      tooltip: 'Preview and share',
      onPressed: () {
        final ReportTableDocument document = ReportTableDocument(
          table: table(),
          companyName: company?.name ?? '',
          generatedAt: DateTime.now(),
        );
        ShareDocument.preview(
          context,
          build: document.build,
          fileName: document.fileName,
          subject: document.table.title,
        );
      },
    );
  }
}
