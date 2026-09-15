"""
FastAPI web layer for the DCF valuation service.

This module is a thin wrapper. It performs no valuation arithmetic and makes
no modelling decisions - it calls analysis.value_company() and serialises the
ValuationReport it gets back. Every number in a response originates in the
engine or the derivation layer.

OUTCOME -> STATUS MAPPING
-------------------------
    200  a valuation was produced; `method` says which model ("dcf" or "ddm")
    404  ticker is malformed, unknown, or has no usable statements
    422  a valuation was refused: the company fails the suitability guard
    400  the caller supplied an unknown or non-numeric assumption override
    429  this client exceeded the request limit, or Yahoo is throttling us
    502  any other upstream failure (network, malformed data)

The two 429s are distinguishable by `code`: "rate_limited" is ours,
"upstream_rate_limited" is Yahoo's.

SPECULATIVE ESTIMATES - OPT-IN ONLY
-----------------------------------
GET /valuation/{ticker}/speculative and POST /valuation/speculative produce a
path-to-profitability estimate for a company the standard valuation refuses
as loss-making. They are the ONLY way to get one: /valuation never returns a
speculative figure, and its override schema does not accept speculative
assumption names. A speculative response carries method "speculative",
is_valuation false, and a disclaimer that must be displayed before the figure,
which is named speculative_value_per_share - never intrinsic_value_per_share.

    200  a speculative estimate was produced
    422  code "speculative_not_applicable": not offered for this company, because
         a standard valuation applies or it was refused for other reasons
    422  code "not_suitable", method "speculative": even a path to
         profitability does not apply (no revenue, shrinking, and so on)

RELATIVE VALUATION - A SECOND OPINION, ON ITS OWN ENDPOINTS
-----------------------------------------------------------
GET /valuation/{ticker}/relative and POST /valuation/relative return the
company's intrinsic valuation summary unchanged, beside a separate
relative_valuation block: how the market prices similar companies right now,
from peer-group multiples. The block is marked method "relative", basis
"market" and is_intrinsic_valuation false, and names every peer. /valuation
never includes it.

    200  a relative figure was produced beside the intrinsic value
    422  code "relative_not_applicable": the intrinsic valuation refused the
         company, so there is nothing for a second opinion to sit beside
    422  code "not_suitable", method "relative": too few comparable peers, or
         no multiple that can be applied; the intrinsic summary is included

Every error body carries a machine-readable `code` alongside the human
message, so clients never have to parse prose - including for 422, which
FastAPI also uses for its own request-validation errors (those carry
code "validation_error", the suitability refusal carries "not_suitable").

CREDENTIALS
-----------
There are none. Market data comes from Yahoo Finance, which needs no key, so
there is no secret to leak, misconfigure, or rotate.
"""

import os
import threading
import time
from collections import defaultdict, deque
from typing import Annotated, Any, Literal, Union

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from analysis import (GLOBAL_LEVERS, RELATIVE_FRAMING, SPECULATIVE_HEADLINE,
                      DDMReport, RelativeNotApplicable, RelativeReport,
                      SpeculativeNotApplicable, SpeculativeReport, ValuationReport,
                      honesty_note, relative_comparison, relative_note,
                      speculative_disclaimer, speculative_estimate_available,
                      value_company, value_company_relatively,
                      value_company_speculatively)
from relative import MAX_PEERS
from speculative_assumptions import DISCOUNT_INPUT_NAMES, SPECULATIVE_ASSUMPTION_NAMES
from ddm_assumptions import COST_OF_EQUITY_INPUT_NAMES, DDM_ASSUMPTION_NAMES
from excel_export import filename_for, workbook_bytes
from market_data import (DataUnavailableError, MarketDataError, RateLimitedError,
                         TickerNotFoundError, UnsupportedListingError,
                         data_source_name, requires_api_key, search_companies)

# Mirrors the names derive_assumptions() accepts. Listing them explicitly gives
# /docs a usable schema; derive_assumptions still validates independently, so a
# drift between the two surfaces as a 400 rather than a wrong answer.
ASSUMPTION_OVERRIDES = (
    "revenue_growth", "operating_margin", "tax_rate", "da_pct", "capex_pct",
    "nwc_pct", "wacc", "terminal_growth", "projection_years",
)
WACC_INPUT_OVERRIDES = ("risk_free_rate", "equity_risk_premium", "beta", "cost_of_debt")
ALL_OVERRIDES = ASSUMPTION_OVERRIDES + WACC_INPUT_OVERRIDES

# The dividend discount model's own overridable names. terminal_growth and the
# CAPM inputs are shared with the DCF; the rest apply to the DDM alone.
DDM_OVERRIDES = DDM_ASSUMPTION_NAMES
COST_OF_EQUITY_INPUT_OVERRIDES = COST_OF_EQUITY_INPUT_NAMES

# Used as the literal, because Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY
# to HTTP_422_UNPROCESSABLE_CONTENT and referencing either by name ties this
# module to a particular Starlette version.
HTTP_422_NOT_SUITABLE = 422


app = FastAPI(
    title="DCF Valuation API",
    version="1.0.0",
    description=(
        "Discounted cash flow valuations for listed companies, with every "
        "assumption derived from the company's own filings and labelled with "
        "its provenance.\n\n"
        "**Values returned are the output of adjustable assumptions, not "
        "measurements.** Every successful response carries a `note` field "
        "saying so, and a sensitivity table showing how far the figure moves "
        "across a plausible range of inputs.\n\n"
        "Companies for which a growth-perpetuity DCF is inappropriate - "
        "loss-making businesses, banks and insurers - are refused with HTTP "
        "422 rather than given a misleading number."
    ),
)


# ---------------------------------------------------------------------------
# Rate limiting
#
# A single free Render instance serving a public app: one client looping a
# request can exhaust the instance and, worse, burn the shared Yahoo quota
# that every other user depends on. This caps each client to a sustainable
# rate and says so plainly when it trips.
#
# IN-MEMORY AND PER-PROCESS, BY DESIGN. The counters live in this process's
# memory, so **they reset on every restart or redeploy**, and a second
# instance would count separately. That is the right trade for one free
# instance - no Redis, no dependency, nothing to provision - but it is not a
# quota anyone should rely on for billing or abuse prevention.
# ---------------------------------------------------------------------------

RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60

# Search is counted in its own bucket, and more generously. It fires as the
# user types, so sharing the valuation budget would let someone looking up two
# company names lock themselves out of valuing the one they found - the search
# would be spending the budget meant for the thing it exists to lead to.
#
# It is cheaper to serve, too: each query is one crumb-free call, cached for an
# hour, where a valuation is several statement fetches. The app debounces and
# caches on its side as well, so a person typing at a normal pace comes
# nowhere near this; it exists to stop a runaway client, not to ration people.
SEARCH_PATH = "/search"
SEARCH_RATE_LIMIT_REQUESTS = 60

# Paths that must never be throttled. /health is the app's wake-up ping and
# Render's own health check: throttling it would let a burst of user traffic
# convince Render the service is down and restart it mid-request.
_RATE_LIMIT_EXEMPT = ("/health", "/docs", "/redoc", "/openapi.json")

_rate_lock = threading.Lock()
_rate_history: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limits() -> None:
    """
    Forget every recorded request.

    Exists for tests: the history is process-global, so without this one test
    making a few dozen requests would throttle whichever test ran next and
    the failure would look like anything but a rate limit.
    """
    with _rate_lock:
        _rate_history.clear()


def _client_key(request: Request) -> str:
    """
    Identify the caller.

    Render terminates TLS at its proxy, so `request.client.host` is the
    proxy's address and would put every user in one bucket. The real client
    is the first entry of X-Forwarded-For.

    That header is client-supplied and therefore spoofable, which would let a
    determined caller evade this limit. Acceptable here: the aim is to stop
    accidental hammering and casual abuse from taking the instance down, not
    to be a security control.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(key: str, now: float, limit: int = RATE_LIMIT_REQUESTS) -> bool:
    """Record a request for *key*, returning True if it exceeds *limit*."""
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        history = _rate_history[key]
        while history and history[0] <= cutoff:
            history.popleft()

        if len(history) >= limit:
            return True

        history.append(now)

        # Stop the dict growing without bound as addresses come and go. Only
        # runs when the map is already large, so the usual path stays cheap.
        if len(_rate_history) > 2048:
            for stale in [k for k, v in _rate_history.items() if not v]:
                del _rate_history[stale]

        return False


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    if request.url.path in _RATE_LIMIT_EXEMPT:
        return await call_next(request)

    client = _client_key(request)
    if request.url.path == SEARCH_PATH:
        key, limit = f"search:{client}", SEARCH_RATE_LIMIT_REQUESTS
    else:
        key, limit = client, RATE_LIMIT_REQUESTS

    if _rate_limited(key, time.monotonic(), limit):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "code": "rate_limited",
                "message": (
                    f"Too many requests - please slow down. This service "
                    f"allows about {limit} requests a minute "
                    f"per user. Wait a moment and try again."
                ),
            },
            # Tells a well-behaved client exactly how long to wait, instead of
            # leaving it to guess or retry straight into the same wall.
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class Overrides(BaseModel):
    """
    Assumption overrides. Any field left null keeps the derived or default
    value. Rates are decimals: 0.09 means 9%, not 9.
    """
    model_config = {"extra": "forbid"}

    revenue_growth: float | None = Field(None, description="Annual revenue growth, e.g. 0.06")
    operating_margin: float | None = Field(None, description="EBIT as a share of revenue")
    tax_rate: float | None = Field(None, description="Effective tax rate on EBIT")
    da_pct: float | None = Field(None, description="D&A as a share of revenue")
    capex_pct: float | None = Field(None, description="Capex as a share of revenue")
    nwc_pct: float | None = Field(None, description="Change in NWC as a share of revenue growth")
    wacc: float | None = Field(None, description="Discount rate; overrides the CAPM build-up")
    terminal_growth: float | None = Field(None, description="Perpetual growth rate; must be below WACC")
    projection_years: int | None = Field(None, ge=1, le=20, description="Explicit forecast horizon")

    risk_free_rate: float | None = Field(None, description="Overrides the live Treasury yield")
    equity_risk_premium: float | None = Field(None, description="Excess return demanded over risk-free")
    beta: float | None = Field(None, description="Overrides the company's reported beta")
    cost_of_debt: float | None = Field(None, description="Pre-tax cost of debt")

    # Dividend discount model only. Sent for a company valued with a DCF, or
    # a DCF-only name sent for a DDM company, each returns a 400 naming the
    # method that name belongs to.
    dividend_growth: float | None = Field(
        None, description="DDM only: annual dividend growth during stage one")
    high_growth_years: int | None = Field(
        None, ge=0, le=30, description="DDM only: length of stage one, in years")
    cost_of_equity: float | None = Field(
        None, description="DDM only: discount rate; overrides the CAPM build-up")

    def as_dict(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ValuationRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12, examples=["AAPL"])
    overrides: Overrides = Field(default_factory=Overrides)


class AssumptionOut(BaseModel):
    name: str
    value: float
    source: str = Field(..., description="derived | derived (clamped) | default | override")
    detail: str
    is_percent: bool = Field(..., description="True if value should render as a percentage")


class SensitivityOut(BaseModel):
    waccs: list[float]
    growth_rates: list[float]
    grid: list[list[float | None]] = Field(
        ..., description="[wacc_row][growth_col]; null where growth >= WACC"
    )
    centre_wacc: float
    centre_terminal_growth: float
    centre_row: int
    centre_col: int


class ProjectionYearOut(BaseModel):
    period: int
    revenue: float
    ebit: float
    nopat: float
    da: float
    capex: float
    delta_nwc: float
    ufcf: float
    discount_factor: float
    pv_ufcf: float


class BaseYearOut(BaseModel):
    revenue: float
    total_debt: float
    cash: float
    shares: float
    current_price: float
    fiscal_year: str


class ValuationOut(BaseModel):
    pv_ufcf_sum: float
    terminal_value: float
    pv_terminal_value: float
    enterprise_value: float
    equity_value: float
    tv_pct_of_ev: float


class CompanyOut(BaseModel):
    ticker: str
    company_name: str
    sector: str
    exchange: str


class ValuationResponse(BaseModel):
    method: Literal["dcf"] = Field(
        "dcf", description="Which method produced the value: discounted cash flow")
    company: CompanyOut
    suitable: bool = True
    intrinsic_value_per_share: float
    current_price: float
    upside_downside: float = Field(..., description="Fraction, e.g. -0.674 for -67.4%")
    note: str = Field(..., description="Why this figure is not a measurement. Display it.")
    base_year: BaseYearOut
    valuation: ValuationOut
    assumptions: list[AssumptionOut]
    global_levers: list[AssumptionOut] = Field(
        ..., description="The judgement-call inputs users will most want to adjust"
    )
    wacc_build_up: dict[str, Any]
    wacc_inputs: list[AssumptionOut] = Field(
        ...,
        description="Where each WACC input came from - notably whether beta "
                    "was computed from price history, quoted by Yahoo, or "
                    "defaulted.",
    )
    sensitivity: SensitivityOut
    projection: list[ProjectionYearOut]
    warnings: list[str]


class NotSuitableResponse(BaseModel):
    code: str = "not_suitable"
    method: str = Field(
        "dcf", description="The last method assessed: 'dcf', or 'ddm' for a "
                           "financial company the dividend discount model "
                           "also could not value")
    company: CompanyOut
    suitable: bool = False
    intrinsic_value_per_share: None = Field(
        None, description="Always null. No figure is produced for these companies."
    )
    message: str
    reasons: list[str]
    assumptions: list[AssumptionOut]
    speculative_estimate_available: bool = Field(
        False,
        description="True when an opt-in speculative estimate can be asked for at "
                    "/valuation/{ticker}/speculative - the company was refused only for losing "
                    "money. Never an endorsement of that estimate, only that one is available.")


class AnnualDividendOut(BaseModel):
    year: int
    dividend_per_share: float


class SpecialDividendOut(BaseModel):
    date: str
    amount: float


class VariableDividendOut(BaseModel):
    date: str
    amount_paid: float = Field(..., description="As reported, which may include a regular payment")
    variable_portion: float = Field(..., description="The part counted as variable dividend")


class DividendBaseOut(BaseModel):
    current_annual_dividend: float = Field(
        ..., description="D0: the indicated annual dividend per share")
    detail: str
    last_regular_payment: float
    last_regular_payment_date: str
    payments_per_year: int
    trailing_twelve_months: float = Field(
        ..., description="Dividends the model counts (regular, plus any recurring "
                         "variable dividend) paid in the last 365 days, for reference")
    dividend_yield: float = Field(..., description="current_annual_dividend / current_price")
    payout_ratio: float | None = Field(
        None, description="Median dividends paid / net income to common, recent years")
    return_on_equity: float | None = Field(
        None, description="Median net income to common / average common equity")
    sustainable_growth: float | None = Field(
        None, description="ROE x (1 - payout): the dividend growth retained earnings can fund")
    annual_history: list[AnnualDividendOut] = Field(
        ..., description="Regular dividend per share by complete calendar year")
    special_dividends_excluded: list[SpecialDividendOut] = Field(
        ..., description="One-off specials that do not recur annually; not counted")
    regular_annual_dividend: float | None = Field(
        None, description="Indicated regular rate: last regular payment x frequency")
    variable_annual_dividend: float | None = Field(
        None, description="Multi-year average of a recurring variable dividend, when "
                          "there is one; included in current_annual_dividend")
    variable_average_years: int | None = None
    variable_dividends_included: list[VariableDividendOut] = Field(
        default_factory=list,
        description="The recurring variable payments averaged into the dividend")


class DividendYearOut(BaseModel):
    period: int
    dividend: float
    discount_factor: float
    pv_dividend: float


class DDMValuationOut(BaseModel):
    pv_dividends_sum: float
    terminal_value: float
    pv_terminal_value: float
    tv_pct_of_value: float


class DDMSensitivityOut(BaseModel):
    costs_of_equity: list[float]
    growth_rates: list[float]
    grid: list[list[float | None]] = Field(
        ..., description="[cost_of_equity_row][growth_col]; null where growth >= cost of equity"
    )
    centre_cost_of_equity: float
    centre_terminal_growth: float
    centre_row: int
    centre_col: int


class DDMValuationResponse(BaseModel):
    method: Literal["ddm"] = Field(
        "ddm", description="Which method produced the value: dividend discount model")
    company: CompanyOut
    suitable: bool = True
    intrinsic_value_per_share: float
    current_price: float
    upside_downside: float = Field(..., description="Fraction, e.g. -0.12 for -12%")
    note: str = Field(..., description="Why this figure is not a measurement. Display it.")
    why_not_dcf: str = Field(..., description="Why a DCF was not used for this company")
    dividend_base: DividendBaseOut
    valuation: DDMValuationOut
    assumptions: list[AssumptionOut]
    global_levers: list[AssumptionOut] = Field(
        ..., description="The judgement-call inputs users will most want to adjust"
    )
    cost_of_equity_build_up: dict[str, Any]
    cost_of_equity_inputs: list[AssumptionOut] = Field(
        ..., description="Where each CAPM input came from"
    )
    sensitivity: DDMSensitivityOut
    projection: list[DividendYearOut]
    warnings: list[str]


# A successful valuation from either method, told apart by `method`. A client
# reads `method` first and parses the rest accordingly.
ValuationResult = Annotated[
    Union[ValuationResponse, DDMValuationResponse],
    Field(discriminator="method"),
]


class SpeculativeOverrides(BaseModel):
    """
    Assumptions behind a speculative estimate. Any field left null keeps its
    derived or default value. Rates are decimals: 0.10 means 10%.
    """
    model_config = {"extra": "forbid"}

    years_to_profitability: int | None = Field(
        None, ge=1, le=15, description="Years until the target operating margin is reached")
    target_operating_margin: float | None = Field(
        None, description="Operating margin reached at profitability, e.g. 0.10")
    speculative_revenue_growth: float | None = Field(
        None, description="Annual revenue growth until profitability")
    post_profitability_growth: float | None = Field(
        None, description="Revenue growth in the DCF forecast after profitability")
    mature_capex_pct: float | None = Field(
        None, description="Capex (and depreciation) as a share of revenue once mature")
    mature_nwc_pct: float | None = Field(
        None, description="Working-capital investment as a share of revenue growth once mature")
    tax_rate: float | None = Field(None, description="Tax rate on future profits")
    wacc: float | None = Field(None, description="Discount rate; overrides the build-up and its floor")
    terminal_growth: float | None = Field(None, description="Perpetual growth; must be below WACC")
    projection_years: int | None = Field(
        None, ge=1, le=20, description="Years of DCF forecast after profitability")

    risk_free_rate: float | None = Field(None, description="Overrides the live Treasury yield")
    equity_risk_premium: float | None = Field(None, description="Excess return demanded over risk-free")
    beta: float | None = Field(None, description="Overrides the company's computed beta")
    cost_of_debt: float | None = Field(None, description="Pre-tax cost of debt")

    def as_dict(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class SpeculativeRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12, examples=["RIVN"])
    overrides: SpeculativeOverrides = Field(default_factory=SpeculativeOverrides)


class RevenueHistoryOut(BaseModel):
    fiscal_year: str
    revenue: float = Field(..., description="$mm")
    operating_margin: float | None
    gross_margin: float | None


class SpeculativeStartOut(BaseModel):
    fiscal_year: str
    revenue: float = Field(..., description="$mm, latest fiscal year")
    operating_margin: float | None
    gross_margin: float | None
    latest_revenue_growth: float | None
    revenue_cagr: float | None
    cash: float = Field(..., description="$mm, cash and investments")
    total_debt: float = Field(..., description="$mm")
    shares: float = Field(..., description="millions, diluted")
    history: list[RevenueHistoryOut]


class PathYearOut(BaseModel):
    period: int
    revenue: float
    operating_margin: float
    ebit: float
    ufcf: float
    discount_factor: float
    pv_ufcf: float


class SpeculativeValuationOut(BaseModel):
    pv_path_sum: float = Field(..., description="PV of cash flows until profitability; usually negative")
    cumulative_cash_burn: float = Field(..., description="$mm of negative cash flow on the path")
    revenue_at_profitability: float
    value_at_profitability: float = Field(..., description="DCF enterprise value at the profitability year")
    pv_value_at_profitability: float
    enterprise_value: float
    equity_value: float
    share_from_after_profitability: float | None = Field(
        None, description="Share of the estimate from after profitability; above 1 when the path subtracts value")


class SpeculativeSensitivityOut(BaseModel):
    target_operating_margins: list[float]
    years_to_profitability: list[int]
    grid: list[list[float | None]] = Field(
        ..., description="[margin_row][years_col]; null where the margin is not a profit")
    centre_row: int
    centre_col: int


class SpeculativeValuationResponse(BaseModel):
    method: Literal["speculative"] = Field(
        "speculative", description="A speculative path-to-profitability estimate")
    is_valuation: Literal[False] = Field(False, description="Always false. This is not a valuation.")
    disclaimer_headline: str
    disclaimer: str = Field(
        ..., description="Must be displayed prominently, before the figure. Not optional.")
    company: CompanyOut
    speculative_value_per_share: float = Field(
        ..., description="Deliberately not named intrinsic_value_per_share: it is not one")
    current_price: float
    estimate_vs_price: float
    why_standard_valuation_refused: list[str]
    starting_point: SpeculativeStartOut
    path_to_profitability: list[PathYearOut]
    valuation: SpeculativeValuationOut
    assumptions: list[AssumptionOut]
    wacc_inputs: list[AssumptionOut]
    sensitivity: SpeculativeSensitivityOut
    warnings: list[str]


class RelativeRequest(BaseModel):
    """A relative valuation with the peer group edited or replaced."""
    model_config = {"extra": "forbid"}

    ticker: str = Field(..., min_length=1, max_length=12, examples=["JPM"])
    peers: list[str] | None = Field(
        None, max_length=MAX_PEERS,
        description="Replace the automatic peer group entirely with these tickers")
    add_peers: list[str] = Field(
        default_factory=list, max_length=MAX_PEERS,
        description="Add these tickers to the automatic selection")
    remove_peers: list[str] = Field(
        default_factory=list, max_length=MAX_PEERS,
        description="Remove these tickers from the selection")


class PeerOut(BaseModel):
    ticker: str
    name: str | None
    industry: str | None
    provenance: str = Field(..., description="selected | chosen by you | added by you")
    detail: str


class ExcludedPeerOut(BaseModel):
    ticker: str
    name: str | None
    reason: str


class PeerSelectionOut(BaseModel):
    mode: str = Field(..., description="automatic | your list | automatic, edited by you")
    rule: str
    peers: list[PeerOut]
    excluded: list[ExcludedPeerOut]


class PeerMultipleOut(BaseModel):
    ticker: str
    value: float | None
    excluded_reason: str | None


class MultipleOut(BaseModel):
    name: str
    label: str
    applicable: bool
    company_value: float | None
    company_reason: str | None = Field(None, description="Why the multiple is not meaningful for the company")
    peer_median: float | None
    peer_count: int
    premium_to_median: float | None = Field(None, description="Company multiple / peer median - 1")
    implied_value_per_share: float | None
    reason: str | None = Field(None, description="Why the multiple was not applied")
    peers: list[PeerMultipleOut]


class MarketFiguresOut(BaseModel):
    as_of: str | None
    trading_currency: str | None
    reporting_currency: str | None
    price: float | None
    market_cap: float | None
    enterprise_value: float | None
    revenue: float | None
    ebitda: float | None
    net_income: float | None
    total_debt: float | None
    cash: float | None
    shares: float | None
    common_equity: float | None


class IntrinsicSummaryOut(BaseModel):
    method: Literal["dcf", "ddm"]
    intrinsic_value_per_share: float
    current_price: float
    upside_downside: float
    note: str
    full_valuation: str = Field(..., description="Where the complete intrinsic valuation lives")


class RelativeValueOut(BaseModel):
    central: float = Field(..., description="Median of the values implied by the multiples applied")
    low: float
    high: float
    multiples_applied: list[str]


class RelativeComparisonOut(BaseModel):
    intrinsic_method: str
    intrinsic_value_per_share: float
    relative_value_per_share: float
    gap: float | None = Field(None, description="Relative / intrinsic - 1")
    statement: str


class RelativeBlockOut(BaseModel):
    method: Literal["relative"] = Field("relative", description="Peer-group multiples")
    basis: Literal["market"] = Field("market", description="How the market prices peers, not cash flows")
    is_intrinsic_valuation: Literal[False] = Field(
        False, description="Always false. This is a market-based second opinion.")
    framing: str
    note: str = Field(..., description="Display it with the figure.")
    peer_selection: PeerSelectionOut
    company_figures: MarketFiguresOut
    multiples: list[MultipleOut]
    relative_value_per_share: RelativeValueOut
    current_price: float
    relative_value_vs_price: float
    comparison_with_intrinsic: RelativeComparisonOut
    warnings: list[str]


class RelativeValuationResponse(BaseModel):
    company: CompanyOut
    intrinsic_valuation: IntrinsicSummaryOut
    relative_valuation: RelativeBlockOut


class RelativeNotSuitableResponse(BaseModel):
    code: str = "not_suitable"
    method: str = "relative"
    company: CompanyOut
    message: str
    reasons: list[str]
    peer_selection: PeerSelectionOut
    intrinsic_valuation: IntrinsicSummaryOut
    company_figures: MarketFiguresOut | None = Field(
        None, description="Present when the comparison got far enough to compute figures")
    multiples: list[MultipleOut] = Field(
        default_factory=list,
        description="The comparison as far as it could be taken, as information only. Implied "
                    "values are withheld: too few multiples applied to stand behind one.")


class ErrorResponse(BaseModel):
    code: str
    message: str


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

PERCENT_NAMES = {
    "revenue_growth", "operating_margin", "tax_rate", "da_pct", "capex_pct",
    "nwc_pct", "wacc", "terminal_growth", "risk_free_rate",
    "equity_risk_premium", "cost_of_debt", "dividend_growth", "cost_of_equity",
    "target_operating_margin", "speculative_revenue_growth",
    "post_profitability_growth", "mature_capex_pct", "mature_nwc_pct",
}


def _assumption_list(store: dict) -> list[AssumptionOut]:
    return [
        AssumptionOut(name=name, value=prov.value, source=prov.source,
                      detail=prov.detail, is_percent=name in PERCENT_NAMES)
        for name, prov in store.items()
    ]


def _company_out(report: ValuationReport) -> CompanyOut:
    return CompanyOut(ticker=report.ticker, company_name=report.company_name,
                      sector=report.sector, exchange=report.exchange)


def _serialise(report: ValuationReport) -> ValuationResponse:
    r = report.result
    combined = {**report.derived.provenance, **report.derived.wacc_inputs}
    levers = {name: combined[name] for name in GLOBAL_LEVERS if name in combined}

    centre = len(r.sensitivity_waccs) // 2

    return ValuationResponse(
        company=_company_out(report),
        intrinsic_value_per_share=r.intrinsic_value_per_share,
        current_price=r.current_price,
        upside_downside=r.upside_downside,
        note=honesty_note(report),
        base_year=BaseYearOut(
            revenue=report.base.revenue,
            total_debt=report.base.total_debt,
            cash=report.base.cash,
            shares=report.base.shares,
            current_price=report.base.current_price,
            fiscal_year=report.fiscal_year,
        ),
        valuation=ValuationOut(
            pv_ufcf_sum=r.pv_ufcf_sum,
            terminal_value=r.terminal_value,
            pv_terminal_value=r.pv_terminal_value,
            enterprise_value=r.enterprise_value,
            equity_value=r.equity_value,
            tv_pct_of_ev=r.tv_pct_of_ev,
        ),
        assumptions=_assumption_list(report.derived.provenance),
        global_levers=_assumption_list(levers),
        wacc_build_up=report.derived.diagnostics.get("wacc_components", {}),
        wacc_inputs=_assumption_list(report.derived.wacc_inputs),
        sensitivity=SensitivityOut(
            waccs=r.sensitivity_waccs,
            growth_rates=r.sensitivity_gs,
            grid=r.sensitivity,
            # Taken from the grid axes rather than from the assumptions, so
            # that waccs[centre_row] == centre_wacc holds exactly and a client
            # can locate the centre cell by comparison. The engine rounds its
            # axes, so the assumption's full-precision value could differ in
            # the last bits. The exact figure used is still available as the
            # `wacc` entry in `assumptions`.
            centre_wacc=r.sensitivity_waccs[centre],
            centre_terminal_growth=r.sensitivity_gs[centre],
            centre_row=centre,
            centre_col=centre,
        ),
        projection=[
            ProjectionYearOut(
                period=y.period, revenue=y.revenue, ebit=y.ebit, nopat=y.nopat,
                da=y.da, capex=y.capex, delta_nwc=y.delta_nwc, ufcf=y.ufcf,
                discount_factor=y.discount_factor, pv_ufcf=y.pv_ufcf,
            )
            for y in r.years
        ],
        warnings=report.suitability.warnings,
    )


def _serialise_ddm(report: DDMReport) -> DDMValuationResponse:
    r = report.result
    d = report.dividends
    diagnostics = report.derived.diagnostics
    combined = {**report.derived.provenance, **report.derived.capm_inputs}
    levers = {name: combined[name] for name in GLOBAL_LEVERS if name in combined}
    centre = len(r.sensitivity_costs_of_equity) // 2

    return DDMValuationResponse(
        company=_company_out(report),
        intrinsic_value_per_share=r.intrinsic_value_per_share,
        current_price=r.current_price,
        upside_downside=r.upside_downside,
        note=honesty_note(report),
        why_not_dcf=report.why_not_dcf,
        dividend_base=DividendBaseOut(
            current_annual_dividend=report.inputs.current_dividend,
            detail=diagnostics.get("current_dividend_detail", ""),
            last_regular_payment=d.last_regular_amount,
            last_regular_payment_date=d.last_regular_date.isoformat(),
            payments_per_year=d.payments_per_year,
            trailing_twelve_months=d.trailing_twelve_months,
            dividend_yield=report.inputs.current_dividend / r.current_price,
            payout_ratio=diagnostics.get("payout_ratio"),
            return_on_equity=diagnostics.get("return_on_equity"),
            sustainable_growth=diagnostics.get("sustainable_growth"),
            annual_history=[AnnualDividendOut(year=y, dividend_per_share=v)
                            for y, v in d.annual_history],
            special_dividends_excluded=[SpecialDividendOut(date=paid.isoformat(), amount=amount)
                                        for paid, amount in d.specials],
            regular_annual_dividend=d.regular_dividend,
            variable_annual_dividend=d.variable_annual_dividend,
            variable_average_years=d.variable_average_years,
            variable_dividends_included=[
                VariableDividendOut(date=paid.isoformat(), amount_paid=gross, variable_portion=net)
                for paid, gross, net in d.variable_dividends],
        ),
        valuation=DDMValuationOut(
            pv_dividends_sum=r.pv_dividends_sum,
            terminal_value=r.terminal_value,
            pv_terminal_value=r.pv_terminal_value,
            tv_pct_of_value=r.tv_pct_of_value,
        ),
        assumptions=_assumption_list(report.derived.provenance),
        global_levers=_assumption_list(levers),
        cost_of_equity_build_up=diagnostics.get("capm", {}),
        cost_of_equity_inputs=_assumption_list(report.derived.capm_inputs),
        sensitivity=DDMSensitivityOut(
            costs_of_equity=r.sensitivity_costs_of_equity,
            growth_rates=r.sensitivity_gs,
            grid=r.sensitivity,
            centre_cost_of_equity=r.sensitivity_costs_of_equity[centre],
            centre_terminal_growth=r.sensitivity_gs[centre],
            centre_row=centre,
            centre_col=centre,
        ),
        projection=[
            DividendYearOut(period=y.period, dividend=y.dividend,
                            discount_factor=y.discount_factor, pv_dividend=y.pv_dividend)
            for y in r.years
        ],
        warnings=report.suitability.warnings,
    )


def _not_suitable_body(report: "ValuationReport | DDMReport") -> dict:
    if isinstance(report, DDMReport):
        # Both methods were assessed, so both explanations are given: why a
        # DCF was never used, then why the dividend discount model could not
        # stand in for it.
        payload = NotSuitableResponse(
            method="ddm",
            company=_company_out(report),
            message=(
                f"No valuation method applies to {report.company_name.rstrip('.')}. "
                "As a financial company it was assessed with a dividend discount "
                "model instead of a discounted cash flow, and that model does not "
                "apply either. No intrinsic value is reported, deliberately: "
                "either model would produce a figure with no economic meaning."
            ),
            reasons=[report.why_not_dcf, *report.suitability.reasons],
            assumptions=_assumption_list(report.derived.provenance),
        )
        return payload.model_dump()

    payload = NotSuitableResponse(
        company=_company_out(report),
        message=(
            f"A standard discounted cash flow valuation isn't suitable for "
            f"{report.company_name.rstrip('.')}. No intrinsic value is reported, "
            "deliberately: the model would produce a figure with no economic meaning."
        ),
        reasons=report.suitability.reasons,
        assumptions=_assumption_list(report.derived.provenance),
        speculative_estimate_available=speculative_estimate_available(report),
    )
    return payload.model_dump()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any):
        super().__init__(status_code=status_code,
                         detail={"code": code, "message": message, **extra})


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Give FastAPI's own 422s a `code`, so they are distinguishable from a
    suitability refusal, which shares the status."""
    return JSONResponse(
        status_code=HTTP_422_NOT_SUITABLE,
        content={"code": "validation_error", "message": "Request parameters were invalid.",
                 "errors": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code,
                        content={"code": "error", "message": str(detail)})


def _run_valuation(ticker: str, overrides: dict, valuer=None) -> Any:
    """Call the analysis layer and translate its failure modes into API errors.

    `valuer` defaults to value_company for every standard endpoint, looked up
    when called rather than bound when this module loads - so the module-level
    value_company stays the one function every standard request goes through.
    Only the speculative endpoints pass value_company_speculatively.
    """
    valuer = valuer or value_company
    try:
        return valuer(ticker, overrides=overrides or None)

    except TickerNotFoundError as e:
        raise ApiError(status.HTTP_404_NOT_FOUND, "ticker_not_found", str(e)) from e

    except RateLimitedError as e:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "upstream_rate_limited",
                       str(e)) from e

    except UnsupportedListingError as e:
        # A permanent property of the listing, not a transient fault: the
        # statements and the quote are in different currencies. 422 rather
        # than 502 because retrying will never help.
        raise ApiError(HTTP_422_NOT_SUITABLE, "unsupported_listing", str(e)) from e

    except RelativeNotApplicable as e:
        raise ApiError(HTTP_422_NOT_SUITABLE, "relative_not_applicable", str(e),
                       method="relative", reasons=e.reasons,
                       standard_valuation=f"/valuation/{ticker.strip().upper()}") from e

    except SpeculativeNotApplicable as e:
        raise ApiError(HTTP_422_NOT_SUITABLE, "speculative_not_applicable", str(e),
                       method="speculative", reasons=e.reasons,
                       standard_valuation=f"/valuation/{ticker.strip().upper()}") from e

    except ValueError as e:
        # derive_assumptions rejects unknown override names.
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_override", str(e)) from e

    except (DataUnavailableError, MarketDataError) as e:
        # Yahoo reachable but unusable, or any other upstream failure. Nothing
        # here returns 402: that was the old FMP data source's paid-plan gate,
        # and Yahoo has no paid tier to be gated behind.
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "upstream_error", str(e)) from e


def _respond(report: "ValuationReport | DDMReport") -> Any:
    if not report.suitable:
        return JSONResponse(status_code=HTTP_422_NOT_SUITABLE,
                            content=_not_suitable_body(report))
    if isinstance(report, DDMReport):
        return _serialise_ddm(report)
    return _serialise(report)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

COMMON_RESPONSES: dict = {
    404: {"model": ErrorResponse, "description": "Ticker unknown, malformed, or without statements"},
    422: {"model": NotSuitableResponse,
          "description": "No valuation method applies: a DCF isn't suitable, "
                         "or for a financial company the dividend discount model isn't either"},
    400: {"model": ErrorResponse, "description": "Unknown or invalid assumption override"},
    502: {"model": ErrorResponse, "description": "Upstream data provider failure"},
}


@app.get("/health", tags=["meta"], summary="Liveness and configuration check")
def health() -> dict:
    """
    Liveness plus which data source is in use.

    Deliberately touches nothing: no Yahoo call, no cache read, no disk. The
    app pings this on launch to wake a sleeping free-tier instance, so it
    has to return the moment the process is up - and it must not fail when
    Yahoo is down, or Render would judge a perfectly healthy service
    unhealthy and restart it. **Keep it that way**: anything added here is
    paid for on every cold start, by a user staring at a spinner.

    The old `fmp_key_configured` flag is gone: the backend reads Yahoo
    Finance, which needs no credentials, so there is no key to report on.
    """
    return {
        "status": "ok",
        "data_source": data_source_name(),
        "requires_api_key": requires_api_key(),
    }


class SearchResultOut(BaseModel):
    ticker: str = Field(..., examples=["AAPL"])
    name: str = Field(..., examples=["Apple Inc."])
    exchange: str = Field(..., description="Human-readable exchange", examples=["NASDAQ"])
    type: str = Field(..., description="Human-readable listing type", examples=["Equity"])


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultOut] = Field(
        ..., description="Best matches first. Empty when nothing valuable matches - "
                         "which is an answer, unlike a 429 or 502.")


@app.get(
    SEARCH_PATH,
    tags=["search"],
    summary="Find companies by ticker or name, for search-as-you-type",
    response_model=SearchResponse,
    responses={
        429: {"model": ErrorResponse,
              "description": "Too many searches from this client (`rate_limited`), or Yahoo "
                             "is throttling (`upstream_rate_limited`)"},
        502: {"model": ErrorResponse, "description": "Yahoo could not be searched just now"},
    },
)
def search(
    q: str = Query(..., min_length=1, max_length=50,
                   description="A ticker, a company name, or a near miss of either",
                   examples=["bank of am"]),
) -> SearchResponse:
    """
    Companies matching *q*, restricted to listings this API can value: common
    stock on the main US exchanges. Funds, futures, options, crypto, foreign and
    OTC listings, and preferred shares are left out, so nothing offered here is
    certain to be turned away - though a listed company can still be refused on
    its own merits, as a loss-maker is.

    Tolerant of near misses: `APPL` finds Apple, `microsft` Microsoft, and
    `BRK.B` finds `BRK-B`. At most eight results.
    """
    try:
        found = search_companies(q)
    except RateLimitedError as e:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "upstream_rate_limited", str(e)) from e
    except MarketDataError as e:
        # Includes a 404 from Yahoo's own search: that is the search endpoint
        # misbehaving, not a statement that the company does not exist.
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "upstream_error",
                       "Company search is unavailable just now. You can still enter "
                       "a ticker directly.") from e

    return SearchResponse(
        query=q,
        results=[SearchResultOut(ticker=r.ticker, name=r.name,
                                 exchange=r.exchange, type=r.type) for r in found])


@app.get("/diagnostics/upstream", tags=["meta"],
         summary="What Yahoo Finance actually returns to this server")
def upstream_diagnostics(ticker: str = "AAPL") -> dict:
    """
    Report Yahoo's raw response to *this* machine.

    Exists because "is Yahoo blocking the server?" cannot be answered from a
    laptop - the whole question is what happens from the deployed IP. Returns
    the symbol-probe verdict and whether a profile actually came back, so a
    block is distinguishable from a genuinely unknown ticker without reading
    server logs.

    Discloses nothing sensitive: no credentials exist, and the only input is
    a ticker symbol.
    """
    from market_data import fetch_financials, probe_symbol

    verdict, detail = probe_symbol(ticker)

    profile_ok, profile_detail = False, ""
    try:
        financials = fetch_financials(ticker)
        profile_ok = True
        profile_detail = (f"{financials.profile.get('companyName')}, "
                          f"{len(financials.income)} year(s) of statements")
    except Exception as e:
        profile_detail = f"{type(e).__name__}: {e}"

    from market_data import endpoint_report

    return {
        "ticker": ticker.upper(),
        "symbol_probe": {"verdict": verdict, "detail": detail},
        "endpoints": endpoint_report(ticker),
        "full_fetch": {"succeeded": profile_ok, "detail": profile_detail},
        "interpretation": {
            "found+succeeded": "Yahoo is serving this server normally.",
            "found+failed": "Yahoo knows the symbol but withheld the data - throttling.",
            "blocked": "Yahoo is refusing this server's requests outright.",
            "absent": "Yahoo answered normally: the symbol genuinely does not exist.",
        }.get(verdict if verdict != "found"
              else f"found+{'succeeded' if profile_ok else 'failed'}",
              "Yahoo could not be reached."),
    }


@app.get("/assumptions", tags=["meta"], summary="Overridable assumption names")
def overridable_assumptions() -> dict:
    """The names accepted in `overrides`, for building sliders against."""
    return {
        "assumptions": list(ASSUMPTION_OVERRIDES),
        "wacc_inputs": list(WACC_INPUT_OVERRIDES),
        "ddm_assumptions": list(DDM_OVERRIDES),
        "cost_of_equity_inputs": list(COST_OF_EQUITY_INPUT_OVERRIDES),
        # Accepted only by the opt-in speculative endpoints.
        "speculative_assumptions": list(SPECULATIVE_ASSUMPTION_NAMES),
        "speculative_discount_inputs": list(DISCOUNT_INPUT_NAMES),
        "global_levers": list(GLOBAL_LEVERS),
        "note": "All rates are decimals: 0.09 means 9%. Which set applies "
                "depends on the `method` a valuation returns.",
    }


@app.get(
    "/valuation/{ticker}",
    tags=["valuation"],
    summary="Value a company using assumptions derived from its own filings",
    response_model=ValuationResult,
    responses=COMMON_RESPONSES,
)
def get_valuation(
    ticker: str = Path(..., min_length=1, max_length=12, examples=["AAPL"]),
) -> Any:
    """
    Value *ticker* with no overrides - every assumption derived or defaulted.

    Try `AAPL` for a normal valuation and `RIVN` for the suitability refusal.
    """
    report = _run_valuation(ticker, {})
    return _respond(report)


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXCEL_RESPONSES: dict = {
    200: {
        "content": {XLSX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        "description": "An .xlsx DCF model built from live Excel formulas",
    },
    **COMMON_RESPONSES,
}


def _excel_response(report: ValuationReport) -> Any:
    """Serialise *report* as a downloadable workbook, or the refusal response.

    The suitability check happens before any workbook is built: handing back a
    spreadsheet for a company the guard refused would produce precisely the
    artefact the guard exists to prevent, and a file outlives the API response
    that carried it.
    """
    if not report.suitable:
        return JSONResponse(status_code=HTTP_422_NOT_SUITABLE,
                            content=_not_suitable_body(report))

    if isinstance(report, DDMReport):
        # Valued, but not with a DCF - and the workbook is a DCF model. Handing
        # back a DCF spreadsheet for a bank would contradict the valuation the
        # API has just produced for it.
        return JSONResponse(
            status_code=HTTP_422_NOT_SUITABLE,
            content={
                "code": "excel_unavailable_for_method",
                "method": report.method,
                "message": (
                    f"The Excel export builds discounted cash flow models, and "
                    f"{report.company_name} was valued with a dividend "
                    "discount model instead. A workbook for that model is not "
                    "available yet."
                ),
            },
        )

    payload = workbook_bytes(report)
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename_for(report)}"',
            "Content-Length": str(len(payload)),
        },
    )


@app.get(
    "/valuation/{ticker}/excel",
    tags=["excel"],
    summary="Download an .xlsx DCF model for a company",
    response_class=Response,
    responses=EXCEL_RESPONSES,
)
def get_valuation_excel(
    ticker: str = Path(..., min_length=1, max_length=12, examples=["AAPL"]),
) -> Any:
    """
    Build a downloadable DCF workbook for *ticker*.

    The file mirrors the structure of the reference model: assumptions, the
    revenue projection, the UFCF build, discounting, a Gordon-growth terminal
    value, the equity bridge, and a sensitivity grid. Only the assumption and
    base-year cells hold literal numbers - everything else is a live formula,
    so editing an assumption recalculates the whole model in Excel.

    Companies the suitability guard refuses return 422 rather than a workbook.
    """
    report = _run_valuation(ticker, {})
    return _excel_response(report)


@app.post(
    "/valuation/excel",
    tags=["excel"],
    summary="Download an .xlsx DCF model with adjusted assumptions",
    response_class=Response,
    responses=EXCEL_RESPONSES,
)
def post_valuation_excel(request: ValuationRequest) -> Any:
    """As above, but with the supplied assumption overrides baked into the
    workbook's editable input cells."""
    report = _run_valuation(request.ticker, request.overrides.as_dict())
    return _excel_response(report)


@app.post(
    "/valuation",
    tags=["valuation"],
    summary="Value a company with adjusted assumptions",
    response_model=ValuationResult,
    responses=COMMON_RESPONSES,
)
def post_valuation(request: ValuationRequest) -> Any:
    """
    Value a company, overriding any assumptions supplied.

    Intended for the app's sliders: send only the assumptions the user has
    moved. Everything omitted keeps its derived or default value, and the
    `source` label on each returned assumption reflects which is which.
    """
    report = _run_valuation(request.ticker, request.overrides.as_dict())
    return _respond(report)


# ---------------------------------------------------------------------------
# Speculative estimates - opt-in only
# ---------------------------------------------------------------------------

def _serialise_speculative(report: SpeculativeReport) -> SpeculativeValuationResponse:
    r = report.result
    d = report.derived.diagnostics
    inputs = report.derived.inputs
    centre = len(r.sensitivity_margins) // 2

    return SpeculativeValuationResponse(
        disclaimer_headline=SPECULATIVE_HEADLINE,
        disclaimer=speculative_disclaimer(report),
        company=_company_out(report),
        speculative_value_per_share=r.value_per_share,
        current_price=r.current_price,
        estimate_vs_price=r.estimate_vs_price,
        why_standard_valuation_refused=report.why_standard_refused,
        starting_point=SpeculativeStartOut(
            fiscal_year=d["fiscal_year"],
            revenue=inputs.revenue,
            operating_margin=d["operating_margin"],
            gross_margin=d["gross_margin"],
            latest_revenue_growth=d["latest_revenue_growth"],
            revenue_cagr=d["revenue_cagr"],
            cash=inputs.cash,
            total_debt=inputs.total_debt,
            shares=inputs.shares,
            history=[RevenueHistoryOut(fiscal_year=year, revenue=revenue / 1e6,
                                       operating_margin=om, gross_margin=gm)
                     for year, revenue, om, gm in d["revenue_history"]],
        ),
        path_to_profitability=[
            PathYearOut(period=y.period, revenue=y.revenue, operating_margin=y.operating_margin,
                        ebit=y.ebit, ufcf=y.ufcf, discount_factor=y.discount_factor,
                        pv_ufcf=y.pv_ufcf)
            for y in r.path
        ],
        valuation=SpeculativeValuationOut(
            pv_path_sum=r.pv_path_sum,
            cumulative_cash_burn=r.cumulative_cash_burn,
            revenue_at_profitability=r.revenue_at_profitability,
            value_at_profitability=r.value_at_profitability,
            pv_value_at_profitability=r.pv_value_at_profitability,
            enterprise_value=r.enterprise_value,
            equity_value=r.equity_value,
            share_from_after_profitability=r.share_from_after_profitability,
        ),
        assumptions=_assumption_list(report.derived.provenance),
        wacc_inputs=_assumption_list(report.derived.wacc_inputs),
        sensitivity=SpeculativeSensitivityOut(
            target_operating_margins=r.sensitivity_margins,
            years_to_profitability=r.sensitivity_years,
            grid=r.sensitivity,
            centre_row=centre,
            centre_col=centre,
        ),
        warnings=report.suitability.warnings,
    )


def _respond_speculative(report: SpeculativeReport) -> Any:
    if not report.suitable:
        # Both refusals are given: why the standard valuation declined, and
        # why even a speculative path to profitability could not stand in.
        body = NotSuitableResponse(
            method="speculative",
            company=_company_out(report),
            message=(
                f"No speculative estimate is produced for {report.company_name.rstrip('.')}. "
                "It was refused a standard valuation because it loses money, and even a "
                "projected path to profitability does not apply to it. No figure is "
                "reported, deliberately."
            ),
            reasons=[*report.why_standard_refused, *report.suitability.reasons],
            assumptions=_assumption_list(report.derived.provenance),
        )
        return JSONResponse(status_code=HTTP_422_NOT_SUITABLE, content=body.model_dump())
    return _serialise_speculative(report)


SPECULATIVE_RESPONSES: dict = {
    404: {"model": ErrorResponse, "description": "Ticker unknown, malformed, or without statements"},
    400: {"model": ErrorResponse, "description": "Unknown or invalid assumption override"},
    422: {"model": NotSuitableResponse,
          "description": "No estimate: not offered for this company (code "
                         "speculative_not_applicable), or even a path to profitability "
                         "does not apply (code not_suitable)"},
    502: {"model": ErrorResponse, "description": "Upstream data provider failure"},
}


@app.get(
    "/valuation/{ticker}/speculative",
    tags=["speculative (opt-in)"],
    summary="OPT-IN: a speculative path-to-profitability estimate for a loss-making "
            "company. NOT a valuation.",
    response_model=SpeculativeValuationResponse,
    responses=SPECULATIVE_RESPONSES,
)
def get_speculative_estimate(
    ticker: str = Path(..., min_length=1, max_length=12, examples=["RIVN"]),
) -> Any:
    """
    Speculatively estimate a loss-making company that /valuation refuses.

    **This is not a valuation.** It assumes the company grows revenue, reaches
    a target operating margin after a number of years, survives and funds its
    losses until then, and then performs like a mature business - none of
    which has happened. The response's `disclaimer` must be shown before the
    figure. Offered only for companies refused as loss-making; any company a
    standard valuation applies to is refused here.
    """
    report = _run_valuation(ticker, {}, valuer=value_company_speculatively)
    return _respond_speculative(report)


@app.post(
    "/valuation/speculative",
    tags=["speculative (opt-in)"],
    summary="OPT-IN: a speculative estimate with adjusted assumptions. NOT a valuation.",
    response_model=SpeculativeValuationResponse,
    responses=SPECULATIVE_RESPONSES,
)
def post_speculative_estimate(request: SpeculativeRequest) -> Any:
    """As above, with any speculative assumption overridden."""
    report = _run_valuation(request.ticker, request.overrides.as_dict(),
                            valuer=value_company_speculatively)
    return _respond_speculative(report)


# ---------------------------------------------------------------------------
# Relative valuation - a market-based second opinion, beside the intrinsic value
# ---------------------------------------------------------------------------

def _intrinsic_summary(report: "ValuationReport | DDMReport") -> IntrinsicSummaryOut:
    r = report.result
    return IntrinsicSummaryOut(
        method=report.method,
        intrinsic_value_per_share=r.intrinsic_value_per_share,
        current_price=r.current_price,
        upside_downside=r.upside_downside,
        note=honesty_note(report),
        full_valuation=f"/valuation/{report.ticker}",
    )


def _peer_selection_out(report: RelativeReport) -> PeerSelectionOut:
    return PeerSelectionOut(
        mode=report.selection_mode,
        rule=report.selection_rule,
        peers=[PeerOut(ticker=p.ticker, name=p.name, industry=p.industry,
                       provenance=p.provenance, detail=p.detail) for p in report.peers],
        excluded=[ExcludedPeerOut(ticker=e.ticker, name=e.name, reason=e.reason)
                  for e in report.excluded],
    )


def _figures_out(figures) -> MarketFiguresOut:
    return MarketFiguresOut(
        as_of=figures.as_of, trading_currency=figures.trading_currency,
        reporting_currency=figures.reporting_currency, price=figures.price,
        market_cap=figures.market_cap, enterprise_value=figures.enterprise_value,
        revenue=figures.revenue, ebitda=figures.ebitda, net_income=figures.net_income,
        total_debt=figures.total_debt, cash=figures.cash, shares=figures.shares,
        common_equity=figures.common_equity)


def _multiples_out(result, show_implied: bool = True) -> list[MultipleOut]:
    """
    The comparison, multiple by multiple.

    With `show_implied` false the per-multiple implied values are withheld.
    That is the case where too few multiples applied to stand behind a figure,
    and repeating one inside the table would be the same fragile number in a
    different place.
    """
    return [
        MultipleOut(
            name=m.name, label=m.label, applicable=m.applicable,
            company_value=m.company_value, company_reason=m.company_reason,
            peer_median=m.peer_median, peer_count=m.peer_count,
            premium_to_median=m.premium_to_median,
            implied_value_per_share=m.implied_value_per_share if show_implied else None,
            reason=m.reason,
            peers=[PeerMultipleOut(ticker=p.ticker, value=p.value,
                                   excluded_reason=p.excluded_reason) for p in m.peers])
        for m in result.multiples
    ]


def _serialise_relative(report: RelativeReport) -> RelativeValuationResponse:
    r = report.result
    figures = report.company_figures
    return RelativeValuationResponse(
        company=_company_out(report),
        intrinsic_valuation=_intrinsic_summary(report.intrinsic),
        relative_valuation=RelativeBlockOut(
            framing=RELATIVE_FRAMING,
            note=relative_note(report),
            peer_selection=_peer_selection_out(report),
            company_figures=_figures_out(figures),
            multiples=_multiples_out(r),
            relative_value_per_share=RelativeValueOut(
                central=r.central_value_per_share, low=r.low_value_per_share,
                high=r.high_value_per_share,
                multiples_applied=[m.label for m in r.multiples if m.applicable]),
            current_price=r.current_price,
            relative_value_vs_price=r.central_vs_price,
            comparison_with_intrinsic=RelativeComparisonOut(**relative_comparison(report)),
            warnings=report.suitability.warnings,
        ),
    )


def _respond_relative(report: RelativeReport) -> Any:
    if not report.suitable:
        measured = report.measured
        # Unstripped: the name sits mid-sentence, before a colon.
        message = (f"No relative valuation is produced for {report.company_name}: a comparison "
                   "with peers could not be made meaningfully, so no market-based figure is "
                   "offered. The intrinsic valuation is unaffected and included here.")
        if measured is not None:
            message += (" The multiples that could be computed are included as information, "
                        "without a value drawn from them.")
        body = RelativeNotSuitableResponse(
            company=_company_out(report),
            message=message,
            reasons=report.suitability.reasons,
            peer_selection=_peer_selection_out(report),
            intrinsic_valuation=_intrinsic_summary(report.intrinsic),
            company_figures=_figures_out(report.company_figures) if measured else None,
            multiples=_multiples_out(measured, show_implied=False) if measured else [],
        )
        return JSONResponse(status_code=HTTP_422_NOT_SUITABLE, content=body.model_dump())
    return _serialise_relative(report)


RELATIVE_RESPONSES: dict = {
    404: {"model": ErrorResponse, "description": "Ticker unknown, malformed, or without statements"},
    400: {"model": ErrorResponse, "description": "Malformed or oversized peer list"},
    422: {"model": RelativeNotSuitableResponse,
          "description": "No relative figure: nothing to sit beside (code "
                         "relative_not_applicable), or peers and multiples cannot support "
                         "one (code not_suitable)"},
    429: {"model": ErrorResponse, "description": "The data source is throttling; peers could not be fetched"},
    502: {"model": ErrorResponse, "description": "Upstream data provider failure"},
}


@app.get(
    "/valuation/{ticker}/relative",
    tags=["relative (market-based)"],
    summary="How the market prices similar companies right now: a relative second opinion "
            "from peer multiples, beside the intrinsic value",
    response_model=RelativeValuationResponse,
    responses=RELATIVE_RESPONSES,
)
def get_relative_valuation(
    ticker: str = Path(..., min_length=1, max_length=12, examples=["JPM"]),
) -> Any:
    """
    A relative, market-based view beside the company's intrinsic valuation.

    Peers are selected automatically - companies in the same industry that
    Yahoo Finance users also watch alongside this one - and every peer is named,
    as is every candidate excluded and why. **It is not an intrinsic valuation**:
    it inherits whatever mispricing the market applies to the whole group.
    """
    report = _run_valuation(ticker, {},
                            valuer=lambda t, overrides=None: value_company_relatively(t))
    return _respond_relative(report)


@app.post(
    "/valuation/relative",
    tags=["relative (market-based)"],
    summary="A relative second opinion with the peer group replaced or edited",
    response_model=RelativeValuationResponse,
    responses=RELATIVE_RESPONSES,
)
def post_relative_valuation(request: RelativeRequest) -> Any:
    """As above, with `peers` replacing the automatic group, or `add_peers` and
    `remove_peers` editing it. Peer selection is the most subjective input here."""
    report = _run_valuation(
        request.ticker, {},
        valuer=lambda t, overrides=None: value_company_relatively(
            t, peers=request.peers, add_peers=request.add_peers,
            remove_peers=request.remove_peers))
    return _respond_relative(report)
