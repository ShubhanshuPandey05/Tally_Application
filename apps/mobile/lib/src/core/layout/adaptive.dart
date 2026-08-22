import 'package:flutter/widgets.dart';

/// Where the layout changes shape.
///
/// The app is built for a phone and always will be, but the same Flutter source
/// produces a web build, and a browser window is whatever width somebody
/// dragged it to. Three sizes are enough to keep that honest: a phone, a tablet
/// or small window, and a desktop browser.
///
/// The numbers are deliberately not the Material breakpoints. What matters here
/// is not "is this a tablet" but two concrete questions: is there room beside
/// the content for navigation, and is a column of figures about to get so wide
/// that the eye loses the line between a name on the left and an amount on the
/// right.
class Breakpoints {
  const Breakpoints._();

  /// Above this, navigation moves off the bottom of the screen and into a rail
  /// down the side. A bottom bar on a wide window is a phone control stranded
  /// in the corner of a desktop.
  static const double rail = 760;

  /// Above this the rail can afford to show labels beside its icons.
  static const double railExpanded = 1160;

  /// The widest a single column of rows should ever be. Past roughly this,
  /// tracking a party's name on the left to its amount on the right stops being
  /// a glance and becomes work.
  static const double column = 680;

  /// The dashboard gets more room than a list does: its tiles are self-contained
  /// blocks, so widening the pane adds columns rather than stretching rows.
  static const double dashboard = 940;
}

extension AdaptiveContext on BuildContext {
  double get windowWidth => MediaQuery.sizeOf(this).width;

  /// Navigation lives beside the content rather than under it.
  bool get usesRail => windowWidth >= Breakpoints.rail;

  /// The rail has room for labels.
  bool get railIsExpanded => windowWidth >= Breakpoints.railExpanded;
}

/// Centres content and stops it stretching past a readable width.
///
/// Wraps a whole screen body rather than being applied padding-by-padding, so
/// the paddings inside a screen stay the phone-sized values they were written
/// as. Widening those instead would leave every list row on a desktop with its
/// name and its amount at opposite ends of a metre of whitespace.
class ContentPane extends StatelessWidget {
  const ContentPane({
    super.key,
    required this.child,
    this.maxWidth = Breakpoints.column,
  });

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: child,
      ),
    );
  }
}
