"""
Derives DCF assumptions from a company's own historical financials.

The DCF engine (dcf.py) is deliberately dumb: it applies whatever assumptions
it is handed. Handing every company Apple's 32% operating margin produces
confident nonsense for companies that look nothing like Apple. This module
reads each company's actual recent history and derives a starting point from
it.

WHAT IS DERIVED vs DEFAULTED
----------------------------
Derived from the company's own filings:
    revenue_growth      trailing revenue CAGR over the available window
    operating_margin    median operatingIncome / revenue
    tax_rate            median incomeTaxExpense / incomeBeforeTax
    da_pct              median D&A / revenue
    capex_pct           median |capitalExpenditure| / revenue
    nwc_pct             aggregate change in NWC / change in revenue

    wacc                CAPM cost of equity and actual cost of debt, weighted
                        by the company's capital structure (see _derive_wacc)

Global defaults (not company-specific):
    terminal_growth     long-run nominal growth; no company grows faster than
                        the economy forever
    projection_years    modelling convention

MEDIAN, NOT MEAN
----------------
Ratios use the median of recent years rather than the average, because single
years are routinely distorted by one-offs. Apple's FY2024 effective tax rate
was 24.1% because of a one-time EU State Aid charge, against ~15% in adjacent
years; the mean would carry that distortion into every projected year, the
median discards it.

CAGR FOR GROWTH
---------------
Growth uses endpoint-to-endpoint CAGR over the full window rather than the
latest year, which would let a single strong or weak year set the trajectory
for the entire projection.

CLAMPING
--------
Derived values are clamped to defensible ranges. Short histories and one-off
years can produce ratios that are arithmetically correct but useless as
forward assumptions (a company that tripled revenue off a tiny base does not
sustain 200% growth for five years). Every clamp is recorded in the
provenance, so a clamped value is never silently passed off as observed.
"""

from dataclasses import dataclass, field
from statistics import median

from dcf import Assumptions
from market_data import (CompanyFinancials, fetch_risk_free_rate, num,
                         total_debt_of)

# --- Global defaults ------------------------------------------------------
DEFAULT_WACC = 0.085             # only used when WACC derivation fails
DEFAULT_TERMINAL_GROWTH = 0.025  # ~long-run nominal GDP growth
DEFAULT_PROJECTION_YEARS = 5
FALLBACK_TAX_RATE = 0.21         # US federal statutory, when no taxed year exists

# --- WACC inputs ----------------------------------------------------------
# The risk-free rate is fetched live from the Treasury curve; this static value
# is only the fallback when that lookup is unavailable. It will drift out of
# date - that is precisely why the live lookup is preferred.
FALLBACK_RISK_FREE_RATE = 0.0425
RISK_FREE_TENOR = "year10"  # matched to a ~perpetual horizon, not a short bill

# Equity risk premium: the excess return demanded over the risk-free rate.
# Held as a global default because credible ERP estimates come from academic
# and practitioner surveys (Damodaran, Fernandez et al.), not from company
# filings. Mainstream estimates for developed markets cluster around 4.5-6%.
DEFAULT_EQUITY_RISK_PREMIUM = 0.055

DEFAULT_BETA = 1.0          # market beta, when the company's own is unusable
DEFAULT_DEBT_SPREAD = 0.02  # over risk-free, when interest expense is unusable

# Sanity bands. Outside these, an input is treated as bad data rather than as
# a surprising-but-real fact, and a documented fallback is substituted.
SANE_BETA = (0.10, 4.00)
SANE_COST_OF_DEBT = (0.005, 0.25)
SANE_WACC = (0.04, 0.20)

# --- Clamps: (floor, ceiling) --------------------------------------------
CLAMP_REVENUE_GROWTH = (0.00, 0.15)
CLAMP_OPERATING_MARGIN = (-1.00, 0.60)
CLAMP_TAX_RATE = (0.00, 0.35)
CLAMP_DA_PCT = (0.00, 0.30)
CLAMP_CAPEX_PCT = (0.00, 0.40)
CLAMP_NWC_PCT = (-0.10, 0.30)

RATIO_WINDOW = 3  # years of history used for median ratios


@dataclass
class Provenance:
    """How one assumption got its value."""
    name: str
    value: float
    source: str      # "derived" | "derived (clamped)" | "default" | "override"
    detail: str

    # For a clamped value, the facts behind it in structured form: the figure
    # the company's own history gave, the limit it was held to, and - where a
    # value can be limited for more than one reason - which. `detail` stays the
    # precise technical record (it is what the workbook carries); these let a
    # plain-language warning be written from the numbers rather than by
    # parsing that string.
    raw: float | None = None
    limit: float | None = None
    why: str | None = None

    @property
    def is_derived(self) -> bool:
        return self.source.startswith("derived")


@dataclass
class DerivedAssumptions:
    assumptions: Assumptions
    provenance: dict[str, Provenance] = field(default_factory=dict)
    # CAPM/WACC inputs are recorded separately: they feed the `wacc` assumption
    # rather than being engine assumptions in their own right, but they are
    # overridable and their provenance matters just as much.
    wacc_inputs: dict[str, Provenance] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)

    def derived_names(self) -> list[str]:
        return [k for k, p in self.provenance.items() if p.is_derived]

    def defaulted_names(self) -> list[str]:
        return [k for k, p in self.provenance.items() if p.source == "default"]

    def overridden_names(self) -> list[str]:
        return [k for k, p in self.provenance.items() if p.source == "override"]


def _clamp(value: float, bounds: tuple[float, float]) -> tuple[float, bool]:
    low, high = bounds
    clamped = max(low, min(high, value))
    return clamped, clamped != value


def _net_working_capital(balance_row: dict) -> float:
    """
    Non-cash, non-debt working capital.

    Cash and short-term investments are excluded because they are counted in
    the equity bridge, and short-term debt is excluded because it is financing,
    not operations. Including either would double-count.
    """
    current_assets = num(balance_row, "totalCurrentAssets")
    current_liabilities = num(balance_row, "totalCurrentLiabilities")
    cash = num(balance_row, "cashAndCashEquivalents")
    short_investments = num(balance_row, "shortTermInvestments")
    short_debt = num(balance_row, "shortTermDebt")

    operating_assets = current_assets - cash - short_investments
    operating_liabilities = current_liabilities - short_debt
    return operating_assets - operating_liabilities


def _cost_of_debt_history(fin: CompanyFinancials) -> list[tuple[str, float]]:
    """
    Implied cost of debt for each year with usable data.

    Interest expense is divided by AVERAGE debt across the year where the prior
    year's balance sheet is available, since interest accrues on the balance
    through the period rather than on the closing figure.

    Years where FMP reports zero interest expense against non-zero debt are
    skipped rather than treated as a 0% cost of debt. That combination is a
    reporting gap, not a fact: Apple's FY2024 and FY2025 both come back as
    zero interest expense against ~$100bn of debt, while FY2023 correctly
    shows $3.9bn. Taking the zeros at face value would drag the cost of debt
    to nil and understate WACC.
    """
    rates: list[tuple[str, float]] = []
    for i, income_row in enumerate(fin.income):
        if i >= len(fin.balance):
            break
        interest = abs(num(income_row, "interestExpense"))
        if interest <= 0:
            continue

        debt_now = total_debt_of(fin.balance[i])
        debt_prior = total_debt_of(fin.balance[i + 1]) if i + 1 < len(fin.balance) else 0.0
        debt = (debt_now + debt_prior) / 2 if debt_prior > 0 else debt_now
        if debt <= 0:
            continue

        rates.append((str(income_row.get("fiscalYear", "?")), interest / debt))
    return rates


def _capm_inputs(fin: CompanyFinancials, record_input) -> tuple[float, float, float]:
    """
    Risk-free rate, equity risk premium and beta, each recorded with provenance.

    Shared by the DCF's WACC build-up and the dividend discount model's cost
    of equity, so the two methods can never disagree about what the market
    demands of the same company.
    """
    # --- Risk-free rate: live Treasury curve, static fallback --------------
    live = fetch_risk_free_rate(RISK_FREE_TENOR)
    if live is not None:
        rf = record_input("risk_free_rate", live[0], "derived", live[1])
    else:
        rf = record_input("risk_free_rate", FALLBACK_RISK_FREE_RATE, "default",
                          "Treasury curve unavailable; used documented static fallback")

    erp = record_input("equity_risk_premium", DEFAULT_EQUITY_RISK_PREMIUM, "default",
                       "global default (developed-market survey estimates cluster 4.5-6%)")

    # --- Beta --------------------------------------------------------------
    raw_beta = num(fin.profile, "beta")
    if SANE_BETA[0] <= raw_beta <= SANE_BETA[1]:
        # Either quoted by Yahoo or computed from price history when the
        # quote endpoint was unavailable; the profile records which, so the
        # report can say rather than imply.
        source = fin.profile.get("betaSource") or "quoted by the market data provider"
        beta = record_input("beta", raw_beta, "derived", source)
    else:
        reason = (fin.profile.get("betaSource")
                  or "not reported by the quote endpoint") if raw_beta == 0 \
            else f"reported {raw_beta:g}, outside plausible range"
        beta = record_input("beta", DEFAULT_BETA, "default",
                            f"beta {reason}; used market beta of 1.0")

    return rf, erp, beta


def _derive_wacc(fin: CompanyFinancials, tax_rate: float,
                 record_input) -> tuple[float | None, str, dict]:
    """
    WACC = We*Re + Wd*Rd*(1-t), with Re from CAPM.

        Re = risk-free rate + beta * equity risk premium
        Rd = interest expense / average debt   (the company's actual rate)
        We, Wd = market value of equity and book debt, as shares of the total

    Equity is weighted at MARKET value and debt at BOOK value. That asymmetry
    is deliberate and conventional: a company's market cap is observable and is
    what equity investors have at stake, whereas the market value of corporate
    debt is rarely observable and book value is a close proxy for
    investment-grade issuers near par.

    Returns (wacc, detail, components). A wacc of None means derivation failed
    and the caller should fall back to DEFAULT_WACC.
    """
    components: dict = {}

    rf, erp, beta = _capm_inputs(fin, record_input)

    cost_of_equity = rf + beta * erp

    # --- Cost of debt ------------------------------------------------------
    history = _cost_of_debt_history(fin)
    components["cost_of_debt_by_year"] = history

    if history:
        candidate = median([rate for _, rate in history])
        years_used = ", ".join(f"FY{y}" for y, _ in history)
        if SANE_COST_OF_DEBT[0] <= candidate <= SANE_COST_OF_DEBT[1]:
            cost_of_debt = record_input("cost_of_debt", candidate, "derived",
                                        f"median interest expense / average debt ({years_used})")
        else:
            cost_of_debt = record_input(
                "cost_of_debt", rf + DEFAULT_DEBT_SPREAD, "default",
                f"implied rate {candidate:.1%} outside plausible range; used risk-free + "
                f"{DEFAULT_DEBT_SPREAD:.0%} spread")
    else:
        cost_of_debt = record_input(
            "cost_of_debt", rf + DEFAULT_DEBT_SPREAD, "default",
            f"no year with usable interest expense; used risk-free + {DEFAULT_DEBT_SPREAD:.0%} spread")

    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)

    # --- Capital structure weights -----------------------------------------
    equity_value = num(fin.profile, "marketCap")
    if equity_value <= 0:
        price = num(fin.profile, "price")
        shares = num(fin.income[0], "weightedAverageShsOutDil")
        equity_value = price * shares

    debt_value = total_debt_of(fin.balance[0]) if fin.balance else 0.0
    capital = equity_value + debt_value

    components.update({
        "risk_free_rate": rf,
        "equity_risk_premium": erp,
        "beta": beta,
        "cost_of_equity": cost_of_equity,
        "cost_of_debt": cost_of_debt,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "tax_rate": tax_rate,
        "equity_value": equity_value,
        "debt_value": debt_value,
    })

    if capital <= 0:
        return None, "market value of equity and debt could not be established", components

    weight_equity = equity_value / capital
    weight_debt = debt_value / capital
    components["weight_equity"] = weight_equity
    components["weight_debt"] = weight_debt

    wacc = weight_equity * cost_of_equity + weight_debt * after_tax_cost_of_debt
    components["wacc"] = wacc

    if not (SANE_WACC[0] <= wacc <= SANE_WACC[1]):
        return None, f"computed WACC of {wacc:.1%} is outside the plausible range", components

    detail = (f"CAPM: {rf:.2%} + {beta:.3g}x{erp:.2%} = {cost_of_equity:.2%} equity; "
              f"debt {cost_of_debt:.2%} ({after_tax_cost_of_debt:.2%} after tax); "
              f"weights {weight_equity:.1%}E / {weight_debt:.1%}D")
    return wacc, detail, components


def derive_assumptions(fin: CompanyFinancials,
                       overrides: dict | None = None) -> DerivedAssumptions:
    """
    Build a starting assumption set for *fin* from its own history.

    `overrides` maps any Assumptions field name to a user-supplied value, which
    wins over both derived and default values and is recorded as such. These
    are starting points meant to be adjusted, not answers.
    """
    overrides = dict(overrides or {})
    income = fin.income
    balance = fin.balance
    cashflow = fin.cashflow

    provenance: dict[str, Provenance] = {}
    wacc_inputs: dict[str, Provenance] = {}
    diagnostics: dict = {}

    def _record_into(store: dict, name: str, value: float, source: str, detail: str,
                     **clamp) -> float:
        if name in overrides:
            supplied = float(overrides[name])
            store[name] = Provenance(name, supplied, "override",
                                     f"supplied by caller (would have been {value:.4g} from {source})")
            return supplied
        store[name] = Provenance(name, value, source, detail, **clamp)
        return value

    def record(name: str, value: float, source: str, detail: str, **clamp) -> float:
        return _record_into(provenance, name, value, source, detail, **clamp)

    def record_input(name: str, value: float, source: str, detail: str) -> float:
        return _record_into(wacc_inputs, name, value, source, detail)

    def derive(name: str, value: float | None, bounds: tuple[float, float],
               detail: str, fallback: float, fallback_detail: str) -> float:
        """Record a derived value, clamping it and noting if the clamp bit."""
        if value is None:
            return record(name, fallback, "default", fallback_detail)
        clamped, was_clamped = _clamp(value, bounds)
        if was_clamped:
            return record(name, clamped, "derived (clamped)",
                          f"{detail}; raw {value:.1%} clamped to [{bounds[0]:.0%}, {bounds[1]:.0%}]",
                          raw=value, limit=clamped, why="range")
        return record(name, clamped, "derived", detail)

    # --- Revenue history ---------------------------------------------------
    revenues = [num(r, "revenue") for r in income]
    years = [r.get("fiscalYear", "?") for r in income]
    diagnostics["revenue_by_year"] = list(zip(years, revenues))

    # --- Revenue growth: endpoint CAGR over the window ---------------------
    growth_value, growth_detail = None, ""
    if len(revenues) >= 2:
        newest, oldest = revenues[0], revenues[-1]
        periods = len(revenues) - 1
        if oldest > 0 and newest > 0:
            cagr = (newest / oldest) ** (1 / periods) - 1
            growth_value = cagr
            growth_detail = (f"{periods}-year revenue CAGR, FY{years[-1]}-FY{years[0]} "
                             f"({oldest / 1e9:,.1f}bn -> {newest / 1e9:,.1f}bn)")
    revenue_growth = derive(
        "revenue_growth", growth_value, CLAMP_REVENUE_GROWTH, growth_detail,
        DEFAULT_TERMINAL_GROWTH, "insufficient revenue history; fell back to terminal growth",
    )

    # --- Ratio medians over the recent window ------------------------------
    window = income[:RATIO_WINDOW]
    cf_window = cashflow[:RATIO_WINDOW]

    def median_ratio(rows: list[dict], numerator: str, denominator_rows: list[dict],
                     denominator: str = "revenue", absolute: bool = False) -> float | None:
        ratios = []
        for row, den_row in zip(rows, denominator_rows):
            den = num(den_row, denominator)
            if den <= 0:
                continue
            value = num(row, numerator)
            ratios.append(abs(value) / den if absolute else value / den)
        return median(ratios) if ratios else None

    # Operating margin
    margin_value = median_ratio(window, "operatingIncome", window)
    operating_margin = derive(
        "operating_margin", margin_value, CLAMP_OPERATING_MARGIN,
        f"median operatingIncome / revenue over {len(window)} years",
        0.0, "no usable revenue history",
    )
    diagnostics["operating_margin_by_year"] = [
        (r.get("fiscalYear"), (num(r, "operatingIncome") / num(r, "revenue")) if num(r, "revenue") > 0 else None)
        for r in income
    ]

    # Effective tax rate - only years with positive pre-tax income are
    # meaningful; a loss year produces a benefit, not a rate.
    tax_ratios = [
        num(r, "incomeTaxExpense") / num(r, "incomeBeforeTax")
        for r in window if num(r, "incomeBeforeTax") > 0
    ]
    tax_rate = derive(
        "tax_rate", median(tax_ratios) if tax_ratios else None, CLAMP_TAX_RATE,
        f"median effective rate over {len(tax_ratios)} profitable year(s)",
        FALLBACK_TAX_RATE, "no profitable year in window; used US statutory rate",
    )

    # D&A and capex come from the cash flow statement, which reports them as
    # actual cash movements rather than accounting allocations.
    da_value = median_ratio(cf_window, "depreciationAndAmortization", window)
    da_pct = derive(
        "da_pct", da_value, CLAMP_DA_PCT,
        f"median D&A / revenue over {len(cf_window)} years",
        0.0, "no cash flow history",
    )

    capex_value = median_ratio(cf_window, "capitalExpenditure", window, absolute=True)
    capex_pct = derive(
        "capex_pct", capex_value, CLAMP_CAPEX_PCT,
        f"median capex / revenue over {len(cf_window)} years",
        0.0, "no cash flow history",
    )

    # --- Change in NWC as a share of revenue growth ------------------------
    # Aggregated endpoint-to-endpoint rather than year-by-year: annual NWC
    # swings are extremely noisy (Apple's year-on-year ratio ranges from +62%
    # to -261% across this window), while the multi-year aggregate is stable
    # and is what actually matters over a projection horizon.
    nwc_value, nwc_detail = None, ""
    if len(balance) >= 2 and len(revenues) >= 2:
        nwc_newest = _net_working_capital(balance[0])
        nwc_oldest = _net_working_capital(balance[-1])
        revenue_delta = revenues[0] - revenues[-1]
        if revenue_delta > 0:
            nwc_value = (nwc_newest - nwc_oldest) / revenue_delta
            nwc_detail = (f"aggregate change in NWC / change in revenue, "
                          f"FY{years[-1]}-FY{years[0]}")
            diagnostics["nwc_newest"] = nwc_newest
            diagnostics["nwc_oldest"] = nwc_oldest
    nwc_pct = derive(
        "nwc_pct", nwc_value, CLAMP_NWC_PCT, nwc_detail,
        0.0, "revenue did not grow over the window; assumed no NWC investment",
    )

    # --- WACC: derived from CAPM + the company's own capital structure -----
    # Derived after tax_rate, which the after-tax cost of debt depends on.
    wacc_value, wacc_detail, wacc_components = _derive_wacc(fin, tax_rate, record_input)
    diagnostics["wacc_components"] = wacc_components

    if wacc_value is None:
        wacc = record("wacc", DEFAULT_WACC, "default",
                      f"derivation failed ({wacc_detail}); fell back to global default")
    else:
        wacc = record("wacc", wacc_value, "derived", wacc_detail)

    # --- Global defaults ---------------------------------------------------
    terminal_growth = record("terminal_growth", DEFAULT_TERMINAL_GROWTH, "default",
                             "global default (~long-run nominal GDP growth)")
    projection_years = record("projection_years", DEFAULT_PROJECTION_YEARS, "default",
                              "modelling convention")

    assumptions = Assumptions(
        revenue_growth=revenue_growth,
        operating_margin=operating_margin,
        tax_rate=tax_rate,
        da_pct=da_pct,
        capex_pct=capex_pct,
        nwc_pct=nwc_pct,
        wacc=wacc,
        terminal_growth=terminal_growth,
        projection_years=int(projection_years),
    )

    unknown = set(overrides) - set(provenance) - set(wacc_inputs)
    if unknown:
        known = sorted(set(provenance) | set(wacc_inputs))
        raise ValueError(
            f"Unknown assumption override(s): {', '.join(sorted(unknown))}. "
            f"Valid names: {', '.join(known)}"
        )

    return DerivedAssumptions(assumptions=assumptions,
                              provenance=provenance,
                              wacc_inputs=wacc_inputs,
                              diagnostics=diagnostics)
