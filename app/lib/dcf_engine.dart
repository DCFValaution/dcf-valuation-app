/// A Dart port of the backend's DCF engine (`dcf.py`), for live recomputation
/// while a slider is being dragged.
///
/// WHY THIS EXISTS
/// ---------------
/// Every assumption change would otherwise be a network round trip, which is
/// far too slow to drag against. This recomputes locally so the figure tracks
/// the slider, and the backend still confirms the result once the drag ends.
///
/// THE BACKEND REMAINS THE SOURCE OF TRUTH
/// ---------------------------------------
/// This engine is a *preview*. It is seeded entirely from what the backend
/// returned - base-year data and the full assumption set - and only the slider
/// values vary. It never fetches data, never derives an assumption, and never
/// decides whether a company is suitable to value. If it and the backend
/// disagree, the backend wins and the user is told.
///
/// Because it duplicates logic that lives in Python, `test/dcf_engine_test.dart`
/// checks it against the reference spreadsheet case and against recorded
/// backend responses for several assumption combinations. Any divergence
/// should fail those tests rather than quietly show a wrong number.
library;

import 'dart:math' as math;

/// The company's reported starting point. All monetary values in $mm.
class BaseYearData {
  final double revenue;
  final double totalDebt;
  final double cash;
  final double shares; // millions
  final double currentPrice;

  const BaseYearData({
    required this.revenue,
    required this.totalDebt,
    required this.cash,
    required this.shares,
    required this.currentPrice,
  });

  factory BaseYearData.fromJson(Map<String, dynamic> json) => BaseYearData(
        revenue: (json['revenue'] as num).toDouble(),
        totalDebt: (json['total_debt'] as num).toDouble(),
        cash: (json['cash'] as num).toDouble(),
        shares: (json['shares'] as num).toDouble(),
        currentPrice: (json['current_price'] as num).toDouble(),
      );
}

/// The inputs that drive the projection. Rates are decimals: 0.06 is 6%.
class DcfAssumptions {
  final double revenueGrowth;
  final double operatingMargin;
  final double taxRate;
  final double daPct;
  final double capexPct;
  final double nwcPct;
  final double wacc;
  final double terminalGrowth;
  final int projectionYears;

  const DcfAssumptions({
    required this.revenueGrowth,
    required this.operatingMargin,
    required this.taxRate,
    required this.daPct,
    required this.capexPct,
    required this.nwcPct,
    required this.wacc,
    required this.terminalGrowth,
    this.projectionYears = 5,
  });

  /// Build from the backend's flat name/value map, which is how the app holds
  /// the assumption set between requests.
  factory DcfAssumptions.fromMap(Map<String, double> m) => DcfAssumptions(
        revenueGrowth: m['revenue_growth'] ?? 0,
        operatingMargin: m['operating_margin'] ?? 0,
        taxRate: m['tax_rate'] ?? 0,
        daPct: m['da_pct'] ?? 0,
        capexPct: m['capex_pct'] ?? 0,
        nwcPct: m['nwc_pct'] ?? 0,
        wacc: m['wacc'] ?? 0,
        terminalGrowth: m['terminal_growth'] ?? 0,
        projectionYears: (m['projection_years'] ?? 5).round(),
      );

  DcfAssumptions copyWith(Map<String, double> overrides) =>
      DcfAssumptions.fromMap({...toMap(), ...overrides});

  Map<String, double> toMap() => {
        'revenue_growth': revenueGrowth,
        'operating_margin': operatingMargin,
        'tax_rate': taxRate,
        'da_pct': daPct,
        'capex_pct': capexPct,
        'nwc_pct': nwcPct,
        'wacc': wacc,
        'terminal_growth': terminalGrowth,
        'projection_years': projectionYears.toDouble(),
      };
}

class ProjectionYear {
  final int period;
  final double revenue;
  final double ebit;
  final double tax;
  final double nopat;
  final double da;
  final double capex;
  final double deltaNwc;
  final double ufcf;
  final double discountFactor;
  final double pvUfcf;

  const ProjectionYear({
    required this.period,
    required this.revenue,
    required this.ebit,
    required this.tax,
    required this.nopat,
    required this.da,
    required this.capex,
    required this.deltaNwc,
    required this.ufcf,
    required this.discountFactor,
    required this.pvUfcf,
  });
}

class DcfResult {
  final List<ProjectionYear> years;
  final double pvUfcfSum;
  final double terminalValue;
  final double pvTerminalValue;
  final double enterpriseValue;
  final double equityValue;
  final double intrinsicValuePerShare;
  final double currentPrice;
  final double upsideDownside;
  final double tvPctOfEv;

  const DcfResult({
    required this.years,
    required this.pvUfcfSum,
    required this.terminalValue,
    required this.pvTerminalValue,
    required this.enterpriseValue,
    required this.equityValue,
    required this.intrinsicValuePerShare,
    required this.currentPrice,
    required this.upsideDownside,
    required this.tvPctOfEv,
  });
}

/// Thrown when the inputs make a growth-perpetuity DCF meaningless.
///
/// Only the arithmetic preconditions are checked here. Judgements about
/// whether a *company* suits this model - operating losses, banks - stay in
/// the backend, which is the only place with the filings to decide.
class DcfInputError implements Exception {
  final String message;
  const DcfInputError(this.message);
  @override
  String toString() => message;
}

/// Run the model. Mirrors `run_dcf` in dcf.py line for line.
DcfResult runDcf(BaseYearData base, DcfAssumptions a) {
  if (a.terminalGrowth >= a.wacc) {
    throw const DcfInputError(
      'Terminal growth must be below WACC for the Gordon growth formula.',
    );
  }
  if (a.projectionYears < 1) {
    throw const DcfInputError('The projection needs at least one year.');
  }
  if (base.shares <= 0) {
    throw const DcfInputError('Share count must be positive.');
  }

  final years = <ProjectionYear>[];
  var prevRevenue = base.revenue;

  for (var t = 1; t <= a.projectionYears; t++) {
    final revenue = prevRevenue * (1 + a.revenueGrowth);
    final ebit = revenue * a.operatingMargin;
    final tax = ebit * a.taxRate;
    final nopat = ebit - tax;
    final da = revenue * a.daPct;
    final capex = revenue * a.capexPct;
    final deltaNwc = (revenue - prevRevenue) * a.nwcPct;
    final ufcf = nopat + da - capex - deltaNwc;
    final discountFactor = 1 / math.pow(1 + a.wacc, t).toDouble();
    final pvUfcf = ufcf * discountFactor;

    years.add(ProjectionYear(
      period: t,
      revenue: revenue,
      ebit: ebit,
      tax: tax,
      nopat: nopat,
      da: da,
      capex: capex,
      deltaNwc: deltaNwc,
      ufcf: ufcf,
      discountFactor: discountFactor,
      pvUfcf: pvUfcf,
    ));
    prevRevenue = revenue;
  }

  final pvUfcfSum = years.fold<double>(0, (sum, y) => sum + y.pvUfcf);

  final lastUfcf = years.last.ufcf;
  final terminalValue =
      lastUfcf * (1 + a.terminalGrowth) / (a.wacc - a.terminalGrowth);
  final pvTerminalValue = terminalValue * years.last.discountFactor;

  final enterpriseValue = pvUfcfSum + pvTerminalValue;
  final equityValue = enterpriseValue + base.cash - base.totalDebt;
  final intrinsicValuePerShare = equityValue / base.shares;

  final upsideDownside = base.currentPrice == 0
      ? 0.0
      : (intrinsicValuePerShare - base.currentPrice) / base.currentPrice;
  final tvPctOfEv = enterpriseValue == 0 ? 0.0 : pvTerminalValue / enterpriseValue;

  return DcfResult(
    years: years,
    pvUfcfSum: pvUfcfSum,
    terminalValue: terminalValue,
    pvTerminalValue: pvTerminalValue,
    enterpriseValue: enterpriseValue,
    equityValue: equityValue,
    intrinsicValuePerShare: intrinsicValuePerShare,
    currentPrice: base.currentPrice,
    upsideDownside: upsideDownside,
    tvPctOfEv: tvPctOfEv,
  );
}

// ---------------------------------------------------------------------------
// Formatting that has to match Python exactly
// ---------------------------------------------------------------------------

/// Equivalent of Python's `f"{value:,.2f}"`.
String formatMoney(double value) {
  final negative = value < 0;
  final fixed = value.abs().toStringAsFixed(2);
  final parts = fixed.split('.');
  final digits = parts[0];

  final buffer = StringBuffer();
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buffer.write(',');
    buffer.write(digits[i]);
  }
  return '${negative ? '-' : ''}$buffer.${parts[1]}';
}

/// Equivalent of Python's `f"{value:.2%}"`.
String formatPercent2(double fraction) =>
    '${(fraction * 100).toStringAsFixed(2)}%';

/// Rebuilds the backend's caveat sentence with locally recomputed figures.
///
/// The wording is duplicated from `analysis.honesty_note()` in Python. That
/// duplication is deliberate - the note must update as the slider moves, and
/// it cannot be re-fetched mid-drag - but it is a drift risk, so a test
/// asserts this reproduces the backend's own string exactly for the
/// un-overridden case.
String buildHonestyNote({
  required double intrinsicValuePerShare,
  required String companyName,
  required double wacc,
  required double terminalGrowth,
  double? equityRiskPremium,
}) {
  // Python does company_name.rstrip('.') to avoid "Apple Inc..".
  final name = companyName.replaceAll(RegExp(r'\.+$'), '');
  final erpText = equityRiskPremium == null
      ? ''
      : ', an equity risk premium of ${formatPercent2(equityRiskPremium)}';

  return '\$${formatMoney(intrinsicValuePerShare)} is the output of these '
      'assumptions, not a fact about $name. It assumes a WACC of '
      '${formatPercent2(wacc)}$erpText, and '
      '${formatPercent2(terminalGrowth)} growth in perpetuity; the equity '
      'risk premium in particular has no single correct value. Move any of '
      'them and the figure moves materially - the sensitivity table shows by '
      'how much.';
}
