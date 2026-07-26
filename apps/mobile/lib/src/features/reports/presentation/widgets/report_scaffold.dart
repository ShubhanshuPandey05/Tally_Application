import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/model/freshness.dart';
import '../../../../core/widgets/freshness_banner.dart';
import '../../../../core/widgets/states.dart';

/// The frame every report screen shares.
///
/// Extracted so that all five reports handle loading, failure, staleness and
/// pull-to-refresh identically. The alternative -- each screen doing it its own
/// way -- is how one report ends up quietly rendering month-old figures with no
/// banner, and nobody notices until an owner acts on them.
class ReportScaffold<T> extends StatelessWidget {
  const ReportScaffold({
    super.key,
    required this.title,
    required this.state,
    required this.onRefresh,
    required this.builder,
    this.subtitle,
    this.actions,
    this.bottom,
    this.emptyBuilder,
  });

  final String title;
  final String? subtitle;
  final AsyncValue<Fresh<T>> state;
  final Future<void> Function() onRefresh;
  final List<Widget> Function(BuildContext context, T data) builder;
  final List<Widget>? actions;
  final PreferredSizeWidget? bottom;

  /// Rendered instead of [builder] when the report came back with nothing --
  /// distinct from a failure, and worded differently.
  final Widget Function(BuildContext context)? emptyBuilder;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: subtitle == null
            ? Text(title)
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(title),
                  Text(
                    subtitle!,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
        actions: actions,
        bottom: bottom,
      ),
      body: RefreshIndicator(
        onRefresh: onRefresh,
        child: state.when(
          skipLoadingOnRefresh: true,
          loading: () => const _ReportSkeleton(),
          error: (Object error, StackTrace stack) => ListView(
            children: <Widget>[
              SizedBox(height: MediaQuery.sizeOf(context).height * 0.18),
              ErrorState(error: error, onRetry: onRefresh),
            ],
          ),
          data: (Fresh<T> result) {
            final List<Widget> content = builder(context, result.data);
            return ListView(
              padding: const EdgeInsets.only(bottom: 32),
              children: <Widget>[
                FreshnessBanner(
                  freshness: result.freshness,
                  onRefresh: onRefresh,
                  dense: true,
                ),
                if (content.isEmpty && emptyBuilder != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 48),
                    child: emptyBuilder!(context),
                  )
                else
                  ...content,
              ],
            );
          },
        ),
      ),
    );
  }
}

class _ReportSkeleton extends StatelessWidget {
  const _ReportSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        const SkeletonBox(height: 84, radius: 16),
        const SizedBox(height: 16),
        for (int i = 0; i < 8; i++) ...<Widget>[
          const SkeletonBox(height: 52, radius: 12),
          const SizedBox(height: 10),
        ],
      ],
    );
  }
}
