"""Tests that the DCF engine reproduces the AAPL_DCF_Model.xlsx figures exactly."""

import pytest
from dcf import BaseYearData, Assumptions, run_dcf

# --- Apple FY2025 actuals & market data (from spreadsheet) ---
AAPL_BASE = BaseYearData(
    revenue=416_161,
    total_debt=98_657,
    cash=132_420,
    shares=14_773.3,
    current_price=296.42,
)

AAPL_ASSUMPTIONS = Assumptions(
    revenue_growth=0.06,
    operating_margin=0.32,
    tax_rate=0.16,
    da_pct=0.028,
    capex_pct=0.031,
    nwc_pct=0.03,
    wacc=0.085,
    terminal_growth=0.03,
    projection_years=5,
)

# Tolerance: $0.01 per share is sufficient given the spreadsheet uses rounded intermediates
SHARE_PRICE_TOL = 0.01


@pytest.fixture(scope="module")
def result():
    return run_dcf(AAPL_BASE, AAPL_ASSUMPTIONS)


# ---------------------------------------------------------------------------
# Revenue projection
# ---------------------------------------------------------------------------

def test_revenue_year1(result):
    assert abs(result.years[0].revenue - 441_131) < 1

def test_revenue_year5(result):
    assert abs(result.years[4].revenue - 556_917) < 1


# ---------------------------------------------------------------------------
# UFCF line items (Year 1)
# ---------------------------------------------------------------------------

def test_ebit_year1(result):
    assert abs(result.years[0].ebit - 141_162) < 1

def test_nopat_year1(result):
    assert abs(result.years[0].nopat - 118_576) < 1

def test_ufcf_year1(result):
    assert abs(result.years[0].ufcf - 116_503) < 1

def test_ufcf_year5(result):
    assert abs(result.years[4].ufcf - 147_083) < 1


# ---------------------------------------------------------------------------
# Discounting
# ---------------------------------------------------------------------------

def test_discount_factor_year1(result):
    assert abs(result.years[0].discount_factor - 0.922) < 0.001

def test_pv_ufcf_year1(result):
    assert abs(result.years[0].pv_ufcf - 107_376) < 1

def test_pv_ufcf_sum(result):
    assert abs(result.pv_ufcf_sum - 512_705) < 1


# ---------------------------------------------------------------------------
# Terminal value
# ---------------------------------------------------------------------------

def test_terminal_value(result):
    assert abs(result.terminal_value - 2_754_462) < 1

def test_pv_terminal_value(result):
    assert abs(result.pv_terminal_value - 1_831_842) < 1


# ---------------------------------------------------------------------------
# Enterprise & equity value
# ---------------------------------------------------------------------------

def test_enterprise_value(result):
    assert abs(result.enterprise_value - 2_344_547) < 1

def test_equity_value(result):
    assert abs(result.equity_value - 2_378_310) < 1

def test_intrinsic_value_per_share(result):
    assert abs(result.intrinsic_value_per_share - 160.99) <= SHARE_PRICE_TOL

def test_upside_downside(result):
    # -45.7% implied downside
    assert abs(result.upside_downside - (-0.457)) < 0.001

def test_tv_pct_of_ev(result):
    assert abs(result.tv_pct_of_ev - 0.781) < 0.001


# ---------------------------------------------------------------------------
# Sensitivity table  (WACC rows x g columns)
# Spreadsheet values: rows 7.5%→9.5%, cols 2.0%→4.0%
# ---------------------------------------------------------------------------

EXPECTED_SENSITIVITY = [
    [166.57, 180.12, 196.69, 217.40, 244.02],  # WACC 7.5%
    [152.66, 163.75, 177.05, 193.31, 213.64],  # WACC 8.0%
    [140.89, 150.10, 160.99, 174.05, 190.01],  # WACC 8.5%
    [130.81, 138.56, 147.60, 158.29, 171.11],  # WACC 9.0%
    [122.08, 128.67, 136.28, 145.16, 155.65],  # WACC 9.5%
]

def test_sensitivity_shape(result):
    assert len(result.sensitivity) == 5
    assert all(len(row) == 5 for row in result.sensitivity)

def test_sensitivity_values(result):
    for i, row in enumerate(EXPECTED_SENSITIVITY):
        for j, expected in enumerate(row):
            actual = result.sensitivity[i][j]
            assert abs(actual - expected) <= SHARE_PRICE_TOL, (
                f"Sensitivity mismatch at WACC={result.sensitivity_waccs[i]:.1%}, "
                f"g={result.sensitivity_gs[j]:.1%}: "
                f"expected {expected}, got {actual:.2f}"
            )


# ---------------------------------------------------------------------------
# A horizon the user chose
#
# The projection length is a slider between 5 and 10 years, so the model's
# shape is an input rather than a constant. These rebuild the whole thing from
# the definition at each length instead of comparing against a stored answer,
# so a horizon that silently kept projecting five years - or attached the
# terminal value to the wrong year - fails here.
# ---------------------------------------------------------------------------

HORIZONS = [5, 6, 7, 8, 9, 10]


def _at(years: int):
    return run_dcf(AAPL_BASE, Assumptions(
        revenue_growth=AAPL_ASSUMPTIONS.revenue_growth,
        operating_margin=AAPL_ASSUMPTIONS.operating_margin,
        tax_rate=AAPL_ASSUMPTIONS.tax_rate,
        da_pct=AAPL_ASSUMPTIONS.da_pct,
        capex_pct=AAPL_ASSUMPTIONS.capex_pct,
        nwc_pct=AAPL_ASSUMPTIONS.nwc_pct,
        wacc=AAPL_ASSUMPTIONS.wacc,
        terminal_growth=AAPL_ASSUMPTIONS.terminal_growth,
        projection_years=years,
    ))


@pytest.mark.parametrize("years", HORIZONS)
def test_the_projection_runs_for_the_length_asked_for(years):
    r = _at(years)
    assert len(r.years) == years
    assert [y.period for y in r.years] == list(range(1, years + 1))


@pytest.mark.parametrize("years", HORIZONS)
def test_revenue_compounds_across_the_whole_horizon(years):
    r = _at(years)
    g = AAPL_ASSUMPTIONS.revenue_growth
    for y in r.years:
        assert y.revenue == pytest.approx(
            AAPL_BASE.revenue * (1 + g) ** y.period, rel=1e-12)


@pytest.mark.parametrize("years", HORIZONS)
def test_each_year_is_discounted_by_its_own_period(years):
    r = _at(years)
    w = AAPL_ASSUMPTIONS.wacc
    for y in r.years:
        assert y.discount_factor == pytest.approx(1 / (1 + w) ** y.period, rel=1e-12)
        assert y.pv_ufcf == pytest.approx(y.ufcf * y.discount_factor, rel=1e-12)


@pytest.mark.parametrize("years", HORIZONS)
def test_the_terminal_value_grows_the_final_year_and_discounts_from_it(years):
    r = _at(years)
    last = r.years[-1]
    assert last.period == years
    g, w = AAPL_ASSUMPTIONS.terminal_growth, AAPL_ASSUMPTIONS.wacc
    assert r.terminal_value == pytest.approx(last.ufcf * (1 + g) / (w - g), rel=1e-12)
    assert r.pv_terminal_value == pytest.approx(
        r.terminal_value * last.discount_factor, rel=1e-12)


@pytest.mark.parametrize("years", HORIZONS)
def test_the_bridge_and_the_headline_recompute_at_any_length(years):
    r = _at(years)
    assert r.pv_ufcf_sum == pytest.approx(sum(y.pv_ufcf for y in r.years), rel=1e-12)
    assert r.enterprise_value == pytest.approx(
        r.pv_ufcf_sum + r.pv_terminal_value, rel=1e-12)
    assert r.equity_value == pytest.approx(
        r.enterprise_value + AAPL_BASE.cash - AAPL_BASE.total_debt, rel=1e-12)
    assert r.intrinsic_value_per_share == pytest.approx(
        r.equity_value / AAPL_BASE.shares, rel=1e-12)


@pytest.mark.parametrize("years", HORIZONS)
def test_the_sensitivity_grid_is_built_at_the_same_horizon(years):
    """Its centre cell is the headline, which is only true if every cell was
    computed over the same number of years as the model above it."""
    r = _at(years)
    centre = len(r.sensitivity) // 2
    assert r.sensitivity[centre][centre] == pytest.approx(
        r.intrinsic_value_per_share, rel=1e-6)


def test_a_longer_horizon_leans_less_on_the_terminal_value():
    """The reason for offering the slider at all: more of the answer comes
    from cash flows that were actually forecast."""
    five, ten = _at(5), _at(10)
    assert ten.tv_pct_of_ev < five.tv_pct_of_ev


def test_five_years_is_unchanged_by_the_horizon_being_adjustable():
    """The default must still produce exactly what it always produced."""
    assert _at(5).intrinsic_value_per_share == pytest.approx(
        run_dcf(AAPL_BASE, AAPL_ASSUMPTIONS).intrinsic_value_per_share, rel=1e-12)


# ---------------------------------------------------------------------------
# The optional growth fade
#
# A flat rate to the final year and then a perpetuity at a much lower one puts
# a cliff in the middle of the model: 18% in year five, 2.5% forever after.
# The fade removes it by holding the starting rate and then gliding to the
# terminal one, arriving exactly at the last projected year.
#
# It is opt-in, and the first test here is the one that matters most: with it
# off, nothing about the model may move.
# ---------------------------------------------------------------------------

from dcf import FADE_EXPONENTIAL, FADE_LINEAR, growth_path  # noqa: E402


def _faded(**kw):
    fields = {f: getattr(AAPL_ASSUMPTIONS, f) for f in (
        "revenue_growth", "operating_margin", "tax_rate", "da_pct",
        "capex_pct", "nwc_pct", "wacc", "terminal_growth", "projection_years")}
    fields.update(kw)
    return Assumptions(**fields)


def test_with_the_fade_off_every_year_grows_at_the_one_rate():
    """The flat projection, unchanged and still the default."""
    a = _faded()
    assert a.fade_enabled is False
    assert growth_path(a) == [a.revenue_growth] * a.projection_years


def test_with_the_fade_off_the_whole_result_is_what_it_always_was():
    """The regression that matters: turning a feature on by accident, or
    changing the arithmetic while adding it, must fail here."""
    plain = run_dcf(AAPL_BASE, AAPL_ASSUMPTIONS)
    explicit_off = run_dcf(AAPL_BASE, _faded(fade_enabled=False))
    for field in ("intrinsic_value_per_share", "pv_ufcf_sum", "terminal_value",
                  "pv_terminal_value", "enterprise_value", "equity_value",
                  "tv_pct_of_ev"):
        assert repr(getattr(plain, field)) == repr(getattr(explicit_off, field)), field
    assert [repr(y.revenue) for y in plain.years] == \
           [repr(y.revenue) for y in explicit_off.years]


@pytest.mark.parametrize("pattern", [FADE_LINEAR, FADE_EXPONENTIAL])
def test_the_rate_holds_flat_and_then_glides(pattern):
    a = _faded(revenue_growth=0.18, terminal_growth=0.025, projection_years=10,
               fade_enabled=True, fade_start_year=3, fade_pattern=pattern)
    path = growth_path(a)

    assert len(path) == 10
    assert path[:3] == [0.18, 0.18, 0.18], "held at the starting rate"
    assert path[-1] == 0.025, "and arrives exactly at the terminal rate"
    # Monotonically down in between, with no step back up.
    glide = path[2:]
    assert all(b <= a_ for a_, b in zip(glide, glide[1:]))


def test_the_last_year_is_the_terminal_rate_to_the_bit():
    """Not approximately: the perpetuity starts from this rate, and daylight
    between them is a seam in the model."""
    for pattern in (FADE_LINEAR, FADE_EXPONENTIAL):
        for years in (5, 7, 10):
            for start in (3, 1, years - 1):
                a = _faded(revenue_growth=0.20, terminal_growth=0.025,
                           projection_years=years, fade_enabled=True,
                           fade_start_year=start, fade_pattern=pattern)
                assert growth_path(a)[-1] == 0.025, (pattern, years, start)


def test_exponential_falls_faster_at_first_than_linear():
    """Which is the point of offering it: decay, rather than a managed glide."""
    kw = dict(revenue_growth=0.18, terminal_growth=0.025, projection_years=10,
              fade_enabled=True, fade_start_year=3)
    lin = growth_path(_faded(fade_pattern=FADE_LINEAR, **kw))
    exp = growth_path(_faded(fade_pattern=FADE_EXPONENTIAL, **kw))
    assert exp[3] < lin[3]
    assert exp[-1] == lin[-1] == 0.025


def test_exponential_falls_back_to_linear_where_a_ratio_has_no_meaning():
    """A constant factor needs both ends positive. Rather than invent one, the
    straight line is used - and the result is still a real fade."""
    a = _faded(revenue_growth=0.0, terminal_growth=0.025, projection_years=6,
               fade_enabled=True, fade_start_year=2,
               fade_pattern=FADE_EXPONENTIAL)
    straight = _faded(revenue_growth=0.0, terminal_growth=0.025,
                      projection_years=6, fade_enabled=True,
                      fade_start_year=2, fade_pattern=FADE_LINEAR)
    assert growth_path(a) == growth_path(straight)


@pytest.mark.parametrize("start_year", [5, 6, 20])
def test_a_fade_with_nowhere_to_run_is_simply_flat(start_year):
    """Starting at or after the final year is not an error; it is the flat
    projection asked for a different way."""
    a = _faded(projection_years=5, fade_enabled=True, fade_start_year=start_year)
    assert growth_path(a) == [a.revenue_growth] * 5
    assert run_dcf(AAPL_BASE, a).intrinsic_value_per_share == pytest.approx(
        run_dcf(AAPL_BASE, AAPL_ASSUMPTIONS).intrinsic_value_per_share)


def test_a_starting_rate_below_terminal_rises_rather_than_failing():
    """Apple's derived growth is below the terminal rate, so its 'fade' goes
    up. Allowed, and still lands on the terminal rate."""
    a = _faded(revenue_growth=0.01, terminal_growth=0.025, projection_years=6,
               fade_enabled=True, fade_start_year=2)
    path = growth_path(a)
    assert path[0] == 0.01
    assert path[-1] == 0.025
    assert all(b >= x for x, b in zip(path, path[1:]))


def test_the_projection_actually_uses_the_faded_rates():
    a = _faded(revenue_growth=0.18, terminal_growth=0.025, projection_years=10,
               fade_enabled=True, fade_start_year=3)
    r = run_dcf(AAPL_BASE, a)
    path = growth_path(a)
    assert [y.revenue_growth for y in r.years] == path
    previous = AAPL_BASE.revenue
    for year, rate in zip(r.years, path):
        assert year.revenue == pytest.approx(previous * (1 + rate), rel=1e-12)
        previous = year.revenue


def test_fading_lowers_the_value_of_a_fast_grower():
    """The sanity check on the whole idea: growth that decays is worth less
    than growth held flat to the cliff edge."""
    kw = dict(revenue_growth=0.18, terminal_growth=0.025, projection_years=10)
    flat = run_dcf(AAPL_BASE, _faded(**kw))
    faded = run_dcf(AAPL_BASE, _faded(fade_enabled=True, fade_start_year=3, **kw))
    assert faded.intrinsic_value_per_share < flat.intrinsic_value_per_share
    assert faded.tv_pct_of_ev < flat.tv_pct_of_ev
