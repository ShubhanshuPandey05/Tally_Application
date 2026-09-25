import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../companies/application/company_providers.dart';
import '../application/entry_providers.dart';
import '../domain/entry_draft.dart';

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

  Future<void> _create(
      BuildContext context, WidgetRef ref, EntryKind kind) async {
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

    // No padding of its own. The Scaffold already lifts a floating button
    // clear of the navigation bar by its standard margin; the 70 that used to
    // be added on top left it hanging well above the bar instead of just over
    // the Profile button.
    return FloatingActionButton(
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
  Duration get transitionDuration => const Duration(milliseconds: 340);

  @override
  Duration get reverseTransitionDuration => const Duration(milliseconds: 260);

  @override
  Widget buildPage(
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
  ) {
    return _Fan(animation: animation, anchor: anchor);
  }
}

/// One ring, evenly spaced from straight up (90 degrees) to straight left
/// (180), in the order [EntryKind] lists them.
///
/// Each name sits just *outside* its button, on the ray from the close
/// button, the way numbers sit on a clock face. Names under the buttons made
/// every option too tall for one ring -- it needed a radius of over 300
/// pixels, off the side of a phone -- and a second ring at the same angles
/// read as a scattered grid rather than a disc.
const double _radius = 180;
const double _firstAngle = 90;
const double _step = 18;

const double _buttonSize = 44;

/// Room between a button's edge and its name.
const double _labelGap = 5;

/// The widest a name may be before it is scaled down to fit. One line always.
const double _labelWidth = 76;
const double _labelHeight = 18;

/// How far the disc turns while it opens or closes.
const double _spin = math.pi / 2;

class _Fan extends StatelessWidget {
  const _Fan({required this.animation, required this.anchor});

  final Animation<double> animation;
  final Rect anchor;

  @override
  Widget build(BuildContext context) {
    // The anchor is in the same coordinates as this full-screen route, so
    // the disc is centred on the button directly. A popup route's MediaQuery
    // once disagreed with the box actually being laid out in and put the
    // whole menu 400 pixels off the left edge -- nothing here reads it.
    final Offset centre = anchor.center;
    // A narrow or short window shrinks the ring rather than pushing the
    // left-hand names off the edge.
    const double across =
        _radius + _buttonSize / 2 + _labelGap + _labelWidth + 8;
    const double up = _radius + _buttonSize / 2 + _labelGap + _labelHeight + 8;
    final double scale =
        math.min(1, math.min(centre.dx / across, centre.dy / up));

    return AnimatedBuilder(
      animation: animation,
      builder: (BuildContext context, _) {
        final double t = Curves.easeOutCubic.transform(animation.value);
        // One disc turning clockwise the whole time: it swings in from below
        // on the way open and carries on round the same way to close, rather
        // than rewinding back the way it came.
        final bool closing = animation.status == AnimationStatus.reverse;
        final double turn = (closing ? -1 : 1) * _spin * (1 - t);
        final double opacity = t.clamp(0.0, 1.0);

        return Stack(
          children: <Widget>[
            for (final (int i, EntryKind kind) in EntryKind.values.indexed)
              ..._placed(context, kind, i, centre, scale, turn, opacity),
            Positioned(
              left: anchor.left,
              top: anchor.top,
              child: Opacity(
                opacity: opacity,
                // The plus turns into the cross, the same way the disc turns.
                child: Transform.rotate(
                  angle: turn,
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
            ),
          ],
        );
      },
    );
  }

  List<Widget> _placed(
    BuildContext context,
    EntryKind kind,
    int index,
    Offset centre,
    double scale,
    double turn,
    double opacity,
  ) {
    final double angle = (_firstAngle + _step * index) * math.pi / 180 + turn;
    // Screen y grows downwards, hence the minus: 90 degrees is up.
    final Offset ray = Offset(math.cos(angle), -math.sin(angle));
    final Offset at = centre + ray * (_radius * scale);

    // The name's anchor: just past the button's edge along the same ray. The
    // button's square extent along the ray, not its radius, so a name on a
    // diagonal clears the button's corner rather than tucking under it.
    final double reach = _buttonSize / 2 / math.max(ray.dx.abs(), ray.dy.abs());
    final Offset nameAt = at + ray * (reach + _labelGap);

    void pick() => Navigator.of(context).pop(kind);

    return <Widget>[
      Positioned(
        left: at.dx - _buttonSize / 2,
        top: at.dy - _buttonSize / 2,
        child: Opacity(
          opacity: opacity,
          child: _OptionButton(kind: kind, onTap: pick),
        ),
      ),
      Positioned(
        left: nameAt.dx,
        top: nameAt.dy,
        // Shifted by its own size so the side facing the button sits on the
        // anchor: above the button at the top of the ring, to its left at the
        // end. Measured from the name's real width, so a short name sits as
        // close as a long one. Outermost, so taps land where the name is
        // drawn: a box inside the shift still accepts taps only where the
        // name was *before* it moved.
        child: FractionalTranslation(
          translation: Offset(-(1 - ray.dx) / 2, -(1 - ray.dy) / 2),
          child: Opacity(
            opacity: opacity,
            child: _OptionLabel(kind: kind, onTap: pick),
          ),
        ),
      ),
    ];
  }
}

/// A kind's round button on the disc.
class _OptionButton extends StatelessWidget {
  const _OptionButton({required this.kind, required this.onTap});

  final EntryKind kind;
  final VoidCallback onTap;

  static const Map<EntryKind, IconData> _icons = <EntryKind, IconData>{
    EntryKind.receipt: Icons.south_west_rounded,
    EntryKind.payment: Icons.north_east_rounded,
    EntryKind.sales: Icons.receipt_long_rounded,
    EntryKind.purchase: Icons.inventory_2_rounded,
    EntryKind.salesOrder: Icons.assignment_turned_in_rounded,
    EntryKind.purchaseOrder: Icons.shopping_cart_rounded,
  };

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: _buttonSize,
      width: _buttonSize,
      child: FloatingActionButton.small(
        key: ValueKey<String>('entry-${kind.wire}'),
        heroTag: 'entry-${kind.wire}',
        onPressed: onTap,
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        elevation: 2,
        child: Icon(_icons[kind], size: 19),
      ),
    );
  }
}

/// A kind's name, beside its button.
///
/// Always visible rather than a tooltip. "Receipt" and "Payment" are opposite
/// actions on the same money, and an icon-only choice between them is one
/// somebody will get wrong in a hurry. Tappable too, since it is the part
/// people read.
class _OptionLabel extends StatelessWidget {
  const _OptionLabel({required this.kind, required this.onTap});

  final EntryKind kind;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(6),
      elevation: 2,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          // One line, always: a name that wraps grows taller than the ring
          // was sized for, and neighbours overlap.
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: _labelWidth - 12),
            child: FittedBox(
              fit: BoxFit.scaleDown,
              child: Text(
                kind.label,
                maxLines: 1,
                style: const TextStyle(
                  fontSize: 11,
                  height: 1.2,
                  fontWeight: FontWeight.w500,
                  color: Colors.black,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
