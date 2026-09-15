/// The opt-in speculative estimate for a loss-making company.
///
/// This is the one screen in the app where a number could genuinely mislead.
/// A loss-maker has no profits to value, so any figure here is built on an
/// imagined path to profitability - and a per-share number, however it is
/// labelled, looks like something to act on. Everything below is arranged
/// around that:
///
///   * it is only ever reached by an explicit tap from the refusal, and it is
///     a separate route over that refusal, so Back returns to the honest
///     answer exactly as it was left;
///   * the backend's disclaimer comes first, in full, under a warning header
///     that is louder than anything on a valuation screen;
///   * the figure is never called "intrinsic value", is drawn in caution
///     colours rather than the green or red of an upside, and is not given an
///     "implied upside" at all - a percentage against the price would read as
///     a buy or sell signal;
///   * every assumption the disclaimer names is on a slider with its source.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'assumption_sliders.dart';
import 'formatting.dart';
import 'sensitivity_table.dart';
import 'theme.dart';
import 'ui.dart';
import 'valuation_api.dart';

class SpeculativeScreen extends StatefulWidget {
  const SpeculativeScreen({
    super.key,
    required this.api,
    required this.ticker,
    required this.companyName,
  });

  final ValuationApi api;
  final String ticker;
  final String companyName;

  @override
  State<SpeculativeScreen> createState() => _SpeculativeScreenState();
}

enum _Status { confirmed, stale, confirming }

class _SpeculativeScreenState extends State<SpeculativeScreen> {
  SpeculativeOutcome? _outcome;
  bool _loading = true;

  Map<String, double> _derived = {};
  Map<String, String> _sources = {};
  Map<String, double> _slider = {};

  _Status _status = _Status.confirmed;
  Timer? _confirmTimer;
  int _seq = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _confirmTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final seq = ++_seq;
    final outcome = await widget.api.speculative(widget.ticker);
    if (!mounted || seq != _seq) return;

    setState(() {
      _loading = false;
      _outcome = outcome;
      if (outcome is SpeculativeSuccess) {
        final e = outcome.estimate;
        _derived = {
          for (final spec in kSpeculativeAdjustable)
            if (e.assumption(spec.name) != null)
              spec.name: e.assumption(spec.name)!.value,
        };
        _sources = {
          for (final spec in kSpeculativeAdjustable)
            if (e.assumption(spec.name) != null)
              spec.name: e.assumption(spec.name)!.source,
        };
        _slider = Map.of(_derived);
      }
      _status = _Status.confirmed;
    });
  }

  Map<String, double> get _overrides => {
    for (final spec in kSpeculativeAdjustable)
      if (_derived[spec.name] != null &&
          _slider[spec.name] != null &&
          (_derived[spec.name]! - _slider[spec.name]!).abs() > 0.00005)
        spec.name: _slider[spec.name]!,
  };

  void _onChanged(String name, double value) {
    setState(() {
      _slider[name] = value;
      // No local engine for this model: the figure above belongs to the old
      // assumptions until the backend answers, and says so.
      _status = _Status.stale;
    });
  }

  void _onChangeEnd() {
    _confirmTimer?.cancel();
    _confirmTimer = Timer(const Duration(milliseconds: 350), _recalculate);
  }

  Future<void> _recalculate() async {
    final seq = ++_seq;
    setState(() => _status = _Status.confirming);

    final outcome = await widget.api.speculative(
      widget.ticker,
      overrides: _overrides,
    );
    if (!mounted || seq != _seq) return;

    setState(() {
      _outcome = outcome;
      _status = _Status.confirmed;
    });
  }

  Future<void> _reset() async {
    _confirmTimer?.cancel();
    setState(() {
      _slider = Map.of(_derived);
      _status = _Status.confirming;
    });
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Speculative estimate'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded),
          tooltip: 'Back to the refusal',
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: SafeArea(
        top: false,
        child: _loading
            ? const _Loading()
            : SingleChildScrollView(
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
                    ..._body(context),
                    if (_derived.isNotEmpty) ...[
                      const SizedBox(height: AppSpacing.xxl),
                      AssumptionSliders(
                        specs: kSpeculativeAdjustable,
                        subtitle:
                            'These are what the estimate rests on. Move '
                            'them and see how far the figure swings - that is '
                            'the point of looking.',
                        derived: _derived,
                        current: _slider,
                        sources: _sources,
                        busy: _status == _Status.confirming,
                        onChanged: _onChanged,
                        onChangeEnd: _onChangeEnd,
                        onReset: _reset,
                        statusLine: _StatusLine(status: _status),
                      ),
                    ],
                    const SizedBox(height: AppSpacing.xxl),
                    Center(
                      child: TextButton.icon(
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

  List<Widget> _body(BuildContext context) {
    final outcome = _outcome;
    return switch (outcome) {
      SpeculativeSuccess(:final estimate) => [
        _DisclaimerPanel(
          headline: estimate.headline,
          disclaimer: estimate.disclaimer,
        ),
        const SizedBox(height: AppSpacing.xl),
        _EstimateFigure(estimate: estimate),
        // Straight after the figure, because here it is the argument: the
        // same company, a few years or margin points apart, and the number
        // changes by multiples or changes sign.
        if (estimate.sensitivity != null) ...[
          const SizedBox(height: AppSpacing.lg),
          SensitivityTable(
            grid: estimate.sensitivity!,
            formatValue: _EstimateFigure._money,
            title: 'How far this estimate swings',
            explanation:
                'Each cell is the estimate if the company reached a '
                'different margin, sooner or later. Nothing here says which '
                'of them - if any - will happen. That is what makes it '
                'speculative.',
            stale: _status != _Status.confirmed,
          ),
        ],
        if (estimate.warnings.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.md),
          NoteList(title: 'Keep in mind', notes: estimate.warnings),
        ],
      ],
      SpeculativeRefused(:final message, :final reasons) => [
        _RefusedPanel(message: message, reasons: reasons),
      ],
      SpeculativeFailure(:final message) => [
        Callout(text: message, tone: Tone.caution),
        const SizedBox(height: AppSpacing.md),
        Center(
          child: OutlinedButton(
            onPressed: () {
              setState(() => _loading = true);
              _load();
            },
            child: const Text('Try again'),
          ),
        ),
      ],
      null => const [],
    };
  }
}

/// The disclaimer, first and loudest.
///
/// Deliberately stronger than the honesty note under a valuation: a solid
/// warning header, a bordered panel, full-contrast body text rather than the
/// secondary grey the ordinary note uses. It is the first thing on the screen
/// so it is read before the number rather than after it.
class _DisclaimerPanel extends StatelessWidget {
  const _DisclaimerPanel({required this.headline, required this.disclaimer});

  final String headline;
  final String disclaimer;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    // Text on the solid header takes whichever of black or white the header
    // colour needs: the negative red is dark in light mode and pale in dark.
    final onHeader =
        ThemeData.estimateBrightnessForColor(colors.negative) == Brightness.dark
        ? Colors.white
        : Colors.black;
    return Container(
      key: const Key('speculative-disclaimer'),
      decoration: BoxDecoration(
        color: colors.negativeSurface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: colors.negative, width: 1.5),
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
                top: Radius.circular(AppRadius.card - 1.5),
              ),
            ),
            child: Row(
              children: [
                Icon(Icons.warning_rounded, size: 20, color: onHeader),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    headline.replaceAll(' - ', ' — '),
                    style: context.text.titleSmall?.copyWith(
                      color: onHeader,
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
            child: Text(
              tidyBackendMessage(disclaimer),
              style: context.text.bodyMedium?.copyWith(height: 1.45),
            ),
          ),
        ],
      ),
    );
  }
}

/// The figure, drawn so it cannot be mistaken for a valuation.
class _EstimateFigure extends StatelessWidget {
  const _EstimateFigure({required this.estimate});

  final SpeculativeEstimate estimate;

  static String _money(double v) =>
      '${v < 0 ? '−' : ''}\$${v.abs().toStringAsFixed(2)}';

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return AppCard(
      tone: Tone.caution,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'SPECULATIVE ESTIMATE PER SHARE',
                  style: context.text.labelMedium?.copyWith(
                    color: colors.caution,
                  ),
                ),
              ),
              const Pill(
                label: 'NOT A VALUATION',
                tone: Tone.caution,
                outlined: true,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          // Caution colour and a lighter weight than a valuation's headline
          // figure: present, legible, and visibly not the same kind of number.
          Text(
            _money(estimate.valuePerShare),
            key: const Key('speculative-figure'),
            style: context.text.displayLarge?.copyWith(
              color: colors.caution,
              fontWeight: FontWeight.w500,
            ),
          ),
          if (estimate.isNegative) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              'Negative: even on these assumptions, the losses on the way and '
              'the debt outweigh the profitable business at the end. It does '
              'not mean a share is worth less than nothing - a shareholder can '
              'lose at most what they paid - but it is no case for value either.',
              style: context.text.bodySmall,
            ),
          ],
          const SizedBox(height: AppSpacing.lg),
          Divider(color: colors.caution.withValues(alpha: 0.3), height: 1),
          const SizedBox(height: AppSpacing.md),
          // The price for context only. No "upside" and no percentage: set
          // against the market price, a speculative figure reads as a signal.
          Text(
            'Market price \$${estimate.currentPrice.toStringAsFixed(2)}',
            style: context.text.bodySmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

/// When even a path to profitability does not apply under the assumptions
/// chosen: say so in place of the figure, and leave the sliders to move back.
class _RefusedPanel extends StatelessWidget {
  const _RefusedPanel({required this.message, required this.reasons});

  final String message;
  final List<String> reasons;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return AppCard(
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
                  'No speculative estimate',
                  style: context.text.titleSmall?.copyWith(
                    color: colors.caution,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Text(tidyBackendMessage(message), style: context.text.bodyMedium),
          for (final reason in reasons) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              tidyBackendMessage(reason),
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

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.status});

  final _Status status;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final (IconData icon, String text) = switch (status) {
      _Status.confirmed => (
        Icons.check_rounded,
        'Figure matches these assumptions',
      ),
      _Status.stale => (
        Icons.pending_outlined,
        'Figure not yet updated · release to recalculate',
      ),
      _Status.confirming => (Icons.sync_rounded, 'Recalculating…'),
    };
    return Row(
      children: [
        Icon(icon, size: 15, color: colors.textSecondary),
        const SizedBox(width: AppSpacing.sm),
        Text(
          text,
          style: context.text.labelSmall?.copyWith(color: colors.textSecondary),
        ),
      ],
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(
            width: 26,
            height: 26,
            child: CircularProgressIndicator(strokeWidth: 2.5),
          ),
          const SizedBox(height: AppSpacing.xl),
          Text(
            'Building a speculative estimate',
            style: context.text.titleMedium,
          ),
        ],
      ),
    );
  }
}
