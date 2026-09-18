/// Client for the DCF valuation backend.
///
/// The backend deliberately answers with several distinct outcomes rather than
/// only success-or-error: it refuses to value companies a growth-perpetuity DCF
/// does not suit, and it reports data-plan limits separately from real
/// failures. Those distinctions are the point, so this client models them as
/// separate result types instead of flattening everything into an error string.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:http/http.dart' as http;

import 'dcf_engine.dart';

/// Where the backend lives, as seen from the app.
///
/// Defaults to the deployed service, because that is what a distributed build
/// has to talk to - a shipped app cannot reach a server on the developer's
/// machine.
///
/// To run against a local backend instead:
///
/// ```
/// flutter run --dart-define=BACKEND_BASE_URL=http://10.0.2.2:8000
/// ```
///
/// 10.0.2.2 is the Android emulator's alias for the *host machine's* loopback:
/// localhost or 127.0.0.1 would resolve to the emulator itself. A physical
/// device needs the host's LAN address, or `adb reverse tcp:8000 tcp:8000`.
const String kBackendBaseUrl = String.fromEnvironment(
  'BACKEND_BASE_URL',
  defaultValue: 'https://dcf-valuation-api.onrender.com',
);

/// The outcome of a valuation request.
sealed class ValuationResult {
  const ValuationResult();
}

/// Which model produced a figure.
///
/// The backend picks this itself - a bank is valued on its dividends because a
/// growth-perpetuity DCF cannot be applied to one honestly - and says so in the
/// response. The app must not present the two as interchangeable: they rest on
/// different inputs and carry different caveats, so the method is displayed,
/// not hidden.
enum ValuationMethod {
  dcf('Discounted Cash Flow'),
  ddm('Dividend Discount Model');

  const ValuationMethod(this.label);

  /// How the method is named on screen.
  final String label;

  static ValuationMethod parse(String? raw) =>
      raw == 'ddm' ? ValuationMethod.ddm : ValuationMethod.dcf;
}

/// What a dividend discount model was actually built on.
///
/// The DDM has no revenue projection to show; its starting point is the
/// dividend itself, so that is what the app shows in place of the DCF's base
/// year. [detail] is the backend's own sentence explaining how the indicated
/// annual dividend was arrived at, including any recurring variable portion.
class DividendBasis {
  final double currentAnnualDividend;
  final String detail;
  final double dividendYield;
  final double? lastRegularPayment;
  final int? paymentsPerYear;
  final double? payoutRatio;
  final double? returnOnEquity;
  final double? variableAnnualDividend;

  const DividendBasis({
    required this.currentAnnualDividend,
    required this.detail,
    required this.dividendYield,
    this.lastRegularPayment,
    this.paymentsPerYear,
    this.payoutRatio,
    this.returnOnEquity,
    this.variableAnnualDividend,
  });

  factory DividendBasis.fromJson(Map<String, dynamic> json) => DividendBasis(
    currentAnnualDividend:
        (json['current_annual_dividend'] as num?)?.toDouble() ?? 0,
    detail: json['detail'] as String? ?? '',
    dividendYield: (json['dividend_yield'] as num?)?.toDouble() ?? 0,
    lastRegularPayment: (json['last_regular_payment'] as num?)?.toDouble(),
    paymentsPerYear: (json['payments_per_year'] as num?)?.toInt(),
    payoutRatio: (json['payout_ratio'] as num?)?.toDouble(),
    returnOnEquity: (json['return_on_equity'] as num?)?.toDouble(),
    variableAnnualDividend: (json['variable_annual_dividend'] as num?)
        ?.toDouble(),
  );
}

/// One assumption behind a valuation, with where its value came from.
///
/// [source] is the backend's own label - "derived", "derived (clamped)",
/// "default" or "override". It matters as much as the number: a figure read
/// off the company's filings and a global placeholder deserve different
/// weight, and the backend is careful to distinguish them.
class Assumption {
  final String name;
  final double value;
  final String source;
  final String detail;
  final bool isPercent;

  const Assumption({
    required this.name,
    required this.value,
    required this.source,
    required this.detail,
    required this.isPercent,
  });

  bool get isOverridden => source == 'override';

  factory Assumption.fromJson(Map<String, dynamic> json) => Assumption(
    name: json['name'] as String? ?? '',
    value: (json['value'] as num?)?.toDouble() ?? 0,
    source: json['source'] as String? ?? '',
    detail: json['detail'] as String? ?? '',
    isPercent: json['is_percent'] as bool? ?? false,
  );
}

/// A company was valued.
class ValuationSuccess extends ValuationResult {
  final String ticker;
  final String companyName;
  final String sector;

  /// The sector as it should be shown, or null when there is none to show.
  ///
  /// The data source intermittently omits a company's sector, and the backend
  /// then reports the placeholder "Unknown". That is not a sector, so it is
  /// left off the screen rather than displayed as one.
  String? get displaySector {
    final s = sector.trim();
    return s.isEmpty || s.toLowerCase() == 'unknown' ? null : s;
  }

  final double intrinsicValuePerShare;
  final double currentPrice;

  /// Fraction, e.g. -0.674 means 67.4% downside.
  final double upsideDownside;

  /// The backend's caveat that this figure follows from adjustable
  /// assumptions. Shown verbatim - it is not decoration.
  final String note;

  /// Every assumption behind this valuation, in the backend's order.
  final List<Assumption> assumptions;

  /// The company's reported starting point, as the backend mapped it.
  ///
  /// Carried so the app can recompute locally while a slider is dragged
  /// without re-deriving anything itself. Null for a dividend discount model,
  /// which projects dividends rather than cash flows and so has no base year
  /// of revenue, debt and capex to work from.
  final BaseYearData? baseYear;

  /// Which model produced [intrinsicValuePerShare].
  final ValuationMethod method;

  /// DDM only: the dividend the projection starts from.
  final DividendBasis? dividendBasis;

  /// DDM only: the backend's explanation of why a DCF was not used here.
  final String whyNotDcf;

  /// The backend's caveats about this particular company - for a bank, that a
  /// dividend model cannot see the cash returned through buybacks. Part of the
  /// answer, not an aside.
  final List<String> warnings;

  /// Doubts about whether the method fits this company at all - not caveats
  /// about an assumption. Robinhood is the case: valued with a DCF, but part of
  /// its revenue comes from lending the data does not show. Shown pinned beside
  /// the figure rather than among [warnings], and never repeated in them.
  final List<String> methodFitWarnings;

  /// How the figure moves across the two key assumptions; null when the
  /// backend sent no grid worth drawing.
  final SensitivityGrid? sensitivity;

  const ValuationSuccess({
    required this.ticker,
    required this.companyName,
    required this.sector,
    required this.intrinsicValuePerShare,
    required this.currentPrice,
    required this.upsideDownside,
    required this.note,
    required this.assumptions,
    required this.baseYear,
    this.method = ValuationMethod.dcf,
    this.dividendBasis,
    this.whyNotDcf = '',
    this.warnings = const [],
    this.methodFitWarnings = const [],
    this.sensitivity,
  });

  bool get isUndervalued => upsideDownside > 0;

  /// Whether the local engine can preview slider changes for this result.
  ///
  /// Only the DCF has a Dart implementation to preview with; a DDM change is
  /// confirmed by the backend instead. Showing a locally computed figure would
  /// mean reimplementing a second model here and keeping the two in step, and
  /// a figure this app computed by a different route than the backend's is
  /// exactly the kind of quiet divergence the confirmation step exists to
  /// prevent.
  bool get supportsLocalPreview =>
      method == ValuationMethod.dcf && baseYear != null;

  Map<String, double> get valuesByName => {
    for (final a in assumptions) a.name: a.value,
  };

  Assumption? assumption(String name) {
    for (final a in assumptions) {
      if (a.name == name) return a;
    }
    return null;
  }

  factory ValuationSuccess.fromJson(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>;
    // Global levers (equity risk premium, risk-free rate) arrive in their own
    // list; merge them in so any adjustable input can be looked up by name.
    final all = <Assumption>[
      ...(json['assumptions'] as List<dynamic>? ?? const []).map(
        (a) => Assumption.fromJson(a as Map<String, dynamic>),
      ),
      ...(json['global_levers'] as List<dynamic>? ?? const []).map(
        (a) => Assumption.fromJson(a as Map<String, dynamic>),
      ),
    ];
    // Deduplicate: terminal_growth appears in both lists.
    final seen = <String>{};
    final merged = <Assumption>[
      for (final a in all)
        if (seen.add(a.name)) a,
    ];

    // Read the method first and parse the rest on its terms. A DDM response
    // carries no `base_year`, so assuming one is what broke a bank.
    final method = ValuationMethod.parse(json['method'] as String?);
    final base = json['base_year'] as Map<String, dynamic>?;
    final dividends = json['dividend_base'] as Map<String, dynamic>?;

    return ValuationSuccess(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      sector: company['sector'] as String? ?? '',
      intrinsicValuePerShare: (json['intrinsic_value_per_share'] as num)
          .toDouble(),
      currentPrice: (json['current_price'] as num).toDouble(),
      upsideDownside: (json['upside_downside'] as num).toDouble(),
      note: json['note'] as String? ?? '',
      assumptions: merged,
      baseYear: base == null ? null : BaseYearData.fromJson(base),
      method: method,
      dividendBasis: dividends == null
          ? null
          : DividendBasis.fromJson(dividends),
      whyNotDcf: json['why_not_dcf'] as String? ?? '',
      warnings: (json['warnings'] as List<dynamic>? ?? const [])
          .map((w) => w.toString())
          .toList(),
      methodFitWarnings:
          (json['method_fit_warnings'] as List<dynamic>? ?? const [])
              .map((w) => w.toString())
              .toList(),
      sensitivity: method == ValuationMethod.ddm
          ? SensitivityGrid.fromDdm(
              json['sensitivity'] as Map<String, dynamic>?,
            )
          : SensitivityGrid.fromDcf(
              json['sensitivity'] as Map<String, dynamic>?,
            ),
    );
  }
}

/// The backend refused to value this company, and said why.
///
/// This is not an error. It is a deliberate answer: for a loss-making company
/// or a bank, a growth-perpetuity DCF would produce a confident but meaningless
/// number, so no figure is returned at all.
class ValuationNotSuitable extends ValuationResult {
  final String ticker;
  final String companyName;
  final String message;
  final List<String> reasons;

  /// True when the company was refused *only* for losing money, and so is one
  /// the opt-in speculative endpoint would actually serve.
  ///
  /// Never an endorsement of that estimate - it says an estimate can be asked
  /// for, nothing about whether it is worth anything. No UI acts on it yet.
  final bool speculativeEstimateAvailable;

  const ValuationNotSuitable({
    required this.ticker,
    required this.companyName,
    required this.message,
    required this.reasons,
    this.speculativeEstimateAvailable = false,
  });

  factory ValuationNotSuitable.fromJson(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>? ?? const {};
    return ValuationNotSuitable(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      message: json['message'] as String? ?? 'A standard DCF is not suitable.',
      reasons: (json['reasons'] as List<dynamic>? ?? const [])
          .map((r) => r.toString())
          .toList(),
      speculativeEstimateAvailable:
          json['speculative_estimate_available'] as bool? ?? false,
    );
  }
}

/// Anything that stopped a valuation being produced.
///
/// [kind] drives presentation: an unknown ticker is the user's typo, a plan
/// limit is a subscription matter, and an upstream failure is nobody's fault -
/// all three deserve different wording.
enum ValuationFailureKind {
  tickerNotFound,
  rateLimited,
  badRequest,

  /// The listing itself cannot be valued - its results and its share price are
  /// in different currencies. Nothing the user typed was wrong, so it must not
  /// be presented as a bad request.
  unsupportedListing,
  backendUnreachable,
  upstreamError,
  unexpected,
}

/// Said before the data provider's own message on a 502. It must be true both
/// when the provider could not be reached and when it was reached but sent
/// back incomplete data - so it claims neither.
const String kDataUnavailablePrefix =
    'The market data needed for this couldn’t be fetched right now. This is '
    'usually temporary, so try again shortly.';

class ValuationFailure extends ValuationResult {
  final ValuationFailureKind kind;
  final String message;

  const ValuationFailure(this.kind, this.message);
}

/// The outcome of an Excel export request.
sealed class ExcelResult {
  const ExcelResult();
}

/// The workbook came back and is ready to be written to disk.
class ExcelSuccess extends ExcelResult {
  final List<int> bytes;
  final String filename;
  const ExcelSuccess(this.bytes, this.filename);
}

/// The export failed, for the same reasons a valuation can.
///
/// Reuses [ValuationFailureKind] so a plan limit or a refusal reads the same
/// whether the user asked for a number or a spreadsheet.
class ExcelFailure extends ExcelResult {
  final ValuationFailureKind kind;
  final String message;
  const ExcelFailure(this.kind, this.message);
}

/// The backend refused to build a workbook because a DCF does not suit the
/// company - the same guard that withholds a valuation.
class ExcelNotSuitable extends ExcelResult {
  final String message;
  const ExcelNotSuitable(this.message);
}

/// How the per-share figure moves across two assumptions - the table every
/// honesty note refers to when it says "the sensitivity table shows by how
/// much".
///
/// One shape for all three methods; only the axes differ. Rows are the first
/// axis (a discount rate, or the speculative target margin), columns the
/// second (terminal growth, or years to profitability). A null cell is one
/// the backend declined to compute because the model breaks there, and it is
/// kept as null - never zero, which would be a number.
class SensitivityGrid {
  final String rowAxis;
  final String columnAxis;
  final List<double> rowValues;
  final List<double> columnValues;

  /// Rates render as percentages; a count of years does not.
  final bool rowsArePercent;
  final bool columnsArePercent;

  final List<List<double?>> cells;
  final int centreRow;
  final int centreCol;

  /// What a null cell means for this model, for the footnote.
  final String blankMeaning;

  const SensitivityGrid({
    required this.rowAxis,
    required this.columnAxis,
    required this.rowValues,
    required this.columnValues,
    required this.rowsArePercent,
    required this.columnsArePercent,
    required this.cells,
    required this.centreRow,
    required this.centreCol,
    required this.blankMeaning,
  });

  double get centreValue => cells[centreRow][centreCol]!;

  /// Every value the table actually shows.
  Iterable<double> get values => cells.expand((r) => r).whereType<double>();

  bool get hasBlanks => cells.any((r) => r.any((c) => c == null));

  static const _growthBlank =
      'no value: terminal growth at or above the discount rate, where the '
      'growth formula breaks down';

  /// The DCF's WACC x terminal growth grid.
  static SensitivityGrid? fromDcf(Map<String, dynamic>? json) => _parse(
    json,
    rowKey: 'waccs',
    colKey: 'growth_rates',
    rowAxis: 'Discount rate (WACC)',
    columnAxis: 'Terminal growth',
    columnsArePercent: true,
    blankMeaning: _growthBlank,
  );

  /// The DDM's cost of equity x terminal growth grid.
  static SensitivityGrid? fromDdm(Map<String, dynamic>? json) => _parse(
    json,
    rowKey: 'costs_of_equity',
    colKey: 'growth_rates',
    rowAxis: 'Cost of equity',
    columnAxis: 'Terminal growth',
    columnsArePercent: true,
    blankMeaning: _growthBlank,
  );

  /// The speculative estimate's target margin x years to profitability grid.
  static SensitivityGrid? fromSpeculative(Map<String, dynamic>? json) => _parse(
    json,
    rowKey: 'target_operating_margins',
    colKey: 'years_to_profitability',
    rowAxis: 'Target operating margin',
    columnAxis: 'Years to profitability',
    columnsArePercent: false,
    blankMeaning:
        'no estimate: a target margin that is not a profit, or '
        'fewer than one year, gives no path to profitability',
  );

  /// Null unless the grid is one worth drawing: axes and rows agree in size,
  /// the centre is inside it, and the centre - the case the headline figure
  /// describes - has a value. A table that fails any of those would contradict
  /// the figure it sits beneath, which is worse than no table.
  static SensitivityGrid? _parse(
    Map<String, dynamic>? json, {
    required String rowKey,
    required String colKey,
    required String rowAxis,
    required String columnAxis,
    required bool columnsArePercent,
    required String blankMeaning,
  }) {
    if (json == null) return null;
    try {
      final rows = (json[rowKey] as List<dynamic>)
          .map((v) => (v as num).toDouble())
          .toList();
      final cols = (json[colKey] as List<dynamic>)
          .map((v) => (v as num).toDouble())
          .toList();
      final cells = (json['grid'] as List<dynamic>)
          .map(
            (row) => (row as List<dynamic>)
                .map((v) => (v as num?)?.toDouble())
                .toList(),
          )
          .toList();
      final centreRow = (json['centre_row'] as num).toInt();
      final centreCol = (json['centre_col'] as num).toInt();

      if (rows.isEmpty || cols.isEmpty || cells.length != rows.length) {
        return null;
      }
      if (cells.any((row) => row.length != cols.length)) return null;
      if (centreRow < 0 || centreRow >= rows.length) return null;
      if (centreCol < 0 || centreCol >= cols.length) return null;
      if (cells[centreRow][centreCol] == null) return null;
      if (cells.any((row) => row.any((c) => c != null && !c.isFinite))) {
        return null;
      }

      return SensitivityGrid(
        rowAxis: rowAxis,
        columnAxis: columnAxis,
        rowValues: rows,
        columnValues: cols,
        rowsArePercent: true,
        columnsArePercent: columnsArePercent,
        cells: cells,
        centreRow: centreRow,
        centreCol: centreCol,
        blankMeaning: blankMeaning,
      );
    } catch (_) {
      return null;
    }
  }
}

/// A company used, or considered, as a peer.
class RelativePeer {
  final String ticker;
  final String name;
  final String industry;

  /// The backend's label for how this peer got into the group: "selected"
  /// when chosen automatically, "added by you" when added to that selection,
  /// "chosen by you" when the whole group was supplied.
  final String provenance;
  final String detail;

  const RelativePeer({
    required this.ticker,
    required this.name,
    required this.industry,
    required this.provenance,
    required this.detail,
  });

  /// How the provenance reads on screen.
  String get provenanceLabel => switch (provenance) {
    'selected' => 'Chosen automatically',
    'added by you' => 'Added by you',
    'chosen by you' => 'Chosen by you',
    _ => provenance,
  };

  bool get isAutomatic => provenance == 'selected';

  factory RelativePeer.fromJson(Map<String, dynamic> json) => RelativePeer(
    ticker: json['ticker'] as String? ?? '',
    name: json['name'] as String? ?? json['ticker'] as String? ?? '',
    industry: json['industry'] as String? ?? '',
    provenance: json['provenance'] as String? ?? '',
    detail: json['detail'] as String? ?? '',
  );
}

/// A company considered for the peer group and left out, with why.
class ExcludedPeer {
  final String ticker;
  final String? name;
  final String reason;

  const ExcludedPeer({required this.ticker, this.name, required this.reason});

  factory ExcludedPeer.fromJson(Map<String, dynamic> json) => ExcludedPeer(
    ticker: json['ticker'] as String? ?? '',
    name: json['name'] as String?,
    reason: json['reason'] as String? ?? '',
  );
}

class PeerSelection {
  /// "automatic", "automatic, edited by you", or "your list".
  final String mode;
  final String rule;
  final List<RelativePeer> peers;
  final List<ExcludedPeer> excluded;

  const PeerSelection({
    required this.mode,
    required this.rule,
    required this.peers,
    required this.excluded,
  });

  bool get isAutomatic => mode == 'automatic';

  factory PeerSelection.fromJson(Map<String, dynamic> json) => PeerSelection(
    mode: json['mode'] as String? ?? '',
    rule: json['rule'] as String? ?? '',
    peers: (json['peers'] as List<dynamic>? ?? const [])
        .map((p) => RelativePeer.fromJson(p as Map<String, dynamic>))
        .toList(),
    excluded: (json['excluded'] as List<dynamic>? ?? const [])
        .map((p) => ExcludedPeer.fromJson(p as Map<String, dynamic>))
        .toList(),
  );
}

/// One peer's value for a multiple, or why it was left out of that one.
class PeerMultipleValue {
  final String ticker;
  final double? value;
  final String? excludedReason;

  const PeerMultipleValue(this.ticker, this.value, this.excludedReason);

  factory PeerMultipleValue.fromJson(Map<String, dynamic> json) =>
      PeerMultipleValue(
        json['ticker'] as String? ?? '',
        (json['value'] as num?)?.toDouble(),
        json['excluded_reason'] as String?,
      );
}

/// One valuation multiple, the company's and its peers'.
class RelativeMultiple {
  final String name;
  final String label;

  /// Whether the multiple could be applied - enough peers with a meaningful
  /// value, and a meaningful value for the company.
  final bool applicable;
  final double? companyValue;
  final String? companyReason;
  final double? peerMedian;
  final int peerCount;

  /// Fraction: 0.11 means 11% above the peer median.
  final double? premiumToMedian;

  /// Null when not applicable, and also when the backend withholds a figure
  /// from an applicable multiple because too few multiples applied overall.
  final double? impliedValuePerShare;
  final String? reason;
  final List<PeerMultipleValue> peers;

  const RelativeMultiple({
    required this.name,
    required this.label,
    required this.applicable,
    required this.companyValue,
    required this.companyReason,
    required this.peerMedian,
    required this.peerCount,
    required this.premiumToMedian,
    required this.impliedValuePerShare,
    required this.reason,
    required this.peers,
  });

  factory RelativeMultiple.fromJson(Map<String, dynamic> json) =>
      RelativeMultiple(
        name: json['name'] as String? ?? '',
        label: json['label'] as String? ?? '',
        applicable: json['applicable'] as bool? ?? false,
        companyValue: (json['company_value'] as num?)?.toDouble(),
        companyReason: json['company_reason'] as String?,
        peerMedian: (json['peer_median'] as num?)?.toDouble(),
        peerCount: (json['peer_count'] as num?)?.toInt() ?? 0,
        premiumToMedian: (json['premium_to_median'] as num?)?.toDouble(),
        impliedValuePerShare: (json['implied_value_per_share'] as num?)
            ?.toDouble(),
        reason: json['reason'] as String?,
        peers: (json['peers'] as List<dynamic>? ?? const [])
            .map((p) => PeerMultipleValue.fromJson(p as Map<String, dynamic>))
            .toList(),
      );
}

/// The intrinsic valuation the relative view is compared against, as the
/// backend computed it for the comparison: on derived assumptions, with no
/// slider overrides.
class IntrinsicReminder {
  final ValuationMethod method;
  final double valuePerShare;
  final double currentPrice;

  const IntrinsicReminder({
    required this.method,
    required this.valuePerShare,
    required this.currentPrice,
  });

  static IntrinsicReminder? fromJson(Map<String, dynamic>? json) {
    final value = (json?['intrinsic_value_per_share'] as num?)?.toDouble();
    if (json == null || value == null) return null;
    return IntrinsicReminder(
      method: ValuationMethod.parse(json['method'] as String?),
      valuePerShare: value,
      currentPrice: (json['current_price'] as num?)?.toDouble() ?? 0,
    );
  }
}

/// A relative, market-based view of a company - produced or declined.
///
/// Both come back with the intrinsic valuation, and a decline can still carry
/// the multiples as information, so one type holds both. [hasFigure] is the
/// only thing that says whether there is a relative value per share.
class RelativeReport {
  final String ticker;
  final String companyName;
  final IntrinsicReminder? intrinsic;
  final PeerSelection? peerSelection;
  final List<RelativeMultiple> multiples;

  // Produced only.
  final double? relativeValue;
  final double? low;
  final double? high;
  final List<String> multiplesApplied;
  final double? currentPrice;
  final double? relativeVsPrice;
  final String framing;

  /// The backend's note - including that relative valuation inherits the
  /// market's own mispricing. Empty on a decline, which carries none.
  final String note;
  final String comparisonStatement;
  final List<String> warnings;

  // Declined only.
  final String declineMessage;
  final List<String> declineReasons;

  const RelativeReport({
    required this.ticker,
    required this.companyName,
    required this.intrinsic,
    required this.peerSelection,
    required this.multiples,
    this.relativeValue,
    this.low,
    this.high,
    this.multiplesApplied = const [],
    this.currentPrice,
    this.relativeVsPrice,
    this.framing = '',
    this.note = '',
    this.comparisonStatement = '',
    this.warnings = const [],
    this.declineMessage = '',
    this.declineReasons = const [],
  });

  bool get hasFigure => relativeValue != null;

  static List<String> _strings(dynamic list) =>
      (list as List<dynamic>? ?? const []).map((s) => s.toString()).toList();

  static List<RelativeMultiple> _multiples(dynamic list) =>
      (list as List<dynamic>? ?? const [])
          .map((m) => RelativeMultiple.fromJson(m as Map<String, dynamic>))
          .toList();

  static PeerSelection? _selection(dynamic json) => json == null
      ? null
      : PeerSelection.fromJson(json as Map<String, dynamic>);

  /// A 200: a relative figure was produced.
  factory RelativeReport.produced(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>? ?? const {};
    final rv = json['relative_valuation'] as Map<String, dynamic>;
    final value = rv['relative_value_per_share'] as Map<String, dynamic>?;
    final comparison = rv['comparison_with_intrinsic'] as Map<String, dynamic>?;
    return RelativeReport(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      intrinsic: IntrinsicReminder.fromJson(
        json['intrinsic_valuation'] as Map<String, dynamic>?,
      ),
      peerSelection: _selection(rv['peer_selection']),
      multiples: _multiples(rv['multiples']),
      relativeValue: (value?['central'] as num?)?.toDouble(),
      low: (value?['low'] as num?)?.toDouble(),
      high: (value?['high'] as num?)?.toDouble(),
      multiplesApplied: _strings(value?['multiples_applied']),
      currentPrice: (rv['current_price'] as num?)?.toDouble(),
      relativeVsPrice: (rv['relative_value_vs_price'] as num?)?.toDouble(),
      framing: rv['framing'] as String? ?? '',
      note: rv['note'] as String? ?? '',
      comparisonStatement: comparison?['statement'] as String? ?? '',
      warnings: _strings(rv['warnings']),
    );
  }

  /// A 422 "not_suitable": no relative figure, but the intrinsic valuation
  /// and any multiples that could be computed.
  factory RelativeReport.declined(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>? ?? const {};
    return RelativeReport(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      intrinsic: IntrinsicReminder.fromJson(
        json['intrinsic_valuation'] as Map<String, dynamic>?,
      ),
      peerSelection: _selection(json['peer_selection']),
      multiples: _multiples(json['multiples']),
      declineMessage: json['message'] as String? ?? '',
      declineReasons: _strings(json['reasons']),
    );
  }
}

sealed class RelativeOutcome {
  const RelativeOutcome();
}

/// A report came back - with a relative figure or without one.
class RelativeAvailable extends RelativeOutcome {
  final RelativeReport report;
  const RelativeAvailable(this.report);
}

/// No intrinsic valuation for a relative view to sit beside.
class RelativeNotApplicable extends RelativeOutcome {
  final String message;
  final List<String> reasons;
  const RelativeNotApplicable(this.message, this.reasons);
}

class RelativeFailure extends RelativeOutcome {
  final String message;
  const RelativeFailure(this.message);
}

/// A speculative path-to-profitability estimate for a loss-making company.
///
/// Deliberately not a [ValuationResult]. It is not a valuation, and a separate
/// type means no screen built for valuations can be handed one and render its
/// figure as "intrinsic value" by accident.
class SpeculativeEstimate {
  final String ticker;
  final String companyName;

  /// The backend's warning headline, e.g. "SPECULATIVE ESTIMATE - NOT A
  /// VALUATION". Shown as given.
  final String headline;

  /// The backend's full speculative disclaimer. Shown in full, never
  /// shortened: every clause of it is load-bearing.
  final String disclaimer;

  /// Per share. Can be negative, and for RIVN is.
  final double valuePerShare;
  final double currentPrice;

  final List<String> warnings;
  final List<Assumption> assumptions;

  /// Target margin x years to profitability. On this screen above all the
  /// table is the point: it is where "speculative" stops being a word.
  final SensitivityGrid? sensitivity;

  const SpeculativeEstimate({
    required this.ticker,
    required this.companyName,
    required this.headline,
    required this.disclaimer,
    required this.valuePerShare,
    required this.currentPrice,
    required this.warnings,
    required this.assumptions,
    this.sensitivity,
  });

  bool get isNegative => valuePerShare < 0;

  Assumption? assumption(String name) {
    for (final a in assumptions) {
      if (a.name == name) return a;
    }
    return null;
  }

  factory SpeculativeEstimate.fromJson(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>? ?? const {};
    return SpeculativeEstimate(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      headline:
          json['disclaimer_headline'] as String? ??
          'SPECULATIVE ESTIMATE - NOT A VALUATION',
      disclaimer: json['disclaimer'] as String? ?? '',
      valuePerShare: (json['speculative_value_per_share'] as num).toDouble(),
      currentPrice: (json['current_price'] as num?)?.toDouble() ?? 0,
      warnings: (json['warnings'] as List<dynamic>? ?? const [])
          .map((w) => w.toString())
          .toList(),
      assumptions: (json['assumptions'] as List<dynamic>? ?? const [])
          .map((a) => Assumption.fromJson(a as Map<String, dynamic>))
          .toList(),
      sensitivity: SensitivityGrid.fromSpeculative(
        json['sensitivity'] as Map<String, dynamic>?,
      ),
    );
  }
}

sealed class SpeculativeOutcome {
  const SpeculativeOutcome();
}

class SpeculativeSuccess extends SpeculativeOutcome {
  final SpeculativeEstimate estimate;
  const SpeculativeSuccess(this.estimate);
}

/// No figure: the estimate is not offered for this company, or these
/// assumptions leave even a path to profitability meaningless. An answer, not
/// an error.
class SpeculativeRefused extends SpeculativeOutcome {
  final String message;
  final List<String> reasons;

  /// True when the backend will accept a hypothetical built from the user's
  /// own assumptions for this company. Never an endorsement of one: it says
  /// only that the refusal above can be argued with.
  final bool hypotheticalAvailable;

  /// The neutral values that screen starts from - no growth, no profit. They
  /// come from the backend so the app cannot quietly pick friendlier ones.
  final HypotheticalInputs? placeholders;

  const SpeculativeRefused(
    this.message,
    this.reasons, {
    this.hypotheticalAvailable = false,
    this.placeholders,
  });
}

class SpeculativeFailure extends SpeculativeOutcome {
  final String message;
  const SpeculativeFailure(this.message);
}

/// The three drivers of a hypothetical, as the user set them.
///
/// There is no default constructor value anywhere in this class: every figure
/// on that screen is the user's, and a forgotten default would quietly become
/// a suggestion.
class HypotheticalInputs {
  final double revenueGrowth;
  final double targetOperatingMargin;
  final int yearsToTarget;

  const HypotheticalInputs({
    required this.revenueGrowth,
    required this.targetOperatingMargin,
    required this.yearsToTarget,
  });

  HypotheticalInputs copyWith({
    double? revenueGrowth,
    double? targetOperatingMargin,
    int? yearsToTarget,
  }) => HypotheticalInputs(
    revenueGrowth: revenueGrowth ?? this.revenueGrowth,
    targetOperatingMargin: targetOperatingMargin ?? this.targetOperatingMargin,
    yearsToTarget: yearsToTarget ?? this.yearsToTarget,
  );

  /// True while this is still the neutral starting point rather than anything
  /// the user has claimed.
  bool get hasNoProfit => targetOperatingMargin <= 0;
}

/// One assumed figure beside the company's actual one.
///
/// The backend writes the sentence; the app never composes its own, so the
/// comparison shown can never drift from the figures it describes.
class RealityContrast {
  final String name;
  final String label;
  final double assumed;
  final double? actual;
  final String statement;

  /// True when the assumption is kinder to the company than what happened.
  final bool contradicts;

  const RealityContrast({
    required this.name,
    required this.label,
    required this.assumed,
    required this.actual,
    required this.statement,
    required this.contradicts,
  });

  factory RealityContrast.fromJson(Map<String, dynamic> json) =>
      RealityContrast(
        name: json['name'] as String? ?? '',
        label: json['label'] as String? ?? '',
        assumed: (json['assumed'] as num?)?.toDouble() ?? 0,
        actual: (json['actual'] as num?)?.toDouble(),
        statement: json['statement'] as String? ?? '',
        contradicts: json['contradicts'] as bool? ?? false,
      );
}

/// Arithmetic on the user's own assumptions. Not a valuation, and named so
/// that no part of the app can accidentally treat it as one.
class Hypothetical {
  final String ticker;
  final String companyName;
  final String headline;

  /// The strongest disclaimer the backend produces. Shown in full, above the
  /// figure, never collapsed.
  final String disclaimer;

  /// What the user's assumptions imply. Never called a value or an estimate.
  final double valuePerShare;

  /// Context only. No upside or downside is computed against it, here or
  /// anywhere else.
  final double currentPrice;

  final HypotheticalInputs inputs;
  final List<RealityContrast> reality;
  final List<String> whyStandardRefused;
  final List<String> whySpeculativeRefused;
  final List<String> warnings;
  final SensitivityGrid? sensitivity;

  const Hypothetical({
    required this.ticker,
    required this.companyName,
    required this.headline,
    required this.disclaimer,
    required this.valuePerShare,
    required this.currentPrice,
    required this.inputs,
    required this.reality,
    required this.whyStandardRefused,
    required this.whySpeculativeRefused,
    required this.warnings,
    this.sensitivity,
  });

  bool get isNegative => valuePerShare < 0;

  factory Hypothetical.fromJson(Map<String, dynamic> json) {
    final company = json['company'] as Map<String, dynamic>? ?? const {};
    final assumed =
        json['your_assumptions'] as Map<String, dynamic>? ?? const {};
    List<String> strings(String key) =>
        (json[key] as List<dynamic>? ?? const [])
            .map((s) => s.toString())
            .toList();

    return Hypothetical(
      ticker: company['ticker'] as String? ?? '',
      companyName: company['company_name'] as String? ?? '',
      headline:
          json['disclaimer_headline'] as String? ??
          'A HYPOTHETICAL YOU BUILT - NOT A VALUATION',
      disclaimer: json['disclaimer'] as String? ?? '',
      valuePerShare: (json['hypothetical_value_per_share'] as num).toDouble(),
      currentPrice: (json['current_price'] as num?)?.toDouble() ?? 0,
      inputs: HypotheticalInputs(
        revenueGrowth: (assumed['revenue_growth'] as num?)?.toDouble() ?? 0,
        targetOperatingMargin:
            (assumed['target_operating_margin'] as num?)?.toDouble() ?? 0,
        yearsToTarget: (assumed['years_to_target'] as num?)?.round() ?? 0,
      ),
      reality: (json['reality'] as List<dynamic>? ?? const [])
          .map((r) => RealityContrast.fromJson(r as Map<String, dynamic>))
          .toList(),
      whyStandardRefused: strings('why_standard_valuation_refused'),
      whySpeculativeRefused: strings('why_speculative_estimate_refused'),
      warnings: strings('warnings'),
      sensitivity: SensitivityGrid.fromSpeculative(
        json['sensitivity'] as Map<String, dynamic>?,
      ),
    );
  }
}

sealed class HypotheticalOutcome {
  const HypotheticalOutcome();
}

class HypotheticalBuilt extends HypotheticalOutcome {
  final Hypothetical hypothetical;
  const HypotheticalBuilt(this.hypothetical);
}

/// The assumptions do not yet describe a profit - the neutral starting point.
/// Not an error: the user has simply not made a claim yet.
class HypotheticalIncomplete extends HypotheticalOutcome {
  final String message;
  const HypotheticalIncomplete(this.message);
}

/// Not offered for this company at all, whatever is assumed.
class HypotheticalRefused extends HypotheticalOutcome {
  final String message;
  final List<String> reasons;
  const HypotheticalRefused(this.message, this.reasons);
}

class HypotheticalFailure extends HypotheticalOutcome {
  final String message;
  const HypotheticalFailure(this.message);
}

/// One company offered by search: what the dropdown shows and what a tap
/// values.
class CompanySearchResult {
  final String ticker;
  final String name;
  final String exchange;

  const CompanySearchResult({
    required this.ticker,
    required this.name,
    required this.exchange,
  });

  factory CompanySearchResult.fromJson(Map<String, dynamic> json) =>
      CompanySearchResult(
        ticker: json['ticker'] as String? ?? '',
        name: json['name'] as String? ?? '',
        exchange: json['exchange'] as String? ?? '',
      );
}

/// The outcome of a company search.
///
/// "Nothing matched" and "could not search" are separate on purpose: telling
/// someone no company is called Apple because a request was throttled would
/// be a small lie, and it would stop them looking.
sealed class SearchOutcome {
  const SearchOutcome();
}

/// Yahoo answered. [results] may be empty, which is itself an answer.
class SearchResults extends SearchOutcome {
  final List<CompanySearchResult> results;
  const SearchResults(this.results);
}

/// The search could not be run. Direct ticker entry still works.
class SearchUnavailable extends SearchOutcome {
  /// True for a 429 - ours or Yahoo's - which is worth backing off from.
  final bool rateLimited;
  const SearchUnavailable({this.rateLimited = false});
}

class ValuationApi {
  ValuationApi({http.Client? client, this.baseUrl = kBackendBaseUrl})
    : _client = client ?? http.Client();

  /// A search that has not answered in this long is no longer useful: the
  /// person has either typed on or given up on the dropdown. Much shorter than
  /// a valuation's budget, which has to survive a cold start - direct entry
  /// is still there if the server is waking.
  static const Duration searchTimeout = Duration(seconds: 15);

  /// A relative, market-based view of [ticker] beside its intrinsic value.
  ///
  /// Never throws. With no edits the peers are the backend's automatic
  /// selection; [addPeers] and [removePeers] edit that selection, which keeps
  /// each peer's provenance - automatic or added by the user - intact.
  Future<RelativeOutcome> relative(
    String ticker, {
    List<String> addPeers = const [],
    List<String> removePeers = const [],
  }) async {
    final cleaned = ticker.trim().toUpperCase();
    final edited = addPeers.isNotEmpty || removePeers.isNotEmpty;
    http.Response response;
    try {
      // Pricing several companies takes a while, and may wake the server.
      response = edited
          ? await _client
                .post(
                  Uri.parse('$baseUrl/valuation/relative'),
                  headers: const {'Content-Type': 'application/json'},
                  body: jsonEncode({
                    'ticker': cleaned,
                    'add_peers': addPeers,
                    'remove_peers': removePeers,
                  }),
                )
                .timeout(_timeout)
          : await _client
                .get(
                  Uri.parse(
                    '$baseUrl/valuation/${Uri.encodeComponent(cleaned)}/relative',
                  ),
                )
                .timeout(_timeout);
    } on TimeoutException {
      return const RelativeFailure(
        'The server did not respond in time. Comparing peers fetches several '
        'companies, and the server may also be waking up - try again.',
      );
    } catch (_) {
      return const RelativeFailure(
        'Could not reach the server. Check your connection and try again.',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (_) {
      debugPrint('unreadable relative response: HTTP ${response.statusCode}');
      return const RelativeFailure(
        'The server sent a response the app couldn’t read. Please try again.',
      );
    }
    final code = body['code'] as String?;

    try {
      switch (response.statusCode) {
        case 200:
          return RelativeAvailable(RelativeReport.produced(body));
        case 422 when code == 'relative_not_applicable':
          return RelativeNotApplicable(
            body['message'] as String? ?? '',
            (body['reasons'] as List<dynamic>? ?? const [])
                .map((r) => r.toString())
                .toList(),
          );
        case 422 when code == 'not_suitable':
          return RelativeAvailable(RelativeReport.declined(body));
        case 429:
          return const RelativeFailure(
            'Too many requests just now. Wait a moment and try again.',
          );
        default:
          final detail = body['message'] as String?;
          if (response.statusCode == 502) {
            return RelativeFailure(
              detail == null
                  ? kDataUnavailablePrefix
                  : '$kDataUnavailablePrefix\n\n$detail',
            );
          }
          return RelativeFailure(
            detail ?? 'The comparison couldn’t be produced. Please try again.',
          );
      }
    } catch (e) {
      debugPrint('could not parse relative view for $cleaned: $e');
      return const RelativeFailure(
        'The comparison couldn’t be read. Please try again.',
      );
    }
  }

  /// A speculative estimate for [ticker] - only ever called because the user
  /// explicitly asked for one.
  ///
  /// Never throws. With [overrides] it POSTs them; the backend labels each
  /// changed assumption as an override.
  Future<SpeculativeOutcome> speculative(
    String ticker, {
    Map<String, double> overrides = const {},
  }) async {
    final cleaned = ticker.trim().toUpperCase();
    http.Response response;
    try {
      response = overrides.isEmpty
          ? await _client
                .get(
                  Uri.parse(
                    '$baseUrl/valuation/${Uri.encodeComponent(cleaned)}/speculative',
                  ),
                )
                .timeout(_timeout)
          : await _client
                .post(
                  Uri.parse('$baseUrl/valuation/speculative'),
                  headers: const {'Content-Type': 'application/json'},
                  body: jsonEncode({
                    'ticker': cleaned,
                    // Whole numbers go as integers: the backend accepts
                    // years_to_profitability only as one.
                    'overrides': {
                      for (final e in overrides.entries)
                        e.key: e.key == 'years_to_profitability'
                            ? e.value.round()
                            : e.value,
                    },
                  }),
                )
                .timeout(_timeout);
    } on TimeoutException {
      return const SpeculativeFailure(
        'The server did not respond in time. It may be waking up - try again.',
      );
    } catch (_) {
      return const SpeculativeFailure(
        'Could not reach the server. Check your connection and try again.',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (_) {
      debugPrint(
        'unreadable speculative response: HTTP ${response.statusCode}',
      );
      return const SpeculativeFailure(
        'The server sent a response the app couldn’t read. Please try again.',
      );
    }
    final message = body['message'] as String? ?? 'Something went wrong.';

    switch (response.statusCode) {
      case 200:
        try {
          return SpeculativeSuccess(SpeculativeEstimate.fromJson(body));
        } catch (e) {
          debugPrint('could not parse speculative estimate for $cleaned: $e');
          return const SpeculativeFailure(
            'The estimate couldn’t be read. Please try again.',
          );
        }
      case 422:
        final placeholders =
            body['hypothetical_placeholders'] as Map<String, dynamic>?;
        return SpeculativeRefused(
          message,
          (body['reasons'] as List<dynamic>? ?? const [])
              .map((r) => r.toString())
              .toList(),
          hypotheticalAvailable:
              body['hypothetical_available'] as bool? ?? false,
          placeholders: placeholders == null
              ? null
              : HypotheticalInputs(
                  revenueGrowth:
                      (placeholders['revenue_growth'] as num?)?.toDouble() ?? 0,
                  targetOperatingMargin:
                      (placeholders['target_operating_margin'] as num?)
                          ?.toDouble() ??
                      0,
                  yearsToTarget:
                      (placeholders['years_to_target'] as num?)?.round() ?? 5,
                ),
        );
      case 429:
        return const SpeculativeFailure(
          'Too many requests just now. Wait a moment and try again.',
        );
      case 502:
        return SpeculativeFailure('$kDataUnavailablePrefix\n\n$message');
      default:
        return SpeculativeFailure(message);
    }
  }

  /// Arithmetic on assumptions the USER supplied, for a company even the
  /// speculative estimate refuses.
  ///
  /// Every driver is sent explicitly; there is no request this can make that
  /// leaves one to the backend to choose.
  Future<HypotheticalOutcome> hypothetical(
    String ticker,
    HypotheticalInputs inputs,
  ) async {
    final cleaned = ticker.trim().toUpperCase();
    http.Response response;
    try {
      response = await _client
          .get(
            Uri.parse(
              '$baseUrl/valuation/${Uri.encodeComponent(cleaned)}/hypothetical',
            ).replace(
              queryParameters: {
                'revenue_growth': inputs.revenueGrowth.toString(),
                'target_operating_margin': inputs.targetOperatingMargin
                    .toString(),
                'years_to_target': inputs.yearsToTarget.toString(),
              },
            ),
          )
          .timeout(_timeout);
    } on TimeoutException {
      return const HypotheticalFailure(
        'The server did not respond in time. It may be waking up - try again.',
      );
    } catch (_) {
      return const HypotheticalFailure(
        'Could not reach the server. Check your connection and try again.',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (_) {
      debugPrint(
        'unreadable hypothetical response: HTTP ${response.statusCode}',
      );
      return const HypotheticalFailure(
        'The server sent a response the app couldn’t read. Please try again.',
      );
    }
    final message = body['message'] as String? ?? 'Something went wrong.';

    switch (response.statusCode) {
      case 200:
        try {
          return HypotheticalBuilt(Hypothetical.fromJson(body));
        } catch (e) {
          debugPrint('could not parse hypothetical for $cleaned: $e');
          return const HypotheticalFailure(
            'The result couldn’t be read. Please try again.',
          );
        }
      case 422:
        final reasons = (body['reasons'] as List<dynamic>? ?? const [])
            .map((r) => r.toString())
            .toList();
        // "No profit assumed yet" is the starting state of the screen, not a
        // refusal of the company, and is shown as an invitation instead.
        return body['code'] == 'hypothetical_incomplete'
            ? HypotheticalIncomplete(message)
            : HypotheticalRefused(message, reasons);
      case 429:
        return const HypotheticalFailure(
          'Too many requests just now. Wait a moment and try again.',
        );
      case 502:
        return HypotheticalFailure('$kDataUnavailablePrefix\n\n$message');
      default:
        return HypotheticalFailure(message);
    }
  }

  /// Companies matching [query], by ticker or name.
  ///
  /// Never throws: a search is an aid to typing, never a precondition for
  /// valuing, so every failure becomes [SearchUnavailable].
  Future<SearchOutcome> search(String query) async {
    final cleaned = query.trim();
    if (cleaned.isEmpty) return const SearchResults([]);

    try {
      final response = await _client
          .get(
            Uri.parse('$baseUrl/search')
                .replace(queryParameters: {'q': cleaned}),
          )
          .timeout(searchTimeout);

      if (response.statusCode == 429) {
        return const SearchUnavailable(rateLimited: true);
      }
      if (response.statusCode != 200) return const SearchUnavailable();

      final body = jsonDecode(response.body) as Map<String, dynamic>;
      final results = (body['results'] as List<dynamic>? ?? const [])
          .map((r) => CompanySearchResult.fromJson(r as Map<String, dynamic>))
          .where((r) => r.ticker.isNotEmpty)
          .toList();
      return SearchResults(results);
    } catch (_) {
      return const SearchUnavailable();
    }
  }

  final http.Client _client;
  final String baseUrl;

  /// Long enough to survive a free-tier cold start.
  ///
  /// The backend sleeps after about fifteen minutes idle and takes roughly a
  /// minute to wake. At the old thirty seconds the first request of a session
  /// failed reliably, which read to the user as a broken app rather than a
  /// sleeping server.
  static const Duration _timeout = Duration(seconds: 90);

  /// Building a workbook does more work than a valuation - it refetches,
  /// re-derives, and writes every formula - so it gets a longer budget, and
  /// it may also be the request that wakes the server.
  static const Duration _excelTimeout = Duration(seconds: 120);

  /// How long a request may run before the UI starts saying the server is
  /// probably waking up. Comfortably past a warm response, well short of the
  /// timeout.
  static const Duration wakingThreshold = Duration(seconds: 4);

  /// Wake a sleeping instance without blocking the user.
  ///
  /// `/health` touches no upstream service, so this returns as soon as the
  /// process is up and costs the backend essentially nothing. Called on app
  /// launch so the spin-up overlaps with the user typing a ticker instead of
  /// being paid for in full by their first valuation.
  ///
  /// Returns true if the server answered. Never throws: failing to wake the
  /// server is not itself an error worth showing anyone, since the real
  /// request that follows will report any genuine problem.
  Future<bool> wakeUp() async {
    try {
      final response = await _client
          .get(Uri.parse('$baseUrl/health'))
          .timeout(_timeout);
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// Value [ticker], optionally overriding assumptions.
  ///
  /// With no overrides this is a plain GET and every assumption is the
  /// backend's own. With overrides it POSTs them, and only the named ones
  /// change - everything omitted keeps its derived or default value, and the
  /// response labels each accordingly.
  Future<ValuationResult> value(
    String ticker, {
    Map<String, double> overrides = const {},
  }) async {
    final cleaned = ticker.trim().toUpperCase();
    if (cleaned.isEmpty) {
      return const ValuationFailure(
        ValuationFailureKind.badRequest,
        'Enter a ticker symbol first.',
      );
    }

    http.Response response;
    try {
      if (overrides.isEmpty) {
        final uri = Uri.parse(
          '$baseUrl/valuation/${Uri.encodeComponent(cleaned)}',
        );
        response = await _client.get(uri).timeout(_timeout);
      } else {
        response = await _client
            .post(
              Uri.parse('$baseUrl/valuation'),
              headers: const {'Content-Type': 'application/json'},
              body: jsonEncode({'ticker': cleaned, 'overrides': overrides}),
            )
            .timeout(_timeout);
      }
    } on TimeoutException {
      return const ValuationFailure(
        ValuationFailureKind.backendUnreachable,
        'The server did not respond within 90 seconds.\n\n'
        'The free hosting tier puts the server to sleep when it is idle, and '
        'waking it usually takes under a minute. Trying again will often '
        'succeed.',
      );
    } on SocketException {
      return ValuationFailure(
        ValuationFailureKind.backendUnreachable,
        'Could not reach the server.\n\n'
        'Check your internet connection and try again.',
      );
    } catch (e) {
      debugPrint('valuation request for $cleaned failed: $e');
      return const ValuationFailure(
        ValuationFailureKind.unexpected,
        'Something went wrong while contacting the server. Please try again.',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (_) {
      debugPrint('unreadable valuation response: HTTP ${response.statusCode}');
      return const ValuationFailure(
        ValuationFailureKind.unexpected,
        'The server sent a response the app couldn’t read. Please try again.',
      );
    }

    final code = body['code'] as String?;
    final message = body['message'] as String? ?? 'Something went wrong.';

    switch (response.statusCode) {
      case 200:
        try {
          return ValuationSuccess.fromJson(body);
        } catch (e) {
          debugPrint('could not parse valuation for $cleaned: $e');
          return const ValuationFailure(
            ValuationFailureKind.unexpected,
            'The valuation couldn’t be read. Please try again.',
          );
        }

      case 422:
        // Shared status, separated by `code`: a suitability refusal, a listing
        // that cannot be valued at all, and a request-validation error.
        if (code == 'not_suitable') {
          return ValuationNotSuitable.fromJson(body);
        }
        if (code == 'unsupported_listing') {
          return ValuationFailure(
            ValuationFailureKind.unsupportedListing,
            message,
          );
        }
        return ValuationFailure(ValuationFailureKind.badRequest, message);

      case 404:
        return ValuationFailure(
          ValuationFailureKind.tickerNotFound,
          '$cleaned was not found.\n\nCheck the spelling. Note that ETFs, '
          'funds and recently listed companies often have no filings to value.',
        );

      case 429:
        // Shared status, separated by `code`: "rate_limited" is our own
        // server asking this user to slow down, "upstream_rate_limited" is
        // the market data provider throttling everybody. The user can act on
        // the first and can only wait out the second, so they read
        // differently.
        return ValuationFailure(
          ValuationFailureKind.rateLimited,
          code == 'rate_limited'
              ? 'You’re going a little fast.\n\nThis is a free service with a '
                    'shared limit, so it asks for a short pause after about 30 '
                    'valuations a minute. Wait a moment and try again.'
              : 'The market data provider is rate-limiting requests right '
                    'now.\n\nThis affects everyone using the service, not just '
                    'you. Wait a moment and try again.',
        );

      case 400:
        return ValuationFailure(ValuationFailureKind.badRequest, message);

      case 502:
        return ValuationFailure(
          ValuationFailureKind.upstreamError,
          '$kDataUnavailablePrefix\n\n$message',
        );

      default:
        debugPrint('unexpected valuation status: HTTP ${response.statusCode}');
        return const ValuationFailure(
          ValuationFailureKind.unexpected,
          'The server gave an unexpected response. Please try again.',
        );
    }
  }

  /// Download the .xlsx DCF model for [ticker], with [overrides] applied so
  /// the workbook matches what is on screen.
  ///
  /// The workbook is always built by the backend, never from the local preview
  /// engine: a spreadsheet outlives the screen that produced it, and it must
  /// carry the authoritative figures with their provenance and sources.
  Future<ExcelResult> downloadExcel(
    String ticker, {
    Map<String, double> overrides = const {},
  }) async {
    final cleaned = ticker.trim().toUpperCase();
    if (cleaned.isEmpty) {
      return const ExcelFailure(
        ValuationFailureKind.badRequest,
        'Enter a ticker symbol first.',
      );
    }

    http.Response response;
    try {
      if (overrides.isEmpty) {
        response = await _client
            .get(
              Uri.parse(
                '$baseUrl/valuation/${Uri.encodeComponent(cleaned)}/excel',
              ),
            )
            .timeout(_excelTimeout);
      } else {
        response = await _client
            .post(
              Uri.parse('$baseUrl/valuation/excel'),
              headers: const {'Content-Type': 'application/json'},
              body: jsonEncode({'ticker': cleaned, 'overrides': overrides}),
            )
            .timeout(_excelTimeout);
      }
    } on TimeoutException {
      return const ExcelFailure(
        ValuationFailureKind.backendUnreachable,
        'The server did not return the workbook within two minutes.\n\n'
        'If the server had gone to sleep it may still be waking up; '
        'try the export again.',
      );
    } on SocketException {
      return ExcelFailure(
        ValuationFailureKind.backendUnreachable,
        'Could not reach the server to build the workbook.\n\n'
        'Check your internet connection and try again.',
      );
    } catch (e) {
      debugPrint('workbook download for $cleaned failed: $e');
      return const ExcelFailure(
        ValuationFailureKind.unexpected,
        'Something went wrong while downloading the spreadsheet. Please try again.',
      );
    }

    if (response.statusCode == 200) {
      // Guard against a JSON error body arriving with a 200 by checking the
      // zip magic number - a .xlsx is a zip, and writing anything else to a
      // .xlsx file would produce a download that silently fails to open.
      final bytes = response.bodyBytes;
      if (bytes.length < 2 || bytes[0] != 0x50 || bytes[1] != 0x4B) {
        return const ExcelFailure(
          ValuationFailureKind.unexpected,
          'The spreadsheet didn’t download correctly. Please try again.',
        );
      }
      return ExcelSuccess(bytes, _filenameFrom(response, cleaned));
    }

    // Errors come back as JSON, in the same shape the valuation uses.
    String message = 'The workbook could not be produced.';
    String? code;
    try {
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      code = body['code'] as String?;
      message =
          (body['message'] as String?) ??
          ((body['reasons'] as List<dynamic>?)?.join('\n') ?? message);
    } catch (_) {
      // Leave the default message.
    }

    return switch (response.statusCode) {
      422 when code == 'not_suitable' => ExcelNotSuitable(message),
      422 when code == 'unsupported_listing' => ExcelFailure(
        ValuationFailureKind.unsupportedListing,
        message,
      ),
      422 => ExcelFailure(ValuationFailureKind.badRequest, message),
      404 => ExcelFailure(
        ValuationFailureKind.tickerNotFound,
        '$cleaned was not found, so there is nothing to export.',
      ),
      429 when code == 'rate_limited' => const ExcelFailure(
        ValuationFailureKind.rateLimited,
        'You’re going a little fast. This is a free service with a shared '
        'limit — wait a moment and try the export again.',
      ),
      429 => const ExcelFailure(
        ValuationFailureKind.rateLimited,
        'The market data provider is rate-limiting requests right now. '
        'Try the export again shortly.',
      ),
      502 => ExcelFailure(
        ValuationFailureKind.upstreamError,
        '$kDataUnavailablePrefix\n\n$message',
      ),
      _ => const ExcelFailure(
        ValuationFailureKind.unexpected,
        'The spreadsheet couldn’t be created. Please try again.',
      ),
    };
  }

  /// Prefer the filename the backend chose, falling back to the ticker.
  String _filenameFrom(http.Response response, String ticker) {
    final disposition = response.headers['content-disposition'];
    if (disposition != null) {
      final match = RegExp(r'filename="?([^";]+)"?').firstMatch(disposition);
      final name = match?.group(1)?.trim();
      if (name != null && name.isNotEmpty) return name;
    }
    return '${ticker}_DCF_Model.xlsx';
  }

  void dispose() => _client.close();
}
