import 'package:flutter/material.dart';

import 'theme.dart';

/// One assumption the user can move, with a range wide enough to be
/// interesting but not absurd.
///
/// The bounds mirror what the backend will accept - it clamps derived values
/// to defensible ranges and rejects a WACC outside roughly 4-20% - so the
/// sliders cannot ask for something the model would refuse.
class AdjustableAssumption {
  final String name;
  final String label;
  final String help;
  final double min;
  final double max;
  final int divisions;

  const AdjustableAssumption({
    required this.name,
    required this.label,
    required this.help,
    required this.min,
    required this.max,
    required this.divisions,
  });
}

const List<AdjustableAssumption> kAdjustable = [
  AdjustableAssumption(
    name: 'revenue_growth',
    label: 'Revenue growth',
    help: 'Annual growth applied across the forecast',
    min: 0.0,
    max: 0.25,
    divisions: 50, // 0.5% steps
  ),
  AdjustableAssumption(
    name: 'operating_margin',
    label: 'Operating margin',
    help: 'EBIT as a share of revenue',
    min: 0.0,
    max: 0.60,
    divisions: 60, // 1% steps
  ),
  AdjustableAssumption(
    name: 'wacc',
    label: 'WACC (discount rate)',
    help: 'Overrides the CAPM build-up',
    min: 0.04,
    max: 0.20,
    divisions: 32, // 0.5% steps
  ),
  AdjustableAssumption(
    name: 'terminal_growth',
    label: 'Terminal growth',
    help: 'Perpetual growth; must stay below WACC',
    min: 0.0,
    max: 0.06,
    divisions: 24, // 0.25% steps
  ),
];

String formatPercent(double v) => '${(v * 100).toStringAsFixed(2)}%';

class AssumptionSliders extends StatelessWidget {
  const AssumptionSliders({
    super.key,
    required this.derived,
    required this.current,
    required this.sources,
    required this.busy,
    required this.onChanged,
    required this.onChangeEnd,
    required this.onReset,
    required this.statusLine,
  });

  /// The backend's own values, as returned with no overrides. The baseline
  /// that "modified" is measured against.
  final Map<String, double> derived;

  /// Current slider positions.
  final Map<String, double> current;

  /// Backend provenance label per assumption, e.g. "derived" or "default".
  final Map<String, String> sources;

  final bool busy;
  final void Function(String name, double value) onChanged;

  /// Fired when a drag finishes, so the backend can confirm the local figure.
  final VoidCallback onChangeEnd;
  final VoidCallback onReset;

  /// Rendered beneath the sliders: says whether the figure above is a local
  /// preview or has been confirmed by the backend.
  final Widget statusLine;

  bool _isModified(String name) {
    final a = derived[name];
    final b = current[name];
    if (a == null || b == null) return false;
    // Slider steps are coarse; treat sub-0.01% differences as unchanged.
    return (a - b).abs() > 0.00005;
  }

  int get modifiedCount =>
      kAdjustable.where((a) => _isModified(a.name)).length;

  /// Gordon growth needs WACC > g. Catching it here gives an explanation in
  /// place, rather than a round trip that comes back as "not suitable".
  bool get _growthExceedsWacc {
    final wacc = current['wacc'];
    final g = current['terminal_growth'];
    if (wacc == null || g == null) return false;
    return g >= wacc;
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final modified = modifiedCount;
    final blocked = _growthExceedsWacc;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Text('Assumptions', style: context.text.titleMedium),
            const SizedBox(width: AppSpacing.md),
            if (modified > 0)
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.md, vertical: 4),
                decoration: BoxDecoration(
                  color: colors.accentSurface,
                  borderRadius: BorderRadius.circular(AppRadius.chip),
                ),
                child: Text(
                  '$modified changed',
                  style: context.text.labelSmall?.copyWith(
                    color: context.scheme.primary,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            const Spacer(),
            if (modified > 0)
              TextButton.icon(
                onPressed: busy ? null : onReset,
                icon: const Icon(Icons.restart_alt_rounded, size: 17),
                label: const Text('Reset'),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(
                      horizontal: AppSpacing.md),
                  visualDensity: VisualDensity.compact,
                ),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.xs),
        Text(
          'Derived from the filings. Drag to explore — the valuation updates '
          'as you go.',
          style: context.text.bodySmall?.copyWith(color: colors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.xl),
        ...kAdjustable.map((spec) => _SliderRow(
              spec: spec,
              value: current[spec.name] ?? derived[spec.name] ?? spec.min,
              derivedValue: derived[spec.name],
              source: sources[spec.name] ?? '',
              modified: _isModified(spec.name),
              // Sliders stay live during a background confirmation: blocking
              // them would make the panel feel stuck for a network round trip
              // the user never asked for.
              enabled: true,
              onChanged: (v) => onChanged(spec.name, v),
              onChangeEnd: onChangeEnd,
            )),
        if (blocked) ...[
          const SizedBox(height: AppSpacing.sm),
          Container(
            padding: const EdgeInsets.all(AppSpacing.lg),
            decoration: BoxDecoration(
              color: colors.negativeSurface,
              borderRadius: BorderRadius.circular(AppRadius.control),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.error_outline_rounded,
                    size: 17, color: colors.negative),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    'Terminal growth must stay below WACC. Nothing grows faster '
                    'than its discount rate forever — the maths gives an '
                    'infinite or negative value.',
                    style: context.text.bodySmall
                        ?.copyWith(color: colors.negative),
                  ),
                ),
              ],
            ),
          ),
        ],
        const SizedBox(height: AppSpacing.lg),
        statusLine,
      ],
    );
  }
}

class _SliderRow extends StatelessWidget {
  const _SliderRow({
    required this.spec,
    required this.value,
    required this.derivedValue,
    required this.source,
    required this.modified,
    required this.enabled,
    required this.onChanged,
    required this.onChangeEnd,
  });

  final AdjustableAssumption spec;
  final double value;
  final double? derivedValue;
  final String source;
  final bool modified;
  final bool enabled;
  final ValueChanged<double> onChanged;
  final VoidCallback onChangeEnd;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final accent = context.scheme.primary;

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg, AppSpacing.md, AppSpacing.lg, AppSpacing.sm),
      decoration: BoxDecoration(
        color: context.scheme.surface,
        borderRadius: BorderRadius.circular(AppRadius.control),
        // A changed row is outlined in the accent rather than merely tinted,
        // so "I moved this" survives a glance down a list of four.
        border: Border.all(
          color: modified ? accent.withValues(alpha: 0.55) : colors.hairline,
          width: modified ? 1.4 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  spec.label,
                  style: context.text.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Text(
                formatPercent(value),
                style: context.text.titleSmall
                    ?.copyWith(color: modified ? accent : null),
              ),
            ],
          ),
          const SizedBox(height: 2),
          // When changed, keep the original in view so the user always knows
          // what they moved away from.
          if (modified && derivedValue != null)
            Row(
              children: [
                Icon(Icons.edit_rounded, size: 11, color: accent),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'was ${formatPercent(derivedValue!)} · $source',
                  style: context.text.labelSmall
                      ?.copyWith(color: accent, fontWeight: FontWeight.w600),
                ),
              ],
            )
          else
            Text(
              '$source · ${spec.help}',
              style: context.text.labelSmall
                  ?.copyWith(color: colors.textSecondary),
            ),
          Slider(
            value: value.clamp(spec.min, spec.max),
            min: spec.min,
            max: spec.max,
            divisions: spec.divisions,
            label: formatPercent(value),
            onChanged: enabled ? onChanged : null,
            onChangeEnd: enabled ? (_) => onChangeEnd() : null,
          ),
        ],
      ),
    );
  }
}
