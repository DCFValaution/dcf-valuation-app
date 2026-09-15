"""
Dividend discount model engine.

The counterpart to dcf.py for companies whose free cash flow is not a
meaningful construct - banks and insurers, where debt is raw material rather
than financing. Pure arithmetic, no I/O: it values whatever inputs it is
handed, and deciding whether those inputs make sense is somebody else's job
(see ddm_assumptions.py and the guard in analysis.py).

TWO-STAGE STRUCTURE
-------------------
    D_t  = D0 x (1 + g1)^t                      for t = 1..N
    TV_N = D_N x (1 + g2) / (r - g2)            Gordon growth at year N
    V    = sum D_t / (1 + r)^t  +  TV_N / (1 + r)^N

With N = 0 this collapses to the single-stage Gordon model,
V = D0 x (1 + g) / (r - g). When g1 == g2 the two-stage value equals the
single-stage value for any N - an identity the tests use to check the
arithmetic independently of any hand calculation.

The shape deliberately mirrors the DCF - an explicit forecast followed by a
terminal value - so the two methods read the same way, and the sensitivity
grid reuses the DCF's offsets and step sizes so their tables are directly
comparable.
"""

from dataclasses import dataclass
from typing import List, Optional

from dcf import SENSITIVITY_GROWTH_STEP, SENSITIVITY_OFFSETS, SENSITIVITY_WACC_STEP

# The DCF steps its discount-rate axis by the WACC step. The DDM's discount
# rate is the cost of equity, and it is stepped by the same amount.
SENSITIVITY_COST_OF_EQUITY_STEP = SENSITIVITY_WACC_STEP


@dataclass
class DDMInputs:
    current_dividend: float   # D0: annual dividend per share, $
    current_price: float      # $


@dataclass
class DDMAssumptions:
    cost_of_equity: float      # r
    dividend_growth: float     # g1: stage-one annual dividend growth
    high_growth_years: int     # N: length of stage one, in years
    terminal_growth: float     # g2: perpetual growth after stage one


@dataclass
class DividendYear:
    period: int
    dividend: float
    discount_factor: float
    pv_dividend: float


@dataclass
class DDMResult:
    years: List[DividendYear]
    pv_dividends_sum: float
    terminal_value: float
    pv_terminal_value: float
    intrinsic_value_per_share: float
    current_price: float
    upside_downside: float          # fraction, e.g. -0.12
    tv_pct_of_value: float          # fraction
    # [cost_of_equity_row][g_col] -> value per share. None where g >= r,
    # for which the perpetuity has no finite value.
    sensitivity: List[List[Optional[float]]]
    sensitivity_costs_of_equity: List[float]
    sensitivity_gs: List[float]


def _validate(inputs: DDMInputs, a: DDMAssumptions) -> None:
    if inputs.current_dividend <= 0:
        raise ValueError("A dividend discount model needs a positive current dividend.")
    if inputs.current_price <= 0:
        raise ValueError("A current share price is needed to compare the value against.")
    if a.high_growth_years < 0:
        raise ValueError("high_growth_years cannot be negative.")
    if a.cost_of_equity <= -1 or a.dividend_growth <= -1 or a.terminal_growth <= -1:
        raise ValueError("Rates must be greater than -100%.")
    if a.terminal_growth >= a.cost_of_equity:
        raise ValueError(
            f"Terminal growth ({a.terminal_growth:.2%}) must be below the cost of "
            f"equity ({a.cost_of_equity:.2%}); otherwise the perpetuity has no "
            "finite value."
        )


def _two_stage(d0: float, r: float, g1: float, n: int,
               g2: float) -> tuple[List[DividendYear], float, float, float]:
    """Returns (years, pv of stage-one dividends, terminal value, pv of TV)."""
    years: List[DividendYear] = []
    dividend = d0
    pv_sum = 0.0
    for t in range(1, n + 1):
        dividend *= 1 + g1
        discount_factor = 1 / (1 + r) ** t
        pv = dividend * discount_factor
        years.append(DividendYear(t, dividend, discount_factor, pv))
        pv_sum += pv

    # `dividend` is now D_N, or D0 itself when there is no stage one.
    terminal_value = dividend * (1 + g2) / (r - g2)
    pv_terminal_value = terminal_value / (1 + r) ** n
    return years, pv_sum, terminal_value, pv_terminal_value


def run_ddm(inputs: DDMInputs, assumptions: DDMAssumptions) -> DDMResult:
    _validate(inputs, assumptions)

    n = int(assumptions.high_growth_years)
    r = assumptions.cost_of_equity
    g1 = assumptions.dividend_growth
    g2 = assumptions.terminal_growth
    d0 = inputs.current_dividend

    years, pv_sum, terminal_value, pv_terminal_value = _two_stage(d0, r, g1, n, g2)
    value = pv_sum + pv_terminal_value

    # --- Sensitivity, centred on this model's own r and g ------------------
    # Rounded at 10dp for the same reason as the DCF grid: clean axis labels,
    # while the centre cell still reproduces the headline value exactly.
    costs = [round(r + i * SENSITIVITY_COST_OF_EQUITY_STEP, 10) for i in SENSITIVITY_OFFSETS]
    growths = [round(g2 + i * SENSITIVITY_GROWTH_STEP, 10) for i in SENSITIVITY_OFFSETS]

    sensitivity: List[List[Optional[float]]] = []
    for cost in costs:
        row: List[Optional[float]] = []
        for growth in growths:
            if growth >= cost:
                row.append(None)
                continue
            _, s_pv, _, s_pv_tv = _two_stage(d0, cost, g1, n, growth)
            row.append(s_pv + s_pv_tv)
        sensitivity.append(row)

    return DDMResult(
        years=years,
        pv_dividends_sum=pv_sum,
        terminal_value=terminal_value,
        pv_terminal_value=pv_terminal_value,
        intrinsic_value_per_share=value,
        current_price=inputs.current_price,
        upside_downside=(value - inputs.current_price) / inputs.current_price,
        tv_pct_of_value=pv_terminal_value / value,
        sensitivity=sensitivity,
        sensitivity_costs_of_equity=costs,
        sensitivity_gs=growths,
    )
