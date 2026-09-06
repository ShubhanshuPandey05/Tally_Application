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
  const HeroCard({super.key, required this.child, this.padding});

  final Widget child;
  final EdgeInsets? padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding ?? const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: context.heroSurface,
        borderRadius: BorderRadius.circular(AppTheme.radiusCard),
      ),
      child: DefaultTextStyle.merge(
        style: const TextStyle(color: Colors.white),
        child: child,
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

/// The TallyFlow mark: the name in a handwriting face, over a rule, on a tile.
///
/// Two constraints shaped this, and both are worth keeping.
///
/// The first: the mark before it -- bars in a squircle -- was the shape half the
/// analytics industry already uses, and a logo that makes somebody think of
/// another product is doing the opposite of its job.
///
/// The second: it is deliberately **not** a version of TallyPrime's own logo.
/// Theirs is a script "Tally" over a swoosh over a plain second word, and
/// rebuilding that lockup with "Flow" in place of "Prime" would produce a mark
/// customers would reasonably take for an official Tally Solutions product --
/// which is the exact claim this app's own footer disclaims. So: one word, not
/// two stacked; a straight rule, not a swoosh; and a handwriting face that
/// looks nothing like theirs.
///
/// Two forms, one rule for choosing. A wordmark needs room; below
/// [_wordmarkFloor] the name would be a few pixels tall and illegible, so the
/// tile falls back to a geometric monogram that survives at a favicon's size.
/// Both keep the rule, which is what makes them read as the same brand.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 72, this.inverted = false});

  final double size;

  /// Black on white instead of white on black. For light surfaces that already
  /// have a dark element on them, where a second black block would be one too
  /// many.
  final bool inverted;

  /// Below this the name will not fit across the tile on one line, and the
  /// stacked form takes over -- the same two-line arrangement the launcher icon
  /// and the favicon use, so the small mark in here is the mark on the home
  /// screen rather than a third shape nobody has seen before.
  static const double _wordmarkFloor = 56;

  static const Color _ink = Color(0xFF0E0F11);

  Color get _tile => inverted ? Colors.white : _ink;
  Color get _mark => inverted ? _ink : Colors.white;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: _tile,
        borderRadius: BorderRadius.circular(size * 0.26),
        border: inverted
            ? Border.all(color: const Color(0xFFE8E9ED))
            : null,
      ),
      child: Center(
        child: size < _wordmarkFloor ? _stacked() : _wordmark(),
      ),
    );
  }

  Widget _wordmark() => Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          // Caveat, at a size that fits "TallyFlow" across the tile with the
          // rule tucked under its tail. `height: 1` because a handwriting face
          // carries a lot of built-in leading, and a logo has no line after it
          // for that leading to belong to.
          Text(
            'TallyFlow',
            maxLines: 1,
            style: TextStyle(
              fontFamily: 'Caveat',
              color: _mark,
              fontSize: size * 0.315,
              fontWeight: FontWeight.w700,
              height: 1,
            ),
          ),
          SizedBox(height: size * 0.02),
          _rule(size * 0.70),
        ],
      );

  /// `Tally` with `Flow` beneath it. Two lines buy each letter roughly double
  /// the height one line would, which is the whole reason a small mark stacks.
  ///
  /// No rule under it: the rule gives the single-line lockup a base, and here
  /// the second word already is one. A third horizontal element inside a tile
  /// this small is clutter.
  Widget _stacked() => Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          _line('Tally'),
          _line('Flow'),
        ],
      );

  /// `height` well under 1 because Caveat reserves a great deal of room above
  /// and below its ink for ascenders and descenders that mostly are not there.
  /// Left at 1 the two words read as two lines of a list rather than as one
  /// signature.
  Widget _line(String text) => Text(
        text,
        maxLines: 1,
        style: TextStyle(
          fontFamily: 'Caveat',
          color: _mark,
          fontSize: size * 0.40,
          fontWeight: FontWeight.w700,
          height: 0.78,
        ),
      );

  Widget _rule(double width) => _bar(width: width, height: size * 0.05);

  Widget _bar({required double width, required double height}) => Container(
        width: width,
        height: height,
        decoration: BoxDecoration(
          color: _mark,
          borderRadius: BorderRadius.circular(height / 2),
        ),
      );
}
