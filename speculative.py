"""
Speculative path-to-profitability engine.

THIS IS NOT A VALUATION ENGINE. It exists for one narrow, explicitly requested
purpose: to show what a loss-making company would be worth IF a set of
assumptions about its future came true. dcf.py values what a business earns;
this projects what a business does not yet earn, and every figure it produces
is conditional on that projection. The honesty obligations - the opt-in, the
refusals, the disclaimer - live in analysis.py and api.py, and nothing here
should ever reach a user without them.

THE PATH
--------
From today's revenue R0 and operating margin m0 (typically negative):

    for t = 1..N (years_to_profitability):
        revenue_t = revenue_{t-1} x (1 + g)
        margin_t  = m0 + (m* - m0) x t / N         a straight line to the target
        tax_t     = max(EBIT_t, 0) x tax rate      no tax on losses, no credit for them
        D&A, capex and NWC investment fade in a straight line from today's
        ratios to the mature ones
        UFCF_t    = EBIT_t - tax_t + D&A_t - capex_t - delta NWC_t

The loss-making years are discounted and SUBTRACTED: the cash burned on the way
is a real cost, not a detail. From year N the existing DCF engine
(dcf.run_dcf) takes over unchanged, starting from year-N revenue at the target
margin, and its enterprise value - a value AT year N - is discounted back N
years. Cash and debt are today's.

When a company already sits at its target, with today's ratios equal to the
mature ones, the path is just the first N years of a DCF, and the estimate
equals run_dcf over N + projection_years. The tests use that identity to check
that the two stages join without a seam.
"""

from dataclasses import dataclass
from typing import List, Optional

from dcf import Assumptions, BaseYearData, DCFResult, run_dcf

SENSITIVITY_OFFSETS = (-2, -1, 0, 1, 2)
SENSITIVITY_MARGIN_STEP = 0.05      # target operating margin, 5 points a step
SENSITIVITY_YEARS_STEP = 1          # years to profitability, a year a step


@dataclass
class SpeculativeInputs:
    revenue: float              # $mm, latest fiscal year
    operating_margin: float     # latest; typically negative
    da_pct: float               # today's D&A / revenue
    capex_pct: float            # today's capex / revenue
    nwc_pct: float              # today's change in NWC / change in revenue
    total_debt: float           # $mm
    cash: float                 # $mm
    shares: float               # millions
    current_price: float        # $


@dataclass
class SpeculativeAssumptions:
    years_to_profitability: int
    target_operating_margin: float
    speculative_revenue_growth: float   # until profitability
    post_profitability_growth: float    # the DCF's growth from year N
    mature_capex_pct: float             # D&A is taken to equal capex once mature
    mature_nwc_pct: float
    tax_rate: float
    wacc: float
    terminal_growth: float
    projection_years: int = 5           # the DCF's forecast after year N


@dataclass
class PathYear:
    period: int
    revenue: float
    operating_margin: float
    ebit: float
    tax: float
    da: float
    capex: float
    delta_nwc: float
    ufcf: float
    discount_factor: float
    pv_ufcf: float


@dataclass
class SpeculativeResult:
    path: List[PathYear]
    pv_path_sum: float
    cumulative_cash_burn: float             # negative path cash flows, as a positive $mm
    revenue_at_profitability: float
    value_at_profitability: float           # enterprise value at year N, from the DCF
    pv_value_at_profitability: float
    enterprise_value: float
    equity_value: float
    value_per_share: float
    current_price: float
    estimate_vs_price: float                # fraction
    # pv_value_at_profitability / enterprise_value. Above 1 when the path
    # itself subtracts value; None when the enterprise value is not positive.
    share_from_after_profitability: Optional[float]
    after_profitability: DCFResult
    # [target_margin_row][years_col] -> value per share. None where the target
    # is not a profit or the years are fewer than one.
    sensitivity: List[List[Optional[float]]]
    sensitivity_margins: List[float]
    sensitivity_years: List[int]


def _validate(inputs: SpeculativeInputs, a: SpeculativeAssumptions) -> None:
    if inputs.revenue <= 0:
        raise ValueError("A path to profitability needs revenue to start from.")
    if inputs.shares <= 0:
        raise ValueError("A share count is needed for a per-share figure.")
    if inputs.current_price <= 0:
        raise ValueError("A current share price is needed to compare the estimate against.")
    if a.years_to_profitability < 1:
        raise ValueError("years_to_profitability must be at least 1.")
    if a.projection_years < 1:
        raise ValueError("projection_years must be at least 1.")
    if a.target_operating_margin <= 0:
        raise ValueError("target_operating_margin must be positive: a path to a loss "
                         "is not a path to profitability.")
    if a.terminal_growth >= a.wacc:
        raise ValueError(f"Terminal growth ({a.terminal_growth:.2%}) must be below WACC "
                         f"({a.wacc:.2%}).")


def _simulate(inputs: SpeculativeInputs, a: SpeculativeAssumptions,
              target: float, years: int):
    path: List[PathYear] = []
    previous = inputs.revenue
    pv_sum = 0.0

    for t in range(1, years + 1):
        progress = t / years
        revenue = previous * (1 + a.speculative_revenue_growth)
        margin = inputs.operating_margin + (target - inputs.operating_margin) * progress
        ebit = revenue * margin
        tax = max(ebit, 0.0) * a.tax_rate
        da = revenue * (inputs.da_pct + (a.mature_capex_pct - inputs.da_pct) * progress)
        capex = revenue * (inputs.capex_pct + (a.mature_capex_pct - inputs.capex_pct) * progress)
        nwc_pct = inputs.nwc_pct + (a.mature_nwc_pct - inputs.nwc_pct) * progress
        delta_nwc = (revenue - previous) * nwc_pct
        ufcf = ebit - tax + da - capex - delta_nwc
        discount_factor = 1 / (1 + a.wacc) ** t
        pv = ufcf * discount_factor

        path.append(PathYear(t, revenue, margin, ebit, tax, da, capex, delta_nwc,
                             ufcf, discount_factor, pv))
        pv_sum += pv
        previous = revenue

    # From year N, the standard DCF, exactly as it values any profitable company.
    after = run_dcf(
        BaseYearData(revenue=previous, total_debt=inputs.total_debt, cash=inputs.cash,
                     shares=inputs.shares, current_price=inputs.current_price),
        Assumptions(revenue_growth=a.post_profitability_growth, operating_margin=target,
                    tax_rate=a.tax_rate, da_pct=a.mature_capex_pct,
                    capex_pct=a.mature_capex_pct, nwc_pct=a.mature_nwc_pct,
                    wacc=a.wacc, terminal_growth=a.terminal_growth,
                    projection_years=a.projection_years),
    )
    pv_after = after.enterprise_value / (1 + a.wacc) ** years
    enterprise_value = pv_sum + pv_after
    equity_value = enterprise_value + inputs.cash - inputs.total_debt
    return path, pv_sum, previous, after, pv_after, enterprise_value, equity_value


def run_speculative(inputs: SpeculativeInputs,
                    assumptions: SpeculativeAssumptions) -> SpeculativeResult:
    _validate(inputs, assumptions)
    years = int(assumptions.years_to_profitability)
    target = assumptions.target_operating_margin

    path, pv_sum, revenue_n, after, pv_after, ev, equity = _simulate(
        inputs, assumptions, target, years)
    per_share = equity / inputs.shares

    # --- Sensitivity: the two assumptions that dominate the estimate -------
    margins = [round(target + i * SENSITIVITY_MARGIN_STEP, 10) for i in SENSITIVITY_OFFSETS]
    years_axis = [years + i * SENSITIVITY_YEARS_STEP for i in SENSITIVITY_OFFSETS]
    grid: List[List[Optional[float]]] = []
    for margin in margins:
        row: List[Optional[float]] = []
        for n in years_axis:
            if margin <= 0 or n < 1:
                row.append(None)
                continue
            *_, cell_equity = _simulate(inputs, assumptions, margin, n)
            row.append(cell_equity / inputs.shares)
        grid.append(row)

    return SpeculativeResult(
        path=path,
        pv_path_sum=pv_sum,
        cumulative_cash_burn=sum(-y.ufcf for y in path if y.ufcf < 0),
        revenue_at_profitability=revenue_n,
        value_at_profitability=after.enterprise_value,
        pv_value_at_profitability=pv_after,
        enterprise_value=ev,
        equity_value=equity,
        value_per_share=per_share,
        current_price=inputs.current_price,
        estimate_vs_price=(per_share - inputs.current_price) / inputs.current_price,
        share_from_after_profitability=(pv_after / ev) if ev > 0 else None,
        after_profitability=after,
        sensitivity=grid,
        sensitivity_margins=margins,
        sensitivity_years=years_axis,
    )
