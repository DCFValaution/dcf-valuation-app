/// The Advanced section: overrides that change the shape of the model rather
/// than one of its numbers.
///
/// Collapsed and off when the screen opens, and it must stay that way. The
/// default valuation is the one the app stands behind; everything in here is
/// the user overruling it, and a reader who never opens this section should
/// see exactly what they saw before it existed.
///
/// The growth fade is the first of these. A single growth rate held to the
/// final year and then a perpetuity at a much lower one puts a cliff in the
/// middle of the model; the fade glides between them instead. Because that
/// changes every projected year rather than one assumption, the resulting
/// rates are listed here in full - a model whose shape a user cannot read
/// back is not one they can argue with.
library;

import 'package:flutter/material.dart';

// The pattern names come from the engine, so the control and the model
// cannot drift apart over a spelling.
import 'dcf_engine.dart' show fadeExponential, fadeLinear;
import 'theme.dart';
import 'ui.dart';


class AdvancedPanel extends StatelessWidget {
  const AdvancedPanel({
    super.key,
    required this.expanded,
    required this.onExpandedChanged,
    required this.fadeEnabled,
    required this.onFadeChanged,
    required this.fadeStartYear,
    required this.onStartYearChanged,
    required this.fadePattern,
    required this.onPatternChanged,
    required this.projectionYears,
    required this.growthPath,
    this.busy = false,
  });

  final bool expanded;
  final ValueChanged<bool> onExpandedChanged;

  final bool fadeEnabled;
  final ValueChanged<bool> onFadeChanged;

  final int fadeStartYear;
  final ValueChanged<int> onStartYearChanged;

  final String fadePattern;
  final ValueChanged<String> onPatternChanged;

  final int projectionYears;

  /// The rate applied in each projected year, in order, as the backend
  /// reported it. Empty until a valuation has arrived.
  final List<double> growthPath;

  final bool busy;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return AppCard(
      key: const Key('advanced-panel'),
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            key: const Key('advanced-header'),
            onTap: () => onExpandedChanged(!expanded),
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.lg),
              child: Row(
                children: [
                  Icon(Icons.tune_rounded,
                      size: 18, color: colors.textSecondary),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Advanced', style: context.text.titleSmall),
                        const SizedBox(height: 2),
                        Text(
                          fadeEnabled
                              ? 'Growth fade on - your override, not the default model'
                              : 'Change the shape of the model, not just its numbers',
                          style: context.text.labelSmall?.copyWith(
                            color: fadeEnabled
                                ? context.scheme.primary
                                : colors.textSecondary,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Icon(
                    expanded
                        ? Icons.expand_less_rounded
                        : Icons.expand_more_rounded,
                    color: colors.textSecondary,
                  ),
                ],
              ),
            ),
          ),
          if (expanded) ...[
            Divider(height: 1, color: colors.hairline),
            Padding(
              padding: const EdgeInsets.all(AppSpacing.lg),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Fade growth to the terminal rate',
                              style: context.text.bodyMedium
                                  ?.copyWith(fontWeight: FontWeight.w600),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              'Hold the starting rate, then glide to the '
                              'terminal rate by the final year, instead of '
                              'dropping to it all at once.',
                              style: context.text.labelSmall
                                  ?.copyWith(color: colors.textSecondary),
                            ),
                          ],
                        ),
                      ),
                      Switch(
                        key: const Key('fade-toggle'),
                        value: fadeEnabled,
                        onChanged: busy ? null : onFadeChanged,
                      ),
                    ],
                  ),
                  if (fadeEnabled) ...[
                    const SizedBox(height: AppSpacing.lg),
                    _StartYear(
                      value: fadeStartYear,
                      projectionYears: projectionYears,
                      onChanged: busy ? null : onStartYearChanged,
                    ),
                    const SizedBox(height: AppSpacing.lg),
                    _Pattern(
                      value: fadePattern,
                      onChanged: busy ? null : onPatternChanged,
                    ),
                    const SizedBox(height: AppSpacing.lg),
                    GrowthPathView(path: growthPath),
                  ],
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// The last year that still grows at the full starting rate.
class _StartYear extends StatelessWidget {
  const _StartYear({
    required this.value,
    required this.projectionYears,
    required this.onChanged,
  });

  final int value;
  final int projectionYears;
  final ValueChanged<int>? onChanged;

  @override
  Widget build(BuildContext context) {
    // The glide needs at least one year to run in, so the last year that can
    // hold the full rate is the one before the final projected year.
    final maximum = projectionYears > 1 ? projectionYears - 1 : 1;
    final current = value.clamp(1, maximum).toDouble();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text('Hold the starting rate through year',
                  style: context.text.bodyMedium),
            ),
            Text(
              '${current.round()}',
              key: const Key('fade-start-year-value'),
              style: context.text.titleSmall
                  ?.copyWith(color: context.scheme.primary),
            ),
          ],
        ),
        Slider(
          key: const Key('fade-start-year'),
          value: current,
          min: 1,
          max: maximum.toDouble(),
          divisions: maximum > 1 ? maximum - 1 : null,
          onChanged: onChanged == null ? null : (v) => onChanged!(v.round()),
        ),
      ],
    );
  }
}

/// Straight line, or decay.
class _Pattern extends StatelessWidget {
  const _Pattern({required this.value, required this.onChanged});

  final String value;
  final ValueChanged<String>? onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Shape of the glide', style: context.text.bodyMedium),
        const SizedBox(height: AppSpacing.sm),
        SegmentedButton<String>(
          key: const Key('fade-pattern'),
          segments: const [
            ButtonSegment(value: fadeLinear, label: Text('Linear')),
            ButtonSegment(value: fadeExponential, label: Text('Exponential')),
          ],
          selected: {value},
          showSelectedIcon: false,
          onSelectionChanged:
              onChanged == null ? null : (s) => onChanged!(s.first),
        ),
        const SizedBox(height: AppSpacing.xs),
        Text(
          value == fadeExponential
              ? 'Equal-proportion decay: falls fast at first, then levels off.'
              : 'Equal steps down, the same number of percentage points a year.',
          style: context.text.labelSmall
              ?.copyWith(color: context.colors.textSecondary),
        ),
      ],
    );
  }
}

/// The rate applied in each projected year, written out.
///
/// The point of showing this is that a fade changes every year of the model at
/// once. A user who cannot see what was applied has swapped one opaque
/// assumption for a more complicated one.
class GrowthPathView extends StatelessWidget {
  const GrowthPathView({super.key, required this.path});

  final List<double> path;

  static String _pct(double v) => '${(v * 100).toStringAsFixed(1)}%';

  /// Consecutive years at the same rate are written as one range, so the flat
  /// run at the start reads as "Yr1-3: 18.0%" rather than three identical
  /// lines.
  static List<String> summarise(List<double> path) {
    if (path.isEmpty) return const [];
    final out = <String>[];
    var runStart = 0;
    for (var i = 1; i <= path.length; i++) {
      if (i < path.length && path[i] == path[runStart]) continue;
      final runEnd = i - 1;
      final label = runStart == runEnd
          ? 'Yr${runStart + 1}'
          : 'Yr${runStart + 1}-${runEnd + 1}';
      out.add('$label: ${_pct(path[runStart])}');
      runStart = i;
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    if (path.isEmpty) {
      return Text(
        'The growth path appears once the valuation has been recalculated.',
        key: const Key('growth-path-empty'),
        style: context.text.labelSmall?.copyWith(color: colors.textSecondary),
      );
    }
    return Container(
      key: const Key('growth-path'),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: context.scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Growth applied each year',
            style:
                context.text.labelSmall?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.md,
            runSpacing: AppSpacing.xs,
            children: [
              for (final entry in summarise(path))
                Text(
                  entry,
                  style: context.text.labelSmall
                      ?.copyWith(color: colors.textSecondary),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'The final year matches the terminal growth rate, so the '
            'perpetuity that follows starts from the same place.',
            style:
                context.text.labelSmall?.copyWith(color: colors.textSecondary),
          ),
        ],
      ),
    );
  }
}
