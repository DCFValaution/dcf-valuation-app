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

# A capital-spending glide wider than this is a claim about a
# build-out ending, not a technicality of the model. Ten points of
# revenue is where it stops being a detail and starts being the
# answer: Meta's whole figure turns on 23 of them.
CAPEX_FADE_DISCLOSE = 0.10

from assumptions import (DAMODARAN_ERP_AS_OF, DAMODARAN_IMPLIED_ERP,
                         LONG_RUN_GROWTH_AS_OF, LONG_RUN_NOMINAL_GROWTH,
                         MARGIN_TREND_DISCLOSE,
                         DerivedAssumptions, derive_assumptions,
                         erp_age_days, erp_is_stale, risk_free_as_of_text,
                         risk_free_is_stale, sep_age_days, sep_is_stale)
from roic import CapitalReturns, compute as roic_diagnostic
from plain_language import (clamp_warning, dividend_growth_default_warning,
                            growth_below_terminal_warning, signed_pct,
                            _possessive)
from market_snapshot import MarketSnapshot, market_snapshot
from dcf import (BaseYearData, DCFResult, growth_economics,
                 growth_reduces_value, run_dcf)
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
from hypothetical import (CAPEX_PCT_BOUNDS, FRAMING_PATH_TO_PROFIT,
                          FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE,
                          HYPOTHETICAL_HEADLINE, NEUTRAL_REVENUE_GROWTH,
                          NEUTRAL_TARGET_MARGIN, NEUTRAL_YEARS_TO_TARGET,
                          REVENUE_GROWTH_BOUNDS, TARGET_MARGIN_BOUNDS,
                          HypotheticalRefused, UserAssumptions,
                          require_a_profit, validate)
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

# A loan book this large relative to revenue means lending IS the business.
# Probing 36 financial companies found nothing between 0.26x (PayPal, whose
# instalment receivables are incidental to taking payments) and 2.4x (Schwab,
# a broker with a bank inside it). Every bank and lender sat above: AXP 3.1x,
# Goldman 4.1x, Synchrony 6.2x, Capital One 8.1x, Ally 15.3x.
MAX_FEE_BUSINESS_LOANS_TO_REVENUE = 1.0

# Interest a company EARNS, as a share of revenue. A lender whose interest
# expense line is missing is still given away by its interest income: fee
# businesses probed at or below 2.9%, lenders at or above 35%.
MAX_FEE_BUSINESS_INTEREST_INCOME = 0.25

# An asset manager whose own balance sheet dwarfs its revenue is holding
# investment assets rather than collecting fees on someone else's - an annuity
# or insurance book, usually. Deliberately scoped to asset managers: exchanges
# hold member collateral (CME 30x, ICE 10.8x) and are fee businesses all the
# same. The weakest test here, and the only one without a clean gap behind it.
MAX_ASSET_MANAGER_ASSETS_TO_REVENUE = 10.0

ASSET_MANAGEMENT_INDUSTRY = "Asset Management"


@dataclass
class FinancialClassification:
    kind: str                       # see classify_financial()
    industry: str
    interest_burden: float | None

    # Which test refused it, for the message that explains the refusal.
    evidence: str = ""

    # True when a fee business was admitted because no lending evidence was
    # found, rather than because the data positively showed there is none.
    # See lending_not_itemised_warning().
    admitted_on_absence: bool = False


def interest_burden(fin: CompanyFinancials) -> float | None:
    """Median interest expense / revenue over recent years, or None if unknown."""
    ratios = [num(row, "interestExpense") / num(row, "revenue")
              for row in fin.income[:INTEREST_BURDEN_WINDOW]
              if row.get("interestExpense") is not None and num(row, "revenue") > 0]
    return median(ratios) if ratios else None


def _income_ratio(fin: CompanyFinancials, field: str) -> float | None:
    """Median of `field` / revenue over recent years, or None if never reported."""
    ratios = [abs(num(row, field)) / num(row, "revenue")
              for row in fin.income[:INTEREST_BURDEN_WINDOW]
              if row.get(field) is not None and num(row, "revenue") > 0]
    return median(ratios) if ratios else None


def _latest_revenue(fin: CompanyFinancials) -> float:
    return num(fin.income[0], "revenue") if fin.income else 0.0


def loans_to_revenue(fin: CompanyFinancials) -> float | None:
    """The loan book against a year of revenue, or None when none is reported."""
    if not fin.balance or not fin.income:
        return None
    loans = fin.balance[0].get("loans")
    revenue = _latest_revenue(fin)
    if loans is None or revenue <= 0:
        return None
    return abs(num(fin.balance[0], "loans")) / revenue


def assets_to_revenue(fin: CompanyFinancials) -> float | None:
    """The whole balance sheet against a year of revenue."""
    if not fin.balance or not fin.income:
        return None
    assets = fin.balance[0].get("totalAssets")
    revenue = _latest_revenue(fin)
    if assets is None or revenue <= 0:
        return None
    return abs(num(fin.balance[0], "totalAssets")) / revenue


def underwrites_insurance(fin: CompanyFinancials) -> bool:
    """
    Whether the company earns premiums or pays policyholder benefits.

    Insurance inside a company the industry label calls something else -
    Ameriprise files as Asset Management and earns $2.3bn of premiums. A DCF
    on an underwriter is meaningless whatever the label says.
    """
    for row in fin.income[:INTEREST_BURDEN_WINDOW]:
        for field in ("premiumsEarned", "policyholderBenefits"):
            if row.get(field) is not None and abs(num(row, field)) > 0:
                return True
    return False


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

    # --- What the company's own statements say it does ---------------------
    #
    # Ordered, first match wins, and every test looks for POSITIVE evidence of
    # lending or underwriting. A missing interest-expense line used to end the
    # sequence here as "unclassified"; it no longer does, because absence of
    # one line is not evidence of a loan book - Robinhood and T. Rowe Price
    # publish no interest expense at all and neither lends the way a bank does.
    burden = interest_burden(fin)

    if underwrites_insurance(fin):
        return FinancialClassification(
            "balance_sheet", industry, burden,
            evidence="underwrites insurance")

    loans = loans_to_revenue(fin)
    if loans is not None and loans >= MAX_FEE_BUSINESS_LOANS_TO_REVENUE:
        return FinancialClassification(
            "balance_sheet", industry, burden,
            evidence=f"a loan book {loans:.1f} times its annual revenue")

    if burden is not None and burden > MAX_FEE_BUSINESS_INTEREST_BURDEN:
        return FinancialClassification(
            "balance_sheet", industry, burden,
            evidence=f"interest expense of {burden:.0%} of revenue")

    earned = _income_ratio(fin, "interestIncome")
    if earned is not None and earned > MAX_FEE_BUSINESS_INTEREST_INCOME:
        return FinancialClassification(
            "balance_sheet", industry, burden,
            evidence=f"interest income of {earned:.0%} of revenue")

    if industry == ASSET_MANAGEMENT_INDUSTRY:
        assets = assets_to_revenue(fin)
        if assets is not None and assets > MAX_ASSET_MANAGER_ASSETS_TO_REVENUE:
            return FinancialClassification(
                "balance_sheet", industry, burden,
                evidence=f"a balance sheet {assets:.0f} times its annual revenue")

    # Nothing here lends or underwrites as far as this data can see. Without a
    # balance sheet to check, that conclusion rests on nothing: refuse instead.
    if not fin.balance or fin.balance[0].get("totalAssets") is None:
        return FinancialClassification("unclassified", industry, burden)

    return FinancialClassification(
        "fee_based", industry, burden,
        # An interest-expense line, even a small one, is the company stating
        # what its borrowing costs. With none at all, the fee conclusion rests
        # on absent lines rather than reported ones, and the valuation says so.
        admitted_on_absence=burden is None)


MIN_HISTORY_YEARS = 2
# Terminal value as a share of enterprise value. Above the first, most of
# the answer comes from the perpetuity and a reader should know before
# acting on it. Above the second the explicitly forecast years are worth
# less than nothing, so the figure rests entirely on the one part of the
# model nobody can check - that is a different claim, and gets a different
# tier.
HIGH_TV_SHARE = 0.85

# Above this the forecast has stopped contributing anything and the
# figure is a perpetuity with a company's name on it. Refused rather
# than warned about: the warning at 100% says the forecast is worth
# less than nothing, and there is a point past which saying it louder
# is not enough. 150% rather than lower because Amazon sits at 124%
# with real cash flow today, and refusing it would blame the company
# for a capital-spending assumption the model made.
TV_REFUSAL_SHARE = 1.50
TV_EXCEEDS_FORECAST = 1.00


@dataclass
class Suitability:
    suitable: bool
    reasons: list[str] = field(default_factory=list)    # blocking
    warnings: list[str] = field(default_factory=list)   # non-blocking
    # Machine-readable counterparts to `reasons`, so a caller can ask WHY a
    # company was refused without parsing prose. Internal: never serialised,
    # so no response changes because they exist.
    codes: list[str] = field(default_factory=list)

    # Not caveats about an assumption but doubts about the method itself: the
    # company was valued, and the model may still not fit it. These belong
    # beside the figure they qualify, not among notes on growth or tax, so
    # they are kept apart from `warnings` and never repeated in it.
    method_fit: list[str] = field(default_factory=list)


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
    # A read-only diagnostic beside the valuation, never an input to it.
    # None wherever it would not mean anything - which, because the
    # suitability guards run first, is every refusal.
    capital_returns: CapitalReturns | None = None

    # Set for the rare company whose valuation falls as the growth
    # assumption rises. None where it does not, and on every refusal.
    growth_cost_note: str | None = None

    # True on a refusal that has no step after it, so the screen can offer
    # the hypothetical rather than ending the conversation. See
    # hypothetical_available_after_dcf for when that is.
    offer_hypothetical: bool = False

    # Current market multiples, beside the model's estimate rather than
    # part of it. Reference only; nothing here is an input.
    snapshot: MarketSnapshot | None = None

    @property
    def suitable(self) -> bool:
        return self.suitability.suitable

    @property
    def method(self) -> str:
        return "dcf"


# Severity, decided here and nowhere else. The app used to make this
# judgement by position - whatever landed in the bottom list was a
# footnote - which meant the sentence saying 109% of Tesla's value rests on
# an unverifiable assumption sat below the sensitivity table, while a
# doubt about method fit was pinned under the figure. Both are the model
# telling you not to trust the number as read; only one of them looked
# like it.
SEVERITY_INFO = "info"          # true, worth recording, qualifies an input
SEVERITY_CAUTION = "caution"    # sound arithmetic on assumptions worth checking
SEVERITY_SERIOUS = "serious"    # the figure does not mean what it appears to

SEVERITY_ORDER = {SEVERITY_SERIOUS: 0, SEVERITY_CAUTION: 1, SEVERITY_INFO: 2}


class Warning(str):
    """A warning, and how loudly to say it.

    A str subclass rather than a dataclass, because fourteen places already
    treat these as strings - four spreadsheet exporters, two report
    formatters, the API - and every one of them should keep working
    untouched. The severity rides along for the callers that want it.
    """

    severity: str
    kind: str
    detail: dict
    headline: str

    def __new__(cls, text: str, severity: str = SEVERITY_CAUTION,
                kind: str = "", detail: dict | None = None,
                headline: str = ""):
        w = super().__new__(cls, text)
        w.severity = severity
        w.kind = kind
        w.detail = dict(detail or {})
        # The short form that sits under the figure. Falls back to the full
        # text, which is always true if sometimes long.
        w.headline = headline or text
        return w


def severity_of(warning) -> str:
    """The severity of a warning, for the ones built before this existed."""
    return getattr(warning, "severity", SEVERITY_CAUTION)


def most_severe(warnings: list) -> "Warning | None":
    """The one that belongs under the headline.

    Ties go to the more fundamental complaint: a problem with the method
    outranks a problem with an input, because no amount of adjusting the
    inputs answers it.
    """
    if not warnings:
        return None
    return min(warnings, key=lambda w: (SEVERITY_ORDER.get(severity_of(w), 1),
                                        0 if getattr(w, "kind", "") in
                                        _METHOD_KINDS else 1))


# Complaints about what the model can see at all, rather than about the
# numbers fed into it.
_METHOD_KINDS = {"method_fit", "dividends_only", "negative_equity",
                 "terminal_value_over_100",
                 # A generic discount rate is a statement about the whole
                 # output rather than about one input, so it outranks a
                 # complaint the reader could answer by moving a slider.
                 "wacc_default", "cost_of_equity_default"}


def _annual_base_reason(meta: dict | None) -> str | None:
    """Why this valuation is on an annual base, if it should not have been.

    None where the trailing base was used, and None where there was never a
    trailing base to want - a company whose only filing is its first annual
    report is not working from stale figures, it is working from all the
    figures there are. Only a REFUSED trailing base is worth saying.
    """
    meta = meta or {}
    if meta.get("base_period") != "annual":
        return None
    return meta.get("ttm_declined") or None


def input_source_warnings(derived, *, dcf: bool,
                          price_unavailable: bool = False,
                          price_is_last_known: bool = False,
                          annual_base_reason: str | None = None
                          ) -> list["Warning"]:
    """Caveats about where the discount rate's own inputs came from.

    Every one of these was previously invisible. The provenance recorded
    `source: "default"` and the app rendered that only on the assumption
    row, so a valuation whose entire discount rate was a generic number
    looked exactly like one built from the company's own capital structure.
    The stored risk-free rate was the first of these to be found drifting,
    by accident, from a figure that could not be right; these are the rest
    of the same class, graded by how much of the answer they carry.

    *dcf* selects the inputs that only one method has. The dividend model
    has no WACC and no cost of debt; it has a cost of equity, which is the
    same CAPM build-up and fails in the same way.
    """
    out: list["Warning"] = []
    store = {**(getattr(derived, "wacc_inputs", None) or {}),
             **(getattr(derived, "capm_inputs", None) or {}),
             **(getattr(derived, "provenance", None) or {})}
    diagnostics = getattr(derived, "diagnostics", None) or {}

    def defaulted(name: str):
        prov = store.get(name)
        return prov if prov is not None and prov.source == "default" else None

    stored_rate = stored_rate_warning(store)
    if stored_rate is not None:
        out.append(stored_rate)

    # --- The discount rate itself ------------------------------------------
    # Serious, and the only one of these that is. When WACC derivation fails
    # outright the figure is not a valuation of this company any more: it is
    # this company's cash flows discounted at a number chosen for want of
    # one. A reader comparing it to the market price has to know that before
    # they act on it, not after they go looking at an assumption row.
    wacc_prov = defaulted("wacc")
    if dcf and wacc_prov is not None:
        out.append(Warning(
            f"The discount rate could not be derived for this company, so a "
            f"generic {wacc_prov.value:.1%} was used. The figure reflects "
            "that rate rather than this company's own cost of capital.",
            severity=SEVERITY_SERIOUS, kind="wacc_default",
            detail={"wacc": wacc_prov.value, "why": wacc_prov.detail},
            headline=(f"Valued at a generic {wacc_prov.value:.1%} discount "
                      "rate because this company's own could not be worked "
                      "out; the figure says little about this company.")))

    cost_of_equity_prov = defaulted("cost_of_equity")
    if not dcf and cost_of_equity_prov is not None:
        out.append(Warning(
            f"The cost of equity could not be derived for this company, so "
            f"a generic {cost_of_equity_prov.value:.1%} was used. The figure "
            "reflects that rate rather than this company's own.",
            severity=SEVERITY_SERIOUS, kind="cost_of_equity_default",
            detail={"cost_of_equity": cost_of_equity_prov.value,
                    "why": cost_of_equity_prov.detail},
            headline=(f"Valued at a generic "
                      f"{cost_of_equity_prov.value:.1%} discount rate "
                      "because this company's own could not be worked out; "
                      "the figure says little about this company.")))

    # --- A return on capital the filings would not give --------------------
    #
    # When the company's own return cannot be measured, the long-run return
    # falls back to the cost of capital. That is the conservative choice and
    # the right one - it says growth creates no value rather than inventing
    # a figure - but it is a large, silent haircut to the terminal value,
    # and silence is the problem. The reader sees a valuation that looks
    # like any other while one of its biggest assumptions was not derived
    # from this company at all.
    #
    # The same absence hides the return-on-capital box, so this note stands
    # where that box would have been rather than adding to the page.
    roic_prov = defaulted("terminal_roic")
    if (dcf and roic_prov is not None
            and diagnostics.get("current_roic") is None):
        out.append(Warning(
            "This company's filings do not give enough to work out what it "
            "earns on the money tied up in it. The valuation therefore "
            "assumes it earns exactly its cost of capital once it matures, "
            "which means growth beyond the forecast adds nothing to what "
            "the company is worth.",
            # Caution, not serious. Serious means the figure does not mean
            # what it appears to; this one does. It is a deliberately
            # conservative assumption, applied because the alternative was
            # to invent a number, and the reader needs to know it was made
            # rather than to be told the answer is unreliable.
            severity=SEVERITY_CAUTION, kind="roic_unmeasurable",
            detail={"terminal_roic": roic_prov.value,
                    "why": roic_prov.detail},
            headline=("Return on capital could not be measured, so growth "
                      "beyond the forecast is assumed to add nothing.")))

    # --- A margin that followed a steep trend ------------------------------
    #
    # The rule takes the latest year where the margin has moved one way for
    # the whole window, because a level reached over four years is more
    # likely to hold than to be handed back. That is a judgement, and a
    # reader who disagrees with it has to be able to see it: a company whose
    # margin went from -8.5% to 31.6% is being forecast at 31.6% forever.
    span = (derived.diagnostics or {}).get("operating_margin_trend_span")
    window = (derived.diagnostics or {}).get("operating_margin_window") or []
    rule = (derived.diagnostics or {}).get("operating_margin_rule")
    if (rule == "latest" and span is not None and window
            and abs(span) > MARGIN_TREND_DISCLOSE):
        latest, oldest = window[0], window[-1]
        out.append(Warning(
            f"The forecast assumes the latest operating margin of "
            f"{latest:.1%} holds. It has moved steadily from {oldest:.1%} "
            f"over {len(window)} years, so the figure rests on that gain "
            f"being kept rather than on a long record of earning it."
            if span > 0 else
            f"The forecast assumes the latest operating margin of "
            f"{latest:.1%} holds. It has fallen steadily from {oldest:.1%} "
            f"over {len(window)} years, so the figure assumes the decline "
            f"stops here rather than continuing.",
            severity=SEVERITY_CAUTION, kind="margin_trend",
            detail={"latest": latest, "oldest": oldest,
                    "window": list(window)},
            headline=(f"The forecast holds the latest {latest:.1%} margin, "
                      f"which has moved steadily from {oldest:.1%}.")))

    # --- A build-out the forecast assumes ends ----------------------------
    #
    # The capital-spending path glides from what a company spends now to
    # where the model assumes it settles, and for a company mid-build-out
    # that glide is most of the answer. Meta's figure rests on investment
    # falling from 43% of revenue to 20%: capital spending does 23 points of
    # the work and depreciation catching up only 5. That is a large,
    # checkable claim, and it was being made silently.
    # getattr throughout, because this function also sees a dividend
    # discount model's assumptions - which have no capital spending in them
    # at all - and, in tests, stand-ins that carry only what the case under
    # test needs.
    _assumptions = getattr(derived, "assumptions", None)
    capex_start = getattr(_assumptions, "capex_pct", None)
    capex_end = getattr(_assumptions, "capex_terminal_pct", None)
    if capex_start is None:
        capex_end = None
    if capex_end is not None and capex_start - capex_end > CAPEX_FADE_DISCLOSE:
        out.append(Warning(
            f"The forecast assumes investment falls from {capex_start:.0%} of "
            f"revenue to {capex_end:.0%}. The company is building something, "
            f"and the value depends on that build-out ending. Move the "
            f"capital expenditure assumption to see what a longer one is "
            f"worth.",
            severity=SEVERITY_CAUTION, kind="capex_fade",
            detail={"from": capex_start, "to": capex_end},
            headline=(f"Investment is assumed to fall from {capex_start:.0%} "
                      f"of revenue to {capex_end:.0%}.")))
    # --- An annual base where a trailing one was wanted --------------------
    #
    # Caution, because it is not an error and it is not nothing: the figures
    # are real and audited, they are just up to a year old, and for a fast
    # growing company a year is most of the story. Palantir was valued on
    # $4.5bn of revenue while $7.3bn sat unused in the same response, and
    # the only warning on screen was about the growth cap.
    if annual_base_reason:
        out.append(Warning(
            f"This valuation uses the last full financial year rather than "
            f"the latest twelve months, because {annual_base_reason}. For a "
            f"company that has grown or shrunk since, the starting point is "
            f"out of date and every figure built on it with it.",
            severity=SEVERITY_CAUTION, kind="annual_base",
            detail={"why": annual_base_reason},
            headline=("Built on the last full financial year, not the "
                      "latest twelve months - so the starting figures may "
                      "be up to a year old.")))

    # --- A price from a previous session -----------------------------------
    #
    # Caution, like a missing price, and for the same reason: the valuation
    # is unaffected and the comparison is not what it appears. The date is
    # on screen beside the figure either way, so this says what the date
    # means rather than repeating it.
    if price_is_last_known:
        out.append(Warning(
            "The market price could not be read just now, so the comparison "
            "below uses the last price this app obtained. It was real on the "
            "date shown beside it and is not today's. Every figure in the "
            "valuation is unaffected.",
            severity=SEVERITY_CAUTION, kind="price_last_known",
            headline=("The market price is from an earlier session, not "
                      "today. The valuation itself is unaffected.")))

    # --- The share price, which is not an input at all ---------------------
    #
    # Caution rather than serious, and the distinction is the point: every
    # figure the model produces is unaffected, because the price is not an
    # input to any of them. What is lost is the comparison a reader came
    # for, and a percentage quietly missing from the screen is worse than a
    # sentence saying why.
    if price_unavailable:
        out.append(Warning(
            "The current market price could not be read just now, so there "
            "is no comparison between this valuation and what the shares "
            "actually cost. Every figure here is unaffected - the price is "
            "not an input to the valuation.",
            severity=SEVERITY_CAUTION, kind="price_unavailable",
            headline=("The market price is unavailable right now, so this "
                      "figure is not compared with it. The valuation itself "
                      "is unaffected.")))

    # --- Beta: one term of the discount rate, not the whole of it ----------
    #
    # Two warnings, because there are now two ways the company's own beta
    # can fail to be usable and they do not deserve the same weight.
    #
    # An industry beta is INFO. It is a reasoned estimate - this company's
    # actual industry, relevered on this company's actual balance sheet -
    # and the reader should know it was used without being told the
    # valuation is unsound, because it is not.
    #
    # The flat market beta is CAUTION and no longer reachable from
    # _capm_inputs at all - the industry table always answers, with the
    # whole market where it knows no industry. It is kept as a guard: this
    # function reads provenance rather than calling the derivation, so any
    # future path that defaults a beta gets a warning rather than silence.
    # Its tests record the provenance directly for the same reason.
    beta_prov = store.get("beta")
    if beta_prov is not None and beta_prov.source == "derived (industry)":
        out.append(Warning(
            f"This company's own share-price sensitivity to the market "
            f"could not be measured, so a beta of {beta_prov.value:.2f} was "
            f"estimated from its industry and its own borrowing instead. "
            f"The discount rate rests on it.",
            severity=SEVERITY_INFO, kind="beta_industry",
            detail={"beta": beta_prov.value, "why": beta_prov.detail},
            headline=("How much this share moves with the market could not "
                      f"be measured, so a beta of {beta_prov.value:.2f} "
                      "comes from its industry instead. That feeds the "
                      "discount rate.")))

    beta_prov = defaulted("beta")
    if beta_prov is not None:
        out.append(Warning(
            "This company's share-price sensitivity to the market could not "
            f"be measured, so the market average of {beta_prov.value:.1f} "
            "was used. The discount rate rests on it.",
            severity=SEVERITY_CAUTION, kind="beta_default",
            detail={"beta": beta_prov.value, "why": beta_prov.detail},
            headline=("How much this share moves with the market could not "
                      f"be measured, so the market average of "
                      f"{beta_prov.value:.1f} stands in. That feeds the "
                      "discount rate.")))

    # --- Cost of debt: a weighted minority of the discount rate ------------
    debt_prov = defaulted("cost_of_debt")
    if dcf and debt_prov is not None:
        out.append(Warning(
            f"The rate this company pays on its debt could not be worked "
            f"out from its accounts, so {debt_prov.value:.1%} was assumed - "
            "the risk-free rate plus a standard spread.",
            severity=SEVERITY_INFO, kind="cost_of_debt_default",
            detail={"cost_of_debt": debt_prov.value,
                    "why": debt_prov.detail}))

    # --- The two published figures that expire ----------------------------
    # Both already had a staleness flag. Neither reached the reader: the
    # flag travelled on the assumption row, where a date nobody is looking
    # at is indistinguishable from a date that is current.
    if erp_is_stale():
        erp_prov = store.get("equity_risk_premium")
        value = getattr(erp_prov, "value", DAMODARAN_IMPLIED_ERP)
        out.append(Warning(
            f"The equity risk premium of {value:.2%} was published "
            f"{DAMODARAN_ERP_AS_OF:%B %Y} and is overdue an update. It is "
            "republished monthly, and the discount rate moves with it.",
            severity=SEVERITY_CAUTION, kind="erp_stale",
            detail={"erp": value, "as_of": f"{DAMODARAN_ERP_AS_OF:%B %Y}",
                    "age_days": erp_age_days()},
            headline=(f"The market risk premium this uses is from "
                      f"{DAMODARAN_ERP_AS_OF:%B %Y} and has not been "
                      "refreshed since. The discount rate depends on it.")))

    # Only when the stale figure is the one that bound. The ceiling is the
    # lowest of three limits, and a stale number that lost to the other two
    # changed nothing about the answer - warning about it would be noise
    # that teaches the reader to ignore this tier.
    if dcf and sep_is_stale() \
            and diagnostics.get("terminal_growth_binding") == "long_run_growth":
        out.append(Warning(
            f"The long-run growth figure that caps this forecast's "
            f"perpetuity was published {LONG_RUN_GROWTH_AS_OF:%B %Y} and is "
            "overdue an update. It is the limit that bound here.",
            severity=SEVERITY_CAUTION, kind="long_run_growth_stale",
            detail={"growth": LONG_RUN_NOMINAL_GROWTH,
                    "as_of": f"{LONG_RUN_GROWTH_AS_OF:%B %Y}",
                    "age_days": sep_age_days()},
            headline=("The long-run growth estimate that caps the perpetuity "
                      f"is from {LONG_RUN_GROWTH_AS_OF:%B %Y} and has not "
                      "been refreshed. It is what set the ceiling here.")))

    return out


def stored_rate_warning(inputs: dict) -> "Warning | None":
    """A caution when the discount rate rests on the stored interest rate.

    Worth a tier of its own rather than an entry in the quiet list of
    defaults. The risk-free rate is the floor the whole discount rate is
    built on, and the stored value is a point observation that goes out of
    date on its own: when it last drifted it sat ninety basis points below
    the curve and moved a valuation by eighteen per cent, with nothing in
    the output to say which number the reader was looking at.

    Returns None - the ordinary case by a wide margin - when the rate came
    from either live source.
    """
    prov = (inputs or {}).get("risk_free_rate")
    if prov is None or getattr(prov, "source", "") != "default":
        return None

    headline = (f"The live interest rate was unavailable, so this uses a "
                f"stored rate of {prov.value:.2%} from "
                f"{risk_free_as_of_text()}, which may be out of date.")
    if risk_free_is_stale():
        headline = (f"The live interest rate was unavailable, so this uses a "
                    f"stored rate of {prov.value:.2%} from "
                    f"{risk_free_as_of_text()} - old enough that it is "
                    "probably no longer close to the real one.")
    return Warning(
        "The risk-free interest rate could not be fetched, so a stored "
        f"rate from {risk_free_as_of_text()} was used instead. The discount "
        "rate, and so the figure, rests on it.",
        severity=SEVERITY_CAUTION, kind="risk_free_fallback",
        detail={"rate": prov.value, "as_of": risk_free_as_of_text(),
                "is_stale": risk_free_is_stale()},
        headline=headline)


def growth_cost_note(report: "ValuationReport") -> str | None:
    """Explain a valuation that falls when the growth assumption rises.

    Most businesses are worth more if they grow faster. A few are not: where
    each extra dollar of revenue costs more in working capital and new
    assets than it returns in after-tax operating profit, that revenue is
    bought at a loss, and assuming more of it lowers the figure.

    It is correct, and it looks exactly like a bug - a user drags a slider
    one way and the number goes the other. It was reported as one. So it is
    said plainly, with the two numbers that cause it, and only for the
    companies where it is actually true.

    Whether it is true is decided by running the model again a percentage
    point faster, not by comparing the two per-dollar figures. They are not
    the same test: the working-capital charge falls on the increment while
    the profit falls on the level, so a wide enough margin absorbs the drag
    at any plausible rate. Coca-Cola reinvests more per incremental dollar
    than it earns on one and is still worth more the faster it grows.
    """
    if report.result is None or not report.suitable:
        return None

    assumptions = report.derived.assumptions
    if not growth_reduces_value(report.base, assumptions,
                                report.result.intrinsic_value_per_share):
        return None

    profit, reinvestment = growth_economics(assumptions)
    name = report.company_name.rstrip(".")
    return (
        f"Raising the growth assumption lowers this figure, which is a real "
        f"property of {name} rather than a fault in the model. Each extra "
        f"dollar of revenue earns about {profit * 100:.0f} cents of "
        f"after-tax operating profit, but needs roughly "
        f"{reinvestment * 100:.0f} cents of additional working capital and "
        f"capital spending to support. Revenue bought on those terms costs "
        f"more than it brings in, so assuming more of it is worth less, not "
        f"more. Most companies are the other way round; this one is not."
    )


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
        if classification.evidence == "underwrites insurance":
            # Filed under something other than Insurance, but earning premiums
            # all the same - Ameriprise files as an asset manager. What it does
            # decides the treatment, not what the label says.
            return (
                f"{fin.company_name} earns insurance premiums, whatever its "
                f"{classification.industry} label says. An underwriter's money is made by "
                "holding reserves against policies and investing the float, so free cash "
                "flow to the firm does not describe it and a DCF would produce a figure "
                "with no economic meaning. No value is reported rather than a misleading "
                "one."
            )

        if classification.evidence.startswith("a balance sheet"):
            # Caught by size alone, which is the weakest evidence here: it says
            # the company holds investment assets, not what they are. Refused
            # rather than guessed, and the wording claims no more than that.
            return (
                f"{fin.company_name} runs {classification.evidence} - filed as "
                f"{classification.industry}, but holding an investment book of its own "
                "rather than only collecting fees on other people's. That balance sheet is "
                "usually an annuity or insurance business, and free cash flow to the firm "
                "does not describe one. This data cannot say which it is, so no value is "
                "reported rather than one that assumes the fee business is the whole "
                "company."
            )

        # "a loan book 3.1 times its annual revenue", "interest expense of 34%
        # of revenue".
        evidence = classification.evidence or (
            f"interest expense of {classification.interest_burden:.0%} of revenue"
            if classification.interest_burden is not None else "a lender's balance sheet")
        article = "an" if classification.industry[:1].upper() in "AEIOU" else "a"
        return (
            f"{fin.company_name} is {article} {classification.industry} company with "
            f"{evidence}. "
            "Lending is part of how it earns that revenue rather than how it is "
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
               else "the data source published no balance sheet to show whether it "
                    "lends")
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


def lending_not_itemised_warning(fin: CompanyFinancials) -> str:
    """
    The caveat a fee-earning financial carries when nothing in its statements
    itemises lending.

    The classifier admits these companies because no loan book, no interest
    expense and no interest income appear - which is the absence of evidence
    rather than evidence of absence. Robinhood is the case that matters: it
    earns real money from margin and securities lending, and this data shows
    none of it. The valuation is still produced, because refusing every
    company whose provider omits a line would refuse most fee businesses; but
    it is produced with this attached.
    """
    return (
        f"{fin.company_name} is a financial company, and its published statements do "
        "not itemise interest or a loan book. Part of its revenue may come from "
        "lending activity - margin loans, securities lending, interest on customer "
        "balances - that this data does not fully capture. A discounted cash flow "
        "values a business by the cash its operations throw off, and it describes "
        "lending income poorly, so treat this valuation with extra caution."
    )


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
                       base: BaseYearData,
                       meta: dict | None = None) -> Suitability:
    """Decide whether a standard growth-perpetuity DCF applies to this company.

    *meta* is the base year's provenance, which carries whether the trailing
    twelve months were used and, where they were not, why. Optional because
    the speculative path assesses a company without one.
    """
    reasons: list[str] = []
    warnings: list[str] = []
    method_fit: list[str] = []
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
    elif classification.admitted_on_absence:
        # Valued, but the fee conclusion rests on lines the provider did not
        # publish rather than on lines that say there is no lending. A doubt
        # about whether a DCF fits the company at all, so it is carried as one.
        method_fit.append(lending_not_itemised_warning(fin))

    # --- Blocking: perpetuity formula breaks down -------------------------
    if a.terminal_growth >= a.wacc:
        codes.append("growth_exceeds_wacc")
        reasons.append(
            f"Terminal growth ({a.terminal_growth:.1%}) is not below the "
            f"discount rate ({a.wacc:.1%}). A company cannot grow faster than "
            "its discount rate forever - the maths gives an infinite or "
            "negative value - so terminal growth must be set below it."
        )

    # --- Blocking: the projection never produces cash ----------------------
    # Checked directly rather than inferred, because a company can be
    # profitable on an operating basis yet still consume cash through capex.
    if not reasons:
        probe = run_dcf(base, a, with_sensitivity=False)
        if probe.years[-1].ufcf <= 0:
            codes.append("negative_fcf")
            reasons.append(
                f"Even by the last year of the forecast, the company would still be "
                f"spending more cash than it brings in ({_money(probe.years[-1].ufcf * 1e6)} "
                "that year). Its investment needs outweigh its profits, so there is no "
                "positive cash flow to put a long-term value on."
            )

        # --- Blocking: the answer is not in the part you can check ---------
        #
        # The rule above asks only about the LAST forecast year. Over five
        # years that was a fair proxy for "this never funds itself"; over
        # ten, with depreciation allowed to catch up, a company can consume
        # cash for nine years and turn positive in the tenth. Tesla does
        # exactly that - eight negative years, minus $25bn cumulative, and
        # a final year of plus $1.3bn - so it stopped being refused and
        # started being valued at $11 a share with 668% of that value
        # sitting in the perpetuity.
        #
        # Two other rules were measured and rejected. "Cumulative forecast
        # cash flow is negative" and "negative in half the years" both
        # catch Tesla, and both also catch Amazon - a company whose free
        # cash flow is positive today, and whose forecast only turns
        # negative because the derived capital spending of 27% of revenue
        # outruns its margin. That is a complaint about an input, not about
        # the company, and refusing on it would be the model blaming a
        # business for an assumption the model made.
        #
        # This rule asks a different question: is the answer coming from
        # the part of the model anyone can check? It is the same quantity
        # the serious warning at 100% already reports, so the warning and
        # the refusal are one scale with two thresholds rather than two
        # unrelated tests. At 150% Amazon (124%) is valued and flagged;
        # Tesla (668%) is refused.
        # --- Blocking: the whole business comes out worth nothing ---------
        #
        # This is the far side of the rule below, and the share cannot see
        # it. `tv_pct_of_ev` divides by enterprise value, so as the
        # terminal value grows against a forecast worth less than nothing
        # the share runs up to infinity, crosses, and comes back as a
        # NEGATIVE number - Oracle read -6951% - which sails under a
        # `> 150%` test untouched and is reported as no concern at all.
        #
        # So the denominator is checked on its own terms. A company whose
        # entire value, forecast and perpetuity together, is zero or less
        # has nothing to report whatever the ratio says about it.
        #
        # It is NOT the same question as "the forecast contributes
        # nothing". That rule was measured and rejected: it catches Amazon,
        # whose forecast only turns negative because the derived capital
        # spending outruns its margin, and whose enterprise value is
        # solidly positive. Amazon keeps its serious warning at 136% and
        # keeps being valued.
        elif probe.enterprise_value <= 0:
            codes.append("negative_enterprise_value")
            reasons.append(
                "Even counting everything after the forecast, this company "
                "comes out worth nothing or less: the investment it is "
                "assumed to make outweighs the cash it brings in. There is "
                "no figure here worth reporting."
            )

        elif probe.tv_pct_of_ev > TV_REFUSAL_SHARE:
            codes.append("terminal_value_dominates")
            reasons.append(
                f"Terminal value would be {probe.tv_pct_of_ev:.0%} of the whole "
                f"company. The {a.projection_years} years that were actually "
                f"forecast come to {_money(probe.pv_ufcf_sum * 1e6)}, and everything "
                f"else is a single perpetuity beyond them. There is no figure here "
                f"worth reporting: it would be a statement about one growth rate "
                f"applied forever, not a valuation of this business."
            )

    # --- Non-blocking warnings --------------------------------------------
    if not reasons:
        result = run_dcf(base, a)

        if result.intrinsic_value_per_share <= 0:
            warnings.append(Warning(
                "Equity value came out negative - debt exceeds the value of "
                "the business plus its cash. Treat the per-share figure as an "
                "indication of distress, not a price target.",
                severity=SEVERITY_SERIOUS, kind="negative_equity",
                headline="Debt exceeds the whole business. This is a measure "
                         "of distress, not a price."))

        if result.tv_pct_of_ev > TV_EXCEEDS_FORECAST:
            warnings.append(Warning(
                f"Terminal value is {result.tv_pct_of_ev:.0%} of enterprise "
                f"value. The explicit forecast contributes "
                f"{_money(result.pv_ufcf_sum * 1e6)} and the perpetuity "
                f"{_money(result.pv_terminal_value * 1e6)}, so the part of "
                "the model you can check is not what produced the answer.",
                severity=SEVERITY_SERIOUS, kind="terminal_value_over_100",
                detail={"tv_pct": result.tv_pct_of_ev,
                        "pv_explicit": result.pv_ufcf_sum,
                        "pv_terminal": result.pv_terminal_value},
                headline=(f"The {a.projection_years} forecast years are worth "
                          "less than nothing. Every dollar of this figure "
                          "comes from the perpetuity beyond them.")))
        elif result.tv_pct_of_ev > HIGH_TV_SHARE:
            warnings.append(Warning(
                f"Terminal value is {result.tv_pct_of_ev:.0%} of enterprise "
                f"value. The forecast years contribute "
                f"{_money(result.pv_ufcf_sum * 1e6)} and the perpetuity "
                f"{_money(result.pv_terminal_value * 1e6)}; move the "
                "discount rate or long-run growth a little and the figure "
                "moves a lot.",
                severity=SEVERITY_CAUTION, kind="terminal_value_high",
                detail={"tv_pct": result.tv_pct_of_ev,
                        "pv_explicit": result.pv_ufcf_sum,
                        "pv_terminal": result.pv_terminal_value},
                headline=(f"{result.tv_pct_of_ev:.0%} of the value sits "
                          "beyond the forecast years.")))
        if a.revenue_growth <= a.terminal_growth:
            warnings.append(Warning(
                growth_below_terminal_warning(
                    fin.company_name, a.revenue_growth, a.terminal_growth,
                    a.projection_years),
                severity=SEVERITY_CAUTION, kind="growth_below_terminal",
                headline=(f"The forecast grows more slowly "
                          f"({a.revenue_growth:.1%}) than the perpetuity "
                          f"after it ({a.terminal_growth:.1%}).")))

    warnings.extend(input_source_warnings(
        derived, dcf=True, price_unavailable=base.current_price is None,
        price_is_last_known=bool(fin.profile.get("priceIsLastKnown")),
        annual_base_reason=_annual_base_reason(meta)))

    for prov in derived.provenance.values():
        if prov.source != "derived (clamped)":
            continue
        if prov.name == "revenue_growth":
            # The one clamp that belongs beside the figure. Every other one
            # trims an input the reader never questioned; this one overrules
            # the company's own reported history - the measured trend is
            # replaced by a rate the company did not achieve - and the
            # headline figure is
            # built on the replacement. A reader comparing it to the market
            # price should meet that before they act on it.
            raw, limit = prov.raw, prov.limit
            if raw is not None and limit is not None and raw > limit:
                headline = (
                    f"Years 2-5 grow at {limit:.1%} a year rather than "
                    f"the {raw:.1%} this company has managed, which the "
                    "model treats as too fast to keep up.")
            elif raw is not None and limit is not None:
                # The clamp runs the other way here: revenue shrank, and
                # the forecast holds it flat rather than projecting the
                # decline forever. Saying "too fast" about a company going
                # backwards would be nonsense.
                #
                # The floor is zero today, so "no growth" is what the clamp
                # actually does and reads better than "0.0%". Branched rather
                # than hard-coded: if the floor ever moves off zero the
                # sentence has to move with it, and a figure printed as
                # "assumes 0.0%" was the slip this replaces.
                instead = ("assumes no growth" if abs(limit) < 5e-4
                           else f"assumes {limit:.1%} a year")
                headline = (
                    f"Revenue has been shrinking {abs(raw):.1%} a year. The "
                    f"forecast {instead} instead of projecting the decline "
                    "indefinitely.")
            else:
                headline = ("The forecast uses a growth rate the model "
                            "adjusted, not the one the company has managed.")
            warnings.append(Warning(
                clamp_warning(prov, fin.company_name),
                severity=SEVERITY_CAUTION, kind="growth_clamped",
                detail={"raw": raw, "limit": limit},
                headline=headline))
        else:
            # The rest trim one input to the edge of its range. True, worth
            # recording, and a statement about an assumption rather than
            # about the answer.
            warnings.append(Warning(
                clamp_warning(prov, fin.company_name),
                severity=SEVERITY_INFO, kind="clamped"))

    return Suitability(suitable=not reasons, reasons=reasons, warnings=warnings,
                       codes=codes, method_fit=method_fit)


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
    suitability = assess_suitability(fin, derived, base, meta)

    result = run_dcf(base, derived.assumptions) if suitability.suitable else None

    # Only where a valuation actually stood up. A refused company has no
    # return on capital worth reporting, and computing one would invite it
    # onto a screen that is explaining why there is no figure.
    capital_returns = (
        roic_diagnostic(fin, derived.assumptions.tax_rate, derived.assumptions.wacc)
        if suitability.suitable else None
    )

    report = ValuationReport(
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
        capital_returns=capital_returns,
    )
    # Needs the finished report, because deciding this means valuing the
    # company a second time at a slightly higher growth rate.
    report.growth_cost_note = growth_cost_note(report)
    report.snapshot = market_snapshot(fin, base.current_price)
    # Needs the finished report too, and the statements, which only this
    # function has both of.
    report.offer_hypothetical = hypothetical_available_after_dcf(report, fin)
    return report


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

    # The same market reference block the DCF carries. A dividend payer's
    # multiples are no less worth seeing beside its valuation.
    snapshot: MarketSnapshot | None = None

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
        snapshot=market_snapshot(fin, inputs.current_price),
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
        warnings.append(Warning(
            f"{name} paid out {payout:.0%} of its {fiscal_year} earnings as "
            "dividends, leaving little retained to fund growth. Growth at the "
            "assumed rate depends on earnings rising, not on the payout.",
            severity=SEVERITY_CAUTION, kind="high_payout",
            headline=(f"{payout:.0%} of earnings already goes out as "
                      "dividends, leaving little to fund the assumed "
                      "growth.")))

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
            warnings.append(Warning(
                f"The terminal value is {result.tv_pct_of_value:.0%} of the "
                "valuation. Almost all of it rests on the perpetuity "
                "assumption, so the result is highly sensitive to the cost "
                "of equity and terminal growth.",
                severity=(SEVERITY_SERIOUS
                          if result.tv_pct_of_value > TV_EXCEEDS_FORECAST
                          else SEVERITY_CAUTION),
                kind=("terminal_value_over_100"
                      if result.tv_pct_of_value > TV_EXCEEDS_FORECAST
                      else "terminal_value_high"),
                detail={"tv_pct": result.tv_pct_of_value},
                headline=(f"{result.tv_pct_of_value:.0%} of the value sits "
                          "beyond the explicitly forecast dividends.")))

    buybacks, paid = d.get("latest_buybacks"), d.get("latest_dividends_paid")
    if buybacks and paid and buybacks > paid:
        # Not a caveat about an input: the model structurally cannot see
        # the larger of the two numbers, so the gap to the market price is
        # partly the method rather than the company.
        warnings.append(Warning(
            f"{name} returned ${buybacks / 1e9:,.1f}bn through share buybacks "
            f"against ${paid / 1e9:,.1f}bn in dividends in its {fiscal_year} "
            "financial year. A dividend discount model counts only dividends, "
            "so it does not see most of the cash this company returns to "
            "shareholders and is likely to understate its value on that basis.",
            severity=SEVERITY_SERIOUS, kind="dividends_only",
            detail={"buybacks": buybacks, "dividends": paid},
            headline=("This model counts dividends only, and this company "
                      f"returns ${buybacks / 1e9:,.1f}bn through buybacks "
                      f"against ${paid / 1e9:,.1f}bn of dividends. It is "
                      "likely to understate.")))

    history = d.get("dividend_history_used") or []
    cut_years = [year for (_, before), (year, after) in zip(history, history[1:])
                 if after < before * (1 - DIVIDEND_CUT_TOLERANCE)]
    if cut_years:
        warnings.append(Warning(
            f"The dividend per share fell in "
            f"{', '.join(str(y) for y in cut_years)}. The growth rate spans "
            "that cut, and a company that has cut its dividend once can do "
            "so again.",
            severity=SEVERITY_CAUTION, kind="dividend_cut",
            headline=("The dividend has been cut before, and the growth rate "
                      "spans that cut.")))

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

    # The dividend model builds its cost of equity from the same CAPM
    # inputs, so it rests on the same stand-ins in the same way.
    warnings.extend(input_source_warnings(
        derived, dcf=False,
        price_unavailable=not getattr(inputs, "current_price", None),
        price_is_last_known=bool(fin.profile.get("priceIsLastKnown"))))

    for assumption, prov in derived.provenance.items():
        if prov.source == "derived (clamped)":
            warnings.append(Warning(clamp_warning(prov, name),
                                    severity=SEVERITY_INFO, kind="clamped"))
        elif assumption == "dividend_growth" and prov.source == "default":
            warnings.append(Warning(
                dividend_growth_default_warning(prov, name),
                severity=SEVERITY_INFO, kind="defaulted"))

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
        f"{a.cost_of_equity:.2%}{erp_text} and {growth}. The equity risk premium is "
        f"Damodaran's implied premium for {DAMODARAN_ERP_AS_OF:%B %Y}, not a survey "
        "average, and a different but defensible one moves the figure. It values "
        "only the dividends "
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
            # Both are non-positive here, so the figures are stated without
            # their signs: the sentence already says which way they went, and
            # repeating it as "-0.5%" after a dash read "fallen - -0.5%".
            # Where the fall is too small to print, it is flatness rather
            # than a fall, and the sentence says that instead of claiming a
            # decline of 0.0%.
            flat = (float(f"{latest * 100:.1f}") == 0
                    and float(f"{cagr * 100:.1f}") == 0)
            movement = (
                "revenue has not grown"
                if flat else
                f"revenue has fallen {abs(latest):.1%} in the latest year "
                f"and {abs(cagr):.1%} a year over the period")
            reasons.append(
                f"{_possessive(name)} {movement}. This approach projects growth into "
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
            f"Terminal growth ({a.terminal_growth:.1%}) is not below the "
            f"discount rate ({a.wacc:.1%}). The perpetuity formula needs the "
            "discount rate to be the larger of the two; otherwise the "
            "terminal value is infinite or negative.")

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
# The user-built hypothetical - the last opt-in, and the weakest claim here
#
# Reached only from the speculative refusal, and only for companies that refusal
# turned down for the SHAPE of their trajectory - shrinking revenue, a gross
# margin that does not cover costs, losses too deep to close in a decade. Those
# are the judgements the user replaces with their own; the ones that stay are
# the ones no assumption can replace. See hypothetical.py.
# ---------------------------------------------------------------------------


@dataclass
class RealityContrast:
    """One assumed figure, beside the company's actual one."""
    name: str
    label: str
    assumed: float
    actual: float | None
    statement: str
    contradicts: bool


@dataclass
class HypotheticalReport:
    ticker: str
    company_name: str
    sector: str
    industry: str
    exchange: str
    fiscal_year: str
    user: UserAssumptions
    derived: DerivedSpeculativeAssumptions
    result: SpeculativeResult
    why_standard_refused: list[str]
    why_speculative_refused: list[str]
    reality: list[RealityContrast]
    warnings: list[str]
    # The machine-readable refusal that led here. The disclaimer has to name
    # the right one: a company refused for outspending its profits is not a
    # loss-maker, and calling it one in the app's loudest paragraph would be
    # a plain untruth about the company.
    standard_codes: list[str] = field(default_factory=list)
    # The capital investment actually used - the caller's figure, or the
    # company's own where they supplied none. Reported back so a reader
    # always sees the rate the arithmetic ran on.
    capex_pct: float = 0.0

    @property
    def method(self) -> str:
        return "hypothetical"

    @property
    def framing(self) -> str:
        return hypothetical_framing_for(self.standard_codes)


def hypothetical_available(report: SpeculativeReport) -> bool:
    """
    Whether the user-built hypothetical can be offered on this refusal.

    True only when the speculative engine declined for reasons the user's own
    assumptions genuinely replace. A company with no revenue, too little of it,
    or too short a history is refused here as well, so the opt-in is not
    offered where it would only be refused a step later.
    """
    if report.suitable:
        return False
    return not _hypothetical_blocks(report.derived)



# Refusals the reader's own assumptions genuinely answer, and so the only
# ones the hypothetical is offered past.
#
# The hypothetical screen gives the reader three figures: revenue growth, a
# target operating margin, and how long the company takes to reach it. A
# refusal belongs here only when those three are what stood in the way.
#
#   negative_fcf          the forecast never turns cash positive. Tesla's
#                         case: a higher margin or faster growth is exactly
#                         the claim a reader might want to make.
#   negative_median_margin  the company has typically lost money on its
#                         day-to-day business. The margin is the user's input.
#   operating_loss        a loss in the latest year. Reached here only
#                         alongside another code, since a loss on its own
#                         sends the reader to the speculative tier first.
#
# Deliberately a whitelist rather than a list of exclusions, because the cost
# of the two mistakes is not symmetric. Leaving a code out only withholds an
# opt-in. Letting one in offers a door that opens onto arithmetic as
# meaningless as the refusal - and the figure behind the door looks like an
# answer. So a refusal code added later is not offered until someone decides
# it should be.
#
# What is excluded, and why no assumption reaches it:
#   financial             American Express is refused because interest is how
#                         it earns revenue rather than how it is financed, so
#                         unlevered free cash flow and the enterprise-to-equity
#                         bridge mean nothing for it. The hypothetical builds
#                         that same bridge. No margin the reader types fixes
#                         the shape of the model.
#   growth_exceeds_wacc   terminal growth is at or above the discount rate, so
#                         the perpetuity diverges. Neither figure is on that
#                         screen, so the reader cannot repair it.
#   no_revenue /          there is no business to assume things about. The
#   short_history         hypothetical refuses these in its own terms anyway;
#                         they are also checked directly below, because the
#                         thresholds there are the ones that screen applies.
HYPOTHETICAL_REFUSAL_CODES = frozenset({
    "negative_fcf", "negative_median_margin", "operating_loss",
    # A terminal value that swamps the forecast is a complaint about growth
    # and investment, and those are two of the three figures that screen
    # hands the reader. It is also Tesla's refusal now that the cash-flow
    # rule no longer catches it, so leaving it out would make that refusal
    # the dead end the whole route exists to remove.
    "terminal_value_dominates",
    # The same complaint reaching the same reader, from the other side of
    # the ratio: a company whose whole value comes out at zero or less is
    # refused over its growth and its investment, which are two of the
    # three figures the hypothetical screen hands them. Leaving it out
    # would make this the dead end the route exists to remove.
    "negative_enterprise_value",
})

# Codes that may accompany those without sending the reader to the generic
# "your assumptions do not reach this" refusal. They are not waived: no
# revenue and too little history are both refused on the hypothetical screen
# itself, in its own and better terms - "growth of any rate applied to nothing
# is still nothing" says why no hypothetical can be built, where a message
# about the model not fitting would only say why the DCF could not.
#
# So the screen's own guard answers them, and the opt-in above is never
# offered for them: the door is decided by HYPOTHETICAL_REFUSAL_CODES, which
# is the narrower set. A reader who reaches this screen another way still
# gets the considered answer.
HYPOTHETICAL_ELIGIBLE_CODES = (
    HYPOTHETICAL_REFUSAL_CODES | {"no_revenue", "short_history"})


def hypothetical_framing_for(codes: "list[str] | set[str]") -> str:
    """Which of the two questions this refusal asks.

    `negative_fcf` is a company that makes a profit and spends past it, so
    there is no turnaround to assume and nothing in its figures pointing the
    other way. Every other refusal that reaches this screen is a loss.
    """
    # Both of these refuse a company that MAKES money, so neither gets the
    # loss-maker's words.
    profitable = {"negative_fcf", "terminal_value_dominates",
                  "negative_enterprise_value"}
    return (FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE if set(codes) & profitable
            else FRAMING_PATH_TO_PROFIT)


def hypothetical_placeholders_for(report: "ValuationReport") -> dict:
    """The figures the screen opens on when a DCF refusal offers it.

    The reader here is not assuming a turnaround. They are asking what would
    have to be true for a profitable company that outspends its profits to be
    worth something, and that question starts from what the company actually
    does. So the screen opens on its OWN current growth and margin, which it
    labels as its own - a neutral zero there would not be modest, it would be
    wrong about the company.

    The loss-maker's neutral zeros are NOT produced here, and there is no
    branch for them. Every refusal that reaches this function is on the
    `what_would_have_to_be_true` route, because a refusal for losing money
    has the speculative tier in front of it and never offers this step
    directly - `hypothetical_available_after_dcf` returns False for it. A
    neutral branch here would be code no caller could reach, and a mutation
    run that removed it changed nothing. The screen reached THROUGH the
    speculative refusal gets its neutral values from that response instead.

    `test_only_the_profitable_route_reaches_these_placeholders` holds that
    invariant, so widening the whitelist of refusal codes fails loudly here
    rather than opening a loss-maker's screen on figures chosen for someone
    else.

    Clamped into the screen's own bounds, and a figure that will not fit
    falls back to the neutral one rather than arriving out of range.
    """
    a = report.derived.assumptions
    growth_low, growth_high = REVENUE_GROWTH_BOUNDS
    margin_low, margin_high = TARGET_MARGIN_BOUNDS
    capex_low, capex_high = CAPEX_PCT_BOUNDS
    growth = min(max(a.revenue_growth, growth_low), growth_high)
    margin = min(max(a.operating_margin, margin_low), margin_high)
    return {
        "revenue_growth": growth,
        # A margin of nil would land the reader on the "no profit in it"
        # screen immediately, which is not where this route starts.
        "target_operating_margin": (margin if margin > 0
                                    else NEUTRAL_TARGET_MARGIN),
        "years_to_target": float(NEUTRAL_YEARS_TO_TARGET),
        # What the company spends on investment today. The one figure the
        # screen used to take without asking, and the one that decides the
        # answer for a company refused for spending past its profits.
        "capex_pct": min(max(a.capex_pct, capex_low), capex_high),
    }


def hypothetical_available_after_dcf(report: "ValuationReport",
                                     fin: CompanyFinancials) -> bool:
    """Whether the user-built hypothetical should be offered on a DCF refusal.

    The tiers are deliberately ordered - a standard valuation, then a
    speculative estimate for a loss-maker on a path to profitability, then a
    hypothetical built from the caller's own assumptions - and a screen must
    not skip a step by offering the last one while an earlier one is still
    on the table.

    But a refusal with no step after it is a dead end, and Tesla found one.
    It is refused by the DCF because its investment outspends its profits,
    not because it loses money, so no speculative estimate is offered - and
    the hypothetical was reachable only from a speculative refusal. The
    screen said why the model would not value the company and stopped.

    So: offered when the DCF has refused, a speculative estimate is NOT on
    offer, every blocking reason is one the reader's own three figures
    actually answer, and there is a business to assume them about. The last
    two matter for the same reason - offering a door that opens onto another
    refusal, or onto a figure as meaningless as the one withheld, is worse
    than offering none.
    """
    if speculative_estimate_available(report):
        return False          # the earlier tier is still on the table

    # An empty set of codes carries two cases, and both must withhold the
    # offer. A company the model valued has none, so there is nothing to step
    # past. And a refusal whose reason carried no code is one this whitelist
    # has never seen - it would be offered by `codes <= ...` alone, since the
    # empty set is a subset of everything.
    #
    # A `report.suitable` guard stood here too and was removed: today
    # Suitability appends a code beside every reason, so it decided nothing
    # this line does not, and no test could tell the two apart. This is the
    # stronger of the pair, because it still holds if a reason is ever added
    # without one.
    codes = set(report.suitability.codes)
    if not codes or not codes <= HYPOTHETICAL_REFUSAL_CODES:
        return False
    if (report.base.revenue if report.base else 0.0) <= MIN_SPECULATIVE_REVENUE_MM:
        return False
    return fin.years_available >= MIN_HISTORY_YEARS


def _hypothetical_blocks(derived: DerivedSpeculativeAssumptions,
                         name: str = "This company",
                         years_available: int | None = None) -> list[str]:
    """
    The refusals no user assumption can lift.

    Growth, margin and the horizon are the user's to assume. What is left is
    the existence of a business to assume them about: revenue to grow, enough
    of it to anchor the arithmetic, and enough history to show what the
    assumption contradicts.
    """
    inputs, d = derived.inputs, derived.diagnostics
    fiscal_year = d["fiscal_year"]
    blocks: list[str] = []

    if inputs.revenue <= 0:
        blocks.append(
            f"{name} reported no revenue in its {fiscal_year} financial year. Growth of any "
            "rate applied to nothing is still nothing, so there is no hypothetical to build: "
            "both the revenue and the margin would have to be invented outright.")
    elif inputs.revenue < MIN_SPECULATIVE_REVENUE_MM:
        blocks.append(
            f"{name}'s revenue of {_money(inputs.revenue * 1e6)} in its {fiscal_year} financial "
            "year is too small to anchor a projection. Nearly all of any figure would come "
            "from the assumed growth rather than from the business as it is.")

    if years_available is not None and years_available < MIN_HISTORY_YEARS:
        blocks.append(
            f"Only {_count(years_available, 'year')} of financial history is available, too "
            "little to show what an assumed turnaround would be departing from. This screen "
            "exists to set assumptions against a real trajectory; there is none to set them "
            "against.")

    return blocks


def _reality_contrast(user: UserAssumptions,
                      derived: DerivedSpeculativeAssumptions,
                      name: str) -> list[RealityContrast]:
    """
    Each assumed driver beside what the company actually did.

    The point of this screen is that the two disagree, so the comparison is
    computed for every input and shown whether or not it flatters the
    hypothetical.
    """
    d = derived.diagnostics
    short = name.rstrip(".")
    fiscal_year = d["fiscal_year"]
    cagr, latest = d["revenue_cagr"], d["latest_revenue_growth"]
    actual_margin, gross = d["operating_margin"], d["gross_margin"]
    out: list[RealityContrast] = []

    # --- Revenue growth ------------------------------------------------------
    if cagr is None and latest is None:
        growth_statement = (
            f"No usable revenue history for {short}, so there is nothing to compare this "
            "assumption against.")
        contradicts = False
        actual_growth = None
    else:
        actual_growth = cagr if cagr is not None else latest
        recent = ("" if latest is None
                  else f", and {signed_pct(latest)} in the latest year alone")
        # A change too small to print is not a direction: "grown 0.0% a year"
        # states a trend the figure beside it refuses to support, so the
        # flat case gets a sentence of its own rather than a number.
        if float(f"{actual_growth * 100:.1f}") == 0:
            actually = "has actually been flat"
        else:
            direction = "fallen" if actual_growth < 0 else "grown"
            actually = f"has actually {direction} {abs(actual_growth):.1%} a year"
        growth_statement = (
            f"You have assumed revenue grows {signed_pct(user.revenue_growth)} a year. "
            f"{short}'s revenue {actually}{recent}.")
        contradicts = user.revenue_growth > actual_growth

    out.append(RealityContrast(
        name="revenue_growth",
        label="Revenue growth",
        assumed=user.revenue_growth,
        actual=actual_growth,
        statement=growth_statement,
        contradicts=contradicts,
    ))

    # --- Target operating margin ---------------------------------------------
    if actual_margin is None:
        margin_statement = (
            f"{short}'s operating margin is not available, so there is nothing to compare "
            "this assumption against.")
        contradicts = False
    else:
        gross_note = ("" if gross is None else
                      f" Its gross margin - what is left before operating costs - was "
                      f"{gross:.0%}.")
        # Both to one decimal place. They were printed to different
        # precisions, which on the route that OPENS with the two equal made
        # 5.11% read as "you have assumed 5%" beside "it actually earned
        # 5.1%" - a difference that is not there.
        margin_statement = (
            f"You have assumed an operating margin of "
            f"{user.target_operating_margin:.1%}. "
            f"{short} actually earned {actual_margin:.1%} in its {fiscal_year} financial "
            f"year.{gross_note}")
        contradicts = user.target_operating_margin > actual_margin

    out.append(RealityContrast(
        name="target_operating_margin",
        label="Target operating margin",
        assumed=user.target_operating_margin,
        actual=actual_margin,
        statement=margin_statement,
        contradicts=contradicts,
    ))

    # --- Years to the target -------------------------------------------------
    if actual_margin is None:
        years_statement = (
            "No operating margin is available, so the pace this assumes cannot be compared "
            "with anything.")
        contradicts = False
    else:
        pace = (user.target_operating_margin - actual_margin) / user.years_to_target
        if abs(pace) * 100 < 0.05:
            # The target is the company's own margin, which is where the
            # second route opens. There is no journey to take and nothing to
            # contradict, and the old sentence claimed both - it told a
            # reader assuming no change at all that "nothing in the company's
            # figures says that happens", about its own figures.
            years_statement = (
                f"You have not assumed the margin changes, so this setting does nothing yet. "
                f"It starts to matter as soon as you move the target away from "
                f"{actual_margin:.1%}.")
            contradicts = False
        else:
            direction = "improving" if pace > 0 else "falling"
            years_statement = (
                f"Reaching it in {_count(user.years_to_target, 'year')} means the operating "
                f"margin {direction} by about {abs(pace) * 100:.1f} percentage points every "
                f"year, every year, starting from {actual_margin:.1%}. Nothing in "
                f"{short}'s figures says that happens.")
            contradicts = True

    out.append(RealityContrast(
        name="years_to_target",
        label="Years to the target",
        assumed=float(user.years_to_target),
        actual=actual_margin,
        statement=years_statement,
        contradicts=contradicts,
    ))

    return out


def short_of(name: str) -> str:
    """A company name without its trailing full stop, for mid-sentence use."""
    return name.rstrip(".")


def value_company_hypothetically(ticker: str,
                                 user: UserAssumptions) -> HypotheticalReport:
    """
    Run the projection engine on assumptions the USER supplied.

    Nothing about the three drivers is derived, defaulted or suggested here.
    The remaining inputs - the discount rate, tax, the reinvestment ratios -
    come from the company's own figures exactly as they do for the speculative
    estimate, because they are not what the user is being asked to assume.

    Raises HypotheticalRefused when the company is not one this applies to, or
    when the assumptions are not yet a hypothetical about a profit.
    """

    # The bounds are a property of the controls, so they are checked before
    # anything is fetched. Whether a profit has been assumed is checked once
    # the route is known, because that message is written per route.
    validate(user)

    fin = fetch_financials(ticker)
    name = fin.company_name

    # Banks and insurers first, before any DCF machinery is pointed at them.
    # Their statements do not answer the questions base_year_from and
    # derive_assumptions ask, so running those produces an upstream-looking
    # failure rather than the refusal this is.
    if uses_dividend_discount_model(fin):
        raise HypotheticalRefused(
            f"{name} is a bank or insurer and is valued with the dividend discount model. "
            "Unlevered free cash flow means nothing for it, whoever supplies the "
            "assumptions, so there is no hypothetical of this shape to build.")

    base, _ = base_year_from(fin)

    # Eligibility rests on the untouched standard assessment, judged by the
    # same whitelist the refusal screen uses to decide whether to offer the
    # door. One rule for the door and for what is behind it, so the opt-in
    # can never appear over a refusal this function then makes.
    standard = assess_suitability(fin, derive_assumptions(fin), base)
    if standard.suitable:
        raise HypotheticalRefused(
            f"{name.rstrip('.')} can be valued from its own figures, so there is nothing to "
            "build by hand. Adjust that valuation's assumptions instead.")

    codes = set(standard.codes)
    framing = hypothetical_framing_for(codes)
    require_a_profit(user, framing)

    if not codes or not codes <= HYPOTHETICAL_ELIGIBLE_CODES:
        raise HypotheticalRefused(
            f"{name} was refused a standard valuation for reasons your assumptions here do "
            "not reach. This screen offers three figures - growth, a target margin and how "
            "long it takes - and none of them answers why the model does not fit this "
            "company.",
            reasons=standard.reasons)

    # Two routes arrive here, and the ordering between the tiers has to hold
    # on both.
    #
    # A loss-maker is served by the speculative estimate first, so for one of
    # those the hypothetical is offered only where that estimate was itself
    # refused - otherwise this becomes a way of skipping a figure the model
    # was willing to produce. Tesla's route has no such tier: it is profitable
    # and refused for cash flow, so there is no earlier step to defer to.
    speculative = None
    why_speculative_refused: list[str] = []
    if codes & LOSS_REFUSAL_CODES:
        try:
            speculative = value_company_speculatively(ticker)
        except SpeculativeNotApplicable as e:
            raise HypotheticalRefused(
                f"{str(e).rstrip()} A hypothetical is offered only where a speculative "
                "estimate was, and was then refused for the shape of the company's "
                "trajectory.",
                reasons=e.reasons) from e
        if speculative.suitable:
            raise HypotheticalRefused(
                f"A speculative estimate is already produced for {name.rstrip('.')} from its "
                "own figures, so there is no need to build one by hand. Adjust that "
                "estimate's assumptions instead.")
        why_speculative_refused = speculative.suitability.reasons

    probe = (speculative.derived if speculative is not None
             else derive_speculative_assumptions(fin, base))
    blocks = _hypothetical_blocks(probe, name, fin.years_available)
    if blocks:
        raise HypotheticalRefused(
            f"No hypothetical is built for {name.rstrip('.')}, even on assumptions you supply "
            "yourself.",
            reasons=blocks)

    # Investment is the reader's too, and it is the input that used to be
    # taken quietly. The engine substituted a 5%-of-revenue
    # "mature business" rate for whatever the company actually spends - a
    # gift of 10 points of revenue a year to Tesla, 18 to Rivian - and for a
    # company refused FOR spending past its profits that single substitution
    # decided the answer.
    #
    # So it is set here, and where the reader has not moved it, it is set to
    # what the company spends TODAY. The engine already glides capex and D&A
    # from today's rate to this one over `years_to_profitability`, on the
    # same path as the margin, so "by the year it reaches that margin it is
    # spending this much" needs no new machinery.
    own_capex = probe.diagnostics.get("current_capex_pct")
    capex_pct = user.capex_pct if user.capex_pct is not None else own_capex
    overrides = {
        "speculative_revenue_growth": user.revenue_growth,
        "target_operating_margin": user.target_operating_margin,
        "years_to_profitability": user.years_to_target,
    }
    if capex_pct is not None:
        overrides["mature_capex_pct"] = capex_pct
    derived = derive_speculative_assumptions(fin, base, overrides=overrides)
    result = run_speculative(derived.inputs, derived.assumptions)

    # No value in it, on the route where the figures started as the
    # company's own.
    #
    # A negative number here is not information, it is the refusal restated
    # with a decimal point on it - and a reader who has changed nothing
    # would be looking at what reads like a price target for a company the
    # model has just declined to value. The two levers that move it are
    # named instead.
    #
    # The loss-maker's route keeps its negative figure, because there it IS
    # the finding: the losses on the way and the debt outweigh the
    # profitable business the reader has assumed at the end, and the screen
    # says so in those words.
    if (framing == FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE
            and result.value_per_share <= 0):
        spending = (f"{capex_pct:.1%} of revenue on investment"
                    if capex_pct is not None else "what it invests")
        raise HypotheticalRefused(
            f"On these assumptions there is no value to show. {short_of(name)} earning "
            f"{user.target_operating_margin:.1%} and spending {spending} does not "
            f"produce cash to put a value on - which is why the model would not value "
            f"it either. Raise the margin, or lower the investment, to see what would "
            f"have to be true.",
            code="hypothetical_incomplete")

    # What this path would take, in the same terms the speculative estimate
    # uses - the arithmetic does not care who chose the numbers.
    warnings: list[str] = []
    short = name.rstrip(".")
    burn = result.cumulative_cash_burn
    if burn > derived.inputs.cash:
        warnings.append(
            f"On your assumptions {short} burns ${burn / 1e3:,.1f}bn of cash before turning "
            f"profitable, against ${derived.inputs.cash / 1e3:,.1f}bn of cash and investments "
            "today. The rest would have to be raised, most likely by issuing shares that "
            "dilute existing holders.")
    elif burn > 0:
        warnings.append(
            f"On your assumptions {short} burns ${burn / 1e3:,.1f}bn of cash before turning "
            f"profitable, covered by today's ${derived.inputs.cash / 1e3:,.1f}bn only if "
            "nothing goes worse than you have assumed.")

    share = result.share_from_after_profitability
    if share is not None and share >= 1:
        warnings.append(
            "Every dollar of this figure comes from after your assumed turn to profit. The "
            "years before it subtract value, so the figure is a statement about a future you "
            "have described, and nothing else.")
    elif share is not None and share > HIGH_TV_SHARE:
        warnings.append(
            f"{share:.0%} of this figure comes from after your assumed turn to profit, so it "
            "rests almost entirely on a future you have described.")

    gross = derived.diagnostics["gross_margin"]
    if gross is not None and gross < user.target_operating_margin:
        warnings.append(
            f"{short}'s gross margin was {gross:.0%} - below the "
            f"{user.target_operating_margin:.0%} operating margin you have assumed. Operating "
            "profit comes out of gross profit, so this needs the product economics to change, "
            "not just costs to be cut.")

    if result.value_per_share <= 0:
        warnings.append(
            "Even on your own assumptions, the losses on the way and the debt outweigh the "
            "profitable business at the end, so the figure is negative.")

    return HypotheticalReport(
        ticker=fin.ticker,
        company_name=name,
        sector=fin.sector,
        industry=fin.industry,
        exchange=fin.exchange,
        fiscal_year=derived.diagnostics["fiscal_year"],
        user=user,
        derived=derived,
        result=result,
        why_standard_refused=standard.reasons,
        why_speculative_refused=why_speculative_refused,
        reality=_reality_contrast(user, derived, name),
        warnings=warnings,
        standard_codes=list(standard.codes),
        capex_pct=derived.assumptions.mature_capex_pct,
    )


def hypothetical_disclaimer(report: HypotheticalReport) -> str:
    """One sentence, in normal case.

    This used to be a paragraph opening in capitals, and it was read by
    nobody. It sat under three bullets that already said the same thing, in
    fewer words and in the second person, and the screen's whole top half
    was a wall of red shouting - which is how a warning stops being a
    warning. Loudness is not honesty; the bullets and the headline carry the
    force, and this says the one thing they do not.

    Written per route. A loss-maker's own figures point away from the profit
    being assumed, and that is worth a clause. A profitable company's do
    not, and saying so would be false.
    """
    u, short = report.user, short_of(report.company_name)
    if report.framing == FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE:
        return (
            f"These are your assumptions about {short}, not its results: on "
            f"{signed_pct(u.revenue_growth)} growth, a "
            f"{u.target_operating_margin:.1%} margin and "
            f"{report.capex_pct:.1%} of revenue invested, this is the "
            f"arithmetic - not a valuation, and not a price target.")

    actual = report.derived.diagnostics.get("operating_margin")
    actually = ("" if actual is None
                else f", which actually earned {actual:.1%} last year")
    return (
        f"These are your assumptions about {short}{actually}, not its "
        f"results: on {signed_pct(u.revenue_growth)} growth reaching a "
        f"{u.target_operating_margin:.1%} margin within "
        f"{_count(u.years_to_target, 'year')}, this is the arithmetic - not "
        f"a valuation, and not a price target.")


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
        rule = (f"Companies commonly watched alongside {intrinsic.ticker} that are "
                f"in the same industry ({industry or 'unknown'}), up to "
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
                    f"in the same industry ({industry}), and among the companies "
                    f"commonly watched alongside {intrinsic.ticker}",
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
            excluded.append(ExcludedPeer(candidate, None, "not found with the market data provider"))
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


def fx_conversion_clause(report) -> str | None:
    """The honesty note's exchange-rate sentence, or None for a US filer.

    A converted company has one more thing the figure rests on, and it is
    not an assumption the user can move: the exchange rate. Naming it puts
    it alongside the WACC and the equity risk premium, which is where it
    belongs - the figure moves with it the same way.

    Separated out, and sent to the app as a finished sentence, because
    nothing in it recomputes. The currency pair, the rate, its date and its
    basis are fixed for a company; the app rebuilds the rest of the note
    from local arithmetic as a slider moves, and this part has no arithmetic
    to redo. A second copy of the wording in Dart would be a copy that can
    drift with nothing to gain - which is how the sentence came to be
    missing from the app's note in the first place.

    The basis is named rather than summarised. Where the rate comes from the
    ECB and the pair does not involve the euro, the figure is a cross-rate
    this model computed from two published quotes - not something the ECB
    published - and the licence that makes the data free requires that
    modification to be stated wherever it appears.
    """
    meta = getattr(report, "meta", None) or {}
    fx = meta.get("fx")
    if not fx or not fx.get("pair"):
        return None
    name = report.company_name.rstrip(".")
    basis = fx.get("basis") or "a single day's rate"
    return (
        f" {name}'s figures are reported in "
        f"{meta.get('reporting_currency')} and have been converted "
        f"to {meta.get('currency')} at "
        f"{fx['rate']:.6g} ({fx['pair']}, {fx['as_of']}, {basis}), so the "
        "figure moves with that rate as well. It is one day's rate, and "
        "the discount rate is built from a US risk-free rate, so this "
        "assumes no sustained drift between the two currencies."
    )


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

    fx_text = fx_conversion_clause(report) or ""

    return (
        f"${report.result.intrinsic_value_per_share:,.2f} is the output of these assumptions, "
        f"not a fact about {name}. It assumes a WACC of "
        f"{report.derived.assumptions.wacc:.2%}{erp_text}, and "
        f"{report.derived.assumptions.terminal_growth:.2%} growth in perpetuity. The "
        "equity risk premium is Damodaran's implied premium, computed from what the "
        f"S&P 500 costs today against the cash flows it is expected to produce "
        f"({DAMODARAN_ERP_AS_OF:%B %Y}); a different but defensible premium moves the "
        "figure materially, and the sensitivity table shows by how much."
        # Said without branching on whether the cap bound, so the Dart copy
        # of this sentence can be produced from the same two numbers. It is
        # true either way: when the cap binds, the figure tracks WACC, which
        # is itself built on the same risk-free rate.
        + " The growth in perpetuity defaults to the lowest of the "
        "risk-free rate, long-run US nominal growth and the discount rate "
        "less three points, and that is as high as it can be set: it can be "
        "moved down but not up."
        + fx_text
    )


def _global_assumptions_block(report: "ValuationReport") -> list[str]:
    """
    Surface the global assumptions and state plainly that the headline number
    depends on them.

    An intrinsic value printed without this reads as a measurement. It is not
    one: it is the output of assumptions a reasonable person could set
    differently. The equity risk premium is the clearest case: this model
    uses Damodaran's implied premium, computed from the index's own price,
    where a long-run historical average would be more than a point higher
    and equally defensible - a difference worth a fifth of the answer on a
    high-beta company.
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
