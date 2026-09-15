/// Shared building blocks, so every screen is assembled from the same parts.
///
/// The app grew one feature at a time, and each feature brought its own
/// notice box, card and badge - five slightly different ways to show a
/// warning, with different padding, radius and icon size. These are the one
/// way each is now drawn.
///
/// COLOUR ROLES, which these components encode so screens cannot mix them up:
///
///   * positive / negative - a real valuation's upside or downside, and a
///     genuine error. Nothing else.
///   * caution (amber) - speculation, and things to keep in mind.
///   * neutral - relative, market-based figures and supporting information.
///   * accent (brand) - what can be tapped or is selected. Never a verdict.
library;

import 'package:flutter/material.dart';

import 'formatting.dart';
import 'theme.dart';

/// The meaning a tinted surface carries.
enum Tone { neutral, accent, caution, negative, positive }

extension ToneColors on Tone {
  Color foreground(BuildContext context) {
    final c = context.colors;
    return switch (this) {
      Tone.neutral => c.textSecondary,
      Tone.accent => context.scheme.primary,
      Tone.caution => c.caution,
      Tone.negative => c.negative,
      Tone.positive => c.positive,
    };
  }

  Color surface(BuildContext context) {
    final c = context.colors;
    return switch (this) {
      Tone.neutral => context.scheme.surfaceContainerHighest,
      Tone.accent => c.accentSurface,
      Tone.caution => c.cautionSurface,
      Tone.negative => c.negativeSurface,
      Tone.positive => c.positiveSurface,
    };
  }
}

/// A content card: the surface every grouped block of information sits on.
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.xl),
    this.tone,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  /// Tints the card for a card whose whole meaning is a tone - a refusal, a
  /// speculative figure. Most cards are plain.
  final Tone? tone;

  @override
  Widget build(BuildContext context) {
    final tone = this.tone;
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: tone == null ? context.scheme.surface : tone.surface(context),
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(
          color: tone == null
              ? context.colors.hairline
              : tone.foreground(context).withValues(alpha: 0.28),
        ),
      ),
      child: child,
    );
  }
}

/// A section's heading, with an optional line under it and an action beside it.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.trailing,
    this.badge,
  });

  final String title;
  final String? subtitle;
  final Widget? trailing;
  final Widget? badge;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            // The title and badge share one expanding slot, so the empty space
            // shrinks before the title wraps.
            Expanded(
              child: Row(
                children: [
                  Flexible(child: Text(title, style: context.text.titleMedium)),
                  if (badge != null) ...[
                    const SizedBox(width: AppSpacing.sm),
                    badge!,
                  ],
                ],
              ),
            ),
            ?trailing,
          ],
        ),
        if (subtitle != null) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(subtitle!, style: context.text.bodySmall),
        ],
      ],
    );
  }
}

/// The small uppercase label above a figure.
class Eyebrow extends StatelessWidget {
  const Eyebrow(this.text, {super.key, this.color});

  final String text;
  final Color? color;

  @override
  Widget build(BuildContext context) =>
      Text(text, style: context.text.labelMedium?.copyWith(color: color));
}

/// A tinted panel with an icon: a note, a warning, a refusal.
class Callout extends StatelessWidget {
  const Callout({
    super.key,
    required this.text,
    this.tone = Tone.neutral,
    this.icon,
    this.title,
    this.children = const [],
  });

  final String text;
  final Tone tone;
  final IconData? icon;
  final String? title;

  /// Anything below the text, e.g. reasons or an action.
  final List<Widget> children;

  static IconData _defaultIcon(Tone tone) => switch (tone) {
    Tone.caution => Icons.warning_amber_rounded,
    Tone.negative => Icons.error_outline_rounded,
    Tone.positive => Icons.check_circle_outline_rounded,
    Tone.accent || Tone.neutral => Icons.info_outline_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final fg = tone.foreground(context);
    // Neutral notes read as supporting detail; toned ones carry their colour
    // into the text so the meaning survives a glance.
    final bodyColor = tone == Tone.neutral
        ? context.colors.textSecondary
        : (title == null ? fg : context.scheme.onSurface);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: tone.surface(context),
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 1),
            child: Icon(icon ?? _defaultIcon(tone), size: 18, color: fg),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (title != null) ...[
                  Text(
                    title!,
                    style: context.text.titleSmall?.copyWith(color: fg),
                  ),
                  const SizedBox(height: AppSpacing.xs),
                ],
                if (text.isNotEmpty)
                  Text(
                    tidyBackendMessage(text),
                    style: context.text.bodySmall?.copyWith(color: bodyColor),
                  ),
                ...children,
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Several notes of one kind, as one grouped list rather than a stack of boxes.
///
/// A company can carry four or five warnings. Drawn as separate tinted boxes
/// they buried the result they qualify; as one list under one heading they
/// read as what they are - the small print, still all of it, in one place.
class NoteList extends StatelessWidget {
  const NoteList({
    super.key,
    required this.title,
    required this.notes,
    this.tone = Tone.caution,
  });

  final String title;
  final List<String> notes;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    if (notes.isEmpty) return const SizedBox.shrink();
    final fg = tone.foreground(context);
    return AppCard(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.warning_amber_rounded, size: 18, color: fg),
              const SizedBox(width: AppSpacing.sm),
              Expanded(child: Text(title, style: context.text.titleSmall)),
              if (notes.length > 1) Pill(label: '${notes.length}', tone: tone),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          for (final note in notes)
            Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.md),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    margin: const EdgeInsets.only(top: 8, right: AppSpacing.md),
                    width: 5,
                    height: 5,
                    decoration: BoxDecoration(
                      color: fg,
                      shape: BoxShape.circle,
                    ),
                  ),
                  Expanded(
                    child: Text(
                      tidyBackendMessage(note),
                      style: context.text.bodySmall?.copyWith(
                        color: context.scheme.onSurface,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// A small rounded label: a method, a mode, a count.
class Pill extends StatelessWidget {
  const Pill({
    super.key,
    required this.label,
    this.tone = Tone.neutral,
    this.icon,
    this.outlined = false,
  });

  final String label;
  final Tone tone;
  final IconData? icon;

  /// Outlined for emphasis without fill, e.g. NOT A VALUATION.
  final bool outlined;

  @override
  Widget build(BuildContext context) {
    final fg = tone.foreground(context);
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm + 2,
        vertical: 4,
      ),
      decoration: BoxDecoration(
        color: outlined ? null : tone.surface(context),
        border: outlined ? Border.all(color: fg) : null,
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: fg),
            const SizedBox(width: AppSpacing.xs + 1),
          ],
          Text(
            label,
            style: context.text.labelSmall?.copyWith(
              color: fg,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

/// A ticker, set as an identifier rather than as prose.
class TickerChip extends StatelessWidget {
  const TickerChip(this.ticker, {super.key});

  final String ticker;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 3),
    decoration: BoxDecoration(
      color: context.scheme.surfaceContainerHighest,
      borderRadius: BorderRadius.circular(6),
    ),
    child: Text(
      ticker,
      style: context.text.labelSmall?.copyWith(
        color: context.scheme.onSurface,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.6,
      ),
    ),
  );
}

/// A labelled figure in a row of figures.
class LabelledValue extends StatelessWidget {
  const LabelledValue({
    super.key,
    required this.label,
    required this.value,
    this.valueKey,
    this.emphasis = false,
  });

  final String label;
  final String value;
  final Key? valueKey;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: context.text.labelSmall),
        const SizedBox(height: 2),
        Text(
          value,
          key: valueKey,
          style: emphasis ? context.text.titleLarge : context.text.titleSmall,
        ),
      ],
    );
  }
}
