"""DCF valuation engine — reproduces the logic from AAPL_DCF_Model.xlsx."""

from dataclasses import dataclass, field, replace
from typing import List, Optional


@dataclass
class BaseYearData:
    revenue: float          # $mm
    total_debt: float       # $mm
    cash: float             # $mm — cash & marketable securities
    shares: float           # millions
    current_price: float    # $

    # Minority (non-controlling) interest, $mm.
    #
    # Enterprise value is built from fully consolidated cash flows, which
    # include all of a partly-owned subsidiary's revenue and margin, not the
    # parent's share of it. The outside shareholders' claim on that has to
    # come back out, or the per-share figure values cash flows the parent
    # does not own. It is zero for most companies and worth ten percent of
    # the figure for a holding company.
    #
    # Defaulted rather than required so that a caller constructing a base
    # year by hand - the tests, the hypothetical screen - keeps working and
    # gets the no-minority-interest case, which is the common one.
    minority_interest: float = 0.0


@dataclass
class Assumptions:
    revenue_growth: float       # e.g. 0.06 for 6%
    operating_margin: float     # EBIT / Revenue, e.g. 0.32
    tax_rate: float             # e.g. 0.16
    da_pct: float               # D&A as % of revenue
    # Capex as % of revenue in the FIRST projected year - what the company is
    # spending now, not an average of what it used to. See capex_path().
    capex_pct: float
    nwc_pct: float              # Change in NWC as % of revenue *growth*
    wacc: float                 # discount rate
    terminal_growth: float      # Gordon-growth perpetuity rate (g)
    projection_years: int = 5

    # --- Optional growth fade -------------------------------------------
    # Off by default, and when off the projection is exactly what it always
    # was: one growth rate, held for every year.
    #
    # A flat rate up to the final year and then a perpetuity at a much lower
    # one puts a cliff in the middle of the model - the company grows 18% in
    # year five and 2.5% forever after. The fade removes the cliff by holding
    # the starting rate for a while and then gliding to the terminal rate,
    # arriving exactly at the last projected year so the handoff to the
    # perpetuity is seamless.
    fade_enabled: bool = False
    # The last year that still grows at the full starting rate. The glide runs
    # from here to the final projected year.
    fade_start_year: int = 1
    fade_pattern: str = "linear"   # "linear" | "exponential"

    # --- The capex fade ---------------------------------------------------
    # Always on, unlike the growth fade, because there is no defensible flat
    # default to fall back to. A company mid-buildout spending a third of its
    # revenue on capital does not spend that forever, so holding the current
    # rate projects the buildout into perpetuity - Alphabet at $4.47 a share.
    # But holding a three-year median projects a buildout that is visibly
    # happening as though it were not - Alphabet at $162.36, and the figure
    # the base year now contradicts on screen. Both flat answers are wrong,
    # in opposite directions; the truth is a path.
    #
    # None means no fade: capex_pct is held flat for every year, which is what
    # the model did before and what a caller building assumptions by hand
    # still gets.
    capex_terminal_pct: float | None = None


FADE_LINEAR = "linear"
FADE_EXPONENTIAL = "exponential"


def growth_path(assumptions: Assumptions) -> List[float]:
    """The growth rate used in each projected year, in order.

    With the fade off this is the starting rate repeated, which is what the
    model has always done. With it on, the rate holds through
    `fade_start_year` and then glides to the terminal rate, reaching it
    exactly at the final projected year.

    Two shapes. Linear takes equal percentage-point steps. Exponential
    multiplies by a constant factor each year, so the rate falls fast at
    first and slowly later - usually the more realistic of the two for a
    company whose growth is decaying rather than being managed down.

    The exponential shape is a ratio, so it needs both ends positive and
    non-zero to mean anything; where they are not, there is no sensible
    constant factor and the linear shape is used instead rather than
    inventing one.
    """
    n = assumptions.projection_years
    start = assumptions.revenue_growth
    if not assumptions.fade_enabled:
        return [start] * n

    terminal = assumptions.terminal_growth
    # A fade that starts at or after the last year has nowhere to run, which
    # is not an error: it is simply the flat projection asked for a different
    # way.
    hold = max(1, min(assumptions.fade_start_year, n))
    steps = n - hold
    if steps <= 0:
        return [start] * n

    exponential = (
        assumptions.fade_pattern == FADE_EXPONENTIAL
        and start > 0
        and terminal > 0
    )

    path = [start] * hold
    for i in range(1, steps + 1):
        progress = i / steps
        if exponential:
            path.append(start * (terminal / start) ** progress)
        else:
            path.append(start + (terminal - start) * progress)
    # The last year is the terminal rate exactly, whatever the arithmetic
    # above left behind: the perpetuity that follows begins from this rate,
    # and a float's width of daylight between them is a seam.
    path[-1] = terminal
    return path


def capex_path(assumptions: Assumptions) -> List[float]:
    """Capital expenditure as a share of revenue, per projected year.

    Year one holds at `capex_pct` - the trailing-twelve-month intensity, the
    same figure the base year displays - and the remaining years step to
    `capex_terminal_pct`, landing on it exactly in the final year so the
    perpetuity that follows begins from a normalized level rather than from
    the middle of a buildout.

    The shape mirrors growth_path(): one held year, then equal steps, with the
    last year pinned. It does not share growth_path's parameters, though -
    see the note on the hold below.

    With `capex_terminal_pct` unset this is the starting rate repeated, which
    is exactly what the model did before the fade existed.
    """
    n = assumptions.projection_years
    start = assumptions.capex_pct
    terminal = assumptions.capex_terminal_pct
    if terminal is None:
        return [start] * n

    # One held year, always - deliberately NOT fade_start_year, which belongs
    # to the growth fade. Sharing it would mean that moving the growth glide
    # out to year four quietly held capital spending up for three more years
    # too, which is not a thing the user asked for and not a thing they would
    # see. The two fades answer different questions and keep their own shape.
    steps = n - 1
    if steps <= 0:
        return [start] * n

    path = [start]
    for i in range(1, steps + 1):
        path.append(start + (terminal - start) * (i / steps))
    # Pinned, for the same reason the growth fade pins its last year: the
    # perpetuity is built from this year's cash flow, and a float's width
    # between the final rate and the normalized one is a seam in the model.
    path[-1] = terminal
    return path


@dataclass
class ProjectionYear:
    period: int
    revenue: float
    # The rate this year actually grew at. Equal to the single assumption
    # with the fade off; the point of recording it is that with the fade on
    # it is not, and a reader must be able to see what was applied.
    revenue_growth: float
    ebit: float
    tax: float
    nopat: float
    da: float
    capex: float
    # The share of revenue this year's capex actually was. Equal to the single
    # assumption where there is no fade; where there is, this is the glide
    # made visible, and the table and the workbook both read it.
    capex_pct: float
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
    # One rate per projected year. With the fade off every entry is the same
    # starting rate, so this is the flat projection it has always been.
    growth_by_year = growth_path(assumptions)
    # One capex intensity per projected year: the current rate gliding to the
    # normalized one, or the flat rate repeated where no anchor was given.
    capex_by_year = capex_path(assumptions)

    years = []
    prev_revenue = base.revenue

    for t in range(1, n + 1):
        revenue = prev_revenue * (1 + growth_by_year[t - 1])
        ebit = revenue * assumptions.operating_margin
        tax = ebit * assumptions.tax_rate
        nopat = ebit - tax
        da = revenue * assumptions.da_pct
        capex_pct = capex_by_year[t - 1]
        capex = revenue * capex_pct
        delta_nwc = (revenue - prev_revenue) * assumptions.nwc_pct
        ufcf = nopat + da - capex - delta_nwc
        discount_factor = 1 / (1 + wacc) ** t
        pv_ufcf = ufcf * discount_factor

        years.append(ProjectionYear(
            period=t,
            revenue=revenue,
            revenue_growth=growth_by_year[t - 1],
            ebit=ebit,
            tax=tax,
            nopat=nopat,
            da=da,
            capex=capex,
            capex_pct=capex_pct,
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
    # Minority interest leaves with the debt: both are claims on the
    # consolidated business that are not the parent shareholders'.
    equity_value = (enterprise_value + base.cash - base.total_debt
                    - base.minority_interest)
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
    #
    # The centre is the model's own figure untouched, not a rounded copy of
    # it. Rounding is there to tidy the *offsets* - so 0.085 - 2*0.005 reads
    # as 0.075 rather than 0.07500000000000001 - and the centre has no
    # offset to tidy. Rounding it anyway was harmless while terminal growth
    # was the constant 0.025, which survives a round trip to ten places;
    # once it became a derived figure capped at WACC less a margin it no
    # longer did, and the centre cell stopped reproducing the headline.
    sensitivity_waccs = [wacc if i == 0 else round(wacc + i * SENSITIVITY_WACC_STEP, 10)
                         for i in SENSITIVITY_OFFSETS]
    sensitivity_gs = [g if i == 0 else round(g + i * SENSITIVITY_GROWTH_STEP, 10)
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
                # Carried, or the centre cell loses the capex fade and stops
                # reproducing the headline - which is the one thing this grid
                # is required to do.
                capex_terminal_pct=assumptions.capex_terminal_pct,
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


# How much to raise the growth assumption by when asking which way the
# valuation moves. A percentage point: large enough that the answer is not
# floating-point noise, small enough to be the local slope rather than the
# average over a wide span.
GROWTH_PROBE = 0.01


def growth_reduces_value(base: BaseYearData, assumptions: Assumptions,
                         baseline_per_share: float,
                         probe: float = GROWTH_PROBE) -> bool:
    """Whether assuming faster revenue growth *lowers* the valuation.

    True for a business that has to spend more to grow than growing earns
    it: when each extra dollar of revenue costs more in working capital and
    new assets than it returns in after-tax operating profit, the extra
    revenue is bought at a loss and buying more of it is worth less.

    Measured by running the model again a percentage point faster and
    comparing, rather than by comparing the per-dollar figures directly.
    The two are not equivalent, and the arithmetic reason matters: the
    working-capital charge falls on the *increment* while the profit falls
    on the *level*, so the drag enters as nwc x g/(1+g) rather than nwc x g.
    Coca-Cola reinvests more per incremental dollar than it earns on one and
    its value still rises with growth, because its margin is wide enough to
    absorb the drag at any plausible rate. A rule comparing the two
    per-dollar numbers would wrongly flag it.

    Running the real engine also guarantees the note cannot contradict the
    slider the user is about to move: it is the same calculation, asked the
    same question.
    """
    faster = replace(assumptions,
                     revenue_growth=assumptions.revenue_growth + probe)
    return run_dcf(base, faster).intrinsic_value_per_share < baseline_per_share


def growth_economics(assumptions: Assumptions) -> tuple[float, float]:
    """What a dollar of extra revenue returns, and what it costs to get.

    Returns (profit, reinvestment), both as a fraction of that dollar.
    Depreciation is netted against capex because it is added back in the
    cash flow: what the business actually funds is the capital spending
    above its own depreciation.
    """
    profit = assumptions.operating_margin * (1 - assumptions.tax_rate)
    reinvestment = (assumptions.capex_pct - assumptions.da_pct
                    + assumptions.nwc_pct)
    return profit, reinvestment


def _equity_value_per_share(base: BaseYearData, assumptions: Assumptions) -> float:
    """Lightweight inner calculation used only for the sensitivity table."""
    n = assumptions.projection_years
    g = assumptions.terminal_growth
    wacc = assumptions.wacc

    prev_revenue = base.revenue
    pv_sum = 0.0
    last_ufcf = 0.0
    last_df = 0.0

    # The same capex glide run_dcf applies. Without it the centre cell of the
    # sensitivity table would not reproduce the headline valuation, which is
    # the one thing this inline copy of the bridge exists to guarantee.
    capex_by_year = capex_path(assumptions)

    for t in range(1, n + 1):
        revenue = prev_revenue * (1 + assumptions.revenue_growth)
        ebit = revenue * assumptions.operating_margin
        nopat = ebit * (1 - assumptions.tax_rate)
        da = revenue * assumptions.da_pct
        capex = revenue * capex_by_year[t - 1]
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
    # The same bridge as above. It has to stay the same bridge: the centre
    # cell of the sensitivity table is required to reproduce the headline
    # exactly, so a subtraction missing here would show up as the table
    # disagreeing with the figure it sits under.
    equity = ev + base.cash - base.total_debt - base.minority_interest
    return equity / base.shares
