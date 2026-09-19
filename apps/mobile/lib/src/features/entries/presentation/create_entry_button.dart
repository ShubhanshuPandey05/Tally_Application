import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../companies/application/company_providers.dart';
import '../application/entry_providers.dart';
import '../domain/entry_draft.dart';

/// Where the button sits above the floating navigation bar.
///
/// The bar is 54 high with 12 of padding under it, so this clears it by a few
/// pixels and no more — a button parked halfway up the screen reads as part of
/// the content rather than as the thing you press.
const double _fabBottomInset = 70;

/// Where to put the menu when the button's real position cannot be measured,
/// which only happens if it is opened before a frame has been laid out.
const Rect _fallbackAnchor = Rect.fromLTWH(0, 0, 56, 56);

/// The one thing in this app that writes, so it gets the one button that does.
///
/// It sits above the navigation bar rather than in the app bar because it is
/// the action somebody takes with a customer still standing in front of them,
/// and a thumb on a phone reaches the bottom right corner without the hand
/// moving.
///
/// **The menu is an overlay, not part of this widget.** It lived inside the
/// Scaffold's `floatingActionButton` slot once: that slot sizes itself to its
/// child, so an expanded column of five options made the "button" 300 pixels
/// tall, which shoved the button itself into the middle of the screen and
/// pushed every label off the left edge. A route of its own gets the whole
/// screen to lay out in, and the button below stays exactly where it was.
class CreateEntryButton extends ConsumerStatefulWidget {
  const CreateEntryButton({super.key});

  @override
  ConsumerState<CreateEntryButton> createState() => _CreateEntryButtonState();
}

class _CreateEntryButtonState extends ConsumerState<CreateEntryButton> {
  /// So the menu can be told where this button actually ended up.
  final GlobalKey _anchor = GlobalKey();

  /// Where the button is on screen right now.
  ///
  /// Measured rather than computed. The Scaffold positions a floating button
  /// against whatever navigation bar is present, so any arithmetic here would
  /// be a second copy of the shell's layout -- and it was wrong by exactly the
  /// height of the bar the first time it was tried.
  Rect get _anchorRect {
    final RenderObject? box = _anchor.currentContext?.findRenderObject();
    if (box is! RenderBox || !box.hasSize) {
      return _fallbackAnchor;
    }
    return box.localToGlobal(Offset.zero) & box.size;
  }

  Future<void> _pick(BuildContext context, WidgetRef ref) async {
    final EntryKind? kind = await Navigator.of(context).push<EntryKind>(
      entryMenuRoute(anchor: _anchorRect),
    );
    if (kind == null || !context.mounted) {
      return;
    }
    await _create(context, ref, kind);
  }

  Future<void> _create(BuildContext context, WidgetRef ref, EntryKind kind) async {
    final String? companyId = ref.read(activeCompanyIdResolvedProvider);
    final Object? result =
        await context.push<Object?>('${Routes.newEntry}?kind=${kind.wire}');
    if (!context.mounted || result is! EntryResult || companyId == null) {
      return;
    }

    if (result.queued) {
      // So the badge appears at once. A held entry that shows no sign of
      // itself until the next screen load is the failure the pending list
      // exists to prevent.
      ref.invalidate(pendingEntriesProvider(companyId));
    }

    final String message = result.queued
        ? 'Saved here. It will go to TallyPrime when your PC is back.'
        : result.awaitingApproval
            ? 'Sent. Approve it in TallyPrime to put it into your books.'
            : 'Saved in TallyPrime.';

    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          duration: const Duration(seconds: 6),
          action: result.queued
              ? SnackBarAction(
                  label: 'View',
                  onPressed: () => context.push(Routes.pendingEntries),
                )
              : null,
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    // Nothing to write to until a company is chosen, and a button that only
    // ever says "choose a company first" is one that trains people to ignore
    // it.
    if (ref.watch(activeCompanyIdResolvedProvider) == null) {
      return const SizedBox.shrink();
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: _fabBottomInset),
      child: FloatingActionButton(
        key: _anchor,
        heroTag: 'new-entry',
        onPressed: () => _pick(context, ref),
        tooltip: 'New entry',
        // Fixed black and white rather than scheme colours. This button sits
        // over a dashboard whose tiles are already carrying the palette, and
        // it has to read the same on all three themes.
        backgroundColor: Colors.white,
        foregroundColor: Colors.black,
        child: const Icon(Icons.add),
      ),
    );
  }
}

/// The menu on its own, so a test can open it without a signed-in account.
///
/// Its layout is the part that went wrong before and the part `flutter
/// analyze` cannot check, so it has to be reachable from a widget test.
Route<EntryKind> entryMenuRoute({Rect? anchor}) =>
    _FanRoute(anchor ?? _fallbackAnchor);

/// The menu, as a transparent route over whatever screen was showing.
class _FanRoute extends PopupRoute<EntryKind> {
  _FanRoute(this.anchor);

  /// The button's rectangle in screen coordinates. The close button is drawn
  /// exactly here, so nothing appears to jump when the menu opens.
  final Rect anchor;

  @override
  Color? get barrierColor => Colors.black.withOpacity(0.45);

  @override
  bool get barrierDismissible => true;

  @override
  String get barrierLabel => 'Close';

  @override
  Duration get transitionDuration => const Duration(milliseconds: 180);

  @override
  Widget buildPage(
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
  ) {
    return _Fan(animation: animation, anchor: anchor);
  }
}

class _Fan extends StatelessWidget {
  const _Fan({required this.animation, required this.anchor});

  final Animation<double> animation;
  final Rect anchor;

  @override
  Widget build(BuildContext context) {
    // `LayoutBuilder`, not `MediaQuery.sizeOf`. The two disagree -- a popup
    // route's MediaQuery reports the window rather than the box this Stack is
    // actually being laid out in, which put the whole menu 400 pixels off the
    // left edge. The constraints are the box the anchor's coordinates share.
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        // Measured from the bottom, because that is the edge the button is
        // pinned to and the one a keyboard or a safe area moves.
        final double fromBottom = constraints.maxHeight - anchor.bottom;
        final double fromRight = constraints.maxWidth - anchor.right;
        return _fan(context, fromBottom, fromRight);
      },
    );
  }

  Widget _fan(BuildContext context, double fromBottom, double fromRight) {
    return Stack(
      children: <Widget>[
        Positioned(
          right: fromRight,
          // Directly above the button, which stays put underneath.
          bottom: fromBottom + anchor.height + 12,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              for (final EntryKind kind in EntryKind.values)
                _Option(
                  kind: kind,
                  animation: animation,
                  // Staggered from the bottom of the list upwards, so the fan
                  // reads as coming out of the button that was pressed.
                  order: EntryKind.values.length -
                      EntryKind.values.indexOf(kind) -
                      1,
                  onTap: () => Navigator.of(context).pop(kind),
                ),
            ],
          ),
        ),
        Positioned(
          right: fromRight,
          bottom: fromBottom,
          child: FadeTransition(
            opacity: animation,
            child: FloatingActionButton(
              heroTag: 'close-entry-menu',
              onPressed: () => Navigator.of(context).pop(),
              tooltip: 'Close',
              backgroundColor: Colors.white,
              foregroundColor: Colors.black,
              child: const Icon(Icons.close),
            ),
          ),
        ),
      ],
    );
  }
}

/// One kind in the fan: a label beside a small round button.
///
/// The label is always visible rather than a tooltip. "Receipt" and "Payment"
/// are opposite actions on the same money, and an icon-only choice between
/// them is one somebody will get wrong in a hurry.
class _Option extends StatelessWidget {
  const _Option({
    required this.kind,
    required this.animation,
    required this.order,
    required this.onTap,
  });

  final EntryKind kind;
  final Animation<double> animation;
  final int order;
  final VoidCallback onTap;

  static const Map<EntryKind, IconData> _icons = <EntryKind, IconData>{
    EntryKind.receipt: Icons.south_west_rounded,
    EntryKind.payment: Icons.north_east_rounded,
    EntryKind.sales: Icons.receipt_long_rounded,
    EntryKind.salesOrder: Icons.assignment_turned_in_rounded,
    EntryKind.purchaseOrder: Icons.shopping_cart_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final Animation<double> step = CurvedAnimation(
      parent: animation,
      curve: Interval((order * 0.07).clamp(0.0, 0.6), 1, curve: Curves.easeOut),
    );

    return FadeTransition(
      opacity: step,
      child: SlideTransition(
        // Slides up into place rather than resizing: a SizeTransition here
        // changed each row's height mid-animation, so the buttons drifted
        // diagonally instead of stacking.
        position: Tween<Offset>(
          begin: const Offset(0, 0.4),
          end: Offset.zero,
        ).animate(step),
        child: Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.end,
            children: <Widget>[
              Material(
                color: Colors.white,
                borderRadius: BorderRadius.circular(8),
                elevation: 2,
                child: InkWell(
                  onTap: onTap,
                  borderRadius: BorderRadius.circular(8),
                  child: Padding(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    child: Text(
                      kind.label,
                      style: const TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w500,
                        color: Colors.black,
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                height: 44,
                width: 44,
                child: FloatingActionButton.small(
                  key: ValueKey<String>('entry-${kind.wire}'),
                  heroTag: 'entry-${kind.wire}',
                  onPressed: onTap,
                  backgroundColor: Colors.black,
                  foregroundColor: Colors.white,
                  elevation: 2,
                  child: Icon(_icons[kind], size: 19),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
