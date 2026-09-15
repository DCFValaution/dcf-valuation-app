"""
Tests for the dividend discount model engine (ddm.py).

The engine is pure arithmetic, so it is checked three independent ways:
against values worked by hand, against the identity that a two-stage model
with equal growth rates collapses to single-stage Gordon growth, and against
the structural invariants its sensitivity table must satisfy.

Run with:  python -m pytest test_ddm.py -v
"""

import pytest

from dcf import SENSITIVITY_GROWTH_STEP, SENSITIVITY_WACC_STEP
from ddm import DDMAssumptions, DDMInputs, run_ddm


def ddm(d0=2.0, price=40.0, r=0.08, g1=0.05, n=5, g2=0.03):
    return run_ddm(DDMInputs(current_dividend=d0, current_price=price),
                   DDMAssumptions(cost_of_equity=r, dividend_growth=g1,
                                  high_growth_years=n, terminal_growth=g2))


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------

def test_single_stage_is_gordon_growth():
    # D0 x (1 + g) / (r - g) = 2 x 1.03 / 0.05 = 41.2. Stage-one growth is
    # irrelevant when there is no stage one.
    result = ddm(d0=2.0, r=0.08, g1=0.50, n=0, g2=0.03)
    assert result.intrinsic_value_per_share == pytest.approx(41.2, abs=1e-12)
    assert result.years == []
    assert result.pv_dividends_sum == 0
    assert result.tv_pct_of_value == pytest.approx(1.0)


def test_two_stage_matches_a_hand_calculation():
    """
    D0 = 1, g1 = 10% for 2 years, r = 10%, g2 = 0:
        D1 = 1.10, PV 1.00          D2 = 1.21, PV 1.00
        TV = 1.21 x 1.00 / 0.10 = 12.10, PV = 12.10 / 1.21 = 10.00
        value = 12.00; against a price of 10, upside 20%
    """
    result = ddm(d0=1.0, price=10.0, r=0.10, g1=0.10, n=2, g2=0.0)
    assert [y.dividend for y in result.years] == pytest.approx([1.10, 1.21])
    assert [y.pv_dividend for y in result.years] == pytest.approx([1.0, 1.0])
    assert result.pv_dividends_sum == pytest.approx(2.0)
    assert result.terminal_value == pytest.approx(12.10)
    assert result.pv_terminal_value == pytest.approx(10.0)
    assert result.intrinsic_value_per_share == pytest.approx(12.0)
    assert result.upside_downside == pytest.approx(0.20)
    assert result.tv_pct_of_value == pytest.approx(10.0 / 12.0)


@pytest.mark.parametrize("n", [1, 2, 5, 10, 25])
def test_equal_growth_rates_collapse_to_single_stage(n):
    """An identity, so it checks the two-stage code without trusting any
    hand-worked figure."""
    single = ddm(d0=3.0, r=0.09, g1=0.04, n=0, g2=0.04).intrinsic_value_per_share
    two_stage = ddm(d0=3.0, r=0.09, g1=0.04, n=n, g2=0.04).intrinsic_value_per_share
    assert two_stage == pytest.approx(single, rel=1e-12)


def test_dividends_compound_through_stage_one():
    result = ddm(d0=2.0, g1=0.05, n=5)
    assert [y.period for y in result.years] == [1, 2, 3, 4, 5]
    assert [y.dividend for y in result.years] == pytest.approx(
        [2.0 * 1.05 ** t for t in range(1, 6)])


def test_faster_stage_one_growth_is_worth_more():
    assert ddm(g1=0.08).intrinsic_value_per_share > ddm(g1=0.03).intrinsic_value_per_share


def test_higher_cost_of_equity_is_worth_less():
    assert ddm(r=0.07).intrinsic_value_per_share > ddm(r=0.10).intrinsic_value_per_share


# ---------------------------------------------------------------------------
# Inputs the model cannot value
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("g2", [0.08, 0.09])
def test_growth_at_or_above_cost_of_equity_is_rejected(g2):
    with pytest.raises(ValueError, match="below the cost of equity"):
        ddm(r=0.08, g2=g2)


@pytest.mark.parametrize("d0", [0.0, -1.0])
def test_no_dividend_is_rejected(d0):
    with pytest.raises(ValueError, match="positive current dividend"):
        ddm(d0=d0)


def test_no_price_is_rejected():
    with pytest.raises(ValueError, match="share price"):
        ddm(price=0.0)


def test_negative_stage_one_length_is_rejected():
    with pytest.raises(ValueError, match="high_growth_years"):
        ddm(n=-1)


# ---------------------------------------------------------------------------
# Sensitivity table
# ---------------------------------------------------------------------------

def test_sensitivity_centre_cell_is_the_headline_value():
    # Awkward full-precision rates, like a derived cost of equity, are where a
    # rounded axis would drift off the headline figure.
    result = ddm(r=0.10634, g1=0.09037, n=5, g2=0.025)
    centre = len(result.sensitivity) // 2
    assert result.sensitivity[centre][centre] == pytest.approx(
        result.intrinsic_value_per_share, rel=1e-12)
    assert result.sensitivity_costs_of_equity[centre] == pytest.approx(0.10634)
    assert result.sensitivity_gs[centre] == pytest.approx(0.025)


def test_sensitivity_axes_step_exactly_like_the_dcf():
    result = ddm(r=0.10, g2=0.03)
    costs, growths = result.sensitivity_costs_of_equity, result.sensitivity_gs
    assert len(costs) == len(growths) == 5
    assert all(b - a == pytest.approx(SENSITIVITY_WACC_STEP) for a, b in zip(costs, costs[1:]))
    assert all(b - a == pytest.approx(SENSITIVITY_GROWTH_STEP) for a, b in zip(growths, growths[1:]))


def test_sensitivity_is_monotonic_in_both_directions():
    grid = ddm(r=0.10, g2=0.03).sensitivity
    for row in grid:                       # more growth, more value
        assert row == sorted(row)
    for column in zip(*grid):              # higher cost of equity, less value
        assert list(column) == sorted(column, reverse=True)


def test_sensitivity_blanks_exactly_the_cells_where_growth_meets_cost_of_equity():
    # r = 4% and g = 3% put a 3% cost of equity against growth up to 4%.
    result = ddm(r=0.04, g1=0.03, g2=0.03)
    for cost, row in zip(result.sensitivity_costs_of_equity, result.sensitivity):
        for growth, value in zip(result.sensitivity_gs, row):
            assert (value is None) == (growth >= cost)
    assert any(value is None for row in result.sensitivity for value in row)
