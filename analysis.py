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
from statistics import median

from assumptions import DerivedAssumptions, derive_assumptions
from plain_language import (clamp_warning, dividend_growth_default_warning,
                            growth_below_terminal_warning)
from dcf import BaseYearData, DCFResult, run_dcf
from ddm import DDMInputs, DDMResult, run_ddm
from ddm_assumptions import (DIVIDEND_CUT_TOLERANCE, DerivedDDMAssumptions,
                             DividendRecord, analyse_dividends,
                             derive_ddm_assumptions)
from datetime import date

from market_data import (CompanyFinancials, DataUnavailableError, MarketDataError,
                         MarketFigures, RateLimitedError, TickerNotFoundError,
                         base_year_from, fetch_also_watched, fetch_classification,
                         fetch_dividends, fetch_financials, fetch_market_figures, num)
from relative import (FINANCIAL_MULTIPLES, MAX_AUTO_PEERS, MAX_PEERS,
                      MIN_APPLICABLE_MULTIPLES, MIN_PEERS, SIZE_BAND,
                      STANDARD_MULTIPLES, WIDE_RANGE_RATIO, RelativeResult,
                      peer_exclusion, run_relative)
from speculative import SpeculativeResult, run_speculative
from speculative_assumptions import (MARGIN_IMPROVEMENT_PACE,
                                     DerivedSpeculativeAssumptions,
                                     derive_speculative_assumptions)

# ---------------------------------------------------------------------------
# Which financial companies get which method
#
# "Financial Services" is too broad to route on. It holds banks and insurers,
# for which a DCF is meaningless and a dividend discount model is the right
# tool - but also payments networks, exchanges, data providers and asset
# managers, which are fee businesses a DCF values like any other, and lenders
# that are neither. Probing 27 real companies across every industry in the
# sector showed:
#
#   banks           interest expense 48-71% of revenue; a DCF gives +187-344%
#   insurers        interest expense 0.4-1.7%; a DCF still gives +229-737%,
#                   because reserves and float, not debt, are the raw material
#   card lenders    (COF, SYF, SOFI) 28-37%; a DCF gives up to +495%
#   broker-dealers  (GS, MS, SCHW) 33-134%; a DCF gives +75-149%
#   fee businesses  (V, MA, PYPL, BLK, SPGI, MCO, CME, ICE, AON) 1.2-7.7%;
#                   a DCF gives -50% to +64%, the range it gives industrials
#
# So banks and insurers are identified by industry - interest burden cannot
# find insurers, whose balance sheets are not debt-funded - and the rest of
# the sector is split by interest burden, because Yahoo files Visa and
# Capital One under the same "Credit Services" industry. American Express
# (11.4%) is the closest case to the line and lands on the refusal side,
# deliberately: a card lender's DCF is the kind of number this guard exists
# to stop.
# ---------------------------------------------------------------------------

FINANCIAL_SECTOR = "Financial Services"

# What market_data reports when the data source gave no sector at all.
UNKNOWN_SECTOR = "Unknown"

# Industries valued with the dividend discount model. Insurance brokers are
# carved out: they sell insurance for commission rather than underwriting it,
# hold no reserves or float, and are ordinary fee businesses.
DDM_INDUSTRY_PREFIXES = ("Banks", "Insurance")
NOT_DDM_INDUSTRIES = frozenset({"Insurance Brokers"})

# Interest expense as a share of revenue above which a financial company is
# treated as borrowing to earn its revenue. It sits between ICE (7.7%), the
# highest fee business probed, and SYF (27.6%), the lowest lender.
MAX_FEE_BUSINESS_INTEREST_BURDEN = 0.10
INTEREST_BURDEN_WINDOW = 3


@dataclass
class FinancialClassification:
    kind: str                       # see classify_financial()
    industry: str
    interest_burden: float | None


def interest_burden(fin: CompanyFinancials) -> float | None:
    """Median interest expense / revenue over recent years, or None if unknown."""
    ratios = [num(row, "interestExpense") / num(row, "revenue")
              for row in fin.income[:INTEREST_BURDEN_WINDOW]
              if row.get("interestExpense") is not None and num(row, "revenue") > 0]
    return median(ratios) if ratios else None


def classify_financial(fin: CompanyFinancials) -> FinancialClassification:
    """
    Sort a company into the valuation treatment its business supports.

      "not_financial"    known to be outside the Financial Services sector:
                         the DCF path, untouched by any of this
      "bank_or_insurer"  the dividend discount model
      "fee_based"        a financial company that does not borrow to earn its
                         revenue: the DCF path, with its ordinary guards
      "balance_sheet"    borrows to earn its revenue but is not a bank or an
                         insurer: the DCF is refused, and no DDM substituted
      "unclassified"     in the sector, but the data cannot say which of the
                         above it is: refused rather than guessed
    """
    # Search writes "Banks—Diversified", the quote endpoint "Banks - Diversified".
    industry = (fin.industry or "Unknown").replace("—", " - ").strip()
    sector = (fin.sector or "").strip()

    # An absent sector is not evidence of being non-financial. Reading it that
    # way is how an insurer reaches the DCF, so it is treated as what it is:
    # unknown. fetch_financials refuses these upstream, so this is the second
    # line rather than the first - but the default must be safe wherever a
    # CompanyFinancials comes from.
    if not sector or sector == UNKNOWN_SECTOR:
        return FinancialClassification("unclassified", industry, None)
    if sector != FINANCIAL_SECTOR:
        return FinancialClassification("not_financial", industry, None)
    if industry not in NOT_DDM_INDUSTRIES and industry.startswith(DDM_INDUSTRY_PREFIXES):
        return FinancialClassification("bank_or_insurer", industry, None)
    if industry == "Unknown":
        # Interest burden alone cannot tell a fee business from an insurer,
        # so without an industry the two are indistinguishable.
        return FinancialClassification("unclassified", industry, None)

    burden = interest_burden(fin)
    if burden is None:
        return FinancialClassification("unclassified", industry, None)
    kind = "balance_sheet" if burden > MAX_FEE_BUSINESS_INTEREST_BURDEN else "fee_based"
    return FinancialClassification(kind, industry, burden)

MIN_HISTORY_YEARS = 2
HIGH_TV_SHARE = 0.90  # terminal value share of EV above which we warn


@dataclass
class Suitability:
    suitable: bool
    reasons: list[str] = field(default_factory=list)    # blocking
    warnings: list[str] = field(default_factory=list)   # non-blocking
    # Machine-readable counterparts to `reasons`, so a caller can ask WHY a
    # company was refused without parsing prose. Internal: never serialised,
    # so no response changes because they exist.
    codes: list[str] = field(default_factory=list)


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

    @property
    def method(self) -> str:
        return "dcf"


def financial_sector_reason(fin: CompanyFinancials) -> str:
    """Why a DCF does not apply to a financial company - shared by both paths."""
    return (
        f"{fin.company_name} is in the {fin.sector} sector. For banks and "
        "insurers, borrowing is an input to operations rather than financing, "
        "so unlevered free cash flow and an enterprise-to-equity bridge are "
        "not meaningful. A dividend discount or excess-return model is the "
        "appropriate tool."
    )


def financial_refusal_reason(fin: CompanyFinancials,
                             classification: FinancialClassification) -> str:
    """Why a DCF does not apply to a financial company that is not a bank or insurer."""
    if classification.kind == "balance_sheet":
        latest = fin.cashflow[0] if fin.cashflow else {}
        buybacks, paid = latest.get("stockRepurchased"), latest.get("dividendsPaid")
        if buybacks and paid and abs(buybacks) > abs(paid):
            understated = (
                f"it counts only dividends, and {fin.company_name} returned "
                f"${abs(buybacks) / 1e9:,.1f}bn through buybacks against "
                f"${abs(paid) / 1e9:,.1f}bn in dividends in its "
                f"{latest.get('fiscalYear', '?')} financial year, so it would understate what "
                "shareholders receive")
        else:
            understated = (
                "it counts only dividends, so it would understate an institution "
                "that returns capital through buybacks, as lenders commonly do")
        return (
            f"{fin.company_name} is a {classification.industry} company whose "
            f"interest expense is {classification.interest_burden:.0%} of its revenue. "
            "Borrowing is part of how it earns that revenue rather than how it is "
            "financed, so - as for a bank - unlevered free cash flow and an "
            "enterprise-to-equity bridge are not meaningful, and a DCF would produce "
            "a figure with no economic meaning. A dividend discount model is not "
            f"substituted either: {understated}. The appropriate tool is an "
            "excess-return model, which values a lender from its return on equity "
            "against its cost of equity. That model is not available yet, so no "
            "value is reported."
        )
    # No sector at all is a different statement from a known sector with a
    # missing detail, and must not be phrased as "is in the Unknown sector".
    if not (fin.sector or "").strip() or fin.sector == UNKNOWN_SECTOR:
        return (
            f"The data source did not report which sector {fin.company_name} "
            "is in just now, so it cannot be told apart from a bank, insurer "
            "or lender, for which a DCF is not meaningful. No value is "
            "reported rather than one that assumes it is an ordinary company. "
            "This is usually a temporary upstream limit - try again shortly."
        )

    missing = ("its industry is not available from the data source"
               if classification.industry == "Unknown"
               else "the data source reports no interest expense to show that "
                    "borrowing is not part of how it earns its revenue")
    return (
        f"{fin.company_name} is in the {fin.sector} sector, but {missing}. Without "
        "that it cannot be told apart from a bank, insurer or lender, for which a "
        "DCF is not meaningful, so no DCF value is reported."
    )


def _money(amount: float) -> str:
    """An amount as a person reads it: $3.6bn, $850m, -$1.2bn."""
    sign = "-" if amount < 0 else ""
    value = abs(amount)
    if value >= 1e9:
        return f"{sign}${value / 1e9:,.1f}bn"
    if value >= 1e6:
        return f"{sign}${value / 1e6:,.0f}m"
    return f"{sign}${value:,.0f}"


_LISTING_KINDS = {
    "ETF": "an exchange-traded fund",
    "MUTUALFUND": "a mutual fund",
    "INDEX": "a market index",
    "CURRENCY": "a currency",
    "CRYPTOCURRENCY": "a cryptocurrency",
    "FUTURE": "a futures contract",
    "OPTION": "an option",
}


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def uses_dividend_discount_model(fin: CompanyFinancials) -> bool:
    """
    Whether *fin* is valued with the DDM rather than the DCF: genuine banks
    and insurers, identified by industry, and nobody else.

    Other financial companies are not rerouted. A payments network is a fee
    business the DCF values normally, and a lender that is not a bank is
    refused by the DCF guard rather than handed a dividend discount model it
    was not built for. Nor is a loss-making industrial rerouted: the DDM is
    not a way around that guard.
    """
    return classify_financial(fin).kind == "bank_or_insurer"


def assess_suitability(fin: CompanyFinancials, derived: DerivedAssumptions,
                       base: BaseYearData) -> Suitability:
    """Decide whether a standard growth-perpetuity DCF applies to this company."""
    reasons: list[str] = []
    warnings: list[str] = []
    codes: list[str] = []

    a = derived.assumptions
    latest = fin.income[0]
    fiscal_year = latest.get("fiscalYear", "?")

    # --- Blocking: not enough history to derive anything defensible --------
    if fin.years_available < MIN_HISTORY_YEARS:
        codes.append("short_history")
        reasons.append(
            f"Only {_count(fin.years_available, 'year')} of financial history "
            f"{'is' if fin.years_available == 1 else 'are'} available. At least "
            f"{MIN_HISTORY_YEARS} are needed to see a trend."
        )

    # --- Blocking: no revenue ---------------------------------------------
    revenue = num(latest, "revenue")
    if revenue <= 0:
        codes.append("no_revenue")
        reasons.append(
            f"The company reported no revenue in its {fiscal_year} financial year. The valuation projects "
            "future revenue, so without any to start from there is nothing to project."
        )

    # --- Blocking: operating losses ---------------------------------------
    operating_income = num(latest, "operatingIncome")
    if operating_income <= 0:
        codes.append("operating_loss")
        loss_years = [
            r.get("fiscalYear") for r in fin.income if num(r, "operatingIncome") <= 0
        ]
        reasons.append(
            f"The company is loss-making: its day-to-day business lost "
            f"{_money(-operating_income)} in its {fiscal_year} financial year, and it made a loss in "
            f"{len(loss_years)} of the last {fin.years_available} years.\n"
            "    This kind of valuation assumes a business that already makes a profit "
            "and grows steadily. Applying it to a loss-making company would mean "
            "inventing the profits it then puts a value on."
        )
    elif a.operating_margin <= 0:
        codes.append("negative_median_margin")
        reasons.append(
            f"The company has typically made a loss on its day-to-day business in recent "
            f"years (a typical profit margin of {a.operating_margin:.1%}). This kind of "
            "valuation assumes a business that already makes a profit."
        )

    # --- Blocking: financial companies unlevered FCF means nothing for -------
    # Fee-based financials (payments networks, exchanges, asset managers) pass
    # through to the ordinary guards below; banks, insurers, lenders, and
    # financials the data cannot classify do not.
    classification = classify_financial(fin)
    if classification.kind == "bank_or_insurer":
        codes.append("financial")
        reasons.append(financial_sector_reason(fin))
    elif classification.kind in ("balance_sheet", "unclassified"):
        codes.append("financial")
        reasons.append(financial_refusal_reason(fin, classification))

    # --- Blocking: perpetuity formula breaks down -------------------------
    if a.terminal_growth >= a.wacc:
        codes.append("growth_exceeds_wacc")
        reasons.append(
            f"Terminal growth ({a.terminal_growth:.1%}) is not below WACC "
            f"({a.wacc:.1%}). A company cannot grow faster than its discount rate "
            "forever - the maths gives an infinite or negative value - so terminal "
            "growth must be set below WACC."
        )

    # --- Blocking: the projection never produces cash ----------------------
    # Checked directly rather than inferred, because a company can be
    # profitable on an operating basis yet still consume cash through capex.
    if not reasons:
        probe = run_dcf(base, a)
        if probe.years[-1].ufcf <= 0:
            codes.append("negative_fcf")
            reasons.append(
                f"Even by the last year of the forecast, the company would still be "
                f"spending more cash than it brings in ({_money(probe.years[-1].ufcf * 1e6)} "
                "that year). Its investment needs outweigh its profits, so there is no "
                "positive cash flow to put a long-term value on."
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
            warnings.append(growth_below_terminal_warning(
                fin.company_name, a.revenue_growth, a.terminal_growth, a.projection_years))

    for prov in derived.provenance.values():
        if prov.source == "derived (clamped)":
            warnings.append(clamp_warning(prov, fin.company_name))

    return Suitability(suitable=not reasons, reasons=reasons, warnings=warnings,
                       codes=codes)


def value_company(ticker: str,
                  overrides: dict | None = None) -> "ValuationReport | DDMReport":
    """
    Fetch *ticker*, derive its assumptions, check suitability, and value it.

    Returns a ValuationReport whose `result` is None when the company fails
    the suitability guard. `overrides` is passed through to
    derive_assumptions() so any assumption can be replaced by the caller.

    Banks and insurers take a different path. Unlevered free cash flow means
    nothing for them, so they are valued with a dividend discount model and
    returned as a DDMReport; that model has its own guard and refuses in turn
    when it does not apply. Other financial-sector companies take the DCF
    path: a payments network or an exchange is valued like any fee business,
    while one that borrows to earn its revenue is refused there, with no
    dividend discount model forced in its place. Every non-financial company
    takes the DCF path exactly as before - loss-making ones included, which
    are still refused.
    """
    fin = fetch_financials(ticker)

    if uses_dividend_discount_model(fin):
        return _value_with_ddm(fin, overrides)

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
# Dividend discount model path
# ---------------------------------------------------------------------------

# Fewer regular payments than this cannot establish a frequency or a run
# rate: there is no dividend to project, only an anecdote.
MIN_REGULAR_DIVIDEND_PAYMENTS = 4
# A payout above earnings is funded from capital. Projecting it to grow in
# perpetuity values a payment the business is not generating.
MAX_PAYOUT_RATIO = 1.00
HIGH_PAYOUT_RATIO = 0.80

_SCHEDULES = {12: "monthly", 4: "quarterly", 2: "semi-annual", 1: "annual"}


@dataclass
class DDMReport:
    ticker: str
    company_name: str
    sector: str
    industry: str
    exchange: str
    fiscal_year: str
    suitability: Suitability
    dividends: DividendRecord
    derived: DerivedDDMAssumptions
    inputs: DDMInputs
    why_not_dcf: str
    result: DDMResult | None  # None whenever suitability.suitable is False

    @property
    def suitable(self) -> bool:
        return self.suitability.suitable

    @property
    def method(self) -> str:
        return "ddm"


def _value_with_ddm(fin: CompanyFinancials, overrides: dict | None) -> DDMReport:
    dividends = analyse_dividends(fetch_dividends(fin.ticker))
    derived = derive_ddm_assumptions(fin, dividends, overrides=overrides)

    price = num(fin.profile, "price")
    if price <= 0:
        raise DataUnavailableError(
            f"No current share price is available for '{fin.ticker}', so a "
            "per-share value cannot be compared with the market.")

    inputs = DDMInputs(current_dividend=dividends.current_dividend or 0.0,
                       current_price=price)
    suitability = assess_ddm_suitability(fin, dividends, derived, inputs)
    result = run_ddm(inputs, derived.assumptions) if suitability.suitable else None

    return DDMReport(
        ticker=fin.ticker,
        company_name=fin.company_name,
        sector=fin.sector,
        industry=fin.industry,
        exchange=fin.exchange,
        fiscal_year=str(derived.diagnostics.get("latest_fiscal_year") or "?"),
        suitability=suitability,
        dividends=dividends,
        derived=derived,
        inputs=inputs,
        why_not_dcf=financial_sector_reason(fin),
        result=result,
    )


def assess_ddm_suitability(fin: CompanyFinancials, dividends: DividendRecord,
                           derived: DerivedDDMAssumptions,
                           inputs: DDMInputs) -> Suitability:
    """Decide whether a dividend discount model applies to this company."""
    reasons: list[str] = []
    warnings: list[str] = []
    # Unstripped: the name appears mid-sentence here ("Berkshire Hathaway Inc.
    # has paid..."), unlike the honesty note, where it ends one.
    name = fin.company_name
    a = derived.assumptions
    d = derived.diagnostics
    fiscal_year = d.get("latest_fiscal_year") or "?"

    # --- Blocking: nothing to discount ---------------------------------------
    if not dividends.payments:
        reasons.append(
            f"{name} has paid no dividends in the last ten years. A dividend "
            "discount model values the stream of dividends, so without one there "
            "is nothing to discount - and assuming a dividend will start would "
            "invent the very cash flow the model then values."
        )
    elif (len(dividends.regular) < MIN_REGULAR_DIVIDEND_PAYMENTS
          or dividends.current_dividend is None):
        reasons.append(
            f"{name} has only {len(dividends.regular)} regular dividend payment(s) "
            "on record, too few to establish a dividend rate that can be "
            "projected forward."
        )
    elif dividends.suspended:
        schedule = _SCHEDULES.get(dividends.payments_per_year, "usual")
        reasons.append(
            f"{name}'s last regular dividend was paid on "
            f"{dividends.last_regular_date.isoformat()}, "
            f"{dividends.days_since_last} days ago - well past its {schedule} "
            "schedule. The dividend appears to have been suspended, and a "
            "dividend discount model cannot assume it resumes."
        )

    # --- Blocking: the dividend is not being earned --------------------------
    net_income = d.get("latest_net_income_common")
    payout = d.get("latest_payout_ratio")
    if net_income is not None and net_income <= 0:
        reasons.append(
            f"{name} reported a loss to shareholders of {_money(-net_income)} in its {fiscal_year} "
            f"financial year. A dividend paid while losing money is "
            "funded from capital rather than earnings, so it cannot be projected "
            "to continue, let alone to grow."
        )
    elif payout is not None and payout > MAX_PAYOUT_RATIO:
        reasons.append(
            f"{name} paid out {payout:.0%} of its {fiscal_year} earnings as "
            "dividends. A dividend larger than earnings is funded from capital, "
            "and projecting it to grow in perpetuity would value a payment the "
            "business is not generating."
        )
    elif payout is not None and payout >= HIGH_PAYOUT_RATIO and dividends.payments:
        warnings.append(
            f"{name} paid out {payout:.0%} of its {fiscal_year} earnings as "
            "dividends, leaving little retained to fund growth. Growth at the "
            "assumed rate depends on earnings rising, not on the payout."
        )

    # --- Blocking: the perpetuity formula breaks down ------------------------
    if a.terminal_growth >= a.cost_of_equity:
        reasons.append(
            f"Terminal growth ({a.terminal_growth:.1%}) is not below the cost of "
            f"equity ({a.cost_of_equity:.1%}). The Gordon growth formula requires "
            "r > g; otherwise the terminal value is infinite or negative."
        )

    # --- Non-blocking warnings -----------------------------------------------
    if not reasons:
        result = run_ddm(inputs, a)
        if result.tv_pct_of_value > HIGH_TV_SHARE:
            warnings.append(
                f"The terminal value is {result.tv_pct_of_value:.0%} of the "
                "valuation. Almost all of it rests on the perpetuity assumption, "
                "so the result is highly sensitive to the cost of equity and "
                "terminal growth."
            )

    buybacks, paid = d.get("latest_buybacks"), d.get("latest_dividends_paid")
    if buybacks and paid and buybacks > paid:
        warnings.append(
            f"{name} returned ${buybacks / 1e9:,.1f}bn through share buybacks "
            f"against ${paid / 1e9:,.1f}bn in dividends in its {fiscal_year} financial year. A "
            "dividend discount model counts only dividends, so it does not see "
            "most of the cash this company returns to shareholders and is likely "
            "to understate its value on that basis."
        )

    history = d.get("dividend_history_used") or []
    cut_years = [year for (_, before), (year, after) in zip(history, history[1:])
                 if after < before * (1 - DIVIDEND_CUT_TOLERANCE)]
    if cut_years:
        warnings.append(
            f"The dividend per share fell in {', '.join(str(y) for y in cut_years)}. "
            "The growth rate spans that cut, and a company that has cut its "
            "dividend once can do so again."
        )

    if dividends.variable_dividends:
        variable = [net for _, _, net in dividends.variable_dividends]
        listed = ", ".join(f"${net:,.2f} in {paid_on.strftime('%b %Y')}"
                           for paid_on, _, net in dividends.variable_dividends)
        # Two separate facts, stated separately: how long the annual pattern has
        # run (which decides that it recurs), and which payments fall inside the
        # averaging window (which can include older ones from before a gap).
        warnings.append(
            f"{name} pays a variable dividend on top of its regular one, and has "
            "done so at roughly annual intervals for the last "
            f"{dividends.variable_recurring_years} years running. It is counted as "
            f"part of the dividend at its {dividends.variable_average_years}-year "
            f"average of ${dividends.variable_annual_dividend:,.2f} a year - the "
            f"payments in that window were {listed} - with any year it was not paid "
            f"counting as zero. It has ranged from ${min(variable):,.2f} to "
            f"${max(variable):,.2f}, so the figure discounted here is an average, not "
            "a commitment the company has made, and growth is measured on the "
            "regular dividend alone."
        )

    if dividends.specials:
        listed = ", ".join(f"${amount:,.2f} on {paid_on.isoformat()}"
                           for paid_on, amount in dividends.specials[-3:])
        warnings.append(
            f"Excluded {len(dividends.specials)} special dividend(s) from the run "
            f"rate and growth history ({listed}). They do not recur on an annual "
            "pattern, so they are treated as one-off returns of capital rather than "
            "as a dividend the company can be expected to repeat."
        )

    for assumption, prov in derived.provenance.items():
        if prov.source == "derived (clamped)":
            warnings.append(clamp_warning(prov, name))
        elif assumption == "dividend_growth" and prov.source == "default":
            warnings.append(dividend_growth_default_warning(prov, name))

    return Suitability(suitable=not reasons, reasons=reasons, warnings=warnings)


def _ddm_honesty_note(report: DDMReport) -> str | None:
    """The DDM's version of the caveat that must accompany any value reported."""
    if report.result is None:
        return None

    a = report.derived.assumptions
    erp = report.derived.capm_inputs.get("equity_risk_premium")
    erp_text = f" (with an equity risk premium of {erp.value:.2%})" if erp else ""
    name = report.company_name.rstrip(".")
    if a.high_growth_years > 0:
        growth = (f"dividends growing {a.dividend_growth:.2%} a year for "
                  f"{a.high_growth_years} years, then {a.terminal_growth:.2%} a year "
                  "in perpetuity")
    else:
        growth = f"dividends growing {a.terminal_growth:.2%} a year in perpetuity"

    return (
        f"${report.result.intrinsic_value_per_share:,.2f} is the output of these "
        f"assumptions, not a fact about {name}. It assumes a cost of equity of "
        f"{a.cost_of_equity:.2%}{erp_text} and {growth}; the equity risk premium "
        "in particular has no single correct value. It values only the dividends "
        "paid, so cash returned through share buybacks is not counted. Move any "
        "of them and the figure moves materially - the sensitivity table shows "
        "by how much."
    )


# ---------------------------------------------------------------------------
# Speculative estimates for loss-making companies - OPT-IN ONLY
#
# The honest default for a company with no profits to project is a refusal,
# and value_company() still gives exactly that: nothing in this section is
# reachable from it. value_company_speculatively() is a separate entry point
# that the API exposes only on its own endpoints, so a speculative figure can
# never appear because a caller forgot to ask for something else.
#
# It is offered ONLY to companies the standard guard refused for losing
# money. A company the DCF or DDM can value gets a refusal here, not a
# speculative figure: an estimate resting on assumed profits is never a
# substitute for a valuation resting on real ones. Nor is it a way around the
# other guards - a bank, a lender or a company with a currency mismatch is
# refused for reasons no projection of future profits addresses.
# ---------------------------------------------------------------------------

LOSS_REFUSAL_CODES = frozenset({"operating_loss", "negative_median_margin"})
# Refusal reasons that may accompany a loss without ruling the path out here.
# They are not waived: the speculative guard refuses them in its own terms.
SPECULATIVE_ELIGIBLE_CODES = LOSS_REFUSAL_CODES | {"no_revenue", "short_history"}

MIN_SPECULATIVE_REVENUE_MM = 10.0
MAX_YEARS_TO_BREAK_EVEN = 10
SPECULATIVE_HEADLINE = "SPECULATIVE ESTIMATE - NOT A VALUATION"


def speculative_estimate_available(report: "ValuationReport | DDMReport") -> bool:
    """
    Whether the opt-in speculative endpoint would take this refusal on.

    True only when losing money is the ONLY thing standing in the way - the
    cases /valuation/{ticker}/speculative accepts. A company refused for
    another reason as well (no revenue, too little history, a bank) is not
    advertised the opt-in: it would only be refused again a step later, which
    is a worse experience than not offering it.

    Never an endorsement of the estimate, only a statement that one can be
    asked for.
    """
    if getattr(report, "method", "dcf") != "dcf" or report.suitable:
        return False
    codes = set(report.suitability.codes)
    return bool(codes) and codes <= LOSS_REFUSAL_CODES


class SpeculativeNotApplicable(Exception):
    """The company is not one a speculative estimate is offered for."""

    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = list(reasons or [])


@dataclass
class SpeculativeReport:
    ticker: str
    company_name: str
    sector: str
    industry: str
    exchange: str
    fiscal_year: str
    suitability: Suitability
    derived: DerivedSpeculativeAssumptions
    why_standard_refused: list[str]
    result: SpeculativeResult | None  # None whenever suitability.suitable is False

    @property
    def suitable(self) -> bool:
        return self.suitability.suitable

    @property
    def method(self) -> str:
        return "speculative"


def value_company_speculatively(ticker: str,
                                overrides: dict | None = None) -> SpeculativeReport:
    """
    Produce a speculative path-to-profitability estimate - on request only.

    Raises SpeculativeNotApplicable for any company that is not a loss-making
    refusal. Returns a SpeculativeReport whose `result` is None when even a
    path to profitability does not apply to the company.
    """
    fin = fetch_financials(ticker)
    name = fin.company_name

    if uses_dividend_discount_model(fin):
        raise SpeculativeNotApplicable(
            f"{name} is a bank or insurer and is valued with the dividend discount model. "
            "A speculative estimate is offered only for companies a standard valuation "
            "refuses because they lose money.")

    base, _ = base_year_from(fin)
    # Eligibility is judged on the untouched default assessment, so no
    # override can manufacture it.
    standard = assess_suitability(fin, derive_assumptions(fin), base)

    if standard.suitable:
        raise SpeculativeNotApplicable(
            f"{name} can be valued with a standard DCF, so no speculative estimate is "
            "offered. A figure resting on assumed future profits is never a substitute "
            "for one resting on real ones.")

    codes = set(standard.codes)
    if not codes & LOSS_REFUSAL_CODES or codes - SPECULATIVE_ELIGIBLE_CODES:
        raise SpeculativeNotApplicable(
            f"{name} was refused a standard valuation for reasons other than losing money, "
            "so the speculative path does not apply: it exists only to project a way out "
            "of losses, not a way around the other guards.",
            reasons=standard.reasons)

    derived = derive_speculative_assumptions(fin, base, overrides=overrides)
    suitability = assess_speculative_suitability(fin, derived)
    result = run_speculative(derived.inputs, derived.assumptions) if suitability.suitable else None

    return SpeculativeReport(
        ticker=fin.ticker,
        company_name=name,
        sector=fin.sector,
        industry=fin.industry,
        exchange=fin.exchange,
        fiscal_year=derived.diagnostics["fiscal_year"],
        suitability=suitability,
        derived=derived,
        why_standard_refused=standard.reasons,
        result=result,
    )


def assess_speculative_suitability(fin: CompanyFinancials,
                                   derived: DerivedSpeculativeAssumptions) -> Suitability:
    """Decide whether even a speculative path to profitability applies."""
    reasons: list[str] = []
    warnings: list[str] = []
    name = fin.company_name
    a, inputs, d = derived.assumptions, derived.inputs, derived.diagnostics
    fiscal_year = d["fiscal_year"]
    pace = f"{MARGIN_IMPROVEMENT_PACE * 100:.0f}"

    # --- Blocking: nothing real to project from ---------------------------------
    if fin.years_available < MIN_HISTORY_YEARS:
        reasons.append(
            f"Only {fin.years_available} year(s) of financial history are available, too few "
            f"to show which way {name} is heading. A path to profitability extends a "
            "trajectory, and there is none to extend.")

    if inputs.revenue <= 0:
        reasons.append(
            f"{name} reported no revenue in its {fiscal_year} financial year. A path to profitability grows "
            "revenue until it covers costs; with no revenue, both the revenue and the margins "
            "would have to be invented, which makes any figure fiction rather than speculation.")
    elif inputs.revenue < MIN_SPECULATIVE_REVENUE_MM:
        reasons.append(
            f"{name}'s revenue of {_money(inputs.revenue * 1e6)} in its {fiscal_year} financial year is too small to "
            "anchor a projection: nearly all of any figure would come from assumed growth "
            "rather than from the business as it is.")
    else:
        latest, cagr = d["latest_revenue_growth"], d["revenue_cagr"]
        if latest is not None and cagr is not None and latest <= 0 and cagr <= 0:
            reasons.append(
                f"{name}'s revenue has fallen - {latest:.1%} in the latest year and "
                f"{cagr:.1%} a year over the period. This approach projects growth into "
                "profitability; a shrinking company offers no growth to extend, and assuming "
                "a reversal would invent it.")

        gross = d["gross_margin"]
        if gross is not None and gross <= 0:
            reasons.append(
                f"{name}'s gross margin was {gross:.0%} in its {fiscal_year} financial year: it loses money on "
                "what it sells before any operating costs. No path to operating profit can be "
                "projected without first assuming the basic economics of the product are fixed.")

        if d["years_to_break_even"] > MAX_YEARS_TO_BREAK_EVEN:
            reasons.append(
                f"{name}'s operating margin was {d['operating_margin']:.0%} in its {fiscal_year} financial year. "
                f"Even improving by {pace} percentage points every year - a fast pace to "
                f"sustain - it would take about {d['years_to_break_even']} years to break even, "
                "beyond any horizon a projection can support.")

    # --- Blocking: assumptions under which the model has no meaning -------------
    if a.target_operating_margin <= 0:
        reasons.append(
            f"The target operating margin of {a.target_operating_margin:.1%} is not a profit. "
            "A path that ends in losses is not a path to profitability.")
    if a.terminal_growth >= a.wacc:
        reasons.append(
            f"Terminal growth ({a.terminal_growth:.1%}) is not below WACC ({a.wacc:.1%}). The "
            "Gordon growth formula requires WACC > g; otherwise terminal value is infinite or "
            "negative.")

    # --- Warnings: what getting there would take ---------------------------------
    if not reasons:
        r = run_speculative(inputs, a)

        if r.cumulative_cash_burn > inputs.cash:
            warnings.append(
                f"On this path {name} burns ${r.cumulative_cash_burn / 1e3:,.1f}bn of cash "
                f"before turning profitable, against ${inputs.cash / 1e3:,.1f}bn of cash and "
                "investments today. It would have to raise the difference, most likely by "
                "issuing shares that dilute existing holders - if it can raise it at all. The "
                "estimate subtracts the cost of those losses but assumes the money is found.")
        elif r.cumulative_cash_burn > 0:
            warnings.append(
                f"On this path {name} burns ${r.cumulative_cash_burn / 1e3:,.1f}bn of cash "
                f"before turning profitable, covered by today's ${inputs.cash / 1e3:,.1f}bn of "
                "cash and investments only if nothing goes worse than assumed.")

        # Only meaningful when the estimate is positive. When the losses on the
        # way outweigh everything, there is no "share" to describe, and the
        # negative-estimate warning below says what actually happened.
        share = r.share_from_after_profitability
        if share is not None and share >= 1:
            warnings.append(
                "All of this estimate comes from after the assumed turn to profit - the years "
                "before it subtract value rather than add it - so the figure is entirely a "
                "statement about a future that has not happened.")
        elif share is not None and share > HIGH_TV_SHARE:
            warnings.append(
                f"{share:.0%} of this estimate comes from after the assumed turn to profit, so "
                "it rests almost entirely on a future that has not happened.")

        gross = d["gross_margin"]
        if gross is not None and gross < a.target_operating_margin:
            warnings.append(
                f"{name}'s gross margin was {gross:.0%} in its {fiscal_year} financial year, below the "
                f"{a.target_operating_margin:.0%} operating margin assumed. Reaching it needs "
                "gross margin to rise well above today's level before operating costs are "
                "even covered.")

        if r.value_per_share <= 0:
            warnings.append(
                "Even on these assumptions, the losses on the way and the debt outweigh the "
                "value of the profitable business at the end, so the estimate is negative.")

    for prov in derived.provenance.values():
        if prov.source == "derived (clamped)":
            warnings.append(clamp_warning(prov, name))

    return Suitability(suitable=not reasons, reasons=reasons, warnings=warnings)


def speculative_disclaimer(report: SpeculativeReport) -> str | None:
    """
    The disclaimer every speculative figure must carry - stronger than the
    "output of assumptions" note, because this one rests on a future that has
    not happened. Names the actual assumptions, so it cannot be read as boilerplate.
    """
    if report.result is None:
        return None

    a = report.derived.assumptions
    margin = report.derived.diagnostics["operating_margin"]
    # Unstripped: the name opens a sentence here ("Rivian Automotive, Inc.
    # loses money"), so its full stop belongs to the name.
    name = report.company_name
    starting = f"from {margin:.0%} " if margin is not None else ""
    burn = report.result.cumulative_cash_burn
    funding = f"about ${burn / 1e3:,.1f}bn of losses" if burn > 0 else "its losses"
    years = f"{a.years_to_profitability} year{'s' if a.years_to_profitability != 1 else ''}"

    return (
        f"{name} loses money, so a standard valuation was refused: it has no profits to "
        "project. This figure is not a valuation and must not be treated as one. It was "
        "produced only because it was explicitly requested, and it rests entirely on "
        "assumptions about a future that has not happened: that revenue grows "
        f"{a.speculative_revenue_growth:.1%} a year, that the operating margin climbs "
        f"{starting}to {a.target_operating_margin:.0%} within {years}, that the company can "
        f"fund {funding} along the way, and that it then performs like an established, "
        "profitable business indefinitely. Loss-making companies often never reach sustained "
        "profitability; many raise money on terms that dilute existing shareholders, and some "
        "fail outright, in which case shareholders can lose everything. Modest changes to "
        "these assumptions move this figure by multiples, not percentages - the sensitivity "
        "table shows how far. It is not investment advice, a price target, or a reason to "
        "buy or sell."
    )


# ---------------------------------------------------------------------------
# Relative valuation - a market-based second opinion, beside an intrinsic value
#
# Never a replacement. It is produced only alongside a DCF or DDM valuation that
# succeeded, and only on its own endpoints. A company the intrinsic methods
# refuse gets no relative figure either: otherwise peer multiples would become a
# back door to a number for exactly the companies the guards exist to stop.
#
# PEERS - WHAT PROBING SHOWED
# ---------------------------
# Industry is what separates a genuine peer from noise. Yahoo's "people also
# watch" list is a behavioural signal: filtered to the same industry it gave
# clean groups (CRM -> ADBE, NOW, SNOW; JPM -> C, BAC, WFC), but unfiltered it
# pairs JPMorgan with Coca-Cola and Apple with Tesla. Industry constituent lists
# would be the natural source but need a crumb, and a second hop through the
# co-watched companies' own lists added peers for one of twelve companies probed
# while costing up to twenty requests. So automatic selection is "co-watched AND
# same industry", and it often comes up short - Apple, Microsoft, Coca-Cola and
# Tesla get no peer at all - which is why a user can supply the group, and why a
# thin group declines rather than pretending.
# ---------------------------------------------------------------------------

RELATIVE_FRAMING = "How the market prices similar companies right now"


def _today() -> date:
    return date.today()


class RelativeNotApplicable(Exception):
    """There is no intrinsic valuation for a relative view to sit beside."""

    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = list(reasons or [])


@dataclass
class PeerChoice:
    ticker: str
    name: str | None
    industry: str | None
    provenance: str     # "selected" | "chosen by you" | "added by you"
    detail: str


@dataclass
class ExcludedPeer:
    ticker: str
    name: str | None
    reason: str


@dataclass
class RelativeReport:
    ticker: str
    company_name: str
    sector: str
    exchange: str
    intrinsic: "ValuationReport | DDMReport"
    company_figures: MarketFigures
    selection_mode: str     # "automatic" | "your list" | "automatic, edited by you"
    selection_rule: str
    peers: list[PeerChoice]
    excluded: list[ExcludedPeer]
    suitability: Suitability
    # The comparison as computed, whether or not it can support a figure. Read
    # `result` to get one only when this report stands behind it.
    measured: RelativeResult | None

    @property
    def suitable(self) -> bool:
        return self.suitability.suitable

    @property
    def result(self) -> RelativeResult | None:
        """The comparison this report stands behind: None unless it is suitable."""
        return self.measured if self.suitable else None

    @property
    def method(self) -> str:
        return "relative"


def _normalise_tickers(tickers) -> list[str]:
    out: list[str] = []
    for raw in tickers or []:
        ticker = (raw or "").strip().upper()
        if (not ticker or len(ticker) > 12
                or not ticker.replace(".", "").replace("-", "").isalnum()):
            raise ValueError(f"'{raw}' is not a valid ticker symbol for a peer.")
        if ticker not in out:
            out.append(ticker)
    return out


def _same_industry(a: str | None, b: str | None) -> bool:
    def clean(s):
        return (s or "").replace("—", " - ").strip().lower()
    return bool(clean(a)) and clean(a) == clean(b)


def value_company_relatively(ticker: str, peers: list[str] | None = None,
                             add_peers: list[str] | None = None,
                             remove_peers: list[str] | None = None) -> RelativeReport:
    """
    A relative, market-based view of *ticker* beside its intrinsic valuation.

    `peers` replaces automatic selection entirely; `add_peers` and `remove_peers`
    edit it. Raises RelativeNotApplicable if the company has no intrinsic
    valuation, and ValueError for a malformed or oversized peer list.
    """
    intrinsic = value_company(ticker)
    name = intrinsic.company_name
    if not intrinsic.suitable:
        raise RelativeNotApplicable(
            f"{name} has no intrinsic valuation for a relative view to sit beside - the "
            "standard valuation refused it. A relative figure is a second opinion, never a "
            "substitute for one, so none is produced.",
            reasons=intrinsic.suitability.reasons)

    explicit = _normalise_tickers(peers) if peers is not None else None
    added = _normalise_tickers(add_peers)
    removed = set(_normalise_tickers(remove_peers))
    if len((explicit or []) + added) > MAX_PEERS:
        raise ValueError(f"At most {MAX_PEERS} peers can be supplied.")

    fin = fetch_financials(ticker)
    industry = fin.industry if fin.industry and fin.industry != "Unknown" else None
    multiples = FINANCIAL_MULTIPLES if intrinsic.method == "ddm" else STANDARD_MULTIPLES
    today = _today()
    company = fetch_market_figures(intrinsic.ticker)

    excluded: list[ExcludedPeer] = []
    candidates: list[tuple[str, str, str, str | None]] = []   # ticker, provenance, detail, industry

    if explicit is not None:
        mode = "your list"
        rule = ("The peer group you supplied, excluding only companies whose figures cannot be "
                "set against this company's.")
        candidates = [(t, "chosen by you", "in the peer list you supplied", None) for t in explicit]
    else:
        mode = "automatic"
        rule = (f"Companies that people on Yahoo Finance often look at alongside {intrinsic.ticker} "
                f"and that are in the same industry ({industry or 'unknown'}), up to "
                f"{MAX_AUTO_PEERS}. Left out: other share classes, companies whose results and "
                "share price are in different currencies, out-of-date figures, and companies more "
                "than ten times larger or smaller.")
        for candidate in fetch_also_watched(intrinsic.ticker):
            if candidate == intrinsic.ticker:
                continue
            classification = fetch_classification(candidate)
            candidate_name = classification.get("name")
            quote_type = classification.get("quote_type")
            candidate_industry = classification.get("industry")
            if quote_type and quote_type != "EQUITY":
                excluded.append(ExcludedPeer(candidate, candidate_name,
                                             f"it is {_LISTING_KINDS.get(quote_type, 'a ' + quote_type.lower() + ' listing')}"
                                             ", not an operating company"))
            elif industry is None:
                excluded.append(ExcludedPeer(
                    candidate, candidate_name,
                    f"{name}'s own industry is unavailable, so no company can be confirmed as comparable"))
            elif not candidate_industry:
                excluded.append(ExcludedPeer(
                    candidate, candidate_name,
                    "its industry could not be confirmed from the data source just now, so it "
                    "cannot be counted as comparable"))
            elif not _same_industry(candidate_industry, industry):
                excluded.append(ExcludedPeer(
                    candidate, candidate_name,
                    f"often looked at alongside {intrinsic.ticker}, but it is in {candidate_industry}, "
                    f"not {industry}"))
            elif sum(1 for c in candidates if c[1] == "selected") >= MAX_AUTO_PEERS:
                excluded.append(ExcludedPeer(
                    candidate, candidate_name,
                    f"automatic selection stops at {MAX_AUTO_PEERS} companies"))
            else:
                candidates.append((
                    candidate, "selected",
                    f"in the same industry ({industry}), and among the companies Yahoo Finance "
                    f"users also watch alongside {intrinsic.ticker}",
                    candidate_industry))

        for extra in added:
            if extra not in [c[0] for c in candidates]:
                candidates.append((extra, "added by you", "added to the automatic selection", None))
                mode = "automatic, edited by you"

    if removed:
        kept = []
        for candidate in candidates:
            if candidate[0] in removed:
                excluded.append(ExcludedPeer(candidate[0], None, "removed by you"))
                if explicit is None:
                    mode = "automatic, edited by you"
            else:
                kept.append(candidate)
        candidates = kept

    peer_figures: list[MarketFigures] = []
    choices: list[PeerChoice] = []
    warnings: list[str] = []
    unavailable: list[MarketDataError] = []
    names_seen = {(company.name or "").lower()} - {""}

    for candidate, provenance, detail, candidate_industry in candidates:
        chosen_by_user = provenance != "selected"
        try:
            figures = fetch_market_figures(candidate)
        except TickerNotFoundError:
            excluded.append(ExcludedPeer(candidate, None, "not found on Yahoo Finance"))
            continue
        except (RateLimitedError, DataUnavailableError) as e:
            unavailable.append(e)
            excluded.append(ExcludedPeer(candidate, None, "its figures could not be fetched just now"))
            continue

        reason = peer_exclusion(company, figures, today, enforce_size=not chosen_by_user)
        if reason is None and figures.name and figures.name.lower() in names_seen:
            reason = "another share class of a company already in the group"
        if reason:
            excluded.append(ExcludedPeer(candidate, figures.name, reason))
            continue
        if figures.name:
            names_seen.add(figures.name.lower())

        if chosen_by_user:
            try:
                candidate_industry = fetch_classification(candidate).get("industry")
            except MarketDataError:
                candidate_industry = None
            if industry and candidate_industry and not _same_industry(candidate_industry, industry):
                warnings.append(
                    f"{candidate} ({figures.name}) is in {candidate_industry}, not {industry}. You "
                    "chose it, so it is included, but its multiples may reflect a different kind "
                    "of business.")
            if company.market_cap and figures.market_cap:
                ratio = figures.market_cap / company.market_cap
                if not SIZE_BAND[0] <= ratio <= SIZE_BAND[1]:
                    size = (f"more than {SIZE_BAND[1]:g} times the size of {intrinsic.ticker}"
                            if ratio > SIZE_BAND[1] else
                            f"less than 1/{round(1 / SIZE_BAND[0])} the size of {intrinsic.ticker}")
                    warnings.append(
                        f"{candidate} is {size}, so automatic selection would have left it out. "
                        "You chose it, so it is included, but companies of very different size "
                        "are often valued differently.")

        peer_figures.append(figures)
        choices.append(PeerChoice(candidate, figures.name, candidate_industry, provenance, detail))

    # A throttled source must never be reported as a lack of peers.
    if len(peer_figures) < MIN_PEERS and unavailable:
        raise unavailable[0]

    reasons: list[str] = []
    measured = None
    if len(peer_figures) < MIN_PEERS:
        count = len(peer_figures)
        found = ", ".join(f"{c.ticker} ({c.name})" if c.name else c.ticker for c in choices)
        if explicit is None and not added and not removed:
            # Automatic selection went looking and came back short.
            headline = ("No comparable company could be found" if count == 0 else
                        f"Only {count} comparable compan{'y' if count == 1 else 'ies'} could be "
                        f"found: {found}")
        else:
            # The user shaped this group, so nothing was "found": say how many
            # of the companies in it could actually be compared.
            headline = ("No companies are left to compare" if count == 0 else
                        f"Only {count} of these companies could be compared: {found}")
        # Say how the group was made, because the remedy differs: an automatic
        # selection that came up short, one the user has since edited, or a
        # list the user supplied outright.
        if explicit is not None:
            how = ("These are the companies you chose. Too few of them have figures that can "
                   "be compared with this company's - try adding more you consider comparable.")
        elif added or removed:
            how = ("This is the automatic selection with your changes. Add more companies you "
                   f"consider comparable to reach at least {MIN_PEERS}.")
        else:
            how = ("Peers are chosen automatically from companies in the same industry that "
                   "people often look at alongside this one. The data source does not provide a "
                   "full list of every company in an industry, so this often comes up short for "
                   "companies without close, frequently compared rivals. You can add your own "
                   "peers.")
        reasons.append(
            f"{headline}. A comparison needs at least {MIN_PEERS} companies, because the "
            "prices of one or two say little about how the market values a group, so no "
            f"relative figure is produced. {how}")
    else:
        measured = run_relative(company, peer_figures, multiples)
        applied = [m for m in measured.multiples if m.applicable]
        not_applied = "; ".join(f"{m.label}: {m.reason}"
                                for m in measured.multiples if not m.applicable)
        if not applied:
            reasons.append(
                f"None of the multiples appropriate to {name} could be applied - {not_applied}.")
        elif len(applied) < MIN_APPLICABLE_MULTIPLES:
            # One multiple is not a relative valuation. Salesforce's peer group left
            # only price-to-sales, pulled up by an unprofitable Snowflake at 21x
            # sales: a single peer moves the answer and nothing checks it. The
            # comparison is still worth seeing - as information, with no value
            # drawn from it.
            reasons.append(
                f"Only one of the multiples appropriate to {name} could be applied "
                f"({applied[0].label}), and a relative valuation resting on a single multiple is "
                "too fragile to report: one outlying peer moves it, with nothing to cross-check "
                f"it against. At least {MIN_APPLICABLE_MULTIPLES} are needed. The "
                f"{applied[0].label} comparison is shown as information instead, without a value "
                f"drawn from it. The others could not be applied - {not_applied}.")

    if not reasons and measured is not None:
        for m in measured.multiples:
            if not m.applicable:
                warnings.append(f"{m.label} was not used: {m.reason}.")
        low, high = measured.low_value_per_share, measured.high_value_per_share
        if low and high and high > WIDE_RANGE_RATIO * low:
            warnings.append(
                f"The multiples disagree with each other: they imply values from ${low:,.2f} to "
                f"${high:,.2f} a share, so the central figure hides a wide range.")
        if len(peer_figures) < 5:
            warnings.append(
                f"With only {len(peer_figures)} peers, a single company's share price can move the "
                "result a lot.")

    return RelativeReport(
        ticker=intrinsic.ticker,
        company_name=name,
        sector=intrinsic.sector,
        exchange=intrinsic.exchange,
        intrinsic=intrinsic,
        company_figures=company,
        selection_mode=mode,
        selection_rule=rule,
        peers=choices,
        excluded=excluded,
        suitability=Suitability(suitable=not reasons, reasons=reasons, warnings=warnings),
        measured=measured,
    )


def relative_note(report: RelativeReport) -> str | None:
    """The caveat a relative figure must carry: market-based, peer-dependent, mispricing-prone."""
    if report.result is None:
        return None
    applied = [m.label for m in report.result.multiples if m.applicable]
    labels = applied[0] if len(applied) == 1 else ", ".join(applied[:-1]) + " and " + applied[-1]
    names = ", ".join(p.name or p.ticker for p in report.peers)
    return (
        f"{RELATIVE_FRAMING}: this is a relative, market-based figure, not an estimate of "
        f"intrinsic value. It applies the median {labels} multiple{'s' if len(applied) > 1 else ''} "
        f"of {len(report.peers)} peers ({names}) to {report.company_name}'s own trailing figures. "
        "It inherits the market's own mispricing - if the whole group is overpriced or "
        "underpriced, so is this figure - and agreeing with the intrinsic value would not make "
        "either one right. It also rests on a judgement that these companies are comparable: "
        "change the peer group and the figure changes. Read it beside the intrinsic value as a "
        "second opinion, never as a replacement for it."
    )


def relative_comparison(report: RelativeReport) -> dict:
    """How the relative figure sits against the intrinsic one, stated without taking sides."""
    intrinsic_value = report.intrinsic.result.intrinsic_value_per_share
    central = report.result.central_value_per_share
    method = "DCF" if report.intrinsic.method == "dcf" else "dividend discount model"
    if intrinsic_value <= 0:
        gap = None
        statement = (f"The {method} intrinsic value is not positive, so the relative figure of "
                     f"${central:,.2f} cannot be compared with it as a percentage.")
    else:
        gap = central / intrinsic_value - 1
        position = ("in line with" if abs(gap) < 0.005
                    else f"{abs(gap):.0%} {'above' if gap > 0 else 'below'}")
        statement = (
            f"The relative figure of ${central:,.2f} is {position} the {method} intrinsic value of "
            f"${intrinsic_value:,.2f}. The two answer different questions - what the cash flows "
            "are worth on stated assumptions, and what the market pays for similar companies "
            "today - so a gap between them is information, not an error in either.")
    return {
        "intrinsic_method": report.intrinsic.method,
        "intrinsic_value_per_share": intrinsic_value,
        "relative_value_per_share": central,
        "gap": gap,
        "statement": statement,
    }


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

PERCENT_FIELDS = {
    "revenue_growth", "operating_margin", "tax_rate",
    "da_pct", "capex_pct", "nwc_pct", "wacc", "terminal_growth",
    "risk_free_rate", "equity_risk_premium", "cost_of_debt",
    "dividend_growth", "cost_of_equity",
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
    if isinstance(report, DDMReport):
        return _ddm_honesty_note(report)

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


def format_report(report: "ValuationReport | DDMReport") -> str:
    """Render a ValuationReport (or a DDMReport) as readable text."""
    if isinstance(report, DDMReport):
        return format_ddm_report(report)

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


def format_ddm_report(report: DDMReport) -> str:
    """Render a DDMReport as readable text."""
    out: list[str] = []
    add = out.append
    d = report.dividends

    add("=" * 78)
    add(f"{report.company_name} ({report.ticker}) - {report.exchange}")
    add(f"{report.sector} / {report.industry} | method: DIVIDEND DISCOUNT MODEL")
    add("=" * 78)
    add("\nWHY NOT A DCF")
    add(f"  {report.why_not_dcf}")

    add("\nDIVIDEND RECORD")
    if d.current_dividend is not None:
        add(f"  Current annual dividend       ${d.current_dividend:>10,.2f}   "
            f"{report.derived.diagnostics.get('current_dividend_detail', '')}")
        if d.variable_dividends:
            add(f"    of which regular            ${d.regular_dividend:>10,.2f}")
            add(f"    variable ({d.variable_average_years}-yr average)    "
                f"${d.variable_annual_dividend:>10,.2f}   paid: "
                + ", ".join(f"${net:,.2f} {paid.strftime('%b %Y')}"
                            for paid, _, net in d.variable_dividends))
        add(f"  Trailing twelve months        ${d.trailing_twelve_months:>10,.2f}")
        add(f"  Yield at ${report.inputs.current_price:,.2f}              "
            f"{d.current_dividend / report.inputs.current_price:>10.2%}")
    else:
        add(f"  {len(d.payments)} dividend payment(s) on record")
    if d.annual_history:
        add("  Per share by year: " + ", ".join(f"{y} ${v:,.2f}" for y, v in d.annual_history[-6:]))
    diag = report.derived.diagnostics
    for label, key in (("ROE", "return_on_equity"), ("Payout ratio", "payout_ratio"),
                       ("Sustainable growth", "sustainable_growth")):
        if diag.get(key) is not None:
            add(f"  {label:<30}{diag[key]:>11.2%}")

    add("\nASSUMPTIONS")
    for name, prov in {**report.derived.provenance, **report.derived.capm_inputs}.items():
        add(f"  {name:<20}{_format_value(name, prov.value):>10}  {prov.source:<18}{prov.detail}")

    if not report.suitable:
        add("\n" + "!" * 78)
        add("NO VALUATION METHOD APPLIES - NO VALUATION PRODUCED")
        add("!" * 78)
        for i, reason in enumerate(report.suitability.reasons, 1):
            add(f"\n  {i}. {reason}")
        return "\n".join(out)

    r = report.result
    add("\nVALUATION")
    for y in r.years:
        add(f"  Year {y.period}  dividend ${y.dividend:>8,.2f}   PV ${y.pv_dividend:>8,.2f}")
    add(f"  PV of stage-one dividends     ${r.pv_dividends_sum:>10,.2f}")
    add(f"  PV of terminal value          ${r.pv_terminal_value:>10,.2f}")
    add(f"  Terminal value / total         {r.tv_pct_of_value:>10.1%}")
    add(f"\n  INTRINSIC VALUE PER SHARE     ${r.intrinsic_value_per_share:>10,.2f}")
    add(f"  Current market price          ${r.current_price:>10,.2f}")
    add(f"  Implied upside / (downside)    {r.upside_downside:>10.1%}")

    add("\nSENSITIVITY - value per share (cost of equity \\ terminal growth)")
    add("  r \\ g    " + "".join(f"{g:>10.1%}" for g in r.sensitivity_gs))
    for cost, row in zip(r.sensitivity_costs_of_equity, r.sensitivity):
        add(f"  {cost:>7.2%}  " + "".join("         -" if v is None else f"{v:>10,.2f}" for v in row))

    add(f"\n  NOTE: {honesty_note(report)}")
    if report.suitability.warnings:
        add("\nWARNINGS")
        for warning in report.suitability.warnings:
            add(f"  - {warning}")
    return "\n".join(out)
