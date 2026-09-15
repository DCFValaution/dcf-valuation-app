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

  /// How far one notch of the slider moves the value, chosen per assumption.
  ///
  /// Discount rates and terminal growth step by 0.1%: the figure is most
  /// sensitive to them, and a 0.5% jump in WACC can move a valuation by a
  /// fifth. Growth and margins step by 0.25%. Counts of years step by one,
  /// and only one - there is no such thing as 5.3 years of growth here, and
  /// the backend rejects anything fractional. Finer than 0.1% would put steps
  /// under a fingertip's width on a phone; typing a value covers the rest.
  final double step;

  /// False for a count rather than a rate - a number of years is not 500%.
  final bool isPercent;

  const AdjustableAssumption({
    required this.name,
    required this.label,
    required this.help,
    required this.min,
    required this.max,
    required this.step,
    this.isPercent = true,
  });

  /// Slider notches between [min] and [max].
  int get divisions => ((max - min) / step).round();

  /// Whole numbers only: a count of years.
  bool get isWholeNumber => !isPercent;

  /// The nearest value this slider can actually hold.
  ///
  /// A slider's own arithmetic leaves float noise - 10.3% can arrive as
  /// 0.10300000000000001 - which would otherwise travel to the backend and
  /// show up as a spurious "changed". Rates are held to a millionth.
  double snap(double value) {
    if (isWholeNumber) return value.roundToDouble().clamp(min, max);
    final notches = ((value - min) / step).round();
    final snapped = min + notches * step;
    return (snapped * 1e6).round() / 1e6;
  }

  String format(double value) => isPercent
      ? formatPercent(value)
      : '${value.round()} ${value.round() == 1 ? 'year' : 'years'}';

  /// Bounds as the user types them: "4.00%" and "20.00%", or "1" and "15".
  String get _minText => isPercent ? formatPercent(min) : '${min.round()}';
  String get _maxText =>
      isPercent ? formatPercent(max) : '${max.round()} years';
}

const List<AdjustableAssumption> kAdjustable = [
  AdjustableAssumption(
    name: 'revenue_growth',
    label: 'Revenue growth',
    help: 'Annual growth applied across the forecast',
    min: 0.0,
    max: 0.25,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'operating_margin',
    label: 'Operating margin',
    help: 'EBIT as a share of revenue',
    min: 0.0,
    max: 0.60,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'wacc',
    label: 'WACC (discount rate)',
    help: 'Overrides the CAPM build-up',
    min: 0.04,
    max: 0.20,
    step: 0.001,
  ),
  AdjustableAssumption(
    name: 'terminal_growth',
    label: 'Terminal growth',
    help: 'Perpetual growth; must stay below WACC',
    min: 0.0,
    max: 0.06,
    step: 0.001,
  ),
];

/// The dividend discount model's own levers.
///
/// A different model has different inputs: there is no revenue growth or
/// operating margin to move, because the projection is of dividends. Bounds
/// again mirror what the backend accepts, so no slider position can produce a
/// request it would reject.
const List<AdjustableAssumption> kDdmAdjustable = [
  AdjustableAssumption(
    name: 'dividend_growth',
    label: 'Dividend growth',
    help: 'Annual growth through the first stage',
    min: 0.0,
    max: 0.20,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'high_growth_years',
    label: 'Growth stage length',
    help: 'How long dividends grow at that rate before settling',
    min: 1,
    max: 15,
    step: 1,
    isPercent: false,
  ),
  AdjustableAssumption(
    name: 'cost_of_equity',
    label: 'Cost of equity (discount rate)',
    help: 'Overrides the CAPM build-up',
    min: 0.04,
    max: 0.20,
    step: 0.001,
  ),
  AdjustableAssumption(
    name: 'terminal_growth',
    label: 'Terminal growth',
    help: 'Perpetual growth; must stay below the cost of equity',
    min: 0.0,
    max: 0.06,
    step: 0.001,
  ),
];

/// The speculative estimate's levers: the path to profitability it assumes.
///
/// These are the assumptions the figure rests on most heavily, and the ones
/// the disclaimer names. Ranges stay inside what the backend accepts - years
/// are whole numbers from 1 to 15 there - and the target margin starts above
/// zero, since a company that never earns a margin has no path to value.
const List<AdjustableAssumption> kSpeculativeAdjustable = [
  AdjustableAssumption(
    name: 'years_to_profitability',
    label: 'Years to profitability',
    help: 'How long until the target margin is reached',
    min: 1,
    max: 15,
    step: 1,
    isPercent: false,
  ),
  AdjustableAssumption(
    name: 'target_operating_margin',
    label: 'Target operating margin',
    help: 'The margin it is assumed to reach, and keep',
    min: 0.02,
    max: 0.40,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'speculative_revenue_growth',
    label: 'Revenue growth on the way',
    help: 'Annual growth until profitability',
    min: 0.0,
    max: 0.40,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'wacc',
    label: 'Discount rate',
    help: 'Overrides the build-up and its 10% floor',
    min: 0.06,
    max: 0.25,
    step: 0.001,
  ),
];

String formatPercent(double v) => '${(v * 100).toStringAsFixed(2)}%';

/// The message for terminal growth at or above the discount rate - one
/// wording, used by the panel and by typed entry, so a dragged value and a
/// typed one are refused in the same words.
String growthGuardMessage(List<AdjustableAssumption> specs) {
  final rateLabel = discountRateName(specs) == 'cost_of_equity'
      ? 'the cost of equity'
      : 'WACC';
  return 'Terminal growth must stay below $rateLabel. Nothing grows faster '
      'than its discount rate forever — the maths gives an infinite or '
      'negative value.';
}

/// The outcome of checking a typed value: exactly one of [value] or [error].
class ExactValue {
  final double? value;
  final String? error;
  const ExactValue.accepted(double this.value) : error = null;
  const ExactValue.rejected(String this.error) : value = null;
}

/// Read what someone typed into a slider's exact-value box.
///
/// Rates are typed as percentages - "10.3" or "10.3%" means 10.3% - and held
/// to a hundredth of a percent, finer than any slider step but not so fine
/// that float noise leaks through. Counts must be whole numbers.
///
/// Out-of-range input is rejected with the range, not clamped: silently
/// turning a typed 25% into 20% would put a number on screen the user did not
/// ask for and might not notice. Terminal growth at or above the discount
/// rate is rejected with the same message the panel shows when a drag gets
/// there - checked only when the value typed is one of that pair, so an
/// unrelated field is never refused for a problem it did not cause.
ExactValue parseExactValue({
  required AdjustableAssumption spec,
  required String text,
  required Map<String, double> current,
  required List<AdjustableAssumption> specs,
}) {
  var cleaned = text.trim().toLowerCase().replaceAll(',', '.');
  cleaned = cleaned.replaceAll(RegExp(r'%|years?|y'), '').trim();

  final parsed = double.tryParse(cleaned);
  if (parsed == null || !parsed.isFinite) {
    return ExactValue.rejected(
      spec.isPercent
          ? 'Enter a number, like 10.3 for 10.3%.'
          : 'Enter a whole number of years.',
    );
  }

  final double value;
  if (spec.isWholeNumber) {
    if (parsed != parsed.roundToDouble()) {
      return ExactValue.rejected(
        'Enter a whole number of years, from ${spec._minText} to ${spec._maxText}.',
      );
    }
    value = parsed;
  } else {
    value = (parsed / 100 * 10000).round() / 10000;
  }

  const tolerance = 1e-9;
  if (value < spec.min - tolerance || value > spec.max + tolerance) {
    return ExactValue.rejected(
      'Enter a value from ${spec._minText} to ${spec._maxText}.',
    );
  }

  final rateName = discountRateName(specs);
  if ((spec.name == 'terminal_growth' || spec.name == rateName) &&
      growthExceedsDiscountRate({...current, spec.name: value}, specs)) {
    return ExactValue.rejected(growthGuardMessage(specs));
  }

  return ExactValue.accepted(value);
}

/// The discount rate among [specs] - 'wacc' for a DCF, 'cost_of_equity' for a
/// DDM. Returns null if the panel offers neither.
String? discountRateName(List<AdjustableAssumption> specs) {
  for (final name in const ['wacc', 'cost_of_equity']) {
    if (specs.any((s) => s.name == name)) return name;
  }
  return null;
}

/// Whether terminal growth has been dragged to or past the discount rate,
/// which no growth-perpetuity model can survive.
///
/// Shared with the screen so the warning, the disabled export and the panel
/// itself cannot disagree about when the inputs are unusable.
bool growthExceedsDiscountRate(
  Map<String, double> current,
  List<AdjustableAssumption> specs,
) {
  final rateName = discountRateName(specs);
  if (rateName == null) return false;
  final rate = current[rateName];
  final g = current['terminal_growth'];
  if (rate == null || g == null) return false;
  return g >= rate;
}

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
    this.specs = kAdjustable,
    this.subtitle =
        'Derived from the filings. Drag to explore — the valuation '
        'updates as you go.',
  });

  /// The line under the heading. Overridden where "valuation" would be the
  /// wrong word - the speculative screen above all.
  final String subtitle;

  /// Which levers this panel offers, which depends on the model that produced
  /// the figure. Defaults to the DCF's.
  final List<AdjustableAssumption> specs;

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

  int get modifiedCount => specs.where((a) => _isModified(a.name)).length;

  /// Gordon growth needs a discount rate above g, whichever model is in use -
  /// WACC for the DCF, cost of equity for the DDM. Catching it here gives an
  /// explanation in place, rather than a round trip that comes back as "not
  /// suitable".
  bool get _growthExceedsDiscountRate =>
      growthExceedsDiscountRate(current, specs);

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final modified = modifiedCount;
    final blocked = _growthExceedsDiscountRate;

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
                  horizontal: AppSpacing.md,
                  vertical: 4,
                ),
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
                    horizontal: AppSpacing.md,
                  ),
                  visualDensity: VisualDensity.compact,
                ),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.xs),
        Text(
          subtitle,
          style: context.text.bodySmall?.copyWith(color: colors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.xl),
        ...specs.map(
          (spec) => _SliderRow(
            spec: spec,
            value: current[spec.name] ?? derived[spec.name] ?? spec.min,
            derivedValue: derived[spec.name],
            source: sources[spec.name] ?? '',
            modified: _isModified(spec.name),
            // Sliders stay live during a background confirmation: blocking
            // them would make the panel feel stuck for a network round trip
            // the user never asked for.
            enabled: true,
            onChanged: (v) => onChanged(spec.name, spec.snap(v)),
            onChangeEnd: onChangeEnd,
            // A typed value takes exactly the path a drag does: the same
            // change, then the same end-of-gesture confirmation.
            onExactValue: (v) {
              onChanged(spec.name, v);
              onChangeEnd();
            },
            validate: (text) => parseExactValue(
              spec: spec,
              text: text,
              current: current,
              specs: specs,
            ),
          ),
        ),
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
                Icon(
                  Icons.error_outline_rounded,
                  size: 17,
                  color: colors.negative,
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    growthGuardMessage(specs),
                    style: context.text.bodySmall?.copyWith(
                      color: colors.negative,
                    ),
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
    required this.onExactValue,
    required this.validate,
  });

  final AdjustableAssumption spec;
  final double value;
  final double? derivedValue;
  final String source;
  final bool modified;
  final bool enabled;
  final ValueChanged<double> onChanged;
  final VoidCallback onChangeEnd;
  final ValueChanged<double> onExactValue;
  final ExactValue Function(String text) validate;

  /// Past this many notches the tick marks merge into a dotted smear, and the
  /// value on the right says where the thumb is better than they can.
  static const _maxVisibleTicks = 30;

  Future<void> _typeExactValue(BuildContext context) async {
    final typed = await showExactValueDialog(
      context: context,
      spec: spec,
      current: value,
      validate: validate,
    );
    if (typed != null) onExactValue(typed);
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final accent = context.scheme.primary;

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.sm,
      ),
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
              // The value is also the way to type one exactly. The pencil
              // says so, and the padding makes a fingertip-sized target of a
              // few characters of text.
              Semantics(
                button: true,
                label: 'Type an exact ${spec.label.toLowerCase()}',
                child: InkWell(
                  key: ValueKey('exact-${spec.name}'),
                  onTap: enabled ? () => _typeExactValue(context) : null,
                  borderRadius: BorderRadius.circular(AppRadius.chip),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppSpacing.sm,
                      vertical: 6,
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          spec.format(value),
                          style: context.text.titleSmall?.copyWith(
                            color: modified ? accent : null,
                            decoration: TextDecoration.underline,
                            decorationStyle: TextDecorationStyle.dotted,
                            decorationColor: colors.textSecondary,
                          ),
                        ),
                        const SizedBox(width: AppSpacing.xs),
                        Icon(
                          Icons.edit_outlined,
                          size: 14,
                          color: colors.textSecondary,
                        ),
                      ],
                    ),
                  ),
                ),
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
                  'was ${spec.format(derivedValue!)} · $source',
                  style: context.text.labelSmall?.copyWith(
                    color: accent,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            )
          else
            Text(
              '$source · ${spec.help}',
              style: context.text.labelSmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              tickMarkShape: spec.divisions > _maxVisibleTicks
                  ? SliderTickMarkShape.noTickMark
                  : null,
            ),
            child: Slider(
              value: value.clamp(spec.min, spec.max),
              min: spec.min,
              max: spec.max,
              divisions: spec.divisions,
              label: spec.format(value),
              onChanged: enabled ? onChanged : null,
              onChangeEnd: enabled ? (_) => onChangeEnd() : null,
            ),
          ),
        ],
      ),
    );
  }
}

/// A small box for typing one assumption exactly.
///
/// Returns the accepted value, or null if the user cancelled. Nothing is
/// applied until the input passes [validate]; a rejection is shown in place,
/// with the typed text kept, so it can be corrected rather than retyped.
Future<double?> showExactValueDialog({
  required BuildContext context,
  required AdjustableAssumption spec,
  required double current,
  required ExactValue Function(String text) validate,
}) {
  return showDialog<double>(
    context: context,
    builder: (_) =>
        _ExactValueDialog(spec: spec, current: current, validate: validate),
  );
}

/// Owns its text controller, so it is disposed only once the dialog has
/// finished animating away - not the moment a value is chosen, while the
/// field is still on screen.
class _ExactValueDialog extends StatefulWidget {
  const _ExactValueDialog({
    required this.spec,
    required this.current,
    required this.validate,
  });

  final AdjustableAssumption spec;
  final double current;
  final ExactValue Function(String text) validate;

  @override
  State<_ExactValueDialog> createState() => _ExactValueDialogState();
}

class _ExactValueDialogState extends State<_ExactValueDialog> {
  late final TextEditingController _controller;
  String? _error;

  @override
  void initState() {
    super.initState();
    final spec = widget.spec;
    final initial = spec.isPercent
        ? (widget.current * 100).toStringAsFixed(2)
        : '${widget.current.round()}';
    // Selected, so typing replaces it rather than appending to it.
    _controller = TextEditingController(text: initial)
      ..selection = TextSelection(baseOffset: 0, extentOffset: initial.length);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final result = widget.validate(_controller.text);
    if (result.value != null) {
      Navigator.of(context).pop(result.value);
    } else {
      setState(() => _error = result.error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final spec = widget.spec;
    return AlertDialog(
      title: Text(spec.label),
      content: TextField(
        key: const Key('exact-value-field'),
        controller: _controller,
        autofocus: true,
        keyboardType: TextInputType.numberWithOptions(
          decimal: spec.isPercent,
          signed: false,
        ),
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => _submit(),
        onChanged: (_) {
          if (_error != null) setState(() => _error = null);
        },
        decoration: InputDecoration(
          suffixText: spec.isPercent ? '%' : 'years',
          helperText: 'From ${spec._minText} to ${spec._maxText}',
          errorText: _error,
          errorMaxLines: 4,
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('exact-value-set'),
          onPressed: _submit,
          child: const Text('Set'),
        ),
      ],
    );
  }
}
