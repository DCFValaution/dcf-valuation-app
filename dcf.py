"""DCF valuation engine — reproduces the logic from AAPL_DCF_Model.xlsx."""

from dataclasses import dataclass, field, replace
from typing import List, Optional


@dataclass
class BaseYearData:
    revenue: float          # $mm
    total_debt: float       # $mm
    cash: float             # $mm — cash & marketable securities
    shares: float           # millions

    # None when the price source would not give us one.
    #
    # A valuation does not need a price: it needs the statements, and the
    # price only decides what to compare the answer with. Refusing the whole
    # thing because the quote endpoint was blocked threw away work that was
    # complete, so a missing price now costs the upside percentage and
    # nothing else.
    current_price: float | None    # $

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

    # --- The three-phase growth structure --------------------------------
    #
    # A single held rate, even faded, says the same thing about year one as
    # about year nine, and those are not the same claim. What a company just
    # did is close to a fact; what it does in five years is a judgement; what
    # it does in ten is arithmetic converging on the economy's growth rate.
    # Three inputs, so each can be argued with separately:
    #
    #   year one        the rate it is on now
    #   years 2-5       the rate it is assumed to hold
    #   years 6-10      a decline from that to the terminal rate
    #
    # None leaves the old single-rate path exactly as it was, so nothing
    # built on `revenue_growth` and `fade_enabled` changes.
    year_one_growth: float | None = None
    years_two_to_five_growth: float | None = None
    # "linear" (Damodaran's shape) or "exponential", which drops faster early
    # and flattens near the terminal rate. Exponential needs both ends
    # positive to mean anything - it is a ratio - so where they are not, the
    # straight line is used and the provenance says so.
    growth_decline_pattern: str = "linear"

    # --- Tax converging to the marginal rate ------------------------------
    #
    # An effective rate is what a company paid, and it is usually below the
    # statutory one: carry-forwards run out, structures get challenged,
    # profits repatriate. Nvidia's 13.3% against a 25% US marginal rate is
    # worth eleven points of after-tax profit, forever. None keeps the
    # effective rate flat, which is what shipped.
    marginal_tax_rate: float | None = None

    # --- Margin converging to a target ------------------------------------
    #
    # None means no change: the margin derived from the filings is held, and
    # that is the right default. A reader who believes it improves or erodes
    # says so here and says by when.
    target_operating_margin: float | None = None
    margin_convergence_year: int | None = None

    # --- Terminal reinvestment --------------------------------------------
    #
    # The perpetuity used to extend the last year's free cash flow forever at
    # g, which assumes growth with nothing bought to produce it. Damodaran's
    # rule ties them: to grow at g with a return of ROIC on new capital, a
    # company must reinvest g / ROIC of its profit.
    #
    # None keeps the old perpetuity. Set, it replaces the terminal cash flow
    # with NOPAT x (1 - g / ROIC). `terminal_roic` of None then means the
    # WACC - Damodaran's own default, and the one that says a mature company
    # earns exactly its cost of capital and so creates no value from growth.
    terminal_reinvestment: bool = False
    terminal_roic: float | None = None

    # --- Depreciation rising toward investment ----------------------------
    #
    # The third caller of the shared mechanism, after the speculative path
    # and the user-built hypothetical. Capex already glides, through
    # `capex_terminal_pct`; depreciation did not, so a company building out
    # was assumed to keep depreciating the much smaller asset base it has
    # now - which is why a heavy spender's forecast never turned cash
    # positive however long it ran.
    #
    # None holds depreciation flat, which is what shipped. The end point
    # comes from `reinvestment.py`, so this agrees with the other two
    # callers rather than being a fourth opinion.
    da_terminal_pct: float | None = None


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
    if assumptions.year_one_growth is not None:
        return three_phase_growth(assumptions)

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


HOLD_UNTIL_YEAR = 5          # years 2 to 5 run at the second rate


def three_phase_growth(a: Assumptions) -> List[float]:
    """Year one, then years two to five, then a decline to the terminal rate.

    The decline ends exactly at the final projected year, so the handoff to
    the perpetuity has no seam in it.

    A forecast shorter than six years has no decline phase and simply runs
    out; one shorter than two is year one alone. Neither is the intended
    shape - the horizon is ten - but a caller may ask for either and should
    get the sensible truncation rather than an exception.
    """
    n = a.projection_years
    first = a.year_one_growth
    held = a.years_two_to_five_growth
    if held is None:
        held = first
    terminal = a.terminal_growth

    path = [first]
    for _t in range(2, min(HOLD_UNTIL_YEAR, n) + 1):
        path.append(held)
    steps = n - HOLD_UNTIL_YEAR
    if steps > 0:
        # Exponential is a ratio, so it needs both ends positive to mean
        # anything. Where they are not - a company assumed to shrink, or a
        # terminal rate of nil - there is no constant factor to apply and
        # the straight line is used instead of inventing one.
        exponential = (a.growth_decline_pattern == FADE_EXPONENTIAL
                       and held > 0 and terminal > 0)
        for i in range(1, steps + 1):
            progress = i / steps
            if exponential:
                path.append(held * (terminal / held) ** progress)
            else:
                path.append(held + (terminal - held) * progress)
        path[-1] = terminal
    return path[:n]


def tax_path(a: Assumptions) -> List[float]:
    """The tax rate each projected year.

    An effective rate is what a company paid, and it is usually below the
    statutory one for reasons that expire: carry-forwards run out, structures
    get challenged, profits come home. Carrying it to the terminal year says
    none of that ever happens.

    Flat where no marginal rate was given, which is what shipped.
    """
    n = a.projection_years
    if a.marginal_tax_rate is None:
        return [a.tax_rate] * n
    start, end = a.tax_rate, a.marginal_tax_rate
    if n <= 1:
        return [end]
    return [start + (end - start) * (t / (n - 1)) for t in range(n)]


def margin_path(a: Assumptions) -> List[float]:
    """The operating margin each projected year.

    Flat unless a target and a year to reach it were both given. Holding the
    margin from the filings is the right default: it is the one figure in
    the model that came from the company rather than from a judgement about
    the company.
    """
    n = a.projection_years
    target = a.target_operating_margin
    by_year = a.margin_convergence_year
    if target is None or not by_year:
        return [a.operating_margin] * n
    start = a.operating_margin
    return [start + (target - start) * min(t / by_year, 1.0)
            for t in range(1, n + 1)]


def da_path(a: Assumptions) -> List[float]:
    """Depreciation as a share of revenue, each projected year.

    Assets bought in year three depreciate in years four onward, so a
    company whose capital spending is falling has depreciation that is
    still rising. Holding it flat is what made a heavy spender's forecast
    never turn cash positive, however long it ran.

    Flat where no end point was given, which is what shipped.
    """
    n = a.projection_years
    if a.da_terminal_pct is None:
        return [a.da_pct] * n
    start, end = a.da_pct, a.da_terminal_pct
    if n <= 1:
        return [end]
    return [start + (end - start) * (t / (n - 1)) for t in range(n)]


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
    # Both None together, and only when the price source had no price. The
    # valuation above them is unaffected.
    current_price: float | None
    upside_downside: float | None   # fraction, e.g. -0.457
    tv_pct_of_ev: float             # fraction
    # [wacc_row][g_col] -> intrinsic value per share. A cell is None where that
    # combination is undefined (g >= WACC makes Gordon growth meaningless).
    sensitivity: List[List[Optional[float]]]
    sensitivity_waccs: List[float]
    sensitivity_gs: List[float]


def run_dcf(base: BaseYearData, assumptions: Assumptions,
            *, with_sensitivity: bool = True) -> DCFResult:
    """The model. `with_sensitivity` is False only when called from inside
    the sensitivity table, which would otherwise ask for a table of tables."""
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

    # The tax rate each year, and the margin each year. Both are flat unless
    # the caller asked for convergence, so nothing built on the single-rate
    # assumptions changes.
    tax_by_year = tax_path(assumptions)
    margin_by_year = margin_path(assumptions)
    da_by_year = da_path(assumptions)

    for t in range(1, n + 1):
        revenue = prev_revenue * (1 + growth_by_year[t - 1])
        ebit = revenue * margin_by_year[t - 1]
        tax = ebit * tax_by_year[t - 1]
        nopat = ebit - tax
        da = revenue * da_by_year[t - 1]
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

    # --- Terminal value ---
    if assumptions.terminal_reinvestment:
        # Growth has to be paid for. A company growing at g with a return of
        # ROIC on new capital reinvests g / ROIC of its profit to do it, so
        # the cash left over is NOPAT x (1 - g / ROIC) - not the last
        # forecast year's free cash flow, which carries whatever investment
        # that year happened to need.
        #
        # ROIC defaults to the discount rate, which says a mature company
        # earns exactly its cost of capital and so creates no value by
        # growing. That is Damodaran's default and the conservative one: any
        # higher ROIC is a claim about a competitive advantage lasting
        # forever.
        roic = assumptions.terminal_roic or wacc
        reinvestment_rate = g / roic if roic else 0.0
        terminal_value = (years[-1].nopat * (1 + g) * (1 - reinvestment_rate)
                          / (wacc - g))
    else:
        terminal_value = years[-1].ufcf * (1 + g) / (wacc - g)
    pv_terminal_value = terminal_value * years[-1].discount_factor

    # --- Enterprise & equity value ---
    enterprise_value = pv_ufcf_sum + pv_terminal_value
    # Minority interest leaves with the debt: both are claims on the
    # consolidated business that are not the parent shareholders'.
    equity_value = (enterprise_value + base.cash - base.total_debt
                    - base.minority_interest)
    intrinsic_value_per_share = equity_value / base.shares

    # No price, no comparison. The alternative - a zero price, or today's
    # value expressed against nothing - would read as a figure rather than
    # as an absence, which is the whole thing to avoid here.
    upside_downside = (
        None if not base.current_price
        else (intrinsic_value_per_share - base.current_price) / base.current_price)
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

    # Each cell is this model with two inputs moved, and EVERY other input
    # carried. Rebuilding the assumptions field by field is how the grid
    # drifted: each new input had to be remembered here too, and the capex
    # fade, the growth path, the tax convergence and the depreciation path
    # were each forgotten in turn - which shows up as the centre cell no
    # longer reproducing the figure it sits under.
    sensitivity = []
    if with_sensitivity:
        for w in sensitivity_waccs:
            row: List[Optional[float]] = []
            for g_s in sensitivity_gs:
                if g_s >= w:
                    # Gordon growth requires WACC > g; the cell has no meaning.
                    row.append(None)
                    continue
                row.append(_equity_value_per_share(
                    base, replace(assumptions, wacc=w, terminal_growth=g_s)))
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
    """The same calculation as the headline, for one cell of the table.

    This was an inline copy of the bridge, kept for speed, and it drifted:
    it applied one flat growth rate where the engine had a fade, flat tax
    where the engine converged it, and flat depreciation where the engine
    glided it. Every drift showed up the same way - the centre cell no
    longer reproduced the figure it sits under, which is the one thing the
    table must never do.

    So it runs the engine. Twenty-five cells of a model this size cost
    almost nothing, and a copy that cannot fall behind is worth more than
    the microseconds.
    """
    return run_dcf(base, assumptions,
                   with_sensitivity=False).intrinsic_value_per_share
