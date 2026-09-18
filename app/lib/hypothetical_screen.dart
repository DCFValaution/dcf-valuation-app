/// The user-built hypothetical: the last opt-in, and the weakest claim here.
///
/// Every other screen in this app shows something derived from a company's
/// filings. This one does not. The three drivers - revenue growth, the target
/// operating margin, the years to reach it - come from the person using it,
/// for a company whose own figures point the other way, and the screen is
/// arranged so that can never be forgotten:
///
///   * the disclaimer is first, in full, and is the strongest in the app: a
///     solid header band, then the backend's text, then a plain restatement
///     that this is not a valuation and is not derived from the data;
///   * nothing is pre-filled with a flattering number. The sliders start at
///     the backend's neutral placeholders - no growth, no profit - and until
///     the user assumes a profit there is no figure at all, only an
///     invitation to make the claim themselves;
///   * the company's real figures sit directly under the slider that departs
///     from them, in the backend's own words ("you have assumed revenue grows
///     8%; Intel's revenue has actually fallen 5.7% a year");
///   * the figure is labelled a hypothetical the user built, never an
///     intrinsic value and never a speculative estimate, and no upside or
///     downside is drawn against the market price;
///   * the honest refusal is one tap away at all times, and is what Back
///     returns to.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'assumption_sliders.dart';
import 'sensitivity_table.dart';
import 'theme.dart';
import 'ui.dart';
import 'valuation_api.dart';

/// The three drivers, and only these three.
///
/// Wide bounds, because the hypothetical belongs to the person building it;
/// the honesty comes from showing what the company actually did beside each
/// one, not from narrowing what they may assume.
const List<AdjustableAssumption> kHypotheticalDrivers = [
  AdjustableAssumption(
    name: 'revenue_growth',
    label: 'Revenue growth',
    help:
        'How fast you are assuming revenue grows, every year, until the '
        'target margin is reached.',
    min: -0.50,
    max: 1.00,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'target_operating_margin',
    label: 'Target operating margin',
    help:
        'The operating margin you are assuming the company reaches. There '
        'is no figure until this is above zero: a path to profitability '
        'needs a profit, and choosing it is your call, not the app\'s.',
    min: 0.0,
    max: 0.60,
    step: 0.0025,
  ),
  AdjustableAssumption(
    name: 'years_to_target',
    label: 'Years to the target',
    help: 'How long you are assuming the turnaround takes.',
    min: 1,
    max: 15,
    step: 1,
    isPercent: false,
  ),
];

class HypotheticalScreen extends StatefulWidget {
  const HypotheticalScreen({
    super.key,
    required this.api,
    required this.ticker,
    required this.companyName,
    required this.placeholders,
  });

  final ValuationApi api;
  final String ticker;
  final String companyName;

  /// The neutral starting values, from the backend. Not chosen here, so the
  /// app cannot quietly start somewhere friendlier.
  final HypotheticalInputs placeholders;

  @override
  State<HypotheticalScreen> createState() => _HypotheticalScreenState();
}

class _HypotheticalScreenState extends State<HypotheticalScreen> {
  late HypotheticalInputs _inputs = widget.placeholders;
  HypotheticalOutcome? _outcome;
  bool _busy = false;
  Timer? _debounce;
  int _seq = 0;

  @override
  void initState() {
    super.initState();
    // Asked for immediately, so the neutral state is the backend's own answer
    // ("this has no profit in it yet") rather than one the app invented.
    _request();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Map<String, double> get _sliderValues => {
    'revenue_growth': _inputs.revenueGrowth,
    'target_operating_margin': _inputs.targetOperatingMargin,
    'years_to_target': _inputs.yearsToTarget.toDouble(),
  };

  Map<String, double> get _neutralValues => {
    'revenue_growth': widget.placeholders.revenueGrowth,
    'target_operating_margin': widget.placeholders.targetOperatingMargin,
    'years_to_target': widget.placeholders.yearsToTarget.toDouble(),
  };

  Future<void> _request() async {
    final seq = ++_seq;
    setState(() => _busy = true);
    final outcome = await widget.api.hypothetical(widget.ticker, _inputs);
    if (!mounted || seq != _seq) return;
    setState(() {
      _outcome = outcome;
      _busy = false;
    });
  }

  void _onChanged(String name, double value) {
    setState(() {
      _inputs = switch (name) {
        'revenue_growth' => _inputs.copyWith(revenueGrowth: value),
        'target_operating_margin' => _inputs.copyWith(
          targetOperatingMargin: value,
        ),
        'years_to_target' => _inputs.copyWith(yearsToTarget: value.round()),
        _ => _inputs,
      };
    });
  }

  void _onChangeEnd() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), _request);
  }

  void _reset() {
    _debounce?.cancel();
    setState(() => _inputs = widget.placeholders);
    _request();
  }

  /// The company's own figure for this driver, in the backend's words.
  Widget? _noteFor(String name) {
    final outcome = _outcome;
    if (outcome is! HypotheticalBuilt) return null;
    for (final c in outcome.hypothetical.reality) {
      if (c.name == name) return _RealityNote(contrast: c);
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Your hypothetical'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded),
          tooltip: 'Back to the refusal',
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: SafeArea(
        top: false,
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl,
            AppSpacing.sm,
            AppSpacing.xl,
            AppSpacing.xxxl,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                widget.companyName,
                style: context.text.headlineMedium,
                maxLines: 2,
              ),
              const SizedBox(height: AppSpacing.xs),
              Text(
                widget.ticker,
                style: context.text.labelSmall?.copyWith(
                  color: context.colors.textSecondary,
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              _Disclaimer(outcome: _outcome, companyName: widget.companyName),
              const SizedBox(height: AppGap.section),

              ..._figureSection(context),

              const SizedBox(height: AppGap.section),
              AssumptionSliders(
                specs: kHypotheticalDrivers,
                subtitle:
                    'These are yours. Nothing here is derived from the '
                    'company, and nothing will be filled in for you — what '
                    'the company actually did is shown under each one.',
                derived: _neutralValues,
                current: _sliderValues,
                sources: const {
                  'revenue_growth': 'you set this',
                  'target_operating_margin': 'you set this',
                  'years_to_target': 'you set this',
                },
                busy: _busy,
                onChanged: _onChanged,
                onChangeEnd: _onChangeEnd,
                onReset: _reset,
                noteFor: _noteFor,
                statusLine: _StatusLine(busy: _busy, outcome: _outcome),
              ),

              const SizedBox(height: AppGap.section),
              Center(
                child: TextButton.icon(
                  key: const Key('hypothetical-back'),
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.arrow_back_rounded, size: 18),
                  label: const Text('Back to the refusal'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _figureSection(BuildContext context) {
    return switch (_outcome) {
      HypotheticalBuilt(:final hypothetical) => [
        _Figure(hypothetical: hypothetical),
        if (hypothetical.warnings.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.md),
          NoteList(title: 'What this would take', notes: hypothetical.warnings),
        ],
        if (hypothetical.sensitivity != null) ...[
          const SizedBox(height: AppSpacing.md),
          SensitivityTable(
            grid: hypothetical.sensitivity!,
            formatValue: _money,
            title: 'How far your hypothetical swings',
            explanation:
                'Each cell is what a different pair of assumptions would '
                'imply. None of them is more likely than its neighbours: '
                'they are all hypothetical.',
            stale: _busy,
          ),
        ],
        const SizedBox(height: AppSpacing.md),
        _WhyRefused(hypothetical: hypothetical),
      ],
      HypotheticalIncomplete(:final message) => [
        _NoFigureYet(message: message),
      ],
      HypotheticalRefused(:final message, :final reasons) => [
        _Refused(message: message, reasons: reasons),
      ],
      HypotheticalFailure(:final message) => [
        Callout(text: message, tone: Tone.caution),
      ],
      null => [const _Loading()],
    };
  }
}

String _money(double v) => '${v < 0 ? '−' : ''}\$${v.abs().toStringAsFixed(2)}';

/// The strongest disclaimer in the app.
///
/// Drawn louder than the speculative one on purpose: that figure at least
/// extends a company's own trajectory. This one contradicts it.
class _Disclaimer extends StatelessWidget {
  const _Disclaimer({required this.outcome, required this.companyName});

  final HypotheticalOutcome? outcome;
  final String companyName;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final built = outcome is HypotheticalBuilt
        ? (outcome as HypotheticalBuilt).hypothetical
        : null;
    // Text on the solid band takes whichever of black or white the band needs,
    // so it is readable in both themes.
    final onBand =
        ThemeData.estimateBrightnessForColor(colors.negative) == Brightness.dark
        ? Colors.white
        : Colors.black;

    return Container(
      key: const Key('hypothetical-disclaimer'),
      decoration: BoxDecoration(
        color: colors.negativeSurface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: colors.negative, width: 2),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.lg,
              vertical: AppSpacing.md,
            ),
            decoration: BoxDecoration(
              color: colors.negative,
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(AppRadius.card - 2),
              ),
            ),
            child: Row(
              children: [
                Icon(Icons.warning_rounded, size: 20, color: onBand),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    built?.headline ??
                        'A HYPOTHETICAL YOU BUILT — NOT A VALUATION',
                    style: context.text.titleSmall?.copyWith(
                      color: onBand,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 0.4,
                    ),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Said plainly before the backend's longer text, so the point
                // survives even if nothing else is read.
                _Point(
                  text:
                      'You are building this, not the app. The numbers '
                      'below are the ones you choose.',
                ),
                _Point(
                  text:
                      'Nothing here is derived from '
                      '${companyName.replaceAll(RegExp(r"\.$"), "")}\'s '
                      'figures — its own figures point the other way.',
                ),
                _Point(text: 'It is not a valuation and not a price target.'),
                if (built != null) ...[
                  const SizedBox(height: AppSpacing.md),
                  Divider(
                    height: 1,
                    color: colors.negative.withValues(alpha: 0.35),
                  ),
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    built.disclaimer,
                    style: context.text.bodyMedium?.copyWith(height: 1.45),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Point extends StatelessWidget {
  const _Point({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            margin: const EdgeInsets.only(top: 7, right: AppSpacing.md),
            width: 5,
            height: 5,
            decoration: BoxDecoration(
              color: colors.negative,
              shape: BoxShape.circle,
            ),
          ),
          Expanded(
            child: Text(
              text,
              style: context.text.bodyMedium?.copyWith(
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// The figure, drawn so it cannot be mistaken for any of the app's valuations.
class _Figure extends StatelessWidget {
  const _Figure({required this.hypothetical});

  final Hypothetical hypothetical;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final h = hypothetical;

    return Container(
      key: const Key('hypothetical-figure'),
      padding: const EdgeInsets.all(AppSpacing.xl),
      decoration: BoxDecoration(
        color: context.scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppRadius.card),
        // A dashed-looking heavy outline rather than a tinted card: this is
        // not one of the app's answers, and should not look like one.
        border: Border.all(color: colors.negative, width: 1.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'WHAT YOUR ASSUMPTIONS IMPLY',
                  style: context.text.labelMedium?.copyWith(
                    color: colors.textSecondary,
                  ),
                ),
              ),
              const Pill(
                label: 'NOT A VALUATION',
                tone: Tone.negative,
                outlined: true,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          // Deliberately quieter than a valuation's headline figure: same
          // size, no colour of its own, no arrow, no percentage.
          Text(
            _money(h.valuePerShare),
            key: const Key('hypothetical-value'),
            style: context.text.displayLarge?.copyWith(
              fontWeight: FontWeight.w500,
              color: colors.textSecondary,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'This is what your assumptions imply — a hypothetical you built, '
            'not an estimate of what the company is worth.',
            style: context.text.bodySmall,
          ),
          if (h.isNegative) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Negative: even on your own assumptions, the losses on the way '
              'and the debt outweigh the profitable business at the end.',
              style: context.text.bodySmall,
            ),
          ],
          const SizedBox(height: AppSpacing.lg),
          Divider(color: colors.hairline, height: 1),
          const SizedBox(height: AppSpacing.md),
          // The price, with the comparison explicitly declined.
          Text(
            'Market price ${_money(h.currentPrice)} · no comparison is drawn '
            'between the two, deliberately',
            style: context.text.bodySmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

/// The company's own figure, under the slider that departs from it.
class _RealityNote extends StatelessWidget {
  const _RealityNote({required this.contrast});

  final RealityContrast contrast;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final fg = contrast.contradicts ? colors.negative : colors.textSecondary;

    return Padding(
      key: ValueKey('reality-${contrast.name}'),
      padding: const EdgeInsets.only(bottom: AppSpacing.lg),
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: contrast.contradicts
              ? colors.negativeSurface
              : context.scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(AppRadius.control),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(
              contrast.contradicts
                  ? Icons.compare_arrows_rounded
                  : Icons.info_outline_rounded,
              size: 16,
              color: fg,
            ),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Text(
                contrast.statement,
                style: context.text.bodySmall?.copyWith(
                  color: context.scheme.onSurface,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Before the user has assumed a profit: no figure, and an explanation of why
/// the app will not choose one.
class _NoFigureYet extends StatelessWidget {
  const _NoFigureYet({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      key: const Key('hypothetical-incomplete'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.edit_note_rounded,
                size: 20,
                color: context.colors.textSecondary,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text('No figure yet', style: context.text.titleSmall),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Text(message, style: context.text.bodyMedium),
        ],
      ),
    );
  }
}

/// Not offered for this company at all - the honest refusal, again.
class _Refused extends StatelessWidget {
  const _Refused({required this.message, required this.reasons});

  final String message;
  final List<String> reasons;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return AppCard(
      key: const Key('hypothetical-refused'),
      tone: Tone.caution,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.block_rounded, size: 19, color: colors.caution),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(
                  'Not even a hypothetical',
                  style: context.text.titleSmall?.copyWith(
                    color: colors.caution,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Text(message, style: context.text.bodyMedium),
          for (final reason in reasons) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              reason,
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Both refusals that led here, kept visible under the figure.
class _WhyRefused extends StatelessWidget {
  const _WhyRefused({required this.hypothetical});

  final Hypothetical hypothetical;

  @override
  Widget build(BuildContext context) {
    final reasons = [
      ...hypothetical.whyStandardRefused,
      ...hypothetical.whySpeculativeRefused,
    ];
    if (reasons.isEmpty) return const SizedBox.shrink();

    return AppCard(
      key: const Key('hypothetical-why-refused'),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.xs,
      ),
      child: ExpansionTile(
        dense: true,
        visualDensity: VisualDensity.compact,
        childrenPadding: const EdgeInsets.only(bottom: AppSpacing.md),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        title: Text(
          'Why the app itself would not value this company',
          style: context.text.bodySmall?.copyWith(
            color: context.scheme.onSurface,
            fontWeight: FontWeight.w600,
          ),
        ),
        children: [
          for (final reason in reasons)
            Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.sm),
              child: Text(
                reason,
                style: context.text.bodySmall?.copyWith(
                  color: context.colors.textSecondary,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.busy, required this.outcome});

  final bool busy;
  final HypotheticalOutcome? outcome;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final (IconData icon, String text) = busy
        ? (Icons.sync_rounded, 'Recalculating from your assumptions…')
        : switch (outcome) {
            HypotheticalBuilt() => (
              Icons.check_rounded,
              'The figure above is your assumptions, nothing else',
            ),
            HypotheticalIncomplete() => (
              Icons.edit_outlined,
              'Assume a profit above 0% to see what it would imply',
            ),
            _ => (Icons.pending_outlined, 'No figure'),
          };

    return Row(
      children: [
        Icon(icon, size: 15, color: colors.textSecondary),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(
            text,
            style: context.text.labelSmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
        ),
      ],
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(vertical: AppSpacing.xxl),
    child: Center(
      child: SizedBox(
        width: 26,
        height: 26,
        child: CircularProgressIndicator(strokeWidth: 2.5),
      ),
    ),
  );
}
