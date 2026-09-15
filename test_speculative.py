"""
Tests for the speculative path-to-profitability engine (speculative.py).

Checked against a hand calculation, against the identity that a company
already at its target is just a DCF over a longer horizon - which proves the
path and the reused DCF join without a seam - and against the invariants of
its sensitivity table.

Run with:  python -m pytest test_speculative.py -v
"""

import pytest

from dcf import Assumptions, BaseYearData, run_dcf
from speculative import SpeculativeAssumptions, SpeculativeInputs, run_speculative


def inputs(**overrides) -> SpeculativeInputs:
    values = dict(revenue=100.0, operating_margin=-0.20, da_pct=0.0, capex_pct=0.0,
                  nwc_pct=0.0, total_debt=0.0, cash=0.0, shares=10.0, current_price=5.0)
    values.update(overrides)
    return SpeculativeInputs(**values)


def assumptions(**overrides) -> SpeculativeAssumptions:
    values = dict(years_to_profitability=2, target_operating_margin=0.10,
                  speculative_revenue_growth=0.10, post_profitability_growth=0.05,
                  mature_capex_pct=0.0, mature_nwc_pct=0.0, tax_rate=0.25, wacc=0.10,
                  terminal_growth=0.02, projection_years=1)
    values.update(overrides)
    return SpeculativeAssumptions(**values)


def test_the_path_matches_a_hand_calculation():
    """
    Revenue 100 growing 10%, margin -20% climbing to +10% over two years, 25%
    tax on profits only, WACC 10%:
        year 1: revenue 110, margin -5%, EBIT -5.5, no tax, cash flow -5.500, PV -5.00
        year 2: revenue 121, margin 10%, EBIT 12.1, tax 3.025, cash flow 9.075, PV  7.50
    From year 2 the DCF, one forecast year at 5% growth:
        revenue 127.05, NOPAT 9.52875, PV at year 2 8.6625
        TV 9.52875 x 1.02 / 0.08 = 121.4915625, PV at year 2 110.446875
        value at year 2 119.109375, brought back two years 98.4375
    Enterprise value -5.0 + 7.5 + 98.4375 = 100.9375
    """
    r = run_speculative(inputs(), assumptions())

    assert [y.revenue for y in r.path] == pytest.approx([110.0, 121.0])
    assert [y.operating_margin for y in r.path] == pytest.approx([-0.05, 0.10])
    assert [y.tax for y in r.path] == pytest.approx([0.0, 3.025])
    assert [y.ufcf for y in r.path] == pytest.approx([-5.5, 9.075])
    assert [y.pv_ufcf for y in r.path] == pytest.approx([-5.0, 7.5])
    assert r.cumulative_cash_burn == pytest.approx(5.5)
    assert r.revenue_at_profitability == pytest.approx(121.0)
    assert r.value_at_profitability == pytest.approx(119.109375)
    assert r.pv_value_at_profitability == pytest.approx(98.4375)
    assert r.enterprise_value == pytest.approx(100.9375)
    assert r.value_per_share == pytest.approx(10.09375)
    assert r.estimate_vs_price == pytest.approx(10.09375 / 5.0 - 1)
    assert r.share_from_after_profitability == pytest.approx(98.4375 / 100.9375)


def test_the_value_after_profitability_is_the_existing_dcf():
    r = run_speculative(inputs(), assumptions())
    dcf = run_dcf(
        BaseYearData(revenue=121.0, total_debt=0.0, cash=0.0, shares=10.0, current_price=5.0),
        Assumptions(revenue_growth=0.05, operating_margin=0.10, tax_rate=0.25, da_pct=0.0,
                    capex_pct=0.0, nwc_pct=0.0, wacc=0.10, terminal_growth=0.02,
                    projection_years=1))
    assert r.value_at_profitability == pytest.approx(dcf.enterprise_value, rel=1e-12)


@pytest.mark.parametrize("years", [1, 3, 6])
def test_a_company_already_at_its_target_is_just_a_longer_dcf(years):
    """With nothing left to improve, the path is the first years of a DCF."""
    at_target = inputs(revenue=500.0, operating_margin=0.12, da_pct=0.04, capex_pct=0.04,
                       nwc_pct=0.10, total_debt=50.0, cash=80.0)
    a = assumptions(years_to_profitability=years, target_operating_margin=0.12,
                    speculative_revenue_growth=0.06, post_profitability_growth=0.06,
                    mature_capex_pct=0.04, mature_nwc_pct=0.10, tax_rate=0.21, wacc=0.09,
                    terminal_growth=0.025, projection_years=5)
    dcf = run_dcf(
        BaseYearData(revenue=500.0, total_debt=50.0, cash=80.0, shares=10.0, current_price=5.0),
        Assumptions(revenue_growth=0.06, operating_margin=0.12, tax_rate=0.21, da_pct=0.04,
                    capex_pct=0.04, nwc_pct=0.10, wacc=0.09, terminal_growth=0.025,
                    projection_years=years + 5))

    r = run_speculative(at_target, a)
    assert r.enterprise_value == pytest.approx(dcf.enterprise_value, rel=1e-10)
    assert r.value_per_share == pytest.approx(dcf.intrinsic_value_per_share, rel=1e-10)
    assert r.cumulative_cash_burn == 0


def test_reinvestment_fades_from_todays_ratios_to_the_mature_ones():
    r = run_speculative(inputs(capex_pct=0.25, da_pct=0.15, nwc_pct=0.30),
                        assumptions(years_to_profitability=4, mature_capex_pct=0.05,
                                    mature_nwc_pct=0.10))
    capex_share = [y.capex / y.revenue for y in r.path]
    da_share = [y.da / y.revenue for y in r.path]
    assert capex_share == pytest.approx([0.20, 0.15, 0.10, 0.05])
    assert da_share == pytest.approx([0.125, 0.10, 0.075, 0.05])


def test_losses_are_not_taxed_and_do_not_earn_a_credit():
    r = run_speculative(inputs(operating_margin=-0.50), assumptions(years_to_profitability=4))
    for year in r.path:
        assert year.tax == (0.0 if year.ebit <= 0 else pytest.approx(year.ebit * 0.25))


def test_a_higher_target_margin_gives_a_higher_estimate():
    low = run_speculative(inputs(), assumptions(target_operating_margin=0.08))
    high = run_speculative(inputs(), assumptions(target_operating_margin=0.15))
    assert high.value_per_share > low.value_per_share


def test_a_later_turn_to_profit_gives_a_lower_estimate():
    soon = run_speculative(inputs(operating_margin=-0.50),
                           assumptions(years_to_profitability=3, speculative_revenue_growth=0.05))
    late = run_speculative(inputs(operating_margin=-0.50),
                           assumptions(years_to_profitability=7, speculative_revenue_growth=0.05))
    assert late.value_per_share < soon.value_per_share
    assert late.cumulative_cash_burn > soon.cumulative_cash_burn


# ---------------------------------------------------------------------------
# Inputs the model cannot use
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_inputs, bad_assumptions, match", [
    ({"revenue": 0.0}, {}, "revenue"),
    ({"shares": 0.0}, {}, "share count"),
    ({"current_price": 0.0}, {}, "share price"),
    ({}, {"years_to_profitability": 0}, "years_to_profitability"),
    ({}, {"target_operating_margin": 0.0}, "not a path to profitability"),
    ({}, {"terminal_growth": 0.10}, "below WACC"),
])
def test_inputs_the_model_cannot_use_are_rejected(bad_inputs, bad_assumptions, match):
    with pytest.raises(ValueError, match=match):
        run_speculative(inputs(**bad_inputs), assumptions(**bad_assumptions))


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------

def test_the_sensitivity_centre_is_the_headline_figure():
    r = run_speculative(inputs(operating_margin=-0.4), assumptions(
        years_to_profitability=5, target_operating_margin=0.1234))
    centre = len(r.sensitivity) // 2
    assert r.sensitivity[centre][centre] == pytest.approx(r.value_per_share, rel=1e-9)


def test_the_sensitivity_blanks_margins_that_are_not_profits_and_years_below_one():
    r = run_speculative(inputs(), assumptions(years_to_profitability=2,
                                              target_operating_margin=0.05))
    assert r.sensitivity_years == [0, 1, 2, 3, 4]
    assert r.sensitivity_margins == pytest.approx([-0.05, 0.0, 0.05, 0.10, 0.15])
    for margin, row in zip(r.sensitivity_margins, r.sensitivity):
        for years, value in zip(r.sensitivity_years, row):
            assert (value is None) == (margin <= 0 or years < 1)


def test_the_sensitivity_shows_how_far_the_assumptions_move_the_figure():
    r = run_speculative(inputs(operating_margin=-0.4), assumptions(years_to_profitability=5))
    defined = [v for row in r.sensitivity for v in row if v is not None]
    assert max(defined) > 1.5 * min(defined), "the table must show the spread, not hide it"
