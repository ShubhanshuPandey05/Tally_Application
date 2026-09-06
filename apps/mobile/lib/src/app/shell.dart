import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../core/layout/adaptive.dart';
import '../core/widgets/primitives.dart';
import 'router.dart';
import 'theme.dart';

/// The three places a user can be: their figures, their reports, their profile.
///
/// Kept to three deliberately. The dashboard already answers the questions the
/// product exists for; a five-tab bar would just be Tally's menu tree wearing a
/// different coat.
///
/// Two shapes, chosen by how much width there is:
///
/// * **Phone** — a pill floating over the content. It keeps the page a single
///   uninterrupted surface, and on a tall phone it puts the three destinations
///   inside the thumb's arc rather than in the corner of the screen.
/// * **Wide window** — a rail down the left. The same product served as a web
///   build lands in whatever window somebody dragged open, and a bottom bar
///   there is a phone control stranded at the bottom of a desktop.
class HomeShell extends StatelessWidget {
  const HomeShell({super.key, required this.location, required this.child});

  final String location;
  final Widget child;

  static const List<_Destination> _destinations = <_Destination>[
    _Destination(Routes.dashboard, Icons.grid_view_rounded, 'Home'),
    _Destination(Routes.reports, Icons.receipt_long_rounded, 'Reports'),
    _Destination(Routes.settings, Icons.person_rounded, 'Profile'),
  ];

  int get _index {
    for (int i = _destinations.length - 1; i >= 0; i--) {
      if (location == _destinations[i].path ||
          (location.startsWith(_destinations[i].path) &&
              _destinations[i].path != Routes.dashboard)) {
        return i;
      }
    }
    return 0;
  }

  @override
  Widget build(BuildContext context) {
    void go(int index) => context.go(_destinations[index].path);

    if (context.usesRail) {
      return Scaffold(
        body: Row(
          children: <Widget>[
            _Rail(
              index: _index,
              destinations: _destinations,
              onSelected: go,
              expanded: context.railIsExpanded,
            ),
            Expanded(child: child),
          ],
        ),
      );
    }

    return Scaffold(
      // The bar floats, so the content runs underneath it; screens leave room
      // with `HomeShell.contentInset`.
      extendBody: true,
      body: child,
      bottomNavigationBar: _FloatingBar(
        index: _index,
        destinations: _destinations,
        onSelected: go,
      ),
    );
  }

  /// What a scrollable screen must leave clear at the bottom so its last row is
  /// not sitting under the floating bar.
  ///
  /// Width-dependent, because on a rail layout there is no bar to clear and a
  /// fixed 96px would just be a gap at the end of every list.
  static double contentInset(BuildContext context) =>
      context.usesRail ? 24 : 84;
}

/// The wide-window navigation: a rail down the left-hand side.
class _Rail extends StatelessWidget {
  const _Rail({
    required this.index,
    required this.destinations,
    required this.onSelected,
    required this.expanded,
  });

  final int index;
  final List<_Destination> destinations;
  final ValueChanged<int> onSelected;
  final bool expanded;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Container(
      width: expanded ? 232 : 88,
      decoration: BoxDecoration(
        color: theme.scaffoldBackgroundColor,
        border: Border(right: BorderSide(color: theme.colorScheme.outlineVariant)),
      ),
      child: SafeArea(
        right: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Padding(
              padding: EdgeInsets.fromLTRB(expanded ? 20 : 0, 20, 0, 24),
              child: Row(
                mainAxisAlignment:
                    expanded ? MainAxisAlignment.start : MainAxisAlignment.center,
                children: <Widget>[
                  const BrandMark(size: 38),
                  if (expanded) ...<Widget>[
                    const SizedBox(width: 12),
                    Text('TallyFlow', style: theme.textTheme.titleMedium),
                  ],
                ],
              ),
            ),
            for (int i = 0; i < destinations.length; i++)
              Padding(
                padding: EdgeInsets.fromLTRB(expanded ? 12 : 16, 0, 12, 8),
                child: _RailItem(
                  destination: destinations[i],
                  selected: i == index,
                  expanded: expanded,
                  onTap: () => onSelected(i),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _RailItem extends StatelessWidget {
  const _RailItem({
    required this.destination,
    required this.selected,
    required this.expanded,
    required this.onTap,
  });

  final _Destination destination;
  final bool selected;
  final bool expanded;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color foreground =
        selected ? theme.colorScheme.onPrimary : theme.colorScheme.onSurface;

    return Material(
      color: selected ? _selectedFill(theme) : Colors.transparent,
      shape: const StadiumBorder(),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: SizedBox(
          height: 44,
          child: Row(
            mainAxisAlignment:
                expanded ? MainAxisAlignment.start : MainAxisAlignment.center,
            children: <Widget>[
              if (expanded) const SizedBox(width: 16),
              Icon(
                destination.icon,
                size: 20,
                color: selected ? foreground : context.mutedColor,
              ),
              if (expanded) ...<Widget>[
                const SizedBox(width: 12),
                Text(
                  destination.label,
                  style: theme.textTheme.labelLarge?.copyWith(color: foreground),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// The phone navigation: a pill floating over the content.
class _FloatingBar extends StatelessWidget {
  const _FloatingBar({
    required this.index,
    required this.destinations,
    required this.onSelected,
  });

  final int index;
  final List<_Destination> destinations;
  final ValueChanged<int> onSelected;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool isLight = theme.brightness == Brightness.light;

    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
        child: Container(
          height: 54,
          padding: const EdgeInsets.all(5),
          decoration: BoxDecoration(
            // The card colour, not a literal: on the dim and dark skins a
            // hard-coded near-black bar would sit on a near-black page and stop
            // reading as a floating object at all.
            color: theme.cardTheme.color,
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: theme.colorScheme.outlineVariant),
            boxShadow: <BoxShadow>[
              BoxShadow(
                color: Colors.black.withOpacity(isLight ? 0.07 : 0.4),
                blurRadius: 24,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: Row(
            children: <Widget>[
              for (int i = 0; i < destinations.length; i++)
                Expanded(
                  child: _BarItem(
                    destination: destinations[i],
                    selected: i == index,
                    onTap: () => onSelected(i),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _BarItem extends StatelessWidget {
  const _BarItem({
    required this.destination,
    required this.selected,
    required this.onTap,
  });

  final _Destination destination;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Color foreground =
        selected ? theme.colorScheme.onPrimary : context.mutedColor;

    // The selected item is a filled pill with its label beside the icon; the
    // others are icon-only. Showing three labels at once spends the width on
    // words the user has already read, and the pill is what makes the current
    // place obvious at a glance.
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: selected ? _selectedFill(theme) : Colors.transparent,
          borderRadius: BorderRadius.circular(999),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(destination.icon, size: 20, color: foreground),
            if (selected) ...<Widget>[
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  destination.label,
                  maxLines: 1,
                  overflow: TextOverflow.clip,
                  softWrap: false,
                  style: theme.textTheme.labelLarge?.copyWith(color: foreground),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// The fill behind whichever destination you are on.
///
/// Ink on a light page, white on a dark one — the same inversion the primary
/// button makes, so "selected" reads the same way wherever it appears.
Color _selectedFill(ThemeData theme) =>
    theme.brightness == Brightness.light ? AppTheme.ink : Colors.white;

class _Destination {
  const _Destination(this.path, this.icon, this.label);

  final String path;
  final IconData icon;
  final String label;
}
