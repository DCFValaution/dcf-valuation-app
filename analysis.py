"""
Suitability guard and end-to-end valuation orchestration.

A growth-perpetuity DCF is not a universal instrument. It assumes a business
that earns positive operating profit, generates free cash flow, and settles
into stable perpetual growth. Applied to a company that fits none of those
descriptions, the arithmetic still completes and still prints a number - which
is precisely the danger. Rivian, handed Apple's assumptions, valued at
$26.97/share against a $16.97 market price: a confident +59% "buy signal"
generated for a company losing $3.6bn a year.

This module refuses to produce a number in those cases. A blocking reason
means no valuation is returned at all - not a valuation with a warning
attached, because a number on screen is what people remember.
"""

from dataclasses import dataclass, field

from assumptions import DerivedAssumptions, derive_assumptions
from dcf import BaseYearData, DCFResult, run_dcf
from market_data import (CompanyFinancials, base_year_from, fetch_financials,
                         num)

# Sectors where enterprise free cash flow is not a meaningful construct:
# for banks and insurers, debt is raw material rather than financing, so
# "unlevered" cash flow has no economic meaning. These need a dividend
# discount or excess-return model instead.
UNSUITABLE_SECTORS = {"Financial Services"}

MIN_HISTORY_YEARS = 2
HIGH_TV_SHARE = 0.90  # terminal value share of EV above which we warn


@dataclass
class Suitability:
    suitable: bool
    reasons: list[str] = field(default_factory=list)    # blocking
    warnings: list[str] = field(default_factory=list)   # non-blocking


@dataclass
class ValuationReport:
    ticker: str
    company_name: str
    sector: str
    exchange: str
    fiscal_year: str
    suitability: Suitability
    base: BaseYearData
    derived: DerivedAssumptions
    meta: dict
    result: DCFResult | None  # None whenever suitability.suitable is False

    @property
    def suitable(self) -> bool:
        return self.suitability.suitable


def assess_suitability(fin: CompanyFinancials, derived: DerivedAssumptions,
                       base: BaseYearData) -> Suitability:
    """Decide whether a standard growth-perpetuity DCF applies to this company."""
    reasons: list[str] = []
    warnings: list[str] = []

    a = derived.assumptions
    latest = fin.income[0]
    fiscal_year = latest.get("fiscalYear", "?")

    # --- Blocking: not enough history to derive anything defensible --------
    if fin.years_available < MIN_HISTORY_YEARS:
        reasons.append(
            f"Only {fin.years_available} year(s) of financial history are available. "
            f"At least {MIN_HISTORY_YEARS} are needed to establish a trend."
        )

    # --- Blocking: no revenue ---------------------------------------------
    revenue = num(latest, "revenue")
    if revenue <= 0:
        reasons.append(
            f"The company reported no revenue in FY{fiscal_year}. "
            "A revenue-driven projection cannot be built."
        )

    # --- Blocking: operating losses ---------------------------------------
    operating_income = num(latest, "operatingIncome")
    if operating_income <= 0:
        loss_years = [
            r.get("fiscalYear") for r in fin.income if num(r, "operatingIncome") <= 0
        ]
        reasons.append(
            f"The company is loss-making at the operating level: FY{fiscal_year} "
            f"operating income was ${operating_income / 1e6:,.0f}mm "
            f"(a loss in {len(loss_years)} of the last {fin.years_available} years).\n"
            "    A growth-perpetuity DCF assumes profitable operations that grow "
            "steadily. Projecting a positive margin onto a loss-making company "
            "invents the profitability the model then values."
        )
    elif a.operating_margin <= 0:
        reasons.append(
            "The company's median operating margin over the recent window is "
            f"{a.operating_margin:.1%}. Sustained operating losses make a "
            "growth-perpetuity DCF inapplicable."
        )

    # --- Blocking: sector where unlevered FCF is meaningless ---------------
    if fin.sector in UNSUITABLE_SECTORS:
        reasons.append(
            f"{fin.company_name} is in the {fin.sector} sector. For banks and "
            "insurers, borrowing is an input to operations rather than financing, "
            "so unlevered free cash flow and an enterprise-to-equity bridge are "
            "not meaningful. A dividend discount or excess-return model is the "
            "appropriate tool."
        )

    # --- Blocking: perpetuity formula breaks down -------------------------
    if a.terminal_growth >= a.wacc:
        reasons.append(
            f"Terminal growth ({a.terminal_growth:.1%}) is not below WACC "
            f"({a.wacc:.1%}). The Gordon growth formula requires WACC > g; "
            "otherwise terminal value is infinite or negative."
        )

    # --- Blocking: the projection never produces cash ----------------------
    # Checked directly rather than inferred, because a company can be
    # profitable on an operating basis yet still consume cash through capex.
    if not reasons:
        probe = run_dcf(base, a)
        if probe.years[-1].ufcf <= 0:
            reasons.append(
                f"Projected free cash flow is still negative in the final "
                f"projection year (${probe.years[-1].ufcf:,.0f}mm). "
                "Capital intensity exceeds operating profit, so there is no "
                "positive cash flow stream to capitalise into a terminal value."
            )

    # --- Non-blocking warnings --------------------------------------------
    if not reasons:
        result = run_dcf(base, a)

        if result.intrinsic_value_per_share <= 0:
            warnings.append(
                "Equity value came out negative - debt exceeds the value of the "
                "business plus its cash. Treat the per-share figure as an "
                "indication of distress, not a price target."
            )
        if result.tv_pct_of_ev > HIGH_TV_SHARE:
            warnings.append(
                f"Terminal value is {result.tv_pct_of_ev:.0%} of enterprise value. "
                "Almost all the valuation rests on the perpetuity assumption "
                "rather than the explicit forecast, so the result is highly "
                "sensitive to WACC and terminal growth."
            )
        if a.revenue_growth <= a.terminal_growth:
            warnings.append(
                f"Derived revenue growth ({a.revenue_growth:.1%}) is at or below "
                f"terminal growth ({a.terminal_growth:.1%}), implying the company "
                "grows faster after the forecast period than during it."
            )

    for name, prov in derived.provenance.items():
        if prov.source == "derived (clamped)":
            warnings.append(f"{name} was clamped: {prov.detail}")

    return Suitability(suitable=not reasons, reasons=reasons, warnings=warnings)


def value_company(ticker: str, overrides: dict | None = None) -> ValuationReport:
    """
    Fetch *ticker*, derive its assumptions, check suitability, and value it.

    Returns a ValuationReport whose `result` is None when the company fails
    the suitability guard. `overrides` is passed through to
    derive_assumptions() so any assumption can be replaced by the caller.
    """
    fin = fetch_financials(ticker)
    base, meta = base_year_from(fin)
    derived = derive_assumptions(fin, overrides=overrides)
    suitability = assess_suitability(fin, derived, base)

    result = run_dcf(base, derived.assumptions) if suitability.suitable else None

    return ValuationReport(
        ticker=fin.ticker,
        company_name=fin.company_name,
        sector=fin.sector,
        exchange=fin.exchange,
        fiscal_year=str(meta.get("fiscal_year", "?")),
        suitability=suitability,
        base=base,
        derived=derived,
        meta=meta,
        result=result,
    )


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

PERCENT_FIELDS = {
    "revenue_growth", "operating_margin", "tax_rate",
    "da_pct", "capex_pct", "nwc_pct", "wacc", "terminal_growth",
    "risk_free_rate", "equity_risk_premium", "cost_of_debt",
}


def _format_value(name: str, value: float) -> str:
    return f"{value:.2%}" if name in PERCENT_FIELDS else f"{value:g}"


# The three assumptions that are judgement calls rather than observations of
# the company. They drive the answer more than anything derived from filings,
# and they are the ones a user will legitimately want to argue with.
GLOBAL_LEVERS = ("risk_free_rate", "equity_risk_premium", "terminal_growth")


def honesty_note(report: "ValuationReport") -> str | None:
    """
    The sentence that must accompany any intrinsic value this system reports.

    Extracted so the text renderer and the API return identical wording - the
    caveat should not be something a caller can accidentally drop by consuming
    a different surface. Returns None when no valuation was produced.
    """
    if report.result is None:
        return None

    combined = {**report.derived.wacc_inputs, **report.derived.provenance}
    erp = combined["equity_risk_premium"].value if "equity_risk_premium" in combined else None
    erp_text = f", an equity risk premium of {erp:.2%}" if erp is not None else ""
    # Company names routinely end in "Inc." - avoid "Apple Inc.." below.
    name = report.company_name.rstrip(".")

    return (
        f"${report.result.intrinsic_value_per_share:,.2f} is the output of these assumptions, "
        f"not a fact about {name}. It assumes a WACC of "
        f"{report.derived.assumptions.wacc:.2%}{erp_text}, and "
        f"{report.derived.assumptions.terminal_growth:.2%} growth in perpetuity; the equity "
        "risk premium in particular has no single correct value. Move any of them and the "
        "figure moves materially - the sensitivity table shows by how much."
    )


def _global_assumptions_block(report: "ValuationReport") -> list[str]:
    """
    Surface the global assumptions and state plainly that the headline number
    depends on them.

    An intrinsic value printed without this reads as a measurement. It is not
    one: it is the output of assumptions a reasonable person could set
    differently, and the equity risk premium in particular has no correct
    value - credible published estimates span roughly 4.5-6%, which moves the
    result substantially on its own.
    """
    out = ["\nGLOBAL ASSUMPTIONS - the main levers to adjust"]
    combined = {**report.derived.wacc_inputs, **report.derived.provenance}
    for name in GLOBAL_LEVERS:
        prov = combined.get(name)
        if prov is None:
            continue
        out.append(f"  {name:<22}{_format_value(name, prov.value):>10}  "
                   f"{prov.source:<10}{prov.detail}")

    out.append("  Override any of them:")
    out.append(f"    value_company({report.ticker!r}, "
               "overrides={'equity_risk_premium': 0.05, 'terminal_growth': 0.03})")

    note = honesty_note(report)
    if note:
        out.append(f"\n  NOTE: {note}")
    return out


def _wacc_buildup(report: "ValuationReport") -> list[str]:
    """Render the CAPM/WACC build-up, when WACC was derived rather than defaulted."""
    components = report.derived.diagnostics.get("wacc_components") or {}
    if not components or "wacc" not in components:
        return []

    out = ["\nWACC BUILD-UP"]
    for name, prov in report.derived.wacc_inputs.items():
        shown = _format_value(name, prov.value)
        out.append(f"  {name:<22}{shown:>10}  {prov.source:<20}{prov.detail}")

    equity = components.get("equity_value", 0.0) / 1e6
    debt = components.get("debt_value", 0.0) / 1e6
    out.append(
        f"\n  Cost of equity          {components['cost_of_equity']:>9.2%}"
        f"   = {components['risk_free_rate']:.2%} + "
        f"{components['beta']:.3g} x {components['equity_risk_premium']:.2%}"
    )
    out.append(
        f"  Cost of debt (post-tax) {components['after_tax_cost_of_debt']:>9.2%}"
        f"   = {components['cost_of_debt']:.2%} x (1 - {components['tax_rate']:.2%})"
    )
    out.append(
        f"  Capital structure        E {components.get('weight_equity', 0):.1%} "
        f"/ D {components.get('weight_debt', 0):.1%}"
        f"   (equity ${equity:,.0f}mm at market, debt ${debt:,.0f}mm at book)"
    )
    out.append(f"  WACC                    {components['wacc']:>9.2%}")

    history = components.get("cost_of_debt_by_year") or []
    if history:
        shown = ", ".join(f"FY{year} {rate:.2%}" for year, rate in history)
        out.append(f"  Cost-of-debt years used: {shown}")
    return out


def format_report(report: ValuationReport) -> str:
    """Render a ValuationReport as readable text."""
    out: list[str] = []
    add = out.append

    add("=" * 78)
    add(f"{report.company_name} ({report.ticker}) - {report.exchange}")
    add(f"{report.sector} | base year FY{report.fiscal_year}")
    add("=" * 78)

    add("\nBASE-YEAR DATA")
    add(f"  Revenue                      ${report.base.revenue:>14,.0f}mm")
    add(f"  Total debt                   ${report.base.total_debt:>14,.0f}mm")
    add(f"  Cash & investments           ${report.base.cash:>14,.0f}mm")
    add(f"  Shares (diluted)              {report.base.shares:>14,.1f}mm")
    add(f"  Current price                ${report.base.current_price:>14,.2f}")

    add("\nASSUMPTIONS")
    add(f"  {'assumption':<18}{'value':>10}  {'source':<20}detail")
    add(f"  {'-' * 18}{'-' * 10}  {'-' * 20}{'-' * 24}")
    for name, prov in report.derived.provenance.items():
        add(f"  {name:<18}{_format_value(name, prov.value):>10}  {prov.source:<20}{prov.detail}")

    derived_names = report.derived.derived_names()
    defaulted = report.derived.defaulted_names()
    overridden = report.derived.overridden_names()
    add(f"\n  {len(derived_names)} derived from filings: {', '.join(derived_names) or '-'}")
    add(f"  {len(defaulted)} global defaults:     {', '.join(defaulted) or '-'}")
    if overridden:
        add(f"  {len(overridden)} caller overrides:    {', '.join(overridden)}")

    out.extend(_wacc_buildup(report))

    if not report.suitable:
        add("\n" + "!" * 78)
        add("A STANDARD DCF ISN'T SUITABLE FOR THIS COMPANY - NO VALUATION PRODUCED")
        add("!" * 78)
        for i, reason in enumerate(report.suitability.reasons, 1):
            add(f"\n  {i}. {reason}")
        add("\n  No intrinsic value per share is reported, deliberately: for this")
        add("  company the model would produce a number with no economic meaning.")
        return "\n".join(out)

    r = report.result
    add("\nVALUATION")
    add(f"  PV of explicit UFCF          ${r.pv_ufcf_sum:>14,.0f}mm")
    add(f"  PV of terminal value         ${r.pv_terminal_value:>14,.0f}mm")
    add(f"  Enterprise value             ${r.enterprise_value:>14,.0f}mm")
    add(f"    plus cash                  ${report.base.cash:>14,.0f}mm")
    add(f"    less debt                  (${report.base.total_debt:>13,.0f}mm)")
    add(f"  Equity value                 ${r.equity_value:>14,.0f}mm")
    add(f"  Terminal value / EV           {r.tv_pct_of_ev:>14.1%}")
    add(f"\n  INTRINSIC VALUE PER SHARE    ${r.intrinsic_value_per_share:>14,.2f}")
    add(f"  Current market price         ${report.base.current_price:>14,.2f}")
    add(f"  Implied upside / (downside)   {r.upside_downside:>14.1%}")

    add("\nSENSITIVITY - intrinsic value per share")
    add("  (centred on this company's derived WACC and terminal growth;")
    add("   the centre cell is the headline valuation above)")
    add("  WACC \\ g " + "".join(f"{g:>10.1%}" for g in r.sensitivity_gs))
    for w, row in zip(r.sensitivity_waccs, r.sensitivity):
        cells = "".join("         -" if v is None else f"{v:>10,.2f}" for v in row)
        add(f"  {w:>7.2%}  " + cells)
    if any(v is None for row in r.sensitivity for v in row):
        add("  ('-' marks combinations where terminal growth meets or exceeds WACC,")
        add("   for which the Gordon growth formula has no meaningful value)")

    out.extend(_global_assumptions_block(report))

    if report.suitability.warnings:
        add("\nWARNINGS")
        for warning in report.suitability.warnings:
            add(f"  - {warning}")

    return "\n".join(out)
