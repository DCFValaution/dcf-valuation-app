"""
Derives the assumptions behind a speculative path-to-profitability estimate.

Held to the same labelling discipline as the DCF and DDM inputs - derived,
default, clamped or override, always stated - with one difference that should
never be forgotten: more is defaulted here than anywhere else, because the
thing being assumed has not happened.

WHAT PROBING REAL LOSS-MAKERS SHOWED
------------------------------------
Revenue growth. Rivian's three-year CAGR is 48%, but only because 2022 was a
ramp from $1.7bn; its latest year grew 8.4%. Extrapolating the CAGR would have
compounded a start-up spike for years. Growth until profitability is the LOWER
of the latest year's growth and the multi-year CAGR.

Years to profitability. Extrapolating the margin trend gives nonsense: Lucid,
with a -93% gross margin, "reaches" a 10% operating margin in three years on
its recent improvement. Instead the years come from the DISTANCE between
today's margin and the target at a fixed, fast pace of improvement - 15
percentage points a year - which gives Rivian (-67%) six years and Snap (-9%)
two.

Reinvestment. Today's ratios are build-out ratios: Rivian's capex is 23% of
revenue and its working-capital investment 29% of revenue growth. Carried into
the mature years they leave even a 10%-margin Rivian with no cash flow. So the
mature phase uses capex of at most 5% of revenue (less if the company already
spends less), with depreciation equal to it, and working capital capped at
10% of revenue growth. The path fades from today's ratios to those.

Discount rate. The DCF's WACC came out at 4.2% for Lucid and defaulted to
8.5% for Rocket Lab. A low rate on a company burning cash inflates a
speculative figure more than anything else, so it is floored at 10%.
"""

import math
from dataclasses import dataclass, field

from assumptions import (CLAMP_REVENUE_GROWTH, DEFAULT_PROJECTION_YEARS,
                         DEFAULT_TERMINAL_GROWTH, FALLBACK_TAX_RATE, Provenance,
                         _clamp, derive_assumptions)
from dcf import BaseYearData
from industry_betas import industry_capex_for
from market_data import CompanyFinancials, num
from reinvestment import (MATURE_CAPEX_CEILING, depreciation_cover,
                          derived_mature_capex, mature_investment)
from speculative import SpeculativeAssumptions, SpeculativeInputs

SPECULATIVE_GROWTH_CLAMP = (0.00, 0.30)
DEFAULT_TARGET_OPERATING_MARGIN = 0.10
MARGIN_IMPROVEMENT_PACE = 0.15                # percentage points a year, as a fraction
YEARS_TO_PROFITABILITY_BOUNDS = (2, 10)
DEFAULT_YEARS_TO_PROFITABILITY = 5
MATURE_NWC_CLAMP = (0.00, 0.10)
MIN_SPECULATIVE_WACC = 0.10

SPECULATIVE_ASSUMPTION_NAMES = (
    "speculative_revenue_growth", "target_operating_margin", "years_to_profitability",
    "post_profitability_growth", "mature_capex_pct", "mature_da_pct",
    "mature_nwc_pct", "tax_rate",
    "wacc", "terminal_growth", "projection_years",
)
# Inputs to the DCF's own discount-rate build-up, passed through to it.
DISCOUNT_INPUT_NAMES = ("risk_free_rate", "equity_risk_premium", "beta", "cost_of_debt")

_WHOLE_NUMBER_BOUNDS = {"years_to_profitability": (1, 15), "projection_years": (1, 20)}


@dataclass
class DerivedSpeculativeAssumptions:
    assumptions: SpeculativeAssumptions
    inputs: SpeculativeInputs
    provenance: dict[str, Provenance] = field(default_factory=dict)
    wacc_inputs: dict[str, Provenance] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def _ratio(numerator, denominator: float) -> float | None:
    if numerator is None or not denominator or denominator <= 0:
        return None
    return float(numerator) / denominator


def _reject_bad_overrides(overrides: dict) -> None:
    valid = set(SPECULATIVE_ASSUMPTION_NAMES) | set(DISCOUNT_INPUT_NAMES)
    unknown = set(overrides) - valid
    if unknown:
        raise ValueError(
            f"Unknown speculative assumption override(s): {', '.join(sorted(unknown))}. "
            f"Valid names: {', '.join(sorted(valid))}")
    for name, (low, high) in _WHOLE_NUMBER_BOUNDS.items():
        if name in overrides:
            value = float(overrides[name])
            if value != int(value) or not low <= value <= high:
                raise ValueError(f"{name} must be a whole number from {low} to {high}.")


def derive_speculative_assumptions(fin: CompanyFinancials, base: BaseYearData,
                                   overrides: dict | None = None
                                   ) -> DerivedSpeculativeAssumptions:
    """
    Build the speculative assumption set for a loss-making *fin*.

    `overrides` maps any speculative assumption or discount-rate input to a
    user-supplied value, which wins and is recorded as an override.
    """
    overrides = dict(overrides or {})
    _reject_bad_overrides(overrides)

    # The DCF's own derivation supplies the discount rate and today's
    # reinvestment ratios, with any discount-rate input overrides applied.
    dcf = derive_assumptions(
        fin, overrides={k: v for k, v in overrides.items() if k in DISCOUNT_INPUT_NAMES})

    provenance: dict[str, Provenance] = {}

    def record(name: str, value: float, source: str, detail: str, **clamp) -> float:
        if name in overrides:
            supplied = float(overrides[name])
            provenance[name] = Provenance(
                name, supplied, "override",
                f"supplied by caller (would have been {value:.4g} from {source})")
            return supplied
        provenance[name] = Provenance(name, value, source, detail, **clamp)
        return value

    # --- Where the company stands today --------------------------------------
    income = fin.income
    latest = income[0] if income else {}
    revenue = num(latest, "revenue")
    operating_margin = _ratio(latest.get("operatingIncome"), revenue)
    gross_margin = _ratio(latest.get("grossProfit"), revenue)
    revenues = [num(row, "revenue") for row in income]

    latest_growth = (revenues[0] / revenues[1] - 1
                     if len(revenues) >= 2 and revenues[0] > 0 and revenues[1] > 0 else None)
    cagr = ((revenues[0] / revenues[-1]) ** (1 / (len(revenues) - 1)) - 1
            if len(revenues) >= 2 and revenues[0] > 0 and revenues[-1] > 0 else None)

    diagnostics = {
        "fiscal_year": str(latest.get("fiscalYear", "?")),
        "operating_margin": operating_margin,
        "gross_margin": gross_margin,
        "latest_revenue_growth": latest_growth,
        "revenue_cagr": cagr,
        "revenue_history": [
            (str(row.get("fiscalYear", "?")), num(row, "revenue"),
             _ratio(row.get("operatingIncome"), num(row, "revenue")),
             _ratio(row.get("grossProfit"), num(row, "revenue")))
            for row in income
        ],
        # Years to reach break-even at the assumed pace: a fact about how deep
        # the losses are, independent of any target margin or override.
        "years_to_break_even": (math.ceil(-operating_margin / MARGIN_IMPROVEMENT_PACE - 1e-9)
                                if operating_margin is not None and operating_margin < 0 else 0),
    }

    # --- Revenue growth until profitability: the lower of latest and CAGR ----
    if latest_growth is not None or cagr is not None:
        candidates = [g for g in (latest_growth, cagr) if g is not None]
        raw = min(candidates)
        span = len(revenues) - 1
        parts = []
        if latest_growth is not None:
            parts.append(f"the latest year's revenue growth ({latest_growth:.1%})")
        if cagr is not None:
            parts.append(f"the {span}-year CAGR ({cagr:.1%})")
        detail = (f"the lower of {' and '.join(parts)}, so an early ramp from a small "
                  "base is not extrapolated")
        growth, bit = _clamp(raw, SPECULATIVE_GROWTH_CLAMP)
        if bit:
            detail += (f"; raw {raw:.1%} clamped to [{SPECULATIVE_GROWTH_CLAMP[0]:.0%}, "
                       f"{SPECULATIVE_GROWTH_CLAMP[1]:.0%}]")
        growth = record("speculative_revenue_growth", growth,
                        "derived (clamped)" if bit else "derived", detail,
                        **({"raw": raw, "limit": growth, "why": "range"} if bit else {}))
    else:
        growth = record("speculative_revenue_growth", DEFAULT_TERMINAL_GROWTH, "default",
                        "no usable revenue history; fell back to terminal growth")

    # --- Target operating margin ---------------------------------------------
    target = record(
        "target_operating_margin", DEFAULT_TARGET_OPERATING_MARGIN, "default",
        "a generic mature-company operating margin. It is not specific to this company "
        "or its industry, and the company has never earned it")

    # --- Years to profitability: distance to the target at a fast pace -------
    if operating_margin is not None:
        raw_years = math.ceil((target - operating_margin) / MARGIN_IMPROVEMENT_PACE - 1e-9)
        years, bit = _clamp(raw_years, YEARS_TO_PROFITABILITY_BOUNDS)
        detail = (f"the distance from today's {operating_margin:.0%} operating margin to the "
                  f"{target:.0%} target, closed at {MARGIN_IMPROVEMENT_PACE * 100:.0f} "
                  "percentage points a year - a fast pace to sustain")
        if bit:
            detail += (f"; {raw_years} year(s) clamped to "
                       f"[{YEARS_TO_PROFITABILITY_BOUNDS[0]}, {YEARS_TO_PROFITABILITY_BOUNDS[1]}]")
        years = record("years_to_profitability", years,
                       "derived (clamped)" if bit else "derived", detail,
                       **({"raw": raw_years, "limit": years, "why": "range"} if bit else {}))
    else:
        years = record("years_to_profitability", DEFAULT_YEARS_TO_PROFITABILITY, "default",
                       "no operating margin to measure the distance from")

    # --- After profitability ---------------------------------------------------
    post_raw = max(DEFAULT_TERMINAL_GROWTH, growth / 2)
    post, bit = _clamp(post_raw, CLAMP_REVENUE_GROWTH)
    post_growth = record(
        "post_profitability_growth", post, "default",
        f"half the {growth:.1%} assumed until profitability, as growth slows with scale; "
        "floored at terminal growth" + (f" and capped at {CLAMP_REVENUE_GROWTH[1]:.0%}" if bit else ""))

    current_capex = dcf.assumptions.capex_pct
    current_da = dcf.assumptions.da_pct
    # Recorded so callers can state what the company spends TODAY beside what
    # the model assumes it settles at. The two differ for exactly the
    # companies where the difference decides the answer.
    diagnostics["current_capex_pct"] = current_capex
    diagnostics["current_da_pct"] = current_da

    # Where investment settles, and what depreciation settles at beside it.
    #
    # Two wrong answers shipped here before. A flat 5% of revenue, called
    # "typical of a mature business", was substituted for any company
    # spending more - which handed Lucid 67 points of revenue a year and
    # Rivian 19, silently, and was most of their figures. Replacing it with
    # "hold today's rate forever" was no better: a build-out rate carried to
    # the terminal year says the company never finishes building.
    #
    # Now it glides to a level derived from the company's own figures, and
    # depreciation glides to its covered share of that level, so the two
    # converge without meeting. See `reinvestment.py` for the whole argument.
    industry_capex = industry_capex_for(fin.ticker)
    mature = mature_investment(
        derived_mature_capex(current_capex, current_da), industry_capex,
        chosen_by=(
            f"moderated down from today's {current_capex:.1%}"
            if current_capex > MATURE_CAPEX_CEILING else
            "the company's own current rate"
            if current_capex >= current_da else
            f"its own depreciation, above today's {current_capex:.1%} spend"))
    mature_capex = record("mature_capex_pct", mature.capex_pct, "derived",
                          mature.detail)
    # Recomputed from whatever capex ended up being, so an override of the
    # investment rate carries depreciation with it rather than leaving the
    # two describing different companies.
    cover, cover_industry = depreciation_cover(industry_capex)
    mature_da = record(
        "mature_da_pct", mature_capex * cover, "derived",
        f"{cover:.0%} of the {mature_capex:.1%} invested, which is what "
        f"{cover_industry} companies cover out of depreciation (Damodaran). "
        f"The {mature_capex * (1 - cover):.1%} gap is net investment, and it "
        f"does not go away")
    diagnostics["mature_cover"] = cover
    diagnostics["mature_cover_industry"] = cover_industry

    current_nwc = dcf.assumptions.nwc_pct
    nwc, bit = _clamp(current_nwc, MATURE_NWC_CLAMP)
    mature_nwc = record(
        "mature_nwc_pct", nwc, "derived (clamped)" if bit else "derived",
        f"the company's working-capital investment of {current_nwc:.1%} of revenue growth"
        + (f", limited to [{MATURE_NWC_CLAMP[0]:.0%}, {MATURE_NWC_CLAMP[1]:.0%}] once mature" if bit else ""),
        **({"raw": current_nwc, "limit": nwc, "why": "range"} if bit else {}))

    tax = record(
        "tax_rate", FALLBACK_TAX_RATE, "default",
        "US statutory rate. A loss-maker's tax history says nothing about a profitable "
        "future, and accumulated tax losses that would shelter early profits are not "
        "credited, which errs low")

    dcf_wacc = dcf.provenance["wacc"]
    if dcf_wacc.value < MIN_SPECULATIVE_WACC:
        wacc = record(
            "wacc", MIN_SPECULATIVE_WACC, "derived (clamped)",
            f"{dcf_wacc.detail}; that gave {dcf_wacc.value:.2%}, raised to a "
            f"{MIN_SPECULATIVE_WACC:.0%} floor for a company that has never been profitable",
            raw=dcf_wacc.value, limit=MIN_SPECULATIVE_WACC, why="floor")
    else:
        wacc = record("wacc", dcf_wacc.value, dcf_wacc.source, dcf_wacc.detail)

    terminal = record("terminal_growth", DEFAULT_TERMINAL_GROWTH, "default",
                      "conservative long-run default; adjustable")
    projection = record("projection_years", DEFAULT_PROJECTION_YEARS, "default",
                        "years of mature-company forecast after profitability, before "
                        "the terminal value")

    return DerivedSpeculativeAssumptions(
        assumptions=SpeculativeAssumptions(
            years_to_profitability=int(years),
            target_operating_margin=target,
            speculative_revenue_growth=growth,
            post_profitability_growth=post_growth,
            mature_capex_pct=mature_capex,
            mature_da_pct=mature_da,
            mature_nwc_pct=mature_nwc,
            tax_rate=tax,
            wacc=wacc,
            terminal_growth=terminal,
            projection_years=int(projection),
        ),
        inputs=SpeculativeInputs(
            revenue=base.revenue,
            operating_margin=operating_margin if operating_margin is not None else 0.0,
            da_pct=dcf.assumptions.da_pct,
            capex_pct=dcf.assumptions.capex_pct,
            nwc_pct=dcf.assumptions.nwc_pct,
            total_debt=base.total_debt,
            cash=base.cash,
            shares=base.shares,
            current_price=base.current_price,
        ),
        provenance=provenance,
        wacc_inputs=dcf.wacc_inputs,
        diagnostics=diagnostics,
    )
