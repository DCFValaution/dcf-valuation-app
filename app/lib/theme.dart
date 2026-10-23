/// The app's visual language: colour, type, spacing and shape.
///
/// Everything visual is defined here so the screens stay declarative and the
/// two themes cannot drift apart. Both are hand-built rather than generated
/// from a seed colour, because a seeded scheme gives no control over the
/// semantic pair that matters most in this app - the green and red used for
/// upside and downside - and those need deliberate contrast in both themes.
///
/// ACCESSIBILITY
/// -------------
/// Every foreground colour here clears 4.5:1 against the surface it is used
/// on, so body text stays legible in both themes. Up/down is never signalled
/// by colour alone: an arrow icon and a +/- sign always accompany it, so the
/// meaning survives for a colour-blind reader or a greyscale screenshot.
library;

import 'package:flutter/material.dart';

// ---------------------------------------------------------------------------
// Scale
// ---------------------------------------------------------------------------

/// One spacing scale, used everywhere. Ad-hoc padding is what makes an
/// interface feel unconsidered.
abstract final class AppSpacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;
  static const double xxl = 28;
  static const double xxxl = 40;
}

abstract final class AppRadius {
  static const double card = 20;
  static const double control = 14;
  static const double chip = 999;
}

/// How far apart things sit, by relationship rather than by pixel.
///
/// Screens were spaced by eye as features were added, which is how one screen
/// ended up with 12px between sections and another with 28. These name the
/// three relationships that matter so every screen spaces them alike.
abstract final class AppGap {
  /// Between separate sections of a screen: the figure, the notes, the table.
  static const double section = AppSpacing.xxl;

  /// Between related blocks within a section.
  static const double block = AppSpacing.lg;

  /// Between a heading and what it introduces.
  static const double heading = AppSpacing.sm;
}

// ---------------------------------------------------------------------------
// Semantic colours
// ---------------------------------------------------------------------------

/// Colours that carry meaning rather than brand.
///
/// These live in a ThemeExtension rather than ColorScheme because Material's
/// scheme has no slot for "this number went up" - and inventing one by reusing
/// `tertiary` would make every call site cryptic.
@immutable
class AppColors extends ThemeExtension<AppColors> {
  final Color positive;
  final Color positiveSurface;
  final Color negative;
  final Color negativeSurface;
  final Color caution;
  final Color cautionSurface;
  final Color neutral;
  final Color accentSurface;
  final Color hairline;
  final Color textSecondary;

  const AppColors({
    required this.positive,
    required this.positiveSurface,
    required this.negative,
    required this.negativeSurface,
    required this.caution,
    required this.cautionSurface,
    required this.neutral,
    required this.accentSurface,
    required this.hairline,
    required this.textSecondary,
  });

  static const light = AppColors(
    positive: Color(0xFF067A55),
    positiveSurface: Color(0xFFE4F5EE),
    negative: Color(0xFFC42B1C),
    negativeSurface: Color(0xFFFDECEA),
    caution: Color(0xFF8A5300),
    cautionSurface: Color(0xFFFDF3E2),
    neutral: Color(0xFF5B6478),
    accentSurface: Color(0xFFE9EEFD),
    hairline: Color(0xFFE1E6EF),
    textSecondary: Color(0xFF5B6478),
  );

  static const dark = AppColors(
    positive: Color(0xFF3DD68C),
    positiveSurface: Color(0xFF12291F),
    negative: Color(0xFFFF7B72),
    negativeSurface: Color(0xFF2B1614),
    caution: Color(0xFFF0B429),
    cautionSurface: Color(0xFF2A2113),
    neutral: Color(0xFF98A2B3),
    accentSurface: Color(0xFF1A2340),
    hairline: Color(0xFF2A3341),
    textSecondary: Color(0xFF98A2B3),
  );

  @override
  AppColors copyWith({
    Color? positive,
    Color? positiveSurface,
    Color? negative,
    Color? negativeSurface,
    Color? caution,
    Color? cautionSurface,
    Color? neutral,
    Color? accentSurface,
    Color? hairline,
    Color? textSecondary,
  }) => AppColors(
    positive: positive ?? this.positive,
    positiveSurface: positiveSurface ?? this.positiveSurface,
    negative: negative ?? this.negative,
    negativeSurface: negativeSurface ?? this.negativeSurface,
    caution: caution ?? this.caution,
    cautionSurface: cautionSurface ?? this.cautionSurface,
    neutral: neutral ?? this.neutral,
    accentSurface: accentSurface ?? this.accentSurface,
    hairline: hairline ?? this.hairline,
    textSecondary: textSecondary ?? this.textSecondary,
  );

  @override
  AppColors lerp(ThemeExtension<AppColors>? other, double t) {
    if (other is! AppColors) return this;
    return AppColors(
      positive: Color.lerp(positive, other.positive, t)!,
      positiveSurface: Color.lerp(positiveSurface, other.positiveSurface, t)!,
      negative: Color.lerp(negative, other.negative, t)!,
      negativeSurface: Color.lerp(negativeSurface, other.negativeSurface, t)!,
      caution: Color.lerp(caution, other.caution, t)!,
      cautionSurface: Color.lerp(cautionSurface, other.cautionSurface, t)!,
      neutral: Color.lerp(neutral, other.neutral, t)!,
      accentSurface: Color.lerp(accentSurface, other.accentSurface, t)!,
      hairline: Color.lerp(hairline, other.hairline, t)!,
      textSecondary: Color.lerp(textSecondary, other.textSecondary, t)!,
    );
  }
}

/// Shorthand: `context.colors.positive`.
extension AppColorsX on BuildContext {
  AppColors get colors => Theme.of(this).extension<AppColors>()!;
  TextTheme get text => Theme.of(this).textTheme;
  ColorScheme get scheme => Theme.of(this).colorScheme;
}

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

/// Figures are set with tabular (fixed-width) digits.
///
/// This matters more than it sounds: the headline value recomputes on every
/// frame while a slider is dragged, and with proportional digits the number
/// would jitter horizontally as its glyphs changed width.
const List<FontFeature> _tabular = [FontFeature.tabularFigures()];

TextTheme _textTheme(Color primary, Color secondary) => TextTheme(
  // The hero valuation.
  displayLarge: TextStyle(
    fontSize: 44,
    height: 1.05,
    fontWeight: FontWeight.w700,
    letterSpacing: -1.2,
    color: primary,
    fontFeatures: _tabular,
  ),
  displayMedium: TextStyle(
    fontSize: 32,
    height: 1.1,
    fontWeight: FontWeight.w700,
    letterSpacing: -0.8,
    color: primary,
    fontFeatures: _tabular,
  ),
  headlineMedium: TextStyle(
    fontSize: 24,
    height: 1.2,
    fontWeight: FontWeight.w600,
    letterSpacing: -0.4,
    color: primary,
  ),
  titleLarge: TextStyle(
    fontSize: 19,
    height: 1.3,
    fontWeight: FontWeight.w600,
    letterSpacing: -0.2,
    color: primary,
    fontFeatures: _tabular,
  ),
  titleMedium: TextStyle(
    fontSize: 16,
    height: 1.35,
    fontWeight: FontWeight.w600,
    color: primary,
  ),
  titleSmall: TextStyle(
    fontSize: 14,
    height: 1.35,
    fontWeight: FontWeight.w600,
    color: primary,
    fontFeatures: _tabular,
  ),
  bodyLarge: TextStyle(
    fontSize: 15,
    height: 1.5,
    color: primary,
    fontFeatures: _tabular,
  ),
  bodyMedium: TextStyle(fontSize: 14, height: 1.55, color: primary),
  bodySmall: TextStyle(fontSize: 13, height: 1.55, color: secondary),
  // Eyebrow labels above figures.
  labelLarge: TextStyle(
    fontSize: 14,
    fontWeight: FontWeight.w600,
    color: primary,
  ),
  labelMedium: TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w700,
    letterSpacing: 0.9,
    color: secondary,
  ),
  labelSmall: TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w500,
    letterSpacing: 0.3,
    color: secondary,
  ),
);

// ---------------------------------------------------------------------------
// Themes
// ---------------------------------------------------------------------------

const _accentLight = Color(0xFF3A5BD9);
const _accentDark = Color(0xFF8FA6FF);

ThemeData _build({
  required Brightness brightness,
  required Color accent,
  required Color background,
  required Color surface,
  required Color surfaceMuted,
  required Color textPrimary,
  required AppColors colors,
}) {
  final isDark = brightness == Brightness.dark;
  final text = _textTheme(textPrimary, colors.textSecondary);

  final scheme = ColorScheme(
    brightness: brightness,
    primary: accent,
    onPrimary: isDark ? const Color(0xFF0C1226) : Colors.white,
    secondary: accent,
    onSecondary: isDark ? const Color(0xFF0C1226) : Colors.white,
    error: colors.negative,
    onError: isDark ? const Color(0xFF2B1614) : Colors.white,
    surface: surface,
    onSurface: textPrimary,
    surfaceContainerHighest: surfaceMuted,
    onSurfaceVariant: colors.textSecondary,
    outline: colors.hairline,
    outlineVariant: colors.hairline,
  );

  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    scaffoldBackgroundColor: background,
    canvasColor: background,
    textTheme: text,
    extensions: [colors],
    splashFactory: InkSparkle.splashFactory,

    appBarTheme: AppBarTheme(
      backgroundColor: background,
      surfaceTintColor: Colors.transparent,
      foregroundColor: textPrimary,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: text.titleLarge?.copyWith(
        fontSize: 20,
        letterSpacing: -0.3,
        fontFeatures: const [],
      ),
    ),

    cardTheme: CardThemeData(
      elevation: 0,
      color: surface,
      surfaceTintColor: Colors.transparent,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.card),
        side: BorderSide(color: colors.hairline),
      ),
    ),

    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: surface,
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.lg,
      ),
      hintStyle: text.bodyMedium?.copyWith(color: colors.textSecondary),
      labelStyle: text.bodyMedium?.copyWith(color: colors.textSecondary),
      floatingLabelStyle: text.labelSmall?.copyWith(color: accent),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.control),
        borderSide: BorderSide(color: colors.hairline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.control),
        borderSide: BorderSide(color: colors.hairline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.control),
        borderSide: BorderSide(color: accent, width: 1.6),
      ),
      disabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.control),
        borderSide: BorderSide(color: colors.hairline.withValues(alpha: 0.6)),
      ),
    ),

    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        // Hover only ever resolves under a real pointer, so this is the
        // desktop and web state and costs touch nothing.
        overlayColor: scheme.onPrimary,
        backgroundColor: accent,
        foregroundColor: scheme.onPrimary,
        disabledBackgroundColor: colors.hairline,
        disabledForegroundColor: colors.textSecondary,
        textStyle: text.labelLarge,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.control),
        ),
      ),
    ),

    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        overlayColor: accent,
        foregroundColor: accent,
        disabledForegroundColor: colors.textSecondary,
        textStyle: text.labelLarge,
        side: BorderSide(color: colors.hairline),
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.control),
        ),
      ),
    ),

    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: accent,
        textStyle: text.labelLarge,
      ),
    ),

    sliderTheme: SliderThemeData(
      trackHeight: 4,
      activeTrackColor: accent,
      inactiveTrackColor: colors.hairline,
      thumbColor: accent,
      overlayColor: accent.withValues(alpha: 0.12),
      valueIndicatorColor: isDark ? surfaceMuted : const Color(0xFF101828),
      valueIndicatorTextStyle: text.labelSmall?.copyWith(
        color: isDark ? textPrimary : Colors.white,
        fontWeight: FontWeight.w600,
      ),
      overlayShape: const RoundSliderOverlayShape(overlayRadius: 18),
      thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 9),
    ),

    dividerTheme: DividerThemeData(
      color: colors.hairline,
      thickness: 1,
      space: 1,
    ),

    // A tinted selection rather than a solid brand fill: the switcher chooses
    // what to look at and should never outshout the figure beneath it.
    segmentedButtonTheme: SegmentedButtonThemeData(
      style: ButtonStyle(
        visualDensity: VisualDensity.standard,
        textStyle: WidgetStatePropertyAll(text.labelLarge),
        side: WidgetStatePropertyAll(BorderSide(color: colors.hairline)),
        shape: WidgetStatePropertyAll(
          RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.control),
          ),
        ),
        backgroundColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? colors.accentSurface
              : surface,
        ),
        // This widget resolves its own background, which paints over the
        // default overlay; without this a hovered segment looks dead.
        overlayColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.pressed)) {
            return accent.withValues(alpha: 0.12);
          }
          if (states.contains(WidgetState.hovered)) {
            return accent.withValues(alpha: 0.06);
          }
          return null;
        }),
        foregroundColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? accent
              : colors.textSecondary,
        ),
        iconColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? accent
              : colors.textSecondary,
        ),
      ),
    ),

    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: surface,
      surfaceTintColor: Colors.transparent,
      modalBackgroundColor: surface,
      dragHandleColor: colors.hairline,
      showDragHandle: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(AppRadius.card),
        ),
      ),
    ),

    dialogTheme: DialogThemeData(
      backgroundColor: surface,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      titleTextStyle: text.titleLarge?.copyWith(fontFeatures: const []),
      contentTextStyle: text.bodyMedium,
    ),

    chipTheme: ChipThemeData(
      backgroundColor: surfaceMuted,
      side: BorderSide(color: colors.hairline),
      labelStyle: text.labelLarge?.copyWith(fontSize: 13),
      deleteIconColor: colors.textSecondary,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
    ),

    expansionTileTheme: ExpansionTileThemeData(
      tilePadding: EdgeInsets.zero,
      childrenPadding: const EdgeInsets.only(bottom: AppSpacing.md),
      iconColor: colors.textSecondary,
      collapsedIconColor: colors.textSecondary,
      textColor: textPrimary,
      collapsedTextColor: textPrimary,
      shape: const Border(),
      collapsedShape: const Border(),
    ),

    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: isDark ? surfaceMuted : const Color(0xFF1B2231),
      contentTextStyle: text.bodySmall?.copyWith(
        color: isDark ? textPrimary : Colors.white,
      ),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      insetPadding: const EdgeInsets.all(AppSpacing.lg),
    ),

    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: accent,
      linearTrackColor: colors.hairline,
      circularTrackColor: colors.hairline,
    ),
  );
}

ThemeData buildLightTheme() => _build(
  brightness: Brightness.light,
  accent: _accentLight,
  background: const Color(0xFFF5F7FA),
  surface: Colors.white,
  surfaceMuted: const Color(0xFFEFF2F7),
  textPrimary: const Color(0xFF101828),
  colors: AppColors.light,
);

ThemeData buildDarkTheme() => _build(
  brightness: Brightness.dark,
  accent: _accentDark,
  background: const Color(0xFF0B0E13),
  surface: const Color(0xFF141922),
  surfaceMuted: const Color(0xFF1B212C),
  textPrimary: const Color(0xFFE8ECF3),
  colors: AppColors.dark,
);

/// Durations and curves for the app's three animations.
///
/// There is no general motion system here on purpose. This app's identity is
/// clarity about numbers, and motion that draws attention to itself works
/// against that, so only three moments animate: a result arriving, the
/// Advanced section opening, and nothing else. These tokens exist so those
/// three agree with each other rather than each inventing a duration.
///
/// The curve is the strong ease-out - slow built-in curves read as sluggish
/// at these durations, and ease-in is never used: it holds still at exactly
/// the moment the user is watching.
abstract final class AppMotion {
  /// A result arriving.
  ///
  /// Longer than the usual ceiling for UI, and deliberately: at 200ms the
  /// landing was over before it registered, which is the worst of both -
  /// the cost of an animation with none of the softening. This is the one
  /// place in the app where the motion is meant to be *noticed*, because it
  /// marks the moment the app answers the question it was asked.
  ///
  /// It costs nothing in waiting: the layout is final on the first frame and
  /// only opacity and a small translation move, so the figure is readable
  /// well before the animation ends.
  static const Duration arrive = Duration(milliseconds: 380);

  /// The Advanced section opening. Past the usual 300ms ceiling, which is a
  /// deliberate exception: a disclosure the user chose to open is the one
  /// place a little length reads as grace rather than lag, and at 260ms it
  /// stopped dead rather than settling.
  static const Duration enter = Duration(milliseconds: 330);

  /// Closing something the user has dismissed: quicker than opening, because
  /// they have already decided and are waiting for it to be gone.
  static const Duration exit = Duration(milliseconds: 280);

  /// A segmented control moving its selection.
  static const Duration select = Duration(milliseconds: 180);

  /// cubic-bezier(0.23, 1, 0.32, 1) - a stronger decelerate than
  /// Curves.easeOut, which is too gentle to register at these durations.
  static const Curve enterCurve = Cubic(0.23, 1, 0.32, 1);

  /// cubic-bezier(0.32, 0.72, 0, 1) - the iOS drawer curve. It carries speed
  /// much further into the movement than [enterCurve] and then lands almost
  /// flat, which is what reads as settling into place rather than stopping.
  /// Used where a panel changes its own size.
  static const Curve panelCurve = Cubic(0.32, 0.72, 0, 1);

  /// How far an arriving result travels before settling, in logical pixels.
  ///
  /// Pixels rather than a fraction of the subtree, which is what this was
  /// first: the result subtree is a refusal card on one run and a full DCF
  /// on the next, so a percentage made the travel unpredictable and, on the
  /// short ones, invisible. A fixed distance reads the same every time.
  static const double enterTravel = 28.0;

  /// How far a pressed control shrinks. Subtle: the control should look
  /// like it took the press, not like it performed. The spring carries the
  /// quality here, not the size.
  static const double pressScale = 0.96;

  /// A finger landing on a control.
  ///
  /// A spring rather than a duration and a curve, for two reasons. It can be
  /// interrupted and keep its velocity, so a second press part way through
  /// the spring back continues from where the control actually is instead of
  /// snapping and restarting. And the settle is governed by physics, which
  /// is what separates a control that feels alive from one that performs a
  /// 110ms scale.
  ///
  /// Going down is near-critically damped: a finger pressing a surface does
  /// not bounce into it.
  static final SpringDescription pressDown = SpringDescription.withDampingRatio(
    mass: 1,
    stiffness: 1000,
    ratio: 0.92,
  );

  /// Coming back up carries a little bounce - Apple's 0.2, roughly - because
  /// this is the half the user watches, and a dead return is the thing that
  /// makes a press feel mechanical.
  static final SpringDescription pressRelease =
      SpringDescription.withDampingRatio(mass: 1, stiffness: 620, ratio: 0.78);

  /// Whether the platform has asked for less motion.
  ///
  /// On the web this follows prefers-reduced-motion; on iOS and Android it
  /// follows the system accessibility setting. Reduced motion means gentler,
  /// not absent: a fade stays because it explains that content changed, and
  /// only the travel is dropped.
  static bool reduced(BuildContext context) =>
      MediaQuery.maybeDisableAnimationsOf(context) ?? false;

  /// A duration, honouring a request for reduced motion.
  static Duration forContext(BuildContext context, Duration duration) =>
      reduced(context) ? const Duration(milliseconds: 1) : duration;
}
