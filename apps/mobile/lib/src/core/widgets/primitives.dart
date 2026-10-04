import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// A rounded-square icon tile.
///
/// This is where colour lives. Almost nothing else in the app is coloured, so a
/// screen with six categories on it stays legible instead of turning into a
/// paint chart, and the eye learns that a coloured square means "this row is a
/// kind of thing".
class IconTile extends StatelessWidget {
  const IconTile({
    super.key,
    required this.icon,
    this.colour = AppTheme.tileBlue,
    this.size = 34,
    this.quiet = false,
    this.background,
    this.foreground,
  });

  final IconData icon;
  final Color colour;
  final double size;

  /// Grey tile, ink glyph. For rows that are structural rather than categorical
  /// -- a settings entry is not a category and does not deserve a colour.
  final bool quiet;

  /// Explicit override, for tiles sitting on the dark card where neither the
  /// category colour nor the page grey has the contrast to be seen.
  final Color? background;
  final Color? foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: background ?? (quiet ? context.surfaceColor : colour),
        borderRadius: BorderRadius.circular(AppTheme.radiusTile),
      ),
      child: Icon(
        icon,
        size: size * 0.5,
        color: foreground ??
            (quiet ? Theme.of(context).colorScheme.onSurface : Colors.white),
      ),
    );
  }
}

/// The one dark card on a screen.
///
/// It exists to answer "where do I look first?" without a heading that says so.
/// A second one on the same screen would leave the first saying nothing, so
/// treat it as a budget of one.
class HeroCard extends StatelessWidget {
  const HeroCard({super.key, required this.child, this.padding, this.onTap});

  final Widget child;
  final EdgeInsets? padding;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Material(
        color: context.heroSurface,
        borderRadius: BorderRadius.circular(AppTheme.radiusCard),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Padding(
            padding: padding ?? const EdgeInsets.fromLTRB(16, 13, 16, 14),
            child: DefaultTextStyle.merge(
              style: const TextStyle(color: Colors.white),
              child: child,
            ),
          ),
        ),
      ),
    );
  }
}

/// A segmented control: a white pill sliding inside a grey track.
///
/// Used instead of tabs because these are *filters* rather than places. Tabs
/// promise a different screen; this promises the same figures counted a
/// different way, which is what a period or a kind switch actually does.
class SegmentedPill<T> extends StatelessWidget {
  const SegmentedPill({
    super.key,
    required this.segments,
    required this.value,
    required this.onChanged,
  });

  final List<({T value, String label})> segments;
  final T value;
  final ValueChanged<T> onChanged;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: context.surfaceColor,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        children: <Widget>[
          for (final ({T value, String label}) segment in segments)
            Expanded(
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => onChanged(segment.value),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 160),
                  curve: Curves.easeOut,
                  height: 32,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: segment.value == value
                        ? theme.cardTheme.color
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(999),
                    boxShadow: segment.value == value
                        ? const <BoxShadow>[
                            BoxShadow(
                              color: Color(0x14101114),
                              blurRadius: 3,
                              offset: Offset(0, 1),
                            ),
                          ]
                        : null,
                  ),
                  child: Text(
                    segment.label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelLarge?.copyWith(
                      color: segment.value == value
                          ? theme.colorScheme.onSurface
                          : context.mutedColor,
                      fontWeight:
                          segment.value == value ? FontWeight.w600 : FontWeight.w500,
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// A pill-shaped action with an icon, for the row of secondary actions that
/// sits under a hero card.
class PillAction extends StatelessWidget {
  const PillAction({
    super.key,
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Material(
      color: context.surfaceColor,
      shape: const StadiumBorder(),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 7, 15, 7),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(icon, size: 16, color: theme.colorScheme.onSurface),
              const SizedBox(width: 8),
              Text(
                label,
                style: theme.textTheme.labelLarge
                    ?.copyWith(color: theme.colorScheme.onSurface),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A rounded progress rail. Grey track, blue fill, both fully rounded.
class Meter extends StatelessWidget {
  const Meter({super.key, required this.fraction, this.colour});

  /// Null means "we do not know", and draws an empty rail rather than a full or
  /// an empty one presented as a measurement.
  final double? fraction;
  final Color? colour;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(999),
      child: LinearProgressIndicator(
        value: fraction?.clamp(0.0, 1.0) ?? 0,
        minHeight: 6,
        backgroundColor: context.surfaceColor,
        valueColor: AlwaysStoppedAnimation<Color>(
          colour ?? Theme.of(context).colorScheme.primary,
        ),
      ),
    );
  }
}

/// The Prime Analyst mark: one circle cut in two, the halves slipped against
/// each other, on a tile.
///
/// It is double entry drawn as a shape. Every entry in a set of books has a
/// debit and a credit, the two are the same size, and together they make a
/// whole -- the one idea every person this product is for already carries in
/// their head.
///
/// Two constraints shaped it, and both are worth keeping.
///
/// The first: an earlier mark -- bars in a squircle -- was the shape half the
/// analytics industry already uses, and a logo that makes somebody think of
/// another product is doing the opposite of its job.
///
/// The second: it carries no lettering, on purpose. The name shares a word
/// with TallyPrime, and a wordmark in a tile invites setting "Prime" the way
/// theirs is set. A symbol cannot be mistaken for their lockup, and it stays
/// legible at 16px, where thirteen letters were a smudge.
///
/// Drawn rather than set in a font or loaded as an image: it is two arcs, and
/// it has to appear on the days the shop's connection is down. The geometry is
/// the same as `tools/brand/icon.py`, so the mark in here is the mark on the
/// home screen.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 72, this.inverted = false});

  final double size;

  /// A white tile instead of a black one. For light surfaces that already
  /// have a dark element on them, where a second black block would be one too
  /// many.
  final bool inverted;

  static const Color _ink = Color(0xFF0E0F11);

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: inverted ? Colors.white : _ink,
        borderRadius: BorderRadius.circular(size * 0.26),
        border: inverted
            ? Border.all(color: const Color(0xFFE8E9ED))
            : null,
      ),
      child: CustomPaint(
        painter: _DoubleEntryPainter(
          left: inverted ? _ink : Colors.white,
          // Two blues, because the half has to hold against the tile behind
          // it: the app's primary on white, and the lighter chart blue on
          // black, where the primary is too dark to read as a second half.
          right: inverted ? const Color(0xFF2D5BFF) : const Color(0xFF6887FF),
        ),
      ),
    );
  }
}

/// The two halves, in the 512-unit square `tools/brand/icon.py` draws in.
class _DoubleEntryPainter extends CustomPainter {
  const _DoubleEntryPainter({required this.left, required this.right});

  final Color left;
  final Color right;

  @override
  void paint(Canvas canvas, Size size) {
    final double unit = size.shortestSide / 512;
    final double radius = 130 * unit;

    // The left half sits low and the right half high. The 12-unit gap between
    // their straight edges is what makes them read as two pieces of one
    // circle rather than as a circle with a step in it.
    final Rect lower = Rect.fromCircle(
      center: Offset(250 * unit, 286 * unit),
      radius: radius,
    );
    final Rect upper = Rect.fromCircle(
      center: Offset(262 * unit, 226 * unit),
      radius: radius,
    );

    canvas
      ..drawPath(
        Path()
          ..addArc(lower, math.pi / 2, math.pi)
          ..close(),
        Paint()..color = left,
      )
      ..drawPath(
        Path()
          ..addArc(upper, -math.pi / 2, math.pi)
          ..close(),
        Paint()..color = right,
      );
  }

  @override
  bool shouldRepaint(_DoubleEntryPainter old) =>
      old.left != left || old.right != right;
}
