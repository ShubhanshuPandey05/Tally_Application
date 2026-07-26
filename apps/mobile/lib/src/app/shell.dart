import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'router.dart';

/// The three places a user can be: their figures, their reports, their account.
///
/// Kept to three deliberately. The dashboard already answers the questions the
/// product exists for; a five-tab bar would just be Tally's menu tree wearing a
/// different coat.
class HomeShell extends StatelessWidget {
  const HomeShell({super.key, required this.location, required this.child});

  final String location;
  final Widget child;

  static const List<_Destination> _destinations = <_Destination>[
    _Destination(Routes.dashboard, Icons.dashboard_outlined, Icons.dashboard, 'Home'),
    _Destination(Routes.reports, Icons.receipt_long_outlined, Icons.receipt_long, 'Reports'),
    _Destination(Routes.settings, Icons.person_outline, Icons.person, 'Account'),
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
    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (int index) => context.go(_destinations[index].path),
        destinations: <Widget>[
          for (final _Destination destination in _destinations)
            NavigationDestination(
              icon: Icon(destination.icon),
              selectedIcon: Icon(destination.selectedIcon),
              label: destination.label,
            ),
        ],
      ),
    );
  }
}

class _Destination {
  const _Destination(this.path, this.icon, this.selectedIcon, this.label);

  final String path;
  final IconData icon;
  final IconData selectedIcon;
  final String label;
}
