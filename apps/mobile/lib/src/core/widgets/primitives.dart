import 'package:flutter/material.dart';

import '../../app/theme.dart';
import 'brand_outlines.dart';

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

/// The Prime Analyst mark: the name on a tile -- heavy "Prime" over light
/// "Analyst", stretched to one width, with a blue bar beneath.
///
/// Drawn from outlines rather than set in a font or loaded as an image: it has
/// to appear on the days the shop's connection is down, and Sora is not a font
/// the app otherwise carries. The outlines are generated from the same file as
/// the launcher icon (`tools/brand/wordmark.py`), so the mark in here is the
/// mark on the home screen.
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
            ? Border.all(color: const Color(0xFFDCDDE3))
            : null,
      ),
      child: CustomPaint(
        painter: _WordmarkPainter(
          ink: inverted ? _ink : Colors.white,
          // Two blues, because the bar has to hold against the tile behind
          // it: the app's primary on white, and the lighter chart blue on
          // black, where the primary is too dark to read.
          bar: inverted ? const Color(0xFF2D5BFF) : const Color(0xFF6887FF),
        ),
      ),
    );
  }
}

/// The name and the bar, in the 512-unit square `tools/brand/icon.py` draws in.
class _WordmarkPainter extends CustomPainter {
  const _WordmarkPainter({required this.ink, required this.bar});

  final Color ink;
  final Color bar;

  /// Built once: the outlines never change, and this paints on every frame
  /// the shell's rail animates.
  static final Path _letters = _outline(brandPrime)
    ..addPath(_outline(brandAnalyst), Offset.zero);

  static Path _outline(List<double> data) {
    final Path path = Path();
    int i = 0;
    while (i < data.length) {
      switch (data[i++].toInt()) {
        case 0:
          path.moveTo(data[i], data[i + 1]);
          i += 2;
        case 1:
          path.lineTo(data[i], data[i + 1]);
          i += 2;
        case 2:
          path.quadraticBezierTo(data[i], data[i + 1], data[i + 2], data[i + 3]);
          i += 4;
        case 3:
          path.cubicTo(
            data[i], data[i + 1], data[i + 2], data[i + 3], data[i + 4], data[i + 5],
          );
          i += 6;
        default:
          path.close();
      }
    }
    return path;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final double unit = size.shortestSide / 512;
    canvas
      ..save()
      ..scale(unit)
      ..drawPath(_letters, Paint()..color = ink)
      ..drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(brandBar[0], brandBar[1], brandBar[2], brandBar[3]),
          Radius.circular(brandBar[4]),
        ),
        Paint()..color = bar,
      )
      ..restore();
  }

  @override
  bool shouldRepaint(_WordmarkPainter old) => old.ink != ink || old.bar != bar;
}
