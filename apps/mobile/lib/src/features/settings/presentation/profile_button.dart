import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';

/// The way to Profile, at the top left of every screen the bottom bar leads
/// to. The same person icon the tab it replaced wore.
///
/// Profile used to be a tab. It is on every one of those screens instead
/// because it is also the only way to sign out, and an account with no company
/// yet -- which has nothing else to tap -- still has to be able to reach it.
class ProfileButton extends StatelessWidget {
  const ProfileButton({super.key});

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: 'Profile',
      onPressed: () => context.push(Routes.settings),
      icon: Container(
        width: 32,
        height: 32,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        ),
        child: const Icon(Icons.person_rounded, size: 18),
      ),
    );
  }
}
