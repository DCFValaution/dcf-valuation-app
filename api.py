"""
FastAPI web layer for the DCF valuation service.

This module is a thin wrapper. It performs no valuation arithmetic and makes
no modelling decisions - it calls analysis.value_company() and serialises the
ValuationReport it gets back. Every number in a response originates in the
engine or the derivation layer.

OUTCOME -> STATUS MAPPING
-------------------------
    200  a valuation was produced
    404  ticker is malformed, unknown, or has no usable statements
    402  the ticker is real but gated behind a paid FMP plan
    422  a valuation was refused: the company fails the suitability guard
    400  the caller supplied an unknown or non-numeric assumption override
    429  the upstream FMP quota or rate limit was exhausted
    502  any other upstream failure (network, bad credentials, malformed data)

Every error body carries a machine-readable `code` alongside the human
message, so clients never have to parse prose - including for 422, which
FastAPI also uses for its own request-validation errors (those carry
code "validation_error", the suitability refusal carries "not_suitable").

SECRET HANDLING
---------------
The FMP API key is read from the server's environment by fmp.py and never
leaves it. It is not accepted as a request parameter, never echoed in a
response, and fmp.py redacts it from any upstream error text before raising.
"""

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Path, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from analysis import GLOBAL_LEVERS, ValuationReport, honesty_note, value_company
from excel_export import filename_for, workbook_bytes
from market_data import (DataUnavailableError, MarketDataError, RateLimitedError,
                         TickerNotFoundError, UnsupportedListingError,
                         data_source_name, requires_api_key)

# Mirrors the names derive_assumptions() accepts. Listing them explicitly gives
# /docs a usable schema; derive_assumptions still validates independently, so a
# drift between the two surfaces as a 400 rather than a wrong answer.
ASSUMPTION_OVERRIDES = (
    "revenue_growth", "operating_margin", "tax_rate", "da_pct", "capex_pct",
    "nwc_pct", "wacc", "terminal_growth", "projection_years",
)
WACC_INPUT_OVERRIDES = ("risk_free_rate", "equity_risk_premium", "beta", "cost_of_debt")
ALL_OVERRIDES = ASSUMPTION_OVERRIDES + WACC_INPUT_OVERRIDES

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
    sensitivity: SensitivityOut
    projection: list[ProjectionYearOut]
    warnings: list[str]


class NotSuitableResponse(BaseModel):
    code: str = "not_suitable"
    company: CompanyOut
    suitable: bool = False
    intrinsic_value_per_share: None = Field(
        None, description="Always null. No figure is produced for these companies."
    )
    message: str
    reasons: list[str]
    assumptions: list[AssumptionOut]


class ErrorResponse(BaseModel):
    code: str
    message: str


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

PERCENT_NAMES = {
    "revenue_growth", "operating_margin", "tax_rate", "da_pct", "capex_pct",
    "nwc_pct", "wacc", "terminal_growth", "risk_free_rate",
    "equity_risk_premium", "cost_of_debt",
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


def _not_suitable_body(report: ValuationReport) -> dict:
    payload = NotSuitableResponse(
        company=_company_out(report),
        message=(
            f"A standard discounted cash flow valuation isn't suitable for "
            f"{report.company_name.rstrip('.')}. No intrinsic value is reported, "
            "deliberately: the model would produce a figure with no economic meaning."
        ),
        reasons=report.suitability.reasons,
        assumptions=_assumption_list(report.derived.provenance),
    )
    return payload.model_dump()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


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


def _run_valuation(ticker: str, overrides: dict) -> ValuationReport | JSONResponse:
    """Call the analysis layer and translate its failure modes into API errors."""
    try:
        return value_company(ticker, overrides=overrides or None)

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

    except ValueError as e:
        # derive_assumptions rejects unknown override names.
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_override", str(e)) from e

    except (DataUnavailableError, MarketDataError) as e:
        # Yahoo reachable but unusable, or any other upstream failure. The 402
        # "plan_limited" branch that used to sit here is gone with FMP: Yahoo
        # has no paid tier to be gated behind. Clients that still handle 402
        # are unaffected - the backend simply never sends it now.
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "upstream_error", str(e)) from e


def _respond(report: ValuationReport) -> Any:
    if not report.suitable:
        return JSONResponse(status_code=HTTP_422_NOT_SUITABLE,
                            content=_not_suitable_body(report))
    return _serialise(report)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

COMMON_RESPONSES: dict = {
    402: {"model": ErrorResponse, "description": "Ticker gated behind a paid FMP plan"},
    404: {"model": ErrorResponse, "description": "Ticker unknown, malformed, or without statements"},
    422: {"model": NotSuitableResponse, "description": "A standard DCF isn't suitable for this company"},
    400: {"model": ErrorResponse, "description": "Unknown or invalid assumption override"},
    502: {"model": ErrorResponse, "description": "Upstream data provider failure"},
}


@app.get("/health", tags=["meta"], summary="Liveness and configuration check")
def health() -> dict:
    """
    Liveness plus which data source is in use.

    The old `fmp_key_configured` flag is gone: the backend reads Yahoo
    Finance, which needs no credentials, so there is no key to report on.
    """
    return {
        "status": "ok",
        "data_source": data_source_name(),
        "requires_api_key": requires_api_key(),
    }


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
        "global_levers": list(GLOBAL_LEVERS),
        "note": "All rates are decimals: 0.09 means 9%.",
    }


@app.get(
    "/valuation/{ticker}",
    tags=["valuation"],
    summary="Value a company using assumptions derived from its own filings",
    response_model=ValuationResponse,
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
    response_model=ValuationResponse,
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
