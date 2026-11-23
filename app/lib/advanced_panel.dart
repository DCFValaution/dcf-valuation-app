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
import 'assumption_sliders.dart';
import 'theme.dart';
import 'ui.dart';
import 'formatting.dart';

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
    this.declineFellBack = false,
    this.currentRoic,
    this.wacc = 0.0,
    this.reinvestmentMethod = kReinvestmentCapexPath,
    this.onReinvestmentChanged,
    this.marginTargetEnabled = false,
    this.onMarginTargetEnabledChanged,
    this.marginTarget = 0.0,
    this.onMarginTargetChanged,
    this.marginConvergenceYear = 5,
    this.onMarginConvergenceYearChanged,
    this.onMarginChangeEnd,
  });

  /// The optional claim that the margin goes somewhere other than where the
  /// filings left it. Off unless the reader asks for it.
  final bool marginTargetEnabled;
  final ValueChanged<bool>? onMarginTargetEnabledChanged;
  final double marginTarget;
  final ValueChanged<double>? onMarginTargetChanged;
  final int marginConvergenceYear;
  final ValueChanged<int>? onMarginConvergenceYearChanged;
  final VoidCallback? onMarginChangeEnd;

  /// True when an exponential decline was asked for and the straight line
  /// used instead, because a ratio needs both of its rates positive.
  final bool declineFellBack;

  /// What the company earns on its capital now, and what that capital
  /// costs. Shown beside the long-run return, which is unreadable without
  /// them: 20% means nothing until you know it earns 32% today.
  final double? currentRoic;
  final double wacc;

  /// How the forecast pays for growth. Two methods, named in the backend.
  final String reinvestmentMethod;
  final ValueChanged<String>? onReinvestmentChanged;

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
      // AppCard paints a rounded background but is a plain Container, so the
      // header's ink would land on whatever Material is behind the card -
      // under the opaque white, invisible except where it escaped the curve,
      // which drew grey square corners for exactly as long as a tap lasted.
      // A transparent Material of the card's own shape gives the ink a
      // surface above that background and clips it, and clips the expanding
      // section's bottom corners with it.
      child: Material(
        type: MaterialType.transparency,
        borderRadius: BorderRadius.circular(AppRadius.card),
        clipBehavior: Clip.antiAlias,
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
                    Icon(
                      Icons.tune_rounded,
                      size: 18,
                      color: colors.textSecondary,
                    ),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Advanced', style: context.text.titleSmall),
                          const SizedBox(height: 2),
                          Text(
                            'The growth path, the shape of its decline, '
                            'and how growth is paid for',
                            style: context.text.labelSmall?.copyWith(
                              color: colors.textSecondary,
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
            // Reveals by changing its own height rather than by fading a
            // large subtree: an opacity layer over this content would cost a
            // saveLayer on every frame it ran, and the web build is the
            // surface least able to afford one.
            //
            // The sizing animates; the contents are simply present or absent.
            // A cross-fade here would mean drawing both states at once, which
            // is the expensive thing this avoids.
            //
            AnimatedSize(
              duration: expanded
                  ? AppMotion.forContext(context, AppMotion.enter)
                  : AppMotion.forContext(context, AppMotion.exit),
              curve: AppMotion.panelCurve,
              alignment: Alignment.topCenter,
              child: !expanded
                  ? const SizedBox(width: double.infinity, height: 0)
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Divider(height: 1, color: colors.hairline),
                        Padding(
                          padding: const EdgeInsets.all(AppSpacing.lg),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              // The path first, because it is the thing
                              // the controls beneath it change. The on/off
                              // switch and the start-year control are gone:
                              // the decline is structural now rather than an
                              // override, so there is nothing to turn off
                              // and no year to start it in.
                              GrowthPathView(path: growthPath),
                              const SizedBox(height: AppSpacing.lg),
                              Text(
                                'Year 1 is the latest year\u2019s own growth; '
                                'years 2\u20135 hold the measured trend; from '
                                'year 6 it declines to the terminal rate, '
                                'reaching it exactly at year '
                                '$projectionYears.',
                                style: context.text.labelSmall?.copyWith(
                                  color: colors.textSecondary,
                                ),
                              ),
                              const SizedBox(height: AppSpacing.lg),
                              _Pattern(
                                value: fadePattern,
                                onChanged: busy ? null : onPatternChanged,
                              ),
                              if (declineFellBack) ...[
                                const SizedBox(height: AppSpacing.sm),
                                Text(
                                  'An exponential decline is a ratio between '
                                  'two rates, and one of these is not above '
                                  'zero \u2014 so the straight line was used.',
                                  style: context.text.labelSmall?.copyWith(
                                    color: colors.caution,
                                  ),
                                ),
                              ],
                              const SizedBox(height: AppSpacing.lg),
                              Divider(height: 1, color: colors.hairline),
                              const SizedBox(height: AppSpacing.lg),
                              _Reinvestment(
                                value: reinvestmentMethod,
                                onChanged: busy ? null : onReinvestmentChanged,
                              ),
                              if (currentRoic != null) ...[
                                const SizedBox(height: AppSpacing.md),
                                Text(
                                  'For reference: it earns '
                                  '${(currentRoic! * 100).toStringAsFixed(1)}% '
                                  'on its capital now, against a '
                                  '${(wacc * 100).toStringAsFixed(1)}% cost of '
                                  'capital. The long-run return sits between '
                                  'them, and sets what growth costs.',
                                  style: context.text.labelSmall?.copyWith(
                                    color: colors.textSecondary,
                                  ),
                                ),
                              ],
                              if (onMarginTargetEnabledChanged != null) ...[
                                const SizedBox(height: AppSpacing.lg),
                                Divider(height: 1, color: colors.hairline),
                                const SizedBox(height: AppSpacing.lg),
                                MarginTargetControls(
                                  enabled: marginTargetEnabled,
                                  onEnabledChanged:
                                      onMarginTargetEnabledChanged!,
                                  target: marginTarget,
                                  onTargetChanged:
                                      onMarginTargetChanged ?? (_) {},
                                  year: marginConvergenceYear,
                                  onYearChanged:
                                      onMarginConvergenceYearChanged ?? (_) {},
                                  onChangeEnd: onMarginChangeEnd ?? () {},
                                  busy: busy,
                                ),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

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
          onSelectionChanged: onChanged == null
              ? null
              : (s) => onChanged!(s.first),
        ),
        const SizedBox(height: AppSpacing.xs),
        Text(
          value == fadeExponential
              ? 'Equal-proportion decay: falls fast at first, then levels off.'
              : 'Equal steps down, the same number of percentage points a year.',
          style: context.text.labelSmall?.copyWith(
            color: context.colors.textSecondary,
          ),
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

  static String _pct(double v) => percent(v);

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
            style: context.text.labelSmall?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.md,
            runSpacing: AppSpacing.xs,
            children: [
              for (final entry in summarise(path))
                Text(
                  entry,
                  style: context.text.labelSmall?.copyWith(
                    color: colors.textSecondary,
                  ),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'The final year matches the terminal growth rate, so the '
            'perpetuity that follows starts from the same place.',
            style: context.text.labelSmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

/// How the forecast pays for growth.
///
/// Two methods, and the difference is what investment is a share OF. The
/// capital-spending path makes it a share of revenue, so a company spends
/// whether or not it is growing; the sales-to-capital rule makes it a share
/// of the growth itself, which is what Damodaran's models do and what the
/// terminal rule here already does.
///
/// The first is the default because the second rests on invested capital
/// from the balance sheet, and buybacks make that figure unreliable in
/// exactly the way they made book equity unreliable for the discount rate.
/// It is offered rather than hidden, because for a company mid-build-out it
/// is the more defensible of the two.
const String kReinvestmentCapexPath = 'capex_path';
const String kReinvestmentSalesToCapital = 'sales_to_capital';

class _Reinvestment extends StatelessWidget {
  const _Reinvestment({required this.value, required this.onChanged});

  final String value;
  final ValueChanged<String>? onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'How growth is paid for',
          style: context.text.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 2),
        Text(
          'Investment as a share of revenue, or as a share of the growth it '
          'buys.',
          style: context.text.labelSmall?.copyWith(
            color: context.colors.textSecondary,
          ),
        ),
        const SizedBox(height: AppSpacing.md),
        SegmentedButton<String>(
          key: const Key('reinvestment-toggle'),
          showSelectedIcon: false,
          segments: const [
            ButtonSegment(
              value: kReinvestmentCapexPath,
              label: Text('Capital spending'),
            ),
            ButtonSegment(
              value: kReinvestmentSalesToCapital,
              label: Text('Sales to capital'),
            ),
          ],
          selected: {value},
          onSelectionChanged: onChanged == null
              ? null
              : (selection) => onChanged!(selection.first),
        ),
      ],
    );
  }
}
