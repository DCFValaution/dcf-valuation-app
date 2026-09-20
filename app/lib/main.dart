import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'assumption_sliders.dart';
import 'dcf_engine.dart';
import 'excel_delivery.dart';
import 'formatting.dart';
import 'relative_controller.dart';
import 'relative_view.dart';
import 'ui.dart';
import 'search_dropdown.dart';
import 'sensitivity_table.dart';
import 'speculative_screen.dart';
import 'theme.dart';
import 'ticker_search.dart';
import 'valuation_api.dart';

const String _xlsxMimeType =
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

/// Which analysis of a valued company is on screen.
enum _Lens { intrinsic, relative }

/// Chooses between the intrinsic valuation and the relative view.
///
/// The labels carry the distinction: "Intrinsic value" against "Relative
/// (market)", so the second can never be taken for another intrinsic figure.
class _LensSwitcher extends StatelessWidget {
  const _LensSwitcher({required this.lens, required this.onChanged});

  final _Lens lens;
  final ValueChanged<_Lens> onChanged;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: SegmentedButton<_Lens>(
        key: const Key('lens-switcher'),
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(
            value: _Lens.intrinsic,
            icon: Icon(Icons.functions_rounded, size: 18),
            label: Text('Intrinsic value'),
          ),
          ButtonSegment(
            value: _Lens.relative,
            icon: Icon(Icons.compare_arrows_rounded, size: 18),
            label: Text('Relative (market)'),
          ),
        ],
        selected: {lens},
        onSelectionChanged: (s) => onChanged(s.first),
      ),
    );
  }
}

/// Where the figure on screen came from.
enum ValueStatus {
  /// Straight from the backend.
  confirmed,

  /// Computed locally while dragging; not yet checked against the backend.
  preview,

  /// The assumptions have been moved but the figure has not caught up yet.
  ///
  /// Only the DCF has a local engine to preview with. For a dividend discount
  /// model the displayed number still belongs to the previous assumptions
  /// until the backend answers, and saying so is better than letting a stale
  /// figure sit silently under a moved slider.
  stale,

  /// A background confirmation request is in flight.
  confirming,

  /// The backend disagreed with the local preview and its value replaced it.
  corrected,
}

void main() => runApp(const DcfApp());

class DcfApp extends StatelessWidget {
  const DcfApp({super.key, this.api});

  /// Injected by tests; the app builds its own.
  final ValuationApi? api;

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
      home: ValuationScreen(api: api),
    );
  }
}

class ValuationScreen extends StatefulWidget {
  const ValuationScreen({super.key, this.api});

  final ValuationApi? api;

  @override
  State<ValuationScreen> createState() => _ValuationScreenState();
}

class _ValuationScreenState extends State<ValuationScreen> {
  final _controller = TextEditingController();
  late final ValuationApi _api = widget.api ?? ValuationApi();

  /// Debounces and caches company search, so typing a name costs one request
  /// per pause rather than one per keystroke.
  late final TickerSearchController _search = TickerSearchController(
    fetch: _api.search,
  );

  ValuationResult? _result;
  bool _loading = false;

  /// Which valuation request is the current one. A request that finishes
  /// after a newer one started is stale, and its answer is dropped.
  int _valueSeq = 0;

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

  // --- Relative view -------------------------------------------------------

  _Lens _lens = _Lens.intrinsic;

  /// The relative view and its peer edits, for the company on screen. Made on
  /// first opening and discarded with the company, so edits never carry over.
  RelativeController? _relativeController;

  /// Search for adding peers. Separate from the ticker field's own search, so
  /// the two dropdowns never open each other, but kept for the screen's
  /// lifetime so its cache survives closing and reopening the sheet.
  late final TickerSearchController _peerSearch = TickerSearchController(
    fetch: _api.search,
  );

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
    // After the first frame, not during it: on a phone the first frame is
    // still downloading and compiling the engine, and the ping's connection to
    // a second origin has no business competing with that. Nothing ever waits
    // on this - it is fire-and-forget, and the screen is fully usable while a
    // sleeping server takes a minute to answer it.
    WidgetsBinding.instance.addPostFrameCallback((_) => _pingServer());
  }

  /// Start waking the server as soon as the app is on screen.
  ///
  /// `/health` touches no upstream service, so this costs the backend
  /// essentially nothing, and the spin-up then overlaps with the user typing
  /// a ticker rather than being paid for in full by their first valuation.
  /// Failure is deliberately silent: the real request will report anything
  /// genuinely wrong, and an error about a ping the user never asked for
  /// would be noise.
  ///
  /// Never awaited by anything that draws or handles input. Only a valuation
  /// consults [_serverAwake], and only to decide whether a slow answer should
  /// be explained as the server waking up.
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
    _search.dispose();
    _peerSearch.dispose();
    _relativeController?.dispose();
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
          _slider['terminal_growth'] ??
          _fullAssumptions['terminal_growth'] ??
          0,
      equityRiskPremium: _equityRiskPremium,
    );
  }

  /// Which levers apply, which depends on the model that produced the figure.
  List<AdjustableAssumption> get _specs => _specsFor(_success?.method);

  static List<AdjustableAssumption> _specsFor(ValuationMethod? method) =>
      method == ValuationMethod.ddm ? kDdmAdjustable : kAdjustable;

  /// Mirrors the slider panel's own check, so the export button and the
  /// warning agree about when the inputs are unusable.
  bool get _growthExceedsDiscountRate =>
      growthExceedsDiscountRate(_slider, _specs);

  Map<String, double> get _activeOverrides {
    final out = <String, double>{};
    for (final spec in _specs) {
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

    // The field takes company names now, so "BANK OF AMERICA" can reach this
    // button. Sent as a ticker it would come back "not found" - accurate, and
    // useless when the company is sitting in the dropdown. Point at the list
    // rather than guessing which result was meant.
    if (ticker.isNotEmpty && !looksLikeTicker(ticker)) {
      final hasResults = _search.results.isNotEmpty;
      _showMessage(
        hasResults
            ? 'Pick a company from the list to value it.'
            : 'Type a ticker such as AAPL, or type a company name and pick it '
                  'from the list.',
      );
      return;
    }

    _search.dismiss(currentText: _controller.text);
    // Unfocus the field itself, not the page's scope. Unfocusing the scope
    // hides the keyboard but leaves the field as the scope's remembered
    // child, so the next route to close - the disclaimer, the add-peer
    // sheet, the speculative screen - hands focus straight back to it and
    // the keyboard springs up over whatever the user was reading.
    FocusManager.instance.primaryFocus?.unfocus();
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
      // A new company starts on its intrinsic value, with no relative view
      // carried over from the last one.
      _lens = _Lens.intrinsic;
      _relativeController?.dispose();
      _relativeController = null;
    });
    _confirmTimer?.cancel();
    _confirmSeq++;
    // A request already running is not cancellable, but its answer can be
    // discarded: the company the user asked for last is the one they get.
    final seq = ++_valueSeq;
    _beginWaitingFeedback();

    final result = await _api.value(ticker);
    // Superseded by a later submission - leave that one's waiting state and
    // its eventual answer alone.
    if (!mounted || seq != _valueSeq) return;

    _endWaitingFeedback();
    setState(() {
      _result = result;
      _loading = false;
      _serverAwake = result is! ValuationFailure;
      if (result is ValuationSuccess) {
        // Every assumption, including those without sliders, so the local
        // engine has the complete set to work from.
        _fullAssumptions = {
          for (final a in result.assumptions) a.name: a.value,
        };
        _equityRiskPremium = result.assumption('equity_risk_premium')?.value;
        final specs = _specsFor(result.method);
        _derived = {
          for (final spec in specs)
            if (result.assumption(spec.name) != null)
              spec.name: result.assumption(spec.name)!.value,
        };
        _sources = {
          for (final spec in specs)
            if (result.assumption(spec.name) != null)
              spec.name: result.assumption(spec.name)!.source,
        };
        _slider = Map.of(_derived);
        _baselineValue = result.intrinsicValuePerShare;
      }
    });
  }

  /// Switch lens. The relative view is fetched the first time it is opened
  /// for this company and kept, so switching back and forth costs nothing -
  /// it prices several companies, and should not be asked to twice.
  void _selectLens(_Lens lens) {
    setState(() {
      _lens = lens;
      if (lens == _Lens.relative && _resultTicker.isNotEmpty) {
        _relativeController ??= RelativeController(
          api: _api,
          ticker: _resultTicker,
        );
      }
    });
    if (lens == _Lens.relative) _relativeController?.open();
  }

  /// The user asked, explicitly, to see a speculative estimate past a refusal.
  ///
  /// A separate route rather than a change to this screen's state: the refusal
  /// underneath stays exactly as it was, so Back always returns to it, and
  /// nothing speculative can leak into a valuation shown here later.
  void _openSpeculative(ValuationNotSuitable refusal) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => SpeculativeScreen(
          api: _api,
          ticker: refusal.ticker.isNotEmpty ? refusal.ticker : _resultTicker,
          companyName: refusal.companyName,
        ),
      ),
    );
  }

  /// A company was picked from the dropdown: fill in its ticker and value it.
  void _selectSearchResult(CompanySearchResult result) {
    _controller.value = TextEditingValue(
      text: result.ticker,
      selection: TextSelection.collapsed(offset: result.ticker.length),
    );
    // Before submitting, so writing the ticker does not reopen the dropdown
    // with a search for the ticker just chosen.
    _search.dismiss(currentText: result.ticker);
    _submit();
  }

  /// Recompute locally as the slider moves. No network, no await - this runs
  /// on every drag frame, so it has to be cheap and synchronous.
  void _onSliderChanged(String name, double value) {
    final success = _success;
    if (success == null) return;

    setState(() {
      _slider[name] = value;
      _correctionNote = null;

      // No local engine for the dividend discount model, so there is nothing
      // to preview with: hold the backend's figure, mark it as belonging to
      // the old assumptions, and let the confirmation round trip produce the
      // new one.
      if (!success.supportsLocalPreview) {
        _preview = null;
        _status = ValueStatus.stale;
        return;
      }

      final assumptions = DcfAssumptions.fromMap({
        ..._fullAssumptions,
        ..._slider,
      });
      try {
        _preview = runDcf(success.baseYear!, assumptions);
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
        final diverged =
            localValue != null &&
            (backendValue - localValue).abs() > _tolerance;

        _result = result;
        _preview = null; // the backend's figure is now the displayed one
        _status = diverged ? ValueStatus.corrected : ValueStatus.confirmed;
        _correctionNote = diverged
            ? 'The quick preview showed \$${localValue.toStringAsFixed(2)}, but '
                  'the full calculation gives \$${backendValue.toStringAsFixed(2)}. '
                  'Showing the full calculation.'
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
    final result = await _api.downloadExcel(
      _resultTicker,
      overrides: _activeOverrides,
    );
    if (!mounted) return;

    switch (result) {
      case ExcelSuccess(:final bytes, :final filename):
        // Where the bytes go from here depends on the platform: a file and a
        // share sheet on a phone, a download or the Web Share sheet in a
        // browser. The outcome distinguishes a real failure from a context
        // that cannot save but can say where to do it instead.
        final outcome = await deliverWorkbook(
          bytes: bytes,
          filename: filename,
          ticker: _resultTicker,
          mimeType: _xlsxMimeType,
          announce: (text) {
            if (!mounted) return;
            setState(() => _exporting = false);
            _showMessage(text);
          },
        );
        if (!mounted) return;
        setState(() => _exporting = false);
        switch (outcome) {
          case DeliveryDone(:final message):
            if (message != null) _showMessage(message);
          case DeliveryCancelled():
            break;
          case DeliveryInstruction(:final message):
            _showMessage(message);
          case DeliveryFailure(:final message):
            _showMessage(message, isError: true);
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
      ..showSnackBar(
        SnackBar(
          content: Text(text),
          backgroundColor: isError ? const Color(0xFFB3261E) : null,
          duration: Duration(seconds: isError ? 6 : 3),
        ),
      );
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
        actions: [
          // Always reachable, including before the first valuation - the
          // disclaimer should not depend on having searched something.
          IconButton(
            icon: const Icon(Icons.info_outline_rounded),
            tooltip: 'About and disclaimer',
            onPressed: () => showDisclaimerSheet(context),
          ),
          const SizedBox(width: AppSpacing.sm),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.xl,
                AppSpacing.sm,
                AppSpacing.xl,
                AppSpacing.lg,
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Expanded(
                    child: TextField(
                      controller: _controller,
                      textCapitalization: TextCapitalization.characters,
                      textInputAction: TextInputAction.go,
                      autocorrect: false,
                      // Never disabled. A valuation can take a minute and a
                      // half against a sleeping free-tier instance, and a
                      // field that refuses focus and the keyboard for that
                      // long is indistinguishable from a frozen app - which
                      // is exactly how it read on an iPhone, where the rest
                      // of the screen went on answering taps.
                      style: context.text.titleMedium?.copyWith(
                        letterSpacing: 0.6,
                        fontFeatures: const [],
                      ),
                      inputFormatters: [
                        UpperCaseFormatter(),
                        // Company names as well as tickers: spaces, and the
                        // punctuation in names like AT&T and Moody's.
                        FilteringTextInputFormatter.allow(
                          RegExp(r"[A-Za-z0-9.\-&', ]"),
                        ),
                        LengthLimitingTextInputFormatter(50),
                      ],
                      decoration: InputDecoration(
                        hintText: 'Ticker or company name',
                        prefixIcon: Icon(
                          Icons.search_rounded,
                          size: 20,
                          color: colors.textSecondary,
                        ),
                        prefixIconConstraints: const BoxConstraints(
                          minWidth: 44,
                          minHeight: 24,
                        ),
                      ),
                      // Fires for typing only, not for the ticker a selection
                      // writes in - which is what keeps a pick from searching
                      // for itself.
                      onChanged: _search.onQueryChanged,
                      onTapOutside: (_) {
                        FocusManager.instance.primaryFocus?.unfocus();
                        _search.dismiss(currentText: _controller.text);
                      },
                      onSubmitted: (_) => _submit(),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.md),
                  // Part of the search, not outside it: otherwise pressing
                  // Value closes the dropdown before the press is handled, and
                  // a company name submitted by mistake would be told to pick
                  // from a list that had just disappeared.
                  TextFieldTapRegion(
                    child: SizedBox(
                      height: 54,
                      child: FilledButton(
                        onPressed: _submit,
                        child: const Text('Value'),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              // The dropdown floats over the result rather than pushing it
              // down, so opening it does not shift what is already on screen.
              child: Stack(
                children: [
                  Positioned.fill(child: _buildResultArea()),
                  Positioned(
                    left: AppSpacing.xl,
                    right: AppSpacing.xl,
                    top: 0,
                    child: ListenableBuilder(
                      listenable: _search,
                      builder: (context, _) => _search.isOpen
                          // Taps on the dropdown belong to the field, so
                          // choosing a result is not read as tapping away.
                          ? TextFieldTapRegion(
                              child: SearchDropdown(
                                controller: _search,
                                onSelected: _selectSearchResult,
                              ),
                            )
                          : const SizedBox.shrink(),
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
        AppSpacing.xl,
        0,
        AppSpacing.xl,
        AppSpacing.xxxl,
      ),
      child: switch (result) {
        ValuationSuccess() => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Two lenses on the same company. The intrinsic one is the
            // default and is left exactly as it was; the relative one is a
            // separate, market-based second opinion, fetched only on request.
            _LensSwitcher(lens: _lens, onChanged: _selectLens),
            const SizedBox(height: AppSpacing.xl),
            if (_lens == _Lens.relative && _relativeController != null)
              RelativeView(
                controller: _relativeController!,
                peerSearch: _peerSearch,
                intrinsicAdjusted: _activeOverrides.isNotEmpty,
              )
            else ...[
              _SuccessCard(
                result: result,
                intrinsicValue:
                    _displayIntrinsic ?? result.intrinsicValuePerShare,
                upsideDownside: _displayUpside ?? result.upsideDownside,
                note: _displayNote,
                status: _status,
                // Only meaningful once something has actually been changed.
                baselineValue: (_activeOverrides.isEmpty)
                    ? null
                    : _baselineValue,
                correctionNote: _correctionNote,
              ),
              // The workbook builds a discounted cash flow model, so there is
              // none to offer for a company valued on its dividends. Better no
              // button than one whose only outcome is a refusal.
              if (result.method == ValuationMethod.dcf) ...[
                const SizedBox(height: AppSpacing.xl),
                _ExportButton(
                  busy: _exporting,
                  // Terminal growth at or above WACC would make the backend
                  // refuse; do not offer an export that cannot succeed.
                  blocked: _growthExceedsDiscountRate,
                  overrideCount: _activeOverrides.length,
                  onPressed: _exportExcel,
                ),
              ],
              if (_derived.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xxl),
                AssumptionSliders(
                  specs: _specs,
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
          ],
        ),
        ValuationNotSuitable() => _NotSuitableCard(
          result: result,
          onShowSpeculative: result.speculativeEstimateAvailable
              ? () => _openSpeculative(result)
              : null,
        ),
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
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
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
        AppSpacing.xl,
        AppSpacing.sm,
        AppSpacing.xl,
        AppSpacing.xxxl,
      ),
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
          Text('Value any listed company', style: context.text.headlineMedium),
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
            detail:
                'Growth, margin, tax and WACC derived per company, '
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
          const SizedBox(height: AppSpacing.xxl),
          // Present before the first valuation too, so the framing is set
          // before anyone sees a number rather than after.
          const DisclaimerLine(),
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
                Text(
                  detail,
                  style: context.text.bodySmall?.copyWith(
                    color: colors.textSecondary,
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
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
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
              : 'A spreadsheet with working formulas, matching the '
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
        'Confirmed by the full calculation',
        colors.positive,
        colors.positiveSurface,
      ),
      ValueStatus.preview => (
        Icons.bolt_rounded,
        'Live preview · on device',
        colors.caution,
        colors.cautionSurface,
      ),
      ValueStatus.stale => (
        Icons.pending_outlined,
        'Figure not yet updated · release to recalculate',
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
        'Updated to the full calculation',
        colors.negative,
        colors.negativeSurface,
      ),
    };

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: 6,
        ),
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
                child: CircularProgressIndicator(strokeWidth: 1.8, color: fg),
              )
            else
              Icon(icon, size: 14, color: fg),
            const SizedBox(width: AppSpacing.sm),
            Text(
              text,
              style: context.text.labelSmall?.copyWith(
                color: fg,
                fontWeight: FontWeight.w600,
              ),
            ),
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

  /// The table describes [result]; the headline may be a local preview of
  /// other assumptions, or awaiting confirmation of them.
  bool get _sensitivityStale =>
      status != ValueStatus.confirmed && status != ValueStatus.corrected;

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
        Text(
          result.companyName,
          style: context.text.headlineMedium,
          maxLines: 2,
        ),
        const SizedBox(height: AppSpacing.xs),
        Row(
          children: [
            Text(
              result.ticker,
              style: context.text.labelSmall?.copyWith(
                color: colors.textSecondary,
                letterSpacing: 1.0,
                fontWeight: FontWeight.w700,
              ),
            ),
            // No sector reported: the ticker stands alone, with no separator.
            if (result.displaySector case final sector?) ...[
              const SizedBox(width: AppSpacing.sm),
              Container(
                key: const Key('sector-separator'),
                width: 3,
                height: 3,
                decoration: BoxDecoration(
                  color: colors.textSecondary,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Flexible(
                child: Text(
                  sector,
                  style: context.text.bodySmall?.copyWith(
                    color: colors.textSecondary,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: AppSpacing.lg),
        // Which model produced the figure below. Not decoration: a dividend
        // model and a cash flow model answer different questions, and someone
        // reading the number is entitled to know which one they are reading.
        _MethodBadge(method: result.method),
        const SizedBox(height: AppSpacing.xl),

        // --- Hero -----------------------------------------------------------
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Eyebrow('INTRINSIC VALUE PER SHARE'),
              const SizedBox(height: AppSpacing.md),
              // No implicit animation on the number itself: it must track
              // the finger exactly, and a tween would lag behind the drag.
              Text(_money(intrinsicValue), style: context.text.displayLarge),
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
                  Container(width: 1, height: 40, color: colors.hairline),
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
                            horizontal: AppSpacing.md,
                            vertical: 6,
                          ),
                          decoration: BoxDecoration(
                            color: accentSurface,
                            borderRadius: BorderRadius.circular(AppRadius.chip),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                up
                                    ? Icons.arrow_upward_rounded
                                    : Icons.arrow_downward_rounded,
                                size: 15,
                                color: accent,
                              ),
                              const SizedBox(width: AppSpacing.xs),
                              Text(
                                _percent(upsideDownside),
                                style: context.text.titleSmall?.copyWith(
                                  color: accent,
                                ),
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

        // A doubt about the method itself, not about an assumption: pinned
        // directly under the figure it qualifies, so the number and its
        // upside or downside are never on screen without it. Robinhood is the
        // case - valued with a DCF, with lending income the data cannot see.
        if (result.methodFitWarnings.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.md),
          _MethodFitWarning(warnings: result.methodFitWarnings),
        ],

        // An update to the figure, not a loss: amber for "notice this", so
        // red keeps meaning downside.
        if (correctionNote != null) ...[
          const SizedBox(height: AppSpacing.md),
          Callout(
            icon: Icons.published_with_changes_rounded,
            text: correctionNote!,
            tone: Tone.caution,
          ),
        ],

        // What the dividend projection starts from, standing where the DCF's
        // revenue and cash flow would be. A dividend model has no base year of
        // filings to show; showing the dividend instead is what makes the
        // figure checkable.
        if (result.dividendBasis != null) ...[
          const SizedBox(height: AppSpacing.xl),
          _DividendBasisCard(
            basis: result.dividendBasis!,
            whyNotDcf: result.whyNotDcf,
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
                Icon(
                  Icons.info_outline_rounded,
                  size: 17,
                  color: colors.textSecondary,
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    note,
                    style: context.text.bodySmall?.copyWith(
                      color: colors.textSecondary,
                    ),
                  ),
                ),
              ],
            ),
          ),

        // The table the note above points to. Drawn only when the backend
        // sent one worth drawing.
        if (result.sensitivity != null) ...[
          const SizedBox(height: AppSpacing.lg),
          SensitivityTable(
            grid: result.sensitivity!,
            formatValue: _money,
            title: 'How sensitive is this figure?',
            explanation:
                'Each cell is the value per share if these two '
                'assumptions were different. None of them is more correct '
                'than its neighbours.',
            stale: _sensitivityStale,
          ),
        ],

        // What the model cannot see - for a bank, the cash returned through
        // buybacks that a dividend model counts for nothing. This is the
        // backend telling the user where its own figure is weak, so it sits
        // with the figure rather than being dropped.
        if (result.warnings.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.md),
          NoteList(title: 'Keep in mind', notes: result.warnings),
        ],

        // Sits directly under the figure, where someone about to act on it
        // will actually read it - not buried in a settings screen they will
        // never open.
        const SizedBox(height: AppSpacing.lg),
        const DisclaimerLine(),
      ],
    );
  }
}

/// Doubts about whether the method fits this company at all.
///
/// Deliberately not one of the "Keep in mind" bullets. Those are caveats about
/// an assumption - growth capped, a tax credit ignored - and a reader can
/// weigh them against the figure. This says the figure itself may not
/// describe the company, which qualifies the implied upside or downside too,
/// so it sits under the figure with a heading of its own and amber rather
/// than red: a warning to read, not a loss.
class _MethodFitWarning extends StatelessWidget {
  const _MethodFitWarning({required this.warnings});

  final List<String> warnings;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Container(
      key: const Key('method-fit-warning'),
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: colors.cautionSurface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(
          color: colors.caution.withValues(alpha: 0.55),
          width: 1.5,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.warning_amber_rounded,
                size: 20,
                color: colors.caution,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(
                  'This model may not fit this company',
                  style: context.text.titleSmall?.copyWith(
                    color: colors.caution,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
          for (final warning in warnings) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              warning,
              style: context.text.bodySmall?.copyWith(
                color: context.scheme.onSurface,
                height: 1.45,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Names the model that produced the figure above it.
///
/// Small and quiet, but always present - including for the DCF, so that the
/// DDM's badge cannot be read as an exception or a warning. Two methods, both
/// labelled, neither presented as the default.
class _MethodBadge extends StatelessWidget {
  const _MethodBadge({required this.method});

  final ValuationMethod method;

  @override
  Widget build(BuildContext context) {
    return Pill(
      icon: method == ValuationMethod.ddm
          ? Icons.payments_outlined
          : Icons.waterfall_chart_rounded,
      label: 'Valued with a ${method.label}',
    );
  }
}

/// The dividend a dividend discount model projects from, and why that model
/// was used at all.
///
/// The DCF shows a base year of revenue, debt and shares; this is its
/// counterpart. Without it the DDM's figure would arrive with nothing to check
/// it against.
class _DividendBasisCard extends StatelessWidget {
  const _DividendBasisCard({required this.basis, required this.whyNotDcf});

  final DividendBasis basis;
  final String whyNotDcf;

  static String _money(double v) => '\$${v.toStringAsFixed(2)}';
  static String _pct(double f) => '${(f * 100).toStringAsFixed(1)}%';

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    // Only what the backend actually reported: a payout ratio or return on
    // equity it could not derive is left out rather than shown as zero.
    final facts = <(String, String)>[
      ('Annual dividend', _money(basis.currentAnnualDividend)),
      ('Dividend yield', _pct(basis.dividendYield)),
      if (basis.payoutRatio != null) ('Payout ratio', _pct(basis.payoutRatio!)),
      if (basis.returnOnEquity != null)
        ('Return on equity', _pct(basis.returnOnEquity!)),
    ];

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Eyebrow('DIVIDEND BASIS'),
          const SizedBox(height: AppSpacing.lg),
          Wrap(
            spacing: AppSpacing.xxl,
            runSpacing: AppSpacing.lg,
            children: [
              for (final (label, value) in facts)
                _Metric(label: label.toUpperCase(), value: value),
            ],
          ),
          if (basis.detail.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.lg),
            Text(
              basis.detail,
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
          ],
          if (whyNotDcf.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.lg),
            Divider(color: colors.hairline, height: 1),
            const SizedBox(height: AppSpacing.lg),
            Text(
              whyNotDcf,
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

/// The short form of the disclaimer, shown beneath every valuation.
///
/// Deliberately quiet - secondary text, no icon, no tinted box. A warning
/// styled like an error is one people learn to dismiss; this needs to be
/// read once and remembered, and it sits next to the number it qualifies.
/// Tapping opens the full text.
class DisclaimerLine extends StatelessWidget {
  const DisclaimerLine({super.key});

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return InkWell(
      onTap: () => showDisclaimerSheet(context),
      borderRadius: BorderRadius.circular(AppRadius.control),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          vertical: AppSpacing.sm,
          horizontal: AppSpacing.xs,
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Text(
                'Educational estimate, not investment advice. '
                'Do your own research or speak to a licensed professional.',
                style: context.text.labelSmall?.copyWith(
                  color: colors.textSecondary,
                  height: 1.45,
                ),
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            Icon(
              Icons.chevron_right_rounded,
              size: 16,
              color: colors.textSecondary,
            ),
          ],
        ),
      ),
    );
  }
}

/// The full disclaimer, as a bottom sheet.
///
/// Reachable from the header on every screen and from the line under each
/// valuation, so it is never more than one tap away.
Future<void> showDisclaimerSheet(BuildContext context) {
  final colors = context.colors;
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (context) => SafeArea(
      child: SingleChildScrollView(
        // The text is long enough to overflow a short viewport - a small
        // phone in landscape, or a device with a large font scale - and an
        // unscrollable sheet would simply cut the disclaimer off.
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.xxl,
          0,
          AppSpacing.xxl,
          AppSpacing.xxl,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'About these valuations',
              style: context.text.titleLarge?.copyWith(
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            _DisclaimerParagraph(
              'Every figure here is an educational estimate produced by a '
              'discounted cash flow model from assumptions you can change. '
              'It is not investment advice, a recommendation, or an offer to '
              'buy or sell anything.',
            ),
            _DisclaimerParagraph(
              'The model derives its assumptions from the company’s own '
              'published financials, but a valuation is only ever as good as '
              'those assumptions. Move a slider and the answer moves with it '
              '— often a great deal. Treat the output as one view among '
              'many, not a fact about the company.',
            ),
            _DisclaimerParagraph(
              'Market data comes from a third party and may be delayed, '
              'incomplete or wrong. Nothing here has been reviewed by a '
              'financial adviser.',
            ),
            _DisclaimerParagraph(
              'Do your own research, and consider speaking to a licensed '
              'financial professional before making any investment decision. '
              'You are responsible for what you do with these numbers.',
            ),
            const SizedBox(height: AppSpacing.md),
            Container(
              padding: const EdgeInsets.all(AppSpacing.lg),
              decoration: BoxDecoration(
                color: context.scheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(AppRadius.control),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(
                    Icons.school_outlined,
                    size: 18,
                    color: colors.textSecondary,
                  ),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: Text(
                      'Built to make the mechanics of a DCF visible and '
                      'arguable — that is the point of it.',
                      style: context.text.bodySmall?.copyWith(
                        color: colors.textSecondary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class _DisclaimerParagraph extends StatelessWidget {
  const _DisclaimerParagraph(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.lg),
      child: Text(
        text,
        style: context.text.bodyMedium?.copyWith(
          color: context.colors.textSecondary,
          height: 1.5,
        ),
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
        Icon(
          up ? Icons.north_east_rounded : Icons.south_east_rounded,
          size: 14,
          color: colour,
        ),
        const SizedBox(width: AppSpacing.xs),
        Flexible(
          child: RichText(
            overflow: TextOverflow.ellipsis,
            text: TextSpan(
              style: context.text.bodySmall,
              children: [
                TextSpan(
                  text:
                      '$sign\$${diff.abs().toStringAsFixed(2)} '
                      '($sign${pct.abs().toStringAsFixed(1)}%)',
                  style: context.text.bodySmall?.copyWith(
                    color: colour,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                TextSpan(
                  text: '  vs derived \$${baseline.toStringAsFixed(2)}',
                  style: context.text.bodySmall?.copyWith(
                    color: colors.textSecondary,
                  ),
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
  const _NotSuitableCard({required this.result, this.onShowSpeculative});

  final ValuationNotSuitable result;

  /// Opens the speculative estimate. Offered only when the backend says one
  /// exists for this refusal - a company refused only for losing money - and
  /// never for any other.
  final VoidCallback? onShowSpeculative;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (result.companyName.isNotEmpty) ...[
          Text(
            result.companyName,
            style: context.text.headlineMedium,
            maxLines: 2,
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            result.ticker,
            style: context.text.labelSmall?.copyWith(
              color: colors.textSecondary,
              letterSpacing: 1.0,
              fontWeight: FontWeight.w700,
            ),
          ),
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
                  horizontal: AppSpacing.xl,
                  vertical: AppSpacing.lg,
                ),
                decoration: BoxDecoration(
                  color: colors.cautionSurface,
                  borderRadius: const BorderRadius.vertical(
                    top: Radius.circular(AppRadius.card - 1),
                  ),
                ),
                child: Row(
                  children: [
                    Icon(Icons.info_rounded, size: 19, color: colors.caution),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Text(
                        'A standard DCF isn’t the right tool here',
                        style: context.text.titleSmall?.copyWith(
                          color: colors.caution,
                        ),
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
                    Text(
                      tidyBackendMessage(result.message),
                      style: context.text.bodyMedium,
                    ),
                    if (result.reasons.isNotEmpty) ...[
                      const SizedBox(height: AppSpacing.xl),
                      ...result.reasons.map(
                        (reason) => Padding(
                          padding: const EdgeInsets.only(bottom: AppSpacing.md),
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
                                    color: colors.textSecondary,
                                  ),
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

        // The opt-in. Below the refusal, visually quieter than it, and framed
        // as what it is before anyone taps: the refusal remains the answer,
        // and this is a deliberate step past it. Both conditions are checked
        // so a callback wired by mistake still cannot offer it without the
        // backend's say-so.
        if (result.speculativeEstimateAvailable &&
            onShowSpeculative != null) ...[
          const SizedBox(height: AppSpacing.xxl),
          Divider(color: colors.hairline, height: 1),
          const SizedBox(height: AppSpacing.lg),
          Text(
            'A speculative estimate can be built by assuming a path to '
            'profitability. It is not a valuation, and it can be far off.',
            style: context.text.bodySmall?.copyWith(
              color: colors.textSecondary,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          TextButton.icon(
            key: const Key('speculative-opt-in'),
            onPressed: onShowSpeculative,
            icon: const Icon(Icons.science_outlined, size: 18),
            label: const Text('Show a speculative estimate anyway'),
            style: TextButton.styleFrom(
              foregroundColor: colors.textSecondary,
              padding: EdgeInsets.zero,
            ),
          ),
        ],
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
        ValuationFailureKind.rateLimited => (
          icon: Icons.schedule_rounded,
          title: 'Too many requests',
          severe: false,
        ),
        // Nothing the user typed was wrong: the listing itself cannot be
        // valued, so it is not a "check the request" problem.
        ValuationFailureKind.unsupportedListing => (
          icon: Icons.currency_exchange_rounded,
          title: 'This listing can’t be valued',
          severe: false,
        ),
        ValuationFailureKind.backendUnreachable => (
          icon: Icons.cloud_off_rounded,
          // "Backend" is our word, not the user's.
          title: 'Can’t reach the server',
          severe: true,
        ),
        ValuationFailureKind.upstreamError => (
          icon: Icons.report_gmailerrorred_rounded,
          title: 'Market data unavailable',
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
    final iconSurface = p.severe
        ? colors.negativeSurface
        : context.scheme.surfaceContainerHighest;

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
                Expanded(child: Text(p.title, style: context.text.titleMedium)),
              ],
            ),
            const SizedBox(height: AppSpacing.lg),
            Text(
              body,
              style: context.text.bodySmall?.copyWith(
                color: colors.textSecondary,
              ),
            ),
            if (url != null) ...[
              const SizedBox(height: AppSpacing.md),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.md,
                  vertical: AppSpacing.sm,
                ),
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
