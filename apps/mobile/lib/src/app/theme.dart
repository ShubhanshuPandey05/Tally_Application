import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// The visual language.
///
/// The brief is "Google Analytics for Tally, not remote desktop for Tally", so
/// the app must not look like accounting software. Restrained surfaces, one
/// accent colour, and figures set in tabular numerals -- when a column of
/// amounts is proportionally spaced the digits fail to line up and the whole
/// screen reads as amateur.
class AppTheme {
  const AppTheme._();

  /// Indigo rather than the obvious ledger-green: money up and money down both
  /// need a colour, and a green brand would make every positive figure shout.
  static const Color seed = Color(0xFF3A5AF0);

  static const Color positive = Color(0xFF12805C);
  static const Color negative = Color(0xFFC4314B);
  static const Color caution = Color(0xFFB86E00);

  static ThemeData light() => _build(Brightness.light);
  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: brightness,
    );
    final bool isLight = brightness == Brightness.light;
    final Color surface = isLight ? const Color(0xFFF6F7FB) : const Color(0xFF12141A);
    final Color card = isLight ? Colors.white : const Color(0xFF1B1E26);

    final ThemeData base = ThemeData(
      colorScheme: scheme.copyWith(surface: surface),
      useMaterial3: true,
      scaffoldBackgroundColor: surface,
      splashFactory: InkSparkle.splashFactory,
    );

    return base.copyWith(
      textTheme: _textTheme(base.textTheme),
      appBarTheme: AppBarTheme(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0.5,
        centerTitle: false,
        systemOverlayStyle:
            isLight ? SystemUiOverlayStyle.dark : SystemUiOverlayStyle.light,
        titleTextStyle: base.textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w700,
          color: scheme.onSurface,
        ),
      ),
      cardTheme: CardTheme(
        color: card,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: scheme.outlineVariant.withOpacity(0.5)),
        ),
      ),
      chipTheme: base.chipTheme.copyWith(
        side: BorderSide(color: scheme.outlineVariant),
        labelStyle: base.textTheme.labelMedium,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: card,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: scheme.outlineVariant),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: scheme.outlineVariant),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: scheme.primary, width: 1.6),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(52),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: card,
        surfaceTintColor: Colors.transparent,
        indicatorColor: scheme.primary.withOpacity(0.12),
        elevation: 0,
        height: 64,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      ),
      dividerTheme: DividerThemeData(
        color: scheme.outlineVariant.withOpacity(0.6),
        space: 1,
        thickness: 1,
      ),
      listTileTheme: const ListTileThemeData(
        contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      ),
    );
  }

  static TextTheme _textTheme(TextTheme base) => base.copyWith(
        displaySmall: base.displaySmall?.copyWith(
          fontWeight: FontWeight.w700,
          fontFeatures: _tabular,
        ),
        headlineMedium: base.headlineMedium?.copyWith(
          fontWeight: FontWeight.w700,
          fontFeatures: _tabular,
        ),
        headlineSmall: base.headlineSmall?.copyWith(
          fontWeight: FontWeight.w700,
          fontFeatures: _tabular,
        ),
        titleMedium: base.titleMedium?.copyWith(fontWeight: FontWeight.w600),
        titleSmall: base.titleSmall?.copyWith(fontWeight: FontWeight.w600),
        bodyMedium: base.bodyMedium?.copyWith(height: 1.35),
      );

  /// Every numeric style opts into tabular figures so amounts in a list align.
  static const List<FontFeature> _tabular = <FontFeature>[FontFeature.tabularFigures()];

  static const TextStyle amount = TextStyle(
    fontWeight: FontWeight.w700,
    fontFeatures: _tabular,
  );
}

/// Semantic colours for figures, resolved against the current theme.
extension MoneyColors on BuildContext {
  Color get positiveColor => AppTheme.positive;
  Color get negativeColor => AppTheme.negative;
  Color get cautionColor => AppTheme.caution;

  Color get mutedColor =>
      Theme.of(this).colorScheme.onSurfaceVariant.withOpacity(0.85);
}
