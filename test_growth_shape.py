"""The shape of the growth assumption: a cap on the starting rate, then a glide.

What shipped before this was a single rate held flat for five years and then
dropped to the terminal rate in one step, with the rate capped at 15%. Half
the companies with any growth hit that ceiling - six of twelve - so Palantir
growing 33%, Nvidia growing 100% and Microsoft growing 16% were all modelled
identically, and each fell eleven points off a cliff in year six with 69% to
88% of its value on the far side.

The replacement treats the cap as a cap on the STARTING rate of a glide:
above FAST_GROWTH_THRESHOLD a company is projected over ten years with the
rate fading to its terminal rate, and everything slower keeps the five flat
years, which is right for a company already growing near its terminal rate.

25% rather than 30% or 35% because Nvidia is the constraint: at a 35% start
its $126 share values at $259, which is not a claim a model built for
sobriety should make on a growth rate nobody expects to persist. At 25% it
lands at $183, and Coinbase - the other hypergrower with a real business -
lands within 3% of its price.

The pair is inseparable, and one test here exists only to pin that: the fade
WITHOUT the longer forecast makes a fast grower worse than the flat
projection, because it decelerates from the same rate over the same five
years. Palantir was $7.14 flat and $6.89 faded. It is the pair that helps.
"""
import pytest

import assumptions as A
from assumptions import (CLAMP_REVENUE_GROWTH, DEFAULT_FADE_START_YEAR,
                         DEFAULT_PROJECTION_YEARS, FAST_GROWTH_THRESHOLD,
                         FAST_GROWTH_YEARS, derive_assumptions)
from dcf import growth_path, run_dcf
from market_data import CompanyFinancials, base_year_from


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(annual_growth, *, years=4, latest=1_000e6, margin=0.25):
    """A company growing at a steady *annual_growth*, newest first."""
    revenues = [latest / (1 + annual_growth) ** i for i in range(years)]
    income, balance, cashflow = [], [], []
    for i, revenue in enumerate(revenues):
        year = 2025 - i
        income.append({
            "fiscalYear": str(year), "date": f"{year}-12-31",
            "revenue": revenue,
            "operatingIncome": revenue * margin,
            "incomeBeforeTax": revenue * margin * 0.95,
            "incomeTaxExpense": revenue * margin * 0.95 * 0.21,
            "interestExpense": 40e6, "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(year),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
        })
        cashflow.append({
            "fiscalYear": str(year),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": 1.0,
                 "marketCap": 1_000_000e6, "price": 100.0,
                 "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow)


def shaped(annual_growth, **kwargs):
    return derive_assumptions(make_fin(annual_growth, **kwargs))


# --- The cap is on the starting rate ---------------------------------------

class TestTheCap:
    def test_the_ceiling_is_twenty_five_percent(self):
        assert CLAMP_REVENUE_GROWTH[1] == pytest.approx(0.25)

    def test_a_hypergrower_starts_at_the_ceiling_and_says_what_it_measured(self):
        """Nvidia's shape: a three-year CAGR around 100%."""
        prov = shaped(1.00).provenance["revenue_growth"]
        assert prov.value == pytest.approx(0.25)
        assert prov.source == "derived (clamped)"
        assert prov.raw == pytest.approx(1.00, abs=1e-6)

    def test_a_company_inside_the_ceiling_is_not_touched(self):
        prov = shaped(0.18).provenance["revenue_growth"]
        assert prov.value == pytest.approx(0.18, abs=1e-6)
        assert prov.source == "derived"

    def test_the_old_fifteen_percent_ceiling_no_longer_bites(self):
        """The change that matters most in the aggregate: six of twelve
        companies with any growth used to be pinned to one number."""
        for growth in (0.16, 0.20, 0.24):
            prov = shaped(growth).provenance["revenue_growth"]
            assert prov.value == pytest.approx(growth, abs=1e-6)
            assert prov.source == "derived"


# --- Above the threshold: ten years, gliding --------------------------------

class TestAFastGrower:
    def test_it_gets_ten_years_rather_than_five(self):
        a = shaped(1.00)
        assert a.assumptions.projection_years == FAST_GROWTH_YEARS
        assert a.provenance["projection_years"].source == "derived"
        assert "needs room to slow" in a.provenance["projection_years"].detail

    def test_the_fade_is_on_and_says_where_it_glides_to(self):
        a = shaped(1.00)
        assert a.assumptions.fade_enabled is True
        prov = a.provenance["fade_enabled"]
        assert prov.source == "derived"
        assert "glides from 25.0%" in prov.detail
        assert "rather than dropping to it in one step" in prov.detail

    def test_the_glide_starts_immediately_not_in_year_four(self):
        """The default holds the full rate for three years, which for a 25%
        starter means compounding 25% three times before any deceleration.
        That put Nvidia 64% above its market price against 45% when the glide
        starts at once, and it is too strong a thing to assume silently."""
        a = shaped(1.00)
        assert a.assumptions.fade_start_year == 1
        assert a.provenance["fade_start_year"].source == "derived"
        assert DEFAULT_FADE_START_YEAR != 1, "otherwise this asserts nothing"

    def test_the_path_decelerates_every_year_to_the_terminal_rate(self):
        a = shaped(1.00).assumptions
        path = growth_path(a)
        assert len(path) == FAST_GROWTH_YEARS
        assert path[0] == pytest.approx(0.25)
        assert path[-1] == pytest.approx(a.terminal_growth)
        assert all(b < c for b, c in zip(path[1:], path[:-1])), path

    def test_the_deceleration_is_a_fade_and_not_a_crash(self):
        """Over five years a 25% grower would shed 4.2 points a year. Over
        ten it is around 2.1, which is the difference between a fade and a
        collapse - and the five-year version still leaves a cliff."""
        path = growth_path(shaped(1.00).assumptions)
        steps = [b - c for b, c in zip(path[:-1], path[1:])]
        assert max(steps) < 0.025


# --- Below the threshold: five flat years ----------------------------------

class TestAnOrdinaryCompany:
    def test_it_keeps_the_five_flat_years_that_have_always_shipped(self):
        a = shaped(0.06)
        assert a.assumptions.projection_years == DEFAULT_PROJECTION_YEARS
        assert a.assumptions.fade_enabled is False
        assert a.provenance["projection_years"].source == "default"
        assert a.provenance["fade_enabled"].source == "default"
        assert a.assumptions.fade_start_year == DEFAULT_FADE_START_YEAR

    def test_its_growth_is_held_flat_across_the_forecast(self):
        a = shaped(0.06).assumptions
        path = growth_path(a)
        assert len(path) == DEFAULT_PROJECTION_YEARS
        assert all(g == pytest.approx(path[0]) for g in path)

    def test_a_shrinking_company_is_not_a_fast_grower(self):
        a = shaped(-0.08)
        assert a.assumptions.fade_enabled is False
        assert a.provenance["revenue_growth"].value == pytest.approx(
            CLAMP_REVENUE_GROWTH[0])


class TestTheThreshold:
    def test_exactly_at_the_threshold_is_not_fast(self):
        """Strictly above, so a company growing at precisely the threshold
        keeps the simpler projection.

        The rate is supplied rather than measured, because a CAGR computed
        from revenues lands a hair either side of 15% and a boundary tested
        at 14.99999% is not tested at all - mutation showed `>=` surviving
        for exactly that reason.
        """
        a = derive_assumptions(
            make_fin(0.06),
            overrides={"revenue_growth": FAST_GROWTH_THRESHOLD})
        assert a.assumptions.revenue_growth == FAST_GROWTH_THRESHOLD
        assert a.assumptions.fade_enabled is False
        assert a.assumptions.projection_years == DEFAULT_PROJECTION_YEARS

    def test_a_hair_above_the_threshold_is_fast(self):
        a = derive_assumptions(
            make_fin(0.06),
            overrides={"revenue_growth": FAST_GROWTH_THRESHOLD + 1e-9})
        assert a.assumptions.fade_enabled is True
        assert a.assumptions.projection_years == FAST_GROWTH_YEARS

    def test_just_above_the_threshold_is_fast(self):
        a = shaped(FAST_GROWTH_THRESHOLD + 0.01)
        assert a.assumptions.fade_enabled is True
        assert a.assumptions.projection_years == FAST_GROWTH_YEARS

    def test_the_ceiling_sits_above_the_threshold_so_the_cap_cannot_hide_a_fast_grower(self):
        """The invariant that makes the shape coherent.

        The threshold is judged on the capped rate, which is the rate the
        forecast actually starts from. That reading and a reading of the raw
        rate agree only because the ceiling is above the threshold: were the
        ceiling ever set below it, capping would turn a hypergrower into an
        ordinary company and silently take away the glide it most needs.
        """
        assert CLAMP_REVENUE_GROWTH[1] > FAST_GROWTH_THRESHOLD
        a = shaped(1.00)
        assert a.provenance["revenue_growth"].value == pytest.approx(0.25)
        assert a.assumptions.fade_enabled is True
        # And the reader is told the rate the glide starts from, not the one
        # that was measured and discarded.
        assert "25.0%" in a.provenance["fade_enabled"].detail
        assert "100.0%" not in a.provenance["fade_enabled"].detail


# --- The two halves ship together ------------------------------------------

class TestThePairIsInseparable:
    def test_fading_over_five_years_is_worse_than_not_fading_at_all(self):
        """Why the fade is not switched on by itself.

        Palantir was $7.14 on the flat projection and $6.89 with the fade
        over the same five years: the glide starts from the same rate and
        has nowhere to go, so every year after the first is lower and the
        cliff is still there. Ten years is what makes the fade worth having.
        """
        import dataclasses

        fin = make_fin(1.00)
        base, _ = base_year_from(fin)
        fast = shaped(1.00).assumptions

        flat_five = dataclasses.replace(
            fast, projection_years=DEFAULT_PROJECTION_YEARS,
            fade_enabled=False)
        faded_five = dataclasses.replace(
            flat_five, fade_enabled=True, fade_start_year=1)

        assert (run_dcf(base, faded_five).intrinsic_value_per_share
                < run_dcf(base, flat_five).intrinsic_value_per_share)
        # And the pair that ships beats the faded five-year version it would
        # otherwise have been confused with.
        assert (run_dcf(base, fast).intrinsic_value_per_share
                > run_dcf(base, faded_five).intrinsic_value_per_share)

    def test_a_longer_forecast_moves_value_out_of_the_terminal_figure(self):
        """The point of the ten years. Value that used to sit beyond the
        forecast now sits inside it, where it is at least attached to a
        growth rate someone can argue with."""
        import dataclasses

        fin = make_fin(1.00)
        base, _ = base_year_from(fin)
        fast = shaped(1.00).assumptions
        old = dataclasses.replace(fast, projection_years=5,
                                  fade_enabled=False)

        def tv_share(result):
            return result.pv_terminal_value / result.enterprise_value

        assert tv_share(run_dcf(base, fast)) < tv_share(run_dcf(base, old))


# --- A caller may still say otherwise --------------------------------------

class TestOverrides:
    def test_an_explicit_horizon_wins(self):
        a = derive_assumptions(make_fin(1.00),
                               overrides={"projection_years": 5})
        assert a.assumptions.projection_years == 5
        assert a.provenance["projection_years"].source == "override"

    def test_the_fade_can_be_switched_off_by_hand(self):
        a = derive_assumptions(make_fin(1.00),
                               overrides={"fade_enabled": 0})
        assert a.assumptions.fade_enabled is False
        assert a.provenance["fade_enabled"].source == "override"

    def test_and_on_by_hand_for_a_company_that_would_not_get_it(self):
        a = derive_assumptions(make_fin(0.06), overrides={"fade_enabled": 1})
        assert a.assumptions.fade_enabled is True
        assert a.provenance["fade_enabled"].source == "override"
