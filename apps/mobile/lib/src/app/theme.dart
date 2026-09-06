import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// The three skins a customer can choose between, in the order they appear in
/// the picker.
///
/// [dim] is the default rather than [light] or [dark], and rather than
/// following the system. A shop owner opens this app on a phone at the counter
/// and again at home in the evening; a soft dark reads comfortably in both, and
/// a theme that flips with the system clock changes the look of their books
/// halfway through the day for no reason they asked for.
///
/// [dark] exists separately because "dim" is not dark enough on an OLED phone
/// in a dark room, which is exactly when someone reaches for it.
enum AppThemeMode {
  light('Light'),
  dim('Dim'),
  dark('Dark');

  const AppThemeMode(this.label);

  /// What the picker calls it.
  final String label;

  /// Persisted by name, so reordering or inserting a mode later cannot silently
  /// move an existing customer to a different theme.
  static AppThemeMode fromName(String? name) => AppThemeMode.values.firstWhere(
        (AppThemeMode mode) => mode.name == name,
        orElse: () => AppThemeMode.dim,
      );

  bool get isLight => this == AppThemeMode.light;
}

/// The visual language.
///
/// White page, soft grey for anything that is a *container* rather than
/// content, near-black for the one card on a screen that should be read first,
/// and a single blue for action. Structure comes from radius and from the grey,
/// not from a border around everything.
///
/// The brief is "Google Analytics for Tally, not remote desktop for Tally", so
/// the app must not look like accounting software. Two rules carry most of
/// that: colour appears almost exclusively inside small rounded icon tiles, and
/// every figure is set in tabular numerals -- a column of proportionally spaced
/// amounts does not line up, and the whole screen reads as amateur.
///
/// These values are mirrored in the marketing site's tokens
/// (apps/website/src/styles/base.css). A customer sees both in the same
/// afternoon, and two nearly-identical blues read as a mistake, not a family.

class AppTheme {
  const AppTheme._();

  /// Action, and nothing else. Not a ledger-green: money up and money down both
  /// need a colour of their own, and a green brand would make every positive
  /// figure shout.
  static const Color accent = Color(0xFF2D5BFF);
  static const Color accentInk = Color(0xFF1E3FCC);
  static const Color accentSoft = Color(0xFFEAEFFF);

  static const Color ink = Color(0xFF101114);
  static const Color inkSoft = Color(0xFF3B3D45);

  /// The dark card. One per screen at most — it is how a screen says
  /// "start here", and a second one would leave the first saying nothing.
  static const Color inkCard = Color(0xFF15171C);

  static const Color positive = Color(0xFF12805C);
  static const Color negative = Color(0xFFC4314B);
  static const Color caution = Color(0xFFB8720A);

  /// Tints for the rounded icon tiles. A fixed, small set: a category palette
  /// that grows on demand is how an interface ends up looking like a paint
  /// chart.
  static const Color tileBlue = accent;
  static const Color tileGreen = Color(0xFF17A06B);
  static const Color tileAmber = Color(0xFFE08A15);
  static const Color tileViolet = Color(0xFF7C4DFF);
  static const Color tileRose = Color(0xFFE0447A);

  /// Corner radii. Large on cards, medium on tiles and fields, full on
  /// anything that is pressed.
  static const double radiusCard = 16;
  static const double radiusTile = 10;
  static const double radiusField = 12;

  static ThemeData light() => forMode(AppThemeMode.light);
  static ThemeData dim() => forMode(AppThemeMode.dim);
  static ThemeData dark() => forMode(AppThemeMode.dark);

  static ThemeData forMode(AppThemeMode mode) => _build(mode);

  static ThemeData _build(AppThemeMode mode) {
    final bool isLight = mode.isLight;
    final Brightness brightness = isLight ? Brightness.light : Brightness.dark;

    // Content is the lightest thing on a light page and the lightest thing on a
    // dark one; containers -- field fills, segmented tracks, icon tiles -- are
    // always a step away from it. That inversion is what stops either skin
    // going flat, and it is why each mode names four greys rather than one.
    final (Color page, Color card, Color surface, Color line) = switch (mode) {
      AppThemeMode.light => (
          Colors.white,
          Colors.white,
          const Color(0xFFF4F5F7),
          const Color(0xFFE8E9ED),
        ),
      AppThemeMode.dim => (
          const Color(0xFF1A1D23),
          const Color(0xFF232730),
          const Color(0xFF2C313B),
          const Color(0xFF363B46),
        ),
      AppThemeMode.dark => (
          const Color(0xFF08090B),
          const Color(0xFF101216),
          const Color(0xFF191C22),
          const Color(0xFF23262D),
        ),
    };
    final Color onPage = isLight ? ink : const Color(0xFFF2F3F7);
    final Color muted = isLight ? const Color(0xFF6B6E78) : const Color(0xFF9BA1AE);

    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: accent,
      brightness: brightness,
    ).copyWith(
      primary: isLight ? accent : const Color(0xFF8AA4FF),
      onPrimary: isLight ? Colors.white : const Color(0xFF0E0F13),
      primaryContainer: isLight ? accentSoft : const Color(0xFF23304F),
      surface: page,
      onSurface: onPage,
      surfaceContainerHighest: surface,
      onSurfaceVariant: muted,
      outlineVariant: line,
      error: negative,
    );

    final ThemeData base = ThemeData(
      colorScheme: scheme,
      useMaterial3: true,
      scaffoldBackgroundColor: page,
      // Every Material control one notch tighter. This is the single knob that
      // reaches the widgets we do not style ourselves -- switches, checkboxes,
      // the buttons inside a dialog -- so they stay in proportion with a type
      // ramp that has already been compressed.
      visualDensity: VisualDensity.compact,
      // No ripple splash. The reference language is flat and quiet; an ink
      // sparkle under a pressed pill reads as a different product.
      splashFactory: InkRipple.splashFactory,
    );

    return base.copyWith(
      textTheme: _textTheme(base.textTheme, onPage),
      appBarTheme: AppBarTheme(
        backgroundColor: page,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleSpacing: 16,
        toolbarHeight: 52,
        systemOverlayStyle:
            isLight ? SystemUiOverlayStyle.dark : SystemUiOverlayStyle.light,
        iconTheme: IconThemeData(color: onPage, size: 20),
        titleTextStyle: TextStyle(
          fontSize: 17,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.4,
          color: onPage,
        ),
      ),
      // The one dark card. On a light page it is near-black; on a dark page
      // near-black *is* the page, so it lifts to the card colour's brighter
      // sibling instead. Either way it is the only surface on the screen that
      // is not the page or a container.
      extensions: <ThemeExtension<dynamic>>[
        HeroSurface(
          colour: isLight ? inkCard : Color.alphaBlend(Colors.white10, card),
        ),
      ],
      cardTheme: CardTheme(
        color: card,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusCard),
          side: BorderSide(color: line),
        ),
      ),
      chipTheme: base.chipTheme.copyWith(
        backgroundColor: surface,
        side: BorderSide.none,
        shape: const StadiumBorder(),
        labelStyle: TextStyle(
          fontSize: 11.5,
          fontWeight: FontWeight.w600,
          color: onPage,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 2),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
        // The field is the grey shape; a border as well would be saying the
        // same thing twice.
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: BorderSide(color: scheme.primary, width: 1.6),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: const BorderSide(color: negative, width: 1.4),
        ),
        hintStyle: TextStyle(color: muted),
        prefixIconColor: muted,
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: isLight ? ink : Colors.white,
          foregroundColor: isLight ? Colors.white : ink,
          // Still a comfortable tap target -- 46pt clears the 44pt both
          // platforms ask for. Below that, density starts costing accuracy
          // rather than space, which is the wrong trade on a phone held by
          // somebody in their sixties.
          minimumSize: const Size.fromHeight(46),
          shape: const StadiumBorder(),
          textStyle: const TextStyle(
            fontWeight: FontWeight.w600,
            fontSize: 15,
            letterSpacing: -0.2,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: onPage,
          backgroundColor: surface,
          minimumSize: const Size.fromHeight(44),
          side: BorderSide.none,
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: scheme.primary,
          textStyle: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      dividerTheme: DividerThemeData(color: line, space: 1, thickness: 1),
      listTileTheme: const ListTileThemeData(
        contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 2),
        minVerticalPadding: 6,
        horizontalTitleGap: 12,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: card,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
        ),
      ),
      dialogTheme: DialogTheme(
        backgroundColor: card,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: inkCard,
        contentTextStyle: const TextStyle(color: Colors.white),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        linearTrackColor: surface,
        linearMinHeight: 6,
        color: scheme.primary,
      ),
    );
  }

  /// The type ramp, one step tighter than Material's.
  ///
  /// Material 3's default sizes are drawn for an app with a handful of things
  /// on a screen. This is a set of books: nearly every question in the brief is
  /// answered by a *row* -- an account and its balance, a party and what they
  /// owe -- so every point of type size spent is a row that falls below the
  /// fold, and an owner who has to scroll to see who owes them money is being
  /// asked to do the thing the product exists to save them.
  ///
  /// The compression is not uniform, and that is the point. Headline figures
  /// lose the most, because a number set at 36pt is large for effect rather
  /// than for legibility. Body text loses a little. **The caption sizes do not
  /// move at all** -- 11pt is the floor for everything in this app, because the
  /// people reading it are shop owners and their accountants, often on a phone
  /// held at arm's length in bad light. Density is bought from leading,
  /// padding and the big numbers; never from the smallest text on the screen.
  static TextTheme _textTheme(TextTheme base, Color onPage) => base.copyWith(
        displaySmall: base.displaySmall?.copyWith(
          fontSize: 30,
          fontWeight: FontWeight.w700,
          height: 1.1,
          letterSpacing: -1.0,
          fontFeatures: _tabular,
        ),
        headlineMedium: base.headlineMedium?.copyWith(
          fontSize: 23,
          fontWeight: FontWeight.w700,
          height: 1.15,
          letterSpacing: -0.7,
          fontFeatures: _tabular,
        ),
        headlineSmall: base.headlineSmall?.copyWith(
          fontSize: 20,
          fontWeight: FontWeight.w700,
          height: 1.15,
          letterSpacing: -0.5,
          fontFeatures: _tabular,
        ),
        titleLarge: base.titleLarge?.copyWith(
          fontSize: 18,
          fontWeight: FontWeight.w700,
          height: 1.15,
          letterSpacing: -0.4,
        ),
        titleMedium: base.titleMedium?.copyWith(
          fontSize: 14.5,
          fontWeight: FontWeight.w600,
          height: 1.2,
          letterSpacing: -0.2,
        ),
        titleSmall: base.titleSmall?.copyWith(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          height: 1.25,
        ),
        bodyLarge: base.bodyLarge?.copyWith(fontSize: 14.5, height: 1.25),
        bodyMedium: base.bodyMedium?.copyWith(fontSize: 13, height: 1.3),
        bodySmall: base.bodySmall?.copyWith(fontSize: 11.5, height: 1.3),
        labelLarge: base.labelLarge?.copyWith(fontSize: 13, fontWeight: FontWeight.w600),
        labelMedium: base.labelMedium?.copyWith(fontSize: 11.5),
      );

  /// Every numeric style opts into tabular figures so amounts in a list align.
  static const List<FontFeature> _tabular = <FontFeature>[FontFeature.tabularFigures()];

  static const TextStyle amount = TextStyle(
    fontWeight: FontWeight.w700,
    letterSpacing: -0.5,
    fontFeatures: _tabular,
  );
}

/// Semantic colours and surfaces, resolved against the current theme.
extension MoneyColors on BuildContext {
  Color get positiveColor => AppTheme.positive;
  Color get negativeColor => AppTheme.negative;
  Color get cautionColor => AppTheme.caution;

  Color get mutedColor => Theme.of(this).colorScheme.onSurfaceVariant;

  /// The grey used for containers: field fills, segmented tracks, icon tile
  /// backgrounds, progress rails.
  Color get surfaceColor => Theme.of(this).colorScheme.surfaceContainerHighest;

  Color get lineColor => Theme.of(this).colorScheme.outlineVariant;

  /// The one dark card's fill for the current skin.
  Color get heroSurface =>
      Theme.of(this).extension<HeroSurface>()?.colour ?? AppTheme.inkCard;
}


/// Where the one dark card gets its colour from.
///
/// A theme extension rather than a constant because the answer depends on the
/// skin: "darker than the page" is not available when the page is already dark,
/// and a hero card that disappears is worse than no hero card at all.
@immutable
class HeroSurface extends ThemeExtension<HeroSurface> {
  const HeroSurface({required this.colour});

  final Color colour;

  @override
  HeroSurface copyWith({Color? colour}) => HeroSurface(colour: colour ?? this.colour);

  @override
  HeroSurface lerp(ThemeExtension<HeroSurface>? other, double t) {
    if (other is! HeroSurface) return this;
    return HeroSurface(colour: Color.lerp(colour, other.colour, t) ?? colour);
  }
}
