"""DCF valuation engine — reproduces the logic from AAPL_DCF_Model.xlsx."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BaseYearData:
    revenue: float          # $mm
    total_debt: float       # $mm
    cash: float             # $mm — cash & marketable securities
    shares: float           # millions
    current_price: float    # $


@dataclass
class Assumptions:
    revenue_growth: float       # e.g. 0.06 for 6%
    operating_margin: float     # EBIT / Revenue, e.g. 0.32
    tax_rate: float             # e.g. 0.16
    da_pct: float               # D&A as % of revenue
    capex_pct: float            # Capex as % of revenue
    nwc_pct: float              # Change in NWC as % of revenue *growth*
    wacc: float                 # discount rate
    terminal_growth: float      # Gordon-growth perpetuity rate (g)
    projection_years: int = 5


@dataclass
class ProjectionYear:
    period: int
    revenue: float
    ebit: float
    tax: float
    nopat: float
    da: float
    capex: float
    delta_nwc: float
    ufcf: float
    discount_factor: float
    pv_ufcf: float


# The sensitivity grid is built as offsets around the model's own WACC and
# terminal growth rather than a fixed band. A fixed 7.5-9.5% band was fine when
# every company shared a WACC of 8.5%, but once WACC is derived per company the
# grid can miss the actual figure entirely - a sensitivity table that does not
# bracket the value being used is worse than none.
#
# The offsets below reproduce the original [7.5, 8.0, 8.5, 9.0, 9.5] and
# [2.0, 2.5, 3.0, 3.5, 4.0] grids exactly when WACC is 8.5% and g is 3.0%.
SENSITIVITY_OFFSETS = (-2, -1, 0, 1, 2)
SENSITIVITY_WACC_STEP = 0.005
SENSITIVITY_GROWTH_STEP = 0.005


@dataclass
class DCFResult:
    years: List[ProjectionYear]
    pv_ufcf_sum: float
    terminal_value: float
    pv_terminal_value: float
    enterprise_value: float
    equity_value: float
    intrinsic_value_per_share: float
    current_price: float
    upside_downside: float          # fraction, e.g. -0.457
    tv_pct_of_ev: float             # fraction
    # [wacc_row][g_col] -> intrinsic value per share. A cell is None where that
    # combination is undefined (g >= WACC makes Gordon growth meaningless).
    sensitivity: List[List[Optional[float]]]
    sensitivity_waccs: List[float]
    sensitivity_gs: List[float]


def run_dcf(base: BaseYearData, assumptions: Assumptions) -> DCFResult:
    n = assumptions.projection_years
    g = assumptions.terminal_growth
    wacc = assumptions.wacc

    # --- Revenue projection & UFCF build ---
    years = []
    prev_revenue = base.revenue

    for t in range(1, n + 1):
        revenue = prev_revenue * (1 + assumptions.revenue_growth)
        ebit = revenue * assumptions.operating_margin
        tax = ebit * assumptions.tax_rate
        nopat = ebit - tax
        da = revenue * assumptions.da_pct
        capex = revenue * assumptions.capex_pct
        delta_nwc = (revenue - prev_revenue) * assumptions.nwc_pct
        ufcf = nopat + da - capex - delta_nwc
        discount_factor = 1 / (1 + wacc) ** t
        pv_ufcf = ufcf * discount_factor

        years.append(ProjectionYear(
            period=t,
            revenue=revenue,
            ebit=ebit,
            tax=tax,
            nopat=nopat,
            da=da,
            capex=capex,
            delta_nwc=delta_nwc,
            ufcf=ufcf,
            discount_factor=discount_factor,
            pv_ufcf=pv_ufcf,
        ))
        prev_revenue = revenue

    pv_ufcf_sum = sum(y.pv_ufcf for y in years)

    # --- Terminal value (Gordon Growth) ---
    last_ufcf = years[-1].ufcf
    terminal_value = last_ufcf * (1 + g) / (wacc - g)
    pv_terminal_value = terminal_value * years[-1].discount_factor

    # --- Enterprise & equity value ---
    enterprise_value = pv_ufcf_sum + pv_terminal_value
    equity_value = enterprise_value + base.cash - base.total_debt
    intrinsic_value_per_share = equity_value / base.shares

    upside_downside = (intrinsic_value_per_share - base.current_price) / base.current_price
    tv_pct_of_ev = pv_terminal_value / enterprise_value

    # --- Sensitivity table, centred on this model's own WACC and g ---
    # Rounding cleans up float noise so that, e.g., 0.085 - 2*0.005 reads as
    # exactly 0.075 rather than 0.07500000000000001. It is done at 10dp rather
    # than a display-like precision on purpose: the centre cell must reproduce
    # the headline valuation exactly, and a derived WACC carries many
    # significant digits. Rounding the axis to 6dp shifted the centre cell by
    # ~$0.0002 - invisible on screen, but enough to break the invariant that
    # sensitivity[centre][centre] == intrinsic_value_per_share.
    sensitivity_waccs = [round(wacc + i * SENSITIVITY_WACC_STEP, 10)
                         for i in SENSITIVITY_OFFSETS]
    sensitivity_gs = [round(g + i * SENSITIVITY_GROWTH_STEP, 10)
                      for i in SENSITIVITY_OFFSETS]

    sensitivity = []
    for w in sensitivity_waccs:
        row: List[Optional[float]] = []
        for g_s in sensitivity_gs:
            if g_s >= w:
                # Gordon growth requires WACC > g; the cell has no meaning.
                row.append(None)
                continue
            sens_assumptions = Assumptions(
                revenue_growth=assumptions.revenue_growth,
                operating_margin=assumptions.operating_margin,
                tax_rate=assumptions.tax_rate,
                da_pct=assumptions.da_pct,
                capex_pct=assumptions.capex_pct,
                nwc_pct=assumptions.nwc_pct,
                wacc=w,
                terminal_growth=g_s,
                projection_years=n,
            )
            sens_result = _equity_value_per_share(base, sens_assumptions)
            row.append(sens_result)
        sensitivity.append(row)

    return DCFResult(
        years=years,
        pv_ufcf_sum=pv_ufcf_sum,
        terminal_value=terminal_value,
        pv_terminal_value=pv_terminal_value,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        intrinsic_value_per_share=intrinsic_value_per_share,
        current_price=base.current_price,
        upside_downside=upside_downside,
        tv_pct_of_ev=tv_pct_of_ev,
        sensitivity=sensitivity,
        sensitivity_waccs=sensitivity_waccs,
        sensitivity_gs=sensitivity_gs,
    )


def _equity_value_per_share(base: BaseYearData, assumptions: Assumptions) -> float:
    """Lightweight inner calculation used only for the sensitivity table."""
    n = assumptions.projection_years
    g = assumptions.terminal_growth
    wacc = assumptions.wacc

    prev_revenue = base.revenue
    pv_sum = 0.0
    last_ufcf = 0.0
    last_df = 0.0

    for t in range(1, n + 1):
        revenue = prev_revenue * (1 + assumptions.revenue_growth)
        ebit = revenue * assumptions.operating_margin
        nopat = ebit * (1 - assumptions.tax_rate)
        da = revenue * assumptions.da_pct
        capex = revenue * assumptions.capex_pct
        delta_nwc = (revenue - prev_revenue) * assumptions.nwc_pct
        ufcf = nopat + da - capex - delta_nwc
        df = 1 / (1 + wacc) ** t
        pv_sum += ufcf * df
        prev_revenue = revenue
        last_ufcf = ufcf
        last_df = df

    tv = last_ufcf * (1 + g) / (wacc - g)
    pv_tv = tv * last_df
    ev = pv_sum + pv_tv
    equity = ev + base.cash - base.total_debt
    return equity / base.shares
