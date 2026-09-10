import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import 'assumption_sliders.dart';
import 'dcf_engine.dart';
import 'formatting.dart';
import 'theme.dart';
import 'valuation_api.dart';

const String _xlsxMimeType =
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

/// Where the figure on screen came from.
enum ValueStatus {
  /// Straight from the backend.
  confirmed,

  /// Computed locally while dragging; not yet checked against the backend.
  preview,

  /// A background confirmation request is in flight.
  confirming,

  /// The backend disagreed with the local preview and its value replaced it.
  corrected,
}

void main() => runApp(const DcfApp());

class DcfApp extends StatelessWidget {
  const DcfApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Intrinsic',
      debugShowCheckedModeBanner: false,
      theme: buildLightTheme(),
      darkTheme: buildDarkTheme(),
      // Follows the device setting; both themes are designed, neither is a
      // tinted afterthought of the other.
      themeMode: ThemeMode.system,
      home: const ValuationScreen(),
    );
  }
}

class ValuationScreen extends StatefulWidget {
  const ValuationScreen({super.key});

  @override
  State<ValuationScreen> createState() => _ValuationScreenState();
}

class _ValuationScreenState extends State<ValuationScreen> {
  final _controller = TextEditingController();
  final _api = ValuationApi();

  ValuationResult? _result;
  bool _loading = false;

  /// True once a request has been running long enough that a cold start is
  /// the likely explanation, so the spinner can say so.
  ///
  /// The free tier sleeps after about fifteen minutes idle and takes roughly
  /// a minute to wake. Without this the user watches an unexplained spinner
  /// for a minute and concludes the app is broken - the wait is the same
  /// either way, but only one of them is legible.
  bool _wakingServer = false;
  Timer? _wakingTimer;

  /// Set while the launch ping is still in flight, so a valuation started
  /// immediately after opening the app explains itself too.
  bool _serverAwake = false;

  /// The ticker the displayed result belongs to, so the header cannot drift
  /// out of sync with the text field while the user types a new one.
  String _resultTicker = '';

  /// The backend's own assumption values for the current ticker, captured
  /// from the un-overridden run. The baseline "reset" returns to and that
  /// "changed" is measured against.
  Map<String, double> _derived = {};
  Map<String, String> _sources = {};

  /// Current slider positions.
  Map<String, double> _slider = {};

  /// Intrinsic value from the un-overridden run, kept so the effect of moving
  /// the sliders is visible rather than having to be remembered.
  double? _baselineValue;

  /// True while re-valuing with overrides, as opposed to a fresh search.
  bool _revaluing = false;

  /// True while the workbook is being built and downloaded.
  bool _exporting = false;

  // --- Local preview -------------------------------------------------------

  /// The complete assumption set from the backend, including the ones without
  /// sliders. The local engine needs all nine, not just the four on screen.
  Map<String, double> _fullAssumptions = {};

  /// Equity risk premium, carried only so the honesty note can be rebuilt.
  double? _equityRiskPremium;

  /// Locally computed result shown while dragging. Null when the displayed
  /// figure is the backend's own.
  DcfResult? _preview;

  ValueStatus _status = ValueStatus.confirmed;

  /// Set when the backend's answer differed from the local preview.
  String? _correctionNote;

  /// Debounces confirmation, and lets a stale response be discarded when the
  /// user has dragged again since it was sent.
  Timer? _confirmTimer;
  int _confirmSeq = 0;

  /// How far the local engine may differ from the backend before the figure
  /// is treated as wrong rather than as floating-point noise. A tenth of a
  /// cent per share is far below anything displayed.
  static const double _tolerance = 0.001;

  @override
  void initState() {
    super.initState();
    _pingServer();
  }

  /// Start waking the server as soon as the app opens.
  ///
  /// `/health` touches no upstream service, so this costs the backend
  /// essentially nothing, and the spin-up then overlaps with the user typing
  /// a ticker rather than being paid for in full by their first valuation.
  /// Failure is deliberately silent: the real request will report anything
  /// genuinely wrong, and an error about a ping the user never asked for
  /// would be noise.
  Future<void> _pingServer() async {
    final awake = await _api.wakeUp();
    if (!mounted) return;
    setState(() => _serverAwake = awake);
  }

  /// Start the timer that explains a long wait as a cold start.
  void _beginWaitingFeedback() {
    _wakingTimer?.cancel();
    _wakingServer = false;
    _wakingTimer = Timer(ValuationApi.wakingThreshold, () {
      if (mounted) setState(() => _wakingServer = true);
    });
  }

  void _endWaitingFeedback() {
    _wakingTimer?.cancel();
    _wakingTimer = null;
    _wakingServer = false;
  }

  @override
  void dispose() {
    _confirmTimer?.cancel();
    _wakingTimer?.cancel();
    _controller.dispose();
    _api.dispose();
    super.dispose();
  }

  /// The backend result currently held, if the last outcome was a success.
  ValuationSuccess? get _success =>
      _result is ValuationSuccess ? _result as ValuationSuccess : null;

  // Displayed figures: the local preview when one exists, otherwise the
  // backend's. Keeping this in one place stops the headline number and the
  // note disagreeing about which source they came from.
  double? get _displayIntrinsic =>
      _preview?.intrinsicValuePerShare ?? _success?.intrinsicValuePerShare;

  double? get _displayUpside =>
      _preview?.upsideDownside ?? _success?.upsideDownside;

  String get _displayNote {
    final success = _success;
    if (success == null) return '';
    if (_preview == null) return success.note;

    // Rebuilt locally so the sentence's figures move with the slider.
    return buildHonestyNote(
      intrinsicValuePerShare: _preview!.intrinsicValuePerShare,
      companyName: success.companyName,
      wacc: _slider['wacc'] ?? _fullAssumptions['wacc'] ?? 0,
      terminalGrowth:
          _slider['terminal_growth'] ?? _fullAssumptions['terminal_growth'] ?? 0,
      equityRiskPremium: _equityRiskPremium,
    );
  }

  /// Mirrors the slider panel's own check, so the export button and the
  /// warning agree about when the inputs are unusable.
  bool get _growthExceedsWacc {
    final wacc = _slider['wacc'];
    final g = _slider['terminal_growth'];
    if (wacc == null || g == null) return false;
    return g >= wacc;
  }

  Map<String, double> get _activeOverrides {
    final out = <String, double>{};
    for (final spec in kAdjustable) {
      final derived = _derived[spec.name];
      final current = _slider[spec.name];
      if (derived == null || current == null) continue;
      if ((derived - current).abs() > 0.00005) out[spec.name] = current;
    }
    return out;
  }

  /// A fresh search: clears any slider state, since assumptions belong to the
  /// company they were derived from.
  Future<void> _submit() async {
    final ticker = _controller.text.trim().toUpperCase();
    if (_loading) return;

    FocusScope.of(context).unfocus();
    setState(() {
      _loading = true;
      _result = null;
      _resultTicker = ticker;
      _derived = {};
      _sources = {};
      _slider = {};
      _baselineValue = null;
      _preview = null;
      _fullAssumptions = {};
      _equityRiskPremium = null;
      _correctionNote = null;
      _status = ValueStatus.confirmed;
    });
    _confirmTimer?.cancel();
    _confirmSeq++;
    _beginWaitingFeedback();

    final result = await _api.value(ticker);
    if (!mounted) return;

    _endWaitingFeedback();
    setState(() {
      _result = result;
      _loading = false;
      _serverAwake = result is! ValuationFailure;
      if (result is ValuationSuccess) {
        // Every assumption, including those without sliders, so the local
        // engine has the complete set to work from.
        _fullAssumptions = {
          for (final a in result.assumptions) a.name: a.value
        };
        _equityRiskPremium =
            result.assumption('equity_risk_premium')?.value;
        _derived = {
          for (final spec in kAdjustable)
            if (result.assumption(spec.name) != null)
              spec.name: result.assumption(spec.name)!.value,
        };
        _sources = {
          for (final spec in kAdjustable)
            if (result.assumption(spec.name) != null)
              spec.name: result.assumption(spec.name)!.source,
        };
        _slider = Map.of(_derived);
        _baselineValue = result.intrinsicValuePerShare;
      }
    });
  }

  /// Recompute locally as the slider moves. No network, no await - this runs
  /// on every drag frame, so it has to be cheap and synchronous.
  void _onSliderChanged(String name, double value) {
    final success = _success;
    if (success == null) return;

    setState(() {
      _slider[name] = value;
      _correctionNote = null;

      final assumptions =
          DcfAssumptions.fromMap({..._fullAssumptions, ..._slider});
      try {
        _preview = runDcf(success.baseYear, assumptions);
        _status = ValueStatus.preview;
      } on DcfInputError {
        // Terminal growth has crossed WACC. The slider panel explains it;
        // hold the last good figure rather than showing a broken one.
        _preview = null;
        _status = ValueStatus.preview;
      }
    });
  }

  /// The drag has ended: ask the backend to confirm what we computed.
  ///
  /// Debounced, because releasing and grabbing another slider in quick
  /// succession would otherwise fire a request per gesture.
  void _onSliderChangeEnd() {
    _confirmTimer?.cancel();
    _confirmTimer = Timer(const Duration(milliseconds: 350), _confirm);
  }

  /// Check the local preview against the backend, and defer to it if they
  /// differ. The local engine is a convenience; the backend decides.
  Future<void> _confirm() async {
    final success = _success;
    if (success == null || _resultTicker.isEmpty) return;

    final overrides = Map<String, double>.of(_activeOverrides);
    final seq = ++_confirmSeq;
    final localValue = _preview?.intrinsicValuePerShare;

    setState(() => _status = ValueStatus.confirming);

    final result = await _api.value(_resultTicker, overrides: overrides);
    // A later drag has already superseded this request.
    if (!mounted || seq != _confirmSeq) return;

    setState(() {
      if (result is ValuationSuccess) {
        final backendValue = result.intrinsicValuePerShare;
        final diverged = localValue != null &&
            (backendValue - localValue).abs() > _tolerance;

        _result = result;
        _preview = null; // the backend's figure is now the displayed one
        _status = diverged ? ValueStatus.corrected : ValueStatus.confirmed;
        _correctionNote = diverged
            ? 'Local preview showed \$${localValue.toStringAsFixed(2)}; '
                'the backend calculated \$${backendValue.toStringAsFixed(2)}. '
                'Showing the backend figure.'
            : null;
      } else {
        // A refusal or error under these assumptions - surface it as the
        // result, since it is the backend's real answer.
        _result = result;
        _preview = null;
        _status = ValueStatus.confirmed;
      }
    });
  }

  /// Download the workbook for the current ticker and hand it to the platform
  /// share sheet.
  ///
  /// The overrides sent are the current slider positions, so the spreadsheet
  /// matches what is on screen - including changes that are still only a local
  /// preview, since the backend rebuilds the model from those same inputs.
  Future<void> _exportExcel() async {
    if (_exporting || _resultTicker.isEmpty) return;

    setState(() => _exporting = true);
    final result =
        await _api.downloadExcel(_resultTicker, overrides: _activeOverrides);
    if (!mounted) return;

    switch (result) {
      case ExcelSuccess(:final bytes, :final filename):
        try {
          // The app's own documents directory needs no storage permission,
          // and the share sheet can read from it via the plugin's provider.
          final dir = await getApplicationDocumentsDirectory();
          final file = File('${dir.path}/$filename');
          await file.writeAsBytes(bytes, flush: true);
          if (!mounted) return;

          setState(() => _exporting = false);
          _showMessage('Saved $filename (${(bytes.length / 1024).round()} KB)');

          await SharePlus.instance.share(
            ShareParams(
              files: [XFile(file.path, mimeType: _xlsxMimeType)],
              subject: '$_resultTicker DCF model',
              text: 'DCF model for $_resultTicker',
            ),
          );
        } catch (e) {
          if (!mounted) return;
          setState(() => _exporting = false);
          _showMessage('Downloaded, but could not save or share it: $e',
              isError: true);
        }

      case ExcelNotSuitable(:final message):
        setState(() => _exporting = false);
        _showMessage(message, isError: true);

      case ExcelFailure(:final message):
        setState(() => _exporting = false);
        _showMessage(message, isError: true);
    }
  }

  void _showMessage(String text, {bool isError = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(text),
        backgroundColor: isError ? const Color(0xFFB3261E) : null,
        duration: Duration(seconds: isError ? 6 : 3),
      ));
  }

  /// Back to the backend's own assumptions.
  Future<void> _reset() async {
    if (_revaluing || _resultTicker.isEmpty) return;
    _confirmTimer?.cancel();
    final seq = ++_confirmSeq;

    setState(() {
      _slider = Map.of(_derived);
      _preview = null;
      _correctionNote = null;
      _revaluing = true;
      _status = ValueStatus.confirming;
    });

    final result = await _api.value(_resultTicker);
    if (!mounted || seq != _confirmSeq) return;

    setState(() {
      _revaluing = false;
      _result = result;
      _status = ValueStatus.confirmed;
    });
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.xl,
        title: Row(
          children: [
            const _BrandMark(size: 26),
            const SizedBox(width: AppSpacing.md),
            const Text('Intrinsic'),
          ],
        ),
      ),
      body: SafeArea(
        top: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(
                  AppSpacing.xl, AppSpacing.sm, AppSpacing.xl, AppSpacing.lg),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Expanded(
                    child: TextField(
                      controller: _controller,
                      textCapitalization: TextCapitalization.characters,
                      textInputAction: TextInputAction.go,
                      autocorrect: false,
                      enabled: !_loading,
                      style: context.text.titleMedium?.copyWith(
                        letterSpacing: 0.6,
                        fontFeatures: const [],
                      ),
                      inputFormatters: [
                        UpperCaseFormatter(),
                        FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9.\-]')),
                        LengthLimitingTextInputFormatter(12),
                      ],
                      decoration: InputDecoration(
                        hintText: 'Search a ticker',
                        prefixIcon: Icon(Icons.search_rounded,
                            size: 20, color: colors.textSecondary),
                        prefixIconConstraints: const BoxConstraints(
                            minWidth: 44, minHeight: 24),
                      ),
                      onSubmitted: (_) => _submit(),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.md),
                  SizedBox(
                    height: 54,
                    child: FilledButton(
                      onPressed: _loading ? null : _submit,
                      child: const Text('Value'),
                    ),
                  ),
                ],
              ),
            ),
            Expanded(child: _buildResultArea()),
          ],
        ),
      ),
    );
  }

  Widget _buildResultArea() {
    if (_loading) {
      // Once a request has run past the threshold, a sleeping free-tier
      // instance is the likeliest cause. Saying so turns an alarming silent
      // wait into an expected one - the delay is identical either way, but
      // an unexplained minute reads as a broken app.
      final waking = _wakingServer && !_serverAwake;
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xxl),
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
                waking ? 'Waking up the server' : 'Valuing $_resultTicker',
                style: context.text.titleMedium,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: AppSpacing.xs),
              Text(
                waking
                    ? 'This can take up to a minute on the first use after a '
                        'while. Later valuations are quick.'
                    : 'Reading filings and deriving assumptions',
                style: context.text.bodySmall,
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
    }

    final result = _result;
    if (result == null) {
      return const _EmptyState();
    }

    // The backend's outcomes are distinct by design; render each on its own
    // terms rather than collapsing them into "worked" and "didn't".
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.xl, 0, AppSpacing.xl, AppSpacing.xxxl),
      child: switch (result) {
        ValuationSuccess() => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _SuccessCard(
                result: result,
                intrinsicValue: _displayIntrinsic ?? result.intrinsicValuePerShare,
                upsideDownside: _displayUpside ?? result.upsideDownside,
                note: _displayNote,
                status: _status,
                // Only meaningful once something has actually been changed.
                baselineValue:
                    (_activeOverrides.isEmpty) ? null : _baselineValue,
                correctionNote: _correctionNote,
              ),
              const SizedBox(height: AppSpacing.xl),
              _ExportButton(
                busy: _exporting,
                // Terminal growth at or above WACC would make the backend
                // refuse; do not offer an export that cannot succeed.
                blocked: _growthExceedsWacc,
                overrideCount: _activeOverrides.length,
                onPressed: _exportExcel,
              ),
              if (_derived.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xxl),
                AssumptionSliders(
                  derived: _derived,
                  current: _slider,
                  sources: _sources,
                  busy: _revaluing,
                  onChanged: _onSliderChanged,
                  onChangeEnd: _onSliderChangeEnd,
                  onReset: _reset,
                  statusLine: _StatusLine(status: _status),
                ),
              ],
            ],
          ),
        ValuationNotSuitable() => _NotSuitableCard(result: result),
        ValuationFailure() => _FailureCard(result: result),
      },
    );
  }
}

/// Keeps the field visually uppercase as the user types, so "aapl" reads as
/// the ticker it will actually be sent as.
class UpperCaseFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(
      TextEditingValue oldValue, TextEditingValue newValue) {
    return newValue.copyWith(text: newValue.text.toUpperCase());
  }
}

/// The app's mark: a rising line with a marked endpoint.
///
/// Drawn rather than shipped as an asset so it stays crisp at any size and
/// picks up the accent colour of whichever theme is active.
class _BrandMark extends StatelessWidget {
  const _BrandMark({this.size = 28});

  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _BrandMarkPainter(context.scheme.primary)),
    );
  }
}

class _BrandMarkPainter extends CustomPainter {
  const _BrandMarkPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    final stroke = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = w * 0.11
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    // A four-point ascent: down, up, up - the shape of a recovery.
    final path = Path()
      ..moveTo(w * 0.14, h * 0.70)
      ..lineTo(w * 0.38, h * 0.48)
      ..lineTo(w * 0.56, h * 0.62)
      ..lineTo(w * 0.86, h * 0.24);
    canvas.drawPath(path, stroke);

    canvas.drawCircle(
      Offset(w * 0.86, h * 0.24),
      w * 0.11,
      Paint()..color = color,
    );

    // A grounding baseline, kept light so the ascent stays dominant.
    canvas.drawLine(
      Offset(w * 0.14, h * 0.86),
      Offset(w * 0.86, h * 0.86),
      Paint()
        ..color = color.withValues(alpha: 0.35)
        ..strokeWidth = w * 0.08
        ..strokeCap = StrokeCap.round,
    );
  }

  @override
  bool shouldRepaint(_BrandMarkPainter oldDelegate) =>
      oldDelegate.color != color;
}

/// First-run state: explains what the app does before any search, rather than
/// leaving an empty screen with a lone icon.
class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.xl, AppSpacing.sm, AppSpacing.xl, AppSpacing.xxxl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 60,
            height: 60,
            decoration: BoxDecoration(
              color: colors.accentSurface,
              borderRadius: BorderRadius.circular(AppRadius.control + 4),
            ),
            child: const Center(child: _BrandMark(size: 32)),
          ),
          const SizedBox(height: AppSpacing.xl),
          Text('Value any listed company',
              style: context.text.headlineMedium),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'A discounted cash flow model built from the company’s own '
            'filings — not from anyone’s opinion of it.',
            style: context.text.bodyMedium?.copyWith(
              color: colors.textSecondary,
            ),
          ),
          const SizedBox(height: AppSpacing.xxl),
          const _FeatureRow(
            icon: Icons.auto_graph_rounded,
            title: 'Assumptions from the filings',
            detail: 'Growth, margin, tax and WACC derived per company, '
                'each labelled with where it came from.',
          ),
          const _FeatureRow(
            icon: Icons.tune_rounded,
            title: 'Adjust and see it move',
            detail: 'Drag any assumption and the valuation updates as you go.',
          ),
          const _FeatureRow(
            icon: Icons.grid_on_rounded,
            title: 'Export a live model',
            detail: 'A spreadsheet of real formulas you can keep arguing with.',
          ),
          const SizedBox(height: AppSpacing.xxl),
          Text('TRY', style: context.text.labelMedium),
          const SizedBox(height: AppSpacing.md),
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.sm,
            children: const [
              _TickerHint('AAPL'),
              _TickerHint('MSFT'),
              _TickerHint('NVDA'),
              _TickerHint('GOOGL'),
            ],
          ),
        ],
      ),
    );
  }
}

class _FeatureRow extends StatelessWidget {
  const _FeatureRow({
    required this.icon,
    required this.title,
    required this.detail,
  });

  final IconData icon;
  final String title;
  final String detail;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xl),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 19, color: context.scheme.primary),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: context.text.titleSmall),
                const SizedBox(height: 2),
                Text(detail,
                    style: context.text.bodySmall
                        ?.copyWith(color: colors.textSecondary)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// A worked example, not a control - tapping is deliberately not wired up, so
/// this pass stays styling only.
class _TickerHint extends StatelessWidget {
  const _TickerHint(this.ticker);

  final String ticker;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Container(
      padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md, vertical: AppSpacing.sm),
      decoration: BoxDecoration(
        color: context.scheme.surface,
        borderRadius: BorderRadius.circular(AppRadius.chip),
        border: Border.all(color: colors.hairline),
      ),
      child: Text(
        ticker,
        style: context.text.labelSmall?.copyWith(
          color: colors.textSecondary,
          letterSpacing: 1.0,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// Downloads the backend-built workbook.
///
/// Shown only alongside a successful valuation: the not-suitable and error
/// cards never render it, so there is no button offering to export something
/// that does not exist.
class _ExportButton extends StatelessWidget {
  const _ExportButton({
    required this.busy,
    required this.blocked,
    required this.overrideCount,
    required this.onPressed,
  });

  final bool busy;
  final bool blocked;
  final int overrideCount;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final label = overrideCount == 0
        ? 'Export to Excel'
        : 'Export to Excel · $overrideCount adjusted';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: (busy || blocked) ? null : onPressed,
            icon: busy
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.grid_on_rounded, size: 18),
            label: Text(busy ? 'Building workbook…' : label),
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          blocked
              ? 'Set terminal growth below WACC before exporting.'
              : 'A live-formula model built by the backend, matching the '
                  'assumptions below.',
          style: context.text.labelSmall?.copyWith(
            color: blocked ? colors.negative : colors.textSecondary,
          ),
        ),
      ],
    );
  }
}

/// Distinguishes a live local preview from a backend-confirmed figure.
///
/// Without this the user cannot tell whether the number they are looking at
/// has been checked, which matters when the whole point of the backend is to
/// be the authority.
class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.status});

  final ValueStatus status;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    final (IconData icon, String text, Color fg, Color bg) = switch (status) {
      ValueStatus.confirmed => (
          Icons.verified_rounded,
          'Confirmed by the backend',
          colors.positive,
          colors.positiveSurface,
        ),
      ValueStatus.preview => (
          Icons.bolt_rounded,
          'Live preview · on device',
          colors.caution,
          colors.cautionSurface,
        ),
      ValueStatus.confirming => (
          Icons.sync_rounded,
          'Confirming…',
          colors.textSecondary,
          context.scheme.surfaceContainerHighest,
        ),
      ValueStatus.corrected => (
          Icons.published_with_changes_rounded,
          'Corrected to the backend figure',
          colors.negative,
          colors.negativeSurface,
        ),
    };

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md, vertical: 6),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(AppRadius.chip),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (status == ValueStatus.confirming)
              SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(
                    strokeWidth: 1.8, color: fg),
              )
            else
              Icon(icon, size: 14, color: fg),
            const SizedBox(width: AppSpacing.sm),
            Text(text,
                style: context.text.labelSmall
                    ?.copyWith(color: fg, fontWeight: FontWeight.w600)),
          ],
        ),
      ),
    );
  }
}

class _SuccessCard extends StatelessWidget {
  const _SuccessCard({
    required this.result,
    required this.intrinsicValue,
    required this.upsideDownside,
    required this.note,
    required this.status,
    this.baselineValue,
    this.correctionNote,
  });

  final ValuationSuccess result;

  /// Displayed figures, which may come from the local preview rather than
  /// from [result].
  final double intrinsicValue;
  final double upsideDownside;
  final String note;
  final ValueStatus status;

  /// The value before any overrides. Non-null only when overrides are active,
  /// so the effect of the sliders is visible instead of remembered.
  final double? baselineValue;

  /// Set when the backend overruled the local preview.
  final String? correctionNote;

  String _money(double v) => '\$${v.toStringAsFixed(2)}';

  String _percent(double fraction) {
    final pct = fraction * 100;
    final sign = pct >= 0 ? '+' : '';
    return '$sign${pct.toStringAsFixed(1)}%';
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final up = upsideDownside > 0;
    final accent = up ? colors.positive : colors.negative;
    final accentSurface = up ? colors.positiveSurface : colors.negativeSurface;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(result.companyName,
            style: context.text.headlineMedium, maxLines: 2),
        const SizedBox(height: AppSpacing.xs),
        Row(
          children: [
            Text(result.ticker,
                style: context.text.labelSmall?.copyWith(
                  color: colors.textSecondary,
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w700,
                )),
            const SizedBox(width: AppSpacing.sm),
            Container(width: 3, height: 3, decoration: BoxDecoration(
                color: colors.textSecondary, shape: BoxShape.circle)),
            const SizedBox(width: AppSpacing.sm),
            Flexible(
              child: Text(result.sector,
                  style: context.text.bodySmall
                      ?.copyWith(color: colors.textSecondary),
                  overflow: TextOverflow.ellipsis),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.xl),

        // --- Hero -----------------------------------------------------------
        Card(
          child: Padding(
            padding: const EdgeInsets.all(AppSpacing.xl),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('INTRINSIC VALUE PER SHARE',
                    style: context.text.labelMedium),
                const SizedBox(height: AppSpacing.md),
                // No implicit animation on the number itself: it must track
                // the finger exactly, and a tween would lag behind the drag.
                Text(_money(intrinsicValue),
                    style: context.text.displayLarge),
                if (baselineValue != null) ...[
                  const SizedBox(height: AppSpacing.md),
                  _BaselineDelta(
                    baseline: baselineValue!,
                    current: intrinsicValue,
                  ),
                ],
                const SizedBox(height: AppSpacing.xl),
                Divider(color: colors.hairline, height: 1),
                const SizedBox(height: AppSpacing.xl),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: _Metric(
                        label: 'Market price',
                        value: _money(result.currentPrice),
                      ),
                    ),
                    Container(
                        width: 1, height: 40, color: colors.hairline),
                    const SizedBox(width: AppSpacing.xl),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            up ? 'IMPLIED UPSIDE' : 'IMPLIED DOWNSIDE',
                            style: context.text.labelMedium,
                          ),
                          const SizedBox(height: AppSpacing.sm),
                          // Colour, an arrow and an explicit sign all carry
                          // the same meaning, so none of them is load-bearing
                          // on its own.
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: AppSpacing.md, vertical: 6),
                            decoration: BoxDecoration(
                              color: accentSurface,
                              borderRadius:
                                  BorderRadius.circular(AppRadius.chip),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(
                                    up
                                        ? Icons.arrow_upward_rounded
                                        : Icons.arrow_downward_rounded,
                                    size: 15,
                                    color: accent),
                                const SizedBox(width: AppSpacing.xs),
                                Text(
                                  _percent(upsideDownside),
                                  style: context.text.titleSmall
                                      ?.copyWith(color: accent),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),

        if (correctionNote != null) ...[
          const SizedBox(height: AppSpacing.md),
          _NoticeBar(
            icon: Icons.published_with_changes_rounded,
            text: correctionNote!,
            foreground: colors.negative,
            background: colors.negativeSurface,
          ),
        ],

        const SizedBox(height: AppSpacing.xl),
        // The caveat travels with the number, by design - and its figures are
        // rebuilt locally so they move with the sliders.
        if (note.isNotEmpty)
          Container(
            padding: const EdgeInsets.all(AppSpacing.lg),
            decoration: BoxDecoration(
              color: context.scheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(AppRadius.control),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline_rounded,
                    size: 17, color: colors.textSecondary),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(note,
                      style: context.text.bodySmall
                          ?.copyWith(color: colors.textSecondary)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// A tinted inline notice, used where a message needs to stand out without
/// looking like a system error.
class _NoticeBar extends StatelessWidget {
  const _NoticeBar({
    required this.icon,
    required this.text,
    required this.foreground,
    required this.background,
  });

  final IconData icon;
  final String text;
  final Color foreground;
  final Color background;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(AppRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 17, color: foreground),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(text,
                style: context.text.bodySmall?.copyWith(color: foreground)),
          ),
        ],
      ),
    );
  }
}

/// Shows how far the current figure has moved from the backend's own,
/// so the sliders' effect is legible at a glance.
class _BaselineDelta extends StatelessWidget {
  const _BaselineDelta({required this.baseline, required this.current});

  final double baseline;
  final double current;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final diff = current - baseline;
    final pct = baseline == 0 ? 0.0 : diff / baseline * 100;
    final up = diff >= 0;
    final colour = up ? colors.positive : colors.negative;
    final sign = up ? '+' : '-';

    return Row(
      children: [
        Icon(up ? Icons.north_east_rounded : Icons.south_east_rounded,
            size: 14, color: colour),
        const SizedBox(width: AppSpacing.xs),
        Flexible(
          child: RichText(
            overflow: TextOverflow.ellipsis,
            text: TextSpan(
              style: context.text.bodySmall,
              children: [
                TextSpan(
                  text: '$sign\$${diff.abs().toStringAsFixed(2)} '
                      '($sign${pct.abs().toStringAsFixed(1)}%)',
                  style: context.text.bodySmall?.copyWith(
                      color: colour, fontWeight: FontWeight.w600),
                ),
                TextSpan(
                  text: '  vs derived \$${baseline.toStringAsFixed(2)}',
                  style: context.text.bodySmall
                      ?.copyWith(color: colors.textSecondary),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label.toUpperCase(), style: context.text.labelMedium),
        const SizedBox(height: AppSpacing.sm),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Text(value, style: context.text.titleSmall),
        ),
      ],
    );
  }
}

class _NotSuitableCard extends StatelessWidget {
  const _NotSuitableCard({required this.result});

  final ValuationNotSuitable result;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (result.companyName.isNotEmpty) ...[
          Text(result.companyName,
              style: context.text.headlineMedium, maxLines: 2),
          const SizedBox(height: AppSpacing.xs),
          Text(result.ticker,
              style: context.text.labelSmall?.copyWith(
                color: colors.textSecondary,
                letterSpacing: 1.0,
                fontWeight: FontWeight.w700,
              )),
          const SizedBox(height: AppSpacing.xl),
        ],
        Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // A calm header band rather than a red alert: this is a
              // considered answer, not a failure.
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.xl, vertical: AppSpacing.lg),
                decoration: BoxDecoration(
                  color: colors.cautionSurface,
                  borderRadius: const BorderRadius.vertical(
                      top: Radius.circular(AppRadius.card - 1)),
                ),
                child: Row(
                  children: [
                    Icon(Icons.info_rounded, size: 19, color: colors.caution),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Text(
                        'A standard DCF isn’t the right tool here',
                        style: context.text.titleSmall
                            ?.copyWith(color: colors.caution),
                      ),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(AppSpacing.xl),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(tidyBackendMessage(result.message),
                        style: context.text.bodyMedium),
                    if (result.reasons.isNotEmpty) ...[
                      const SizedBox(height: AppSpacing.xl),
                      ...result.reasons.map(
                        (reason) => Padding(
                          padding:
                              const EdgeInsets.only(bottom: AppSpacing.md),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                margin: const EdgeInsets.only(top: 7),
                                width: 5,
                                height: 5,
                                decoration: BoxDecoration(
                                  color: colors.caution,
                                  shape: BoxShape.circle,
                                ),
                              ),
                              const SizedBox(width: AppSpacing.md),
                              Expanded(
                                child: Text(
                                  tidyBackendMessage(reason),
                                  style: context.text.bodySmall?.copyWith(
                                      color: colors.textSecondary),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.lg),
        Text(
          'No intrinsic value is shown on purpose — for this company the model '
          'would produce a confident figure with no economic meaning.',
          style: context.text.bodySmall?.copyWith(color: colors.textSecondary),
        ),
      ],
    );
  }
}

class _FailureCard extends StatelessWidget {
  const _FailureCard({required this.result});

  final ValuationFailure result;

  /// Tone per failure: a typo and an outage deserve different weight, so
  /// the neutral cases are not dressed up in alarm colours.
  ({IconData icon, String title, bool severe}) get _presentation =>
      switch (result.kind) {
        ValuationFailureKind.tickerNotFound => (
            icon: Icons.travel_explore_rounded,
            title: 'No match for that ticker',
            severe: false,
          ),
        ValuationFailureKind.planLimited => (
            icon: Icons.workspace_premium_outlined,
            title: 'Not on your data plan',
            severe: false,
          ),
        ValuationFailureKind.rateLimited => (
            icon: Icons.schedule_rounded,
            title: 'Rate limit reached',
            severe: false,
          ),
        ValuationFailureKind.backendUnreachable => (
            icon: Icons.cloud_off_rounded,
            title: 'Can’t reach the backend',
            severe: true,
          ),
        ValuationFailureKind.upstreamError => (
            icon: Icons.report_gmailerrorred_rounded,
            title: 'Data provider problem',
            severe: true,
          ),
        ValuationFailureKind.badRequest => (
            icon: Icons.edit_note_rounded,
            title: 'Check the request',
            severe: false,
          ),
        ValuationFailureKind.unexpected => (
            icon: Icons.help_outline_rounded,
            title: 'Unexpected problem',
            severe: true,
          ),
      };

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final p = _presentation;
    final accent = p.severe ? colors.negative : colors.textSecondary;
    final iconSurface =
        p.severe ? colors.negativeSurface : context.scheme.surfaceContainerHighest;

    // The backend writes for a terminal; reflow it, and lift any trailing URL
    // out so it does not wrap mid-word through the paragraph.
    final (body, url) = splitTrailingUrl(tidyBackendMessage(result.message));

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(
                    color: iconSurface,
                    borderRadius: BorderRadius.circular(AppRadius.control - 2),
                  ),
                  child: Icon(p.icon, size: 20, color: accent),
                ),
                const SizedBox(width: AppSpacing.lg),
                Expanded(
                  child: Text(p.title, style: context.text.titleMedium),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.lg),
            Text(body,
                style: context.text.bodySmall
                    ?.copyWith(color: colors.textSecondary)),
            if (url != null) ...[
              const SizedBox(height: AppSpacing.md),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.md, vertical: AppSpacing.sm),
                decoration: BoxDecoration(
                  color: context.scheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(AppSpacing.sm),
                ),
                child: Text(
                  url,
                  style: context.text.labelSmall?.copyWith(
                    color: context.scheme.primary,
                    height: 1.4,
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

