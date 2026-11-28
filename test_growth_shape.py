"""The shape of the growth assumption: three rates, not one.

WHAT CAME BEFORE, AND WHY IT WENT
---------------------------------
A single rate held flat for five years and then dropped to the terminal rate
in one step. Capped at 15%, which half the companies with any growth hit, so
Palantir growing 33%, Nvidia growing 100% and Microsoft growing 16% were
modelled identically with an eleven-point cliff in year six.

That was replaced (1.0.38) by a cap on the STARTING rate of a glide, at 25%,
with a ten-year forecast and a fade for anything above 15%. Better, and
still one rate: it said the same thing about next year as about year nine,
and made the SHAPE of the model depend on the figure being modelled - five
years for a slow company, ten for a fast one.

WHAT SHIPS NOW
--------------
Three rates, because they are three different kinds of claim:

  year one     what the company just did, which is close to a fact
  years 2-5    the measured trend, which is a judgement
  years 6-10   a decline to the terminal rate, which is arithmetic

Ten years for every company. A shorter forecast does not make a model more
honest; it moves value into the perpetuity, which is the part nobody can
check.

Run with:  python -m pytest test_growth_shape.py -v
"""
import pytest

import assumptions as A
from assumptions import (DEFAULT_PROJECTION_YEARS, MARGINAL_TAX_RATE,
                         TERMINAL_ROIC_CAP, YEAR_ONE_GROWTH_CAP,
                         derive_assumptions)
from dcf import FADE_EXPONENTIAL, FADE_LINEAR, growth_path, run_dcf
from market_data import CompanyFinancials, base_year_from


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(revenues, *, margin=0.25, ticker="TEST"):
    """A company with *revenues* newest first."""
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
            # Current assets plus a non-current base. `totalAssets` is what
            # return on capital is measured against - invested capital is
            # built from the assets side - so a fixture without it cannot
            # express a return at all, and every terminal-return test below
            # would read None rather than the edge it is aiming at.
            "totalAssets": revenue * 1.20,
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
            "commonStockEquity": revenue * 0.80,
        })
        cashflow.append({
            "fiscalYear": str(year),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker=ticker,
        profile={"companyName": "Test Co", "beta": 1.0,
                 "marketCap": 1_000_000e6, "price": 100.0,
                 "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow)


def steady(rate, *, years=4, latest=1_000e6, **kwargs):
    """A company growing at a steady *rate*."""
    return make_fin([latest / (1 + rate) ** i for i in range(years)], **kwargs)


def jumped(latest_rate, trend_rate, *, latest=1_000e6):
    """A company on *trend_rate* whose latest year did *latest_rate*."""
    previous = latest / (1 + latest_rate)
    return make_fin([latest, previous,
                     previous / (1 + trend_rate),
                     previous / (1 + trend_rate) ** 2])


def phased(held, terminal, *, first=None, pattern=FADE_LINEAR, years=10):
    """Assumptions with the growth path set exactly, nothing derived."""
    from dcf import Assumptions

    return Assumptions(
        revenue_growth=held, operating_margin=0.30, tax_rate=0.18,
        da_pct=0.05, capex_pct=0.05, nwc_pct=0.02, wacc=0.10,
        terminal_growth=terminal, projection_years=years,
        year_one_growth=held if first is None else first,
        years_two_to_five_growth=held, growth_decline_pattern=pattern)


# --- Three rates -----------------------------------------------------------

class TestTheThreeRates:
    def test_year_one_is_the_latest_year_and_says_so(self):
        d = derive_assumptions(jumped(0.32, 0.10))
        prov = d.provenance["year_one_growth"]
        assert prov.value == pytest.approx(0.32, abs=1e-6)
        assert "latest financial year" in prov.detail

    def test_years_two_to_five_are_the_measured_trend(self):
        d = derive_assumptions(jumped(0.32, 0.10))
        prov = d.provenance["years_two_to_five_growth"]
        assert prov.value == pytest.approx(
            d.provenance["revenue_growth"].value)
        assert "held for years two to five" in prov.detail

    def test_the_two_are_allowed_to_disagree(self):
        """The point of having both. A company that grew 32% last year on a
        10% trend is making two different claims about itself."""
        d = derive_assumptions(jumped(0.32, 0.10))
        assert (d.provenance["year_one_growth"].value
                != pytest.approx(
                    d.provenance["years_two_to_five_growth"].value))

    def test_the_path_holds_each_rate_for_its_own_years(self):
        d = derive_assumptions(jumped(0.32, 0.10))
        path = growth_path(d.assumptions)
        assert len(path) == 10
        assert path[0] == pytest.approx(0.32, abs=1e-6)
        assert all(g == pytest.approx(path[1]) for g in path[1:5])
        assert path[-1] == pytest.approx(d.assumptions.terminal_growth)

    def test_years_two_to_five_are_the_trend_and_not_year_one(self):
        """Equal to each other is not enough: five years of the latest
        year's own growth would satisfy that too, and would be the single
        rate this structure exists to replace."""
        d = derive_assumptions(jumped(0.32, 0.10))
        path = growth_path(d.assumptions)
        trend = d.assumptions.years_two_to_five_growth
        assert trend is not None and trend != pytest.approx(0.32), \
            "fixture sanity: the two rates must genuinely differ"
        assert all(g == pytest.approx(trend) for g in path[1:5]), path
        assert path[0] != pytest.approx(path[1])

    def test_the_final_year_is_the_terminal_rate_bit_for_bit(self):
        """Pinned, not merely arrived at.

        The perpetuity is built from the final year's cash flow, so a
        float's width of daylight between the last projected rate and the
        terminal one is a seam in the model. These two pairs land just off
        it, one on each decline shape.
        """
        for pattern, held, terminal in ((FADE_LINEAR, 0.16334, 0.01103),
                                        (FADE_EXPONENTIAL, 0.39169, 0.00686)):
            path = growth_path(phased(held, terminal, pattern=pattern))
            assert path[-1] == terminal, (pattern, path[-1])

        # And the drift being guarded against is real, not hypothetical.
        held, terminal = 0.16334, 0.01103
        assert held + (terminal - held) * (5 / 5) != terminal
        held, terminal = 0.39169, 0.00686
        assert held * (terminal / held) ** 1.0 != terminal

    def test_years_six_to_ten_decline_every_year(self):
        path = growth_path(derive_assumptions(jumped(0.32, 0.10)).assumptions)
        tail = path[4:]
        assert all(b < a for a, b in zip(tail[:-1], tail[1:])), tail


# --- The caps --------------------------------------------------------------

class TestTheCaps:
    def test_the_latest_year_is_capped_at_forty_percent(self):
        prov = derive_assumptions(
            jumped(0.90, 0.10)).provenance["year_one_growth"]
        assert prov.value == pytest.approx(YEAR_ONE_GROWTH_CAP)
        assert prov.source == "derived (clamped)"
        assert prov.raw == pytest.approx(0.90, abs=1e-6)

    def test_forty_rather_than_fifty(self):
        """Of the fourteen companies measured only Nvidia and Palantir reach
        it, and 50% moved them 7% and 3% while changing nothing else - a
        stronger claim buying no extra explanation."""
        assert YEAR_ONE_GROWTH_CAP == pytest.approx(0.40)

    def test_the_trend_is_capped_harder_than_the_latest_year(self):
        """A single twelve months can be extraordinary in a way a
        three-year rate cannot, so the rate held for four years is the one
        held to the tighter bound."""
        assert A.CLAMP_REVENUE_GROWTH[1] < YEAR_ONE_GROWTH_CAP
        prov = derive_assumptions(
            steady(0.90)).provenance["years_two_to_five_growth"]
        assert prov.value == pytest.approx(A.CLAMP_REVENUE_GROWTH[1])

    def test_a_shrinking_company_is_floored_at_nil(self):
        d = derive_assumptions(steady(-0.08))
        assert d.provenance["year_one_growth"].value >= 0
        assert d.provenance["years_two_to_five_growth"].value >= 0


# --- The horizon -----------------------------------------------------------

class TestTheHorizon:
    @pytest.mark.parametrize("rate", [0.02, 0.10, 0.35, 0.90])
    def test_it_is_ten_years_whatever_the_company_does(self, rate):
        """It used to be five, or ten for a fast grower, which made the
        shape of the model depend on the figure being modelled."""
        d = derive_assumptions(steady(rate))
        assert d.assumptions.projection_years == DEFAULT_PROJECTION_YEARS
        assert DEFAULT_PROJECTION_YEARS == 10
        assert len(growth_path(d.assumptions)) == 10

    def test_the_old_single_rate_fade_is_off(self):
        """Two shapes applied to one forecast would be two answers."""
        d = derive_assumptions(steady(0.35))
        assert d.assumptions.fade_enabled is False
        assert "replaced by the three-phase" in d.provenance[
            "fade_enabled"].detail


# --- The shape of the decline ----------------------------------------------

class TestTheDeclineShape:
    def test_linear_by_default(self):
        d = derive_assumptions(steady(0.20))
        assert d.diagnostics["growth_decline_pattern"] == FADE_LINEAR
        path = growth_path(d.assumptions)[4:]
        steps = [a - b for a, b in zip(path[:-1], path[1:])]
        assert all(s == pytest.approx(steps[0]) for s in steps)

    def test_exponential_drops_faster_early(self):
        d = derive_assumptions(
            steady(0.20), overrides={"growth_decline_pattern":
                                     FADE_EXPONENTIAL})
        assert d.diagnostics["growth_decline_pattern"] == FADE_EXPONENTIAL
        curved = growth_path(d.assumptions)
        straight = growth_path(derive_assumptions(steady(0.20)).assumptions)
        # Same ends, lower in between.
        assert curved[4] == pytest.approx(straight[4])
        assert curved[-1] == pytest.approx(straight[-1])
        assert all(c < s for c, s in zip(curved[5:-1], straight[5:-1]))

    def test_it_falls_back_to_the_straight_line_where_a_ratio_has_no_meaning(
            self):
        """Exponential is a ratio between two rates. A company assumed to
        shrink has no constant factor to apply, and inventing one would be
        worse than using the straight line and saying so."""
        d = derive_assumptions(
            steady(-0.08), overrides={"growth_decline_pattern":
                                      FADE_EXPONENTIAL})
        assert d.diagnostics["growth_decline_pattern"] == FADE_LINEAR
        assert d.diagnostics["growth_decline_fell_back"] is True

    def test_the_engine_guards_the_ratio_itself(self):
        """Called directly, not through the derivation.

        derive_assumptions downgrades an exponential request on a negative
        rate before the engine sees it, so the engine's own guard is never
        reached that way - which is exactly why it needs its own test. A
        ratio between a negative rate and a positive one raises a negative
        number to a fractional power, and Python answers with a complex
        number: not a growth rate, and not something that fails loudly
        either.
        """
        path = growth_path(phased(-0.08, 0.04, pattern=FADE_EXPONENTIAL))
        assert all(isinstance(g, float) for g in path), path
        steps = [round(b - a, 10) for a, b in zip(path[4:-1], path[5:])]
        assert len(set(steps)) == 1, ("the straight line was not used", steps)

    def test_a_terminal_rate_of_nil_is_guarded_the_same_way(self):
        """The other end of the ratio. Zero is a rate a reader can set."""
        path = growth_path(phased(0.20, 0.0, pattern=FADE_EXPONENTIAL))
        assert all(isinstance(g, float) for g in path), path
        assert path[-1] == 0.0

    def test_an_unknown_name_is_not_a_failed_valuation(self):
        d = derive_assumptions(
            steady(0.20), overrides={"growth_decline_pattern": "sigmoid"})
        assert d.diagnostics["growth_decline_pattern"] == FADE_LINEAR


# --- The other levers that ship with it ------------------------------------

class TestTheOtherLevers:
    def test_tax_converges_to_the_marginal_rate(self):
        d = derive_assumptions(steady(0.10))
        assert d.assumptions.marginal_tax_rate == pytest.approx(
            MARGINAL_TAX_RATE)
        prov = d.provenance["marginal_tax_rate"]
        assert "the reasons it sits below the statutory rate expire" in \
            prov.detail

    def test_the_terminal_return_is_capped_and_floored(self):
        d = derive_assumptions(steady(0.10))
        roic = d.assumptions.terminal_roic
        assert d.assumptions.wacc <= roic <= TERMINAL_ROIC_CAP

    def test_growth_in_perpetuity_is_paid_for(self):
        """Without this the perpetuity extends a cash flow forever at a
        growth rate nothing was bought to produce."""
        d = derive_assumptions(steady(0.10))
        assert d.assumptions.terminal_reinvestment is True

    def test_depreciation_rises_toward_investment(self):
        d = derive_assumptions(steady(0.10))
        end = d.assumptions.da_terminal_pct
        assert end is not None
        assert 0 < end < d.assumptions.capex_terminal_pct
        assert "cover out of depreciation" in d.provenance[
            "da_terminal_pct"].detail


# --- What it does to a figure ----------------------------------------------

class TestTheEffect:
    def test_a_fast_latest_year_is_worth_more_than_the_trend_alone(self):
        """Year one is a year of compounding the model used to throw away."""
        fin = jumped(0.35, 0.08)
        base, _meta = base_year_from(fin)
        a = derive_assumptions(fin).assumptions
        from dataclasses import replace
        flat = replace(a, year_one_growth=a.years_two_to_five_growth)
        assert (run_dcf(base, a, with_sensitivity=False)
                .intrinsic_value_per_share
                > run_dcf(base, flat, with_sensitivity=False)
                .intrinsic_value_per_share)

    def test_the_forecast_carries_more_of_the_answer_than_five_years_did(self):
        from dataclasses import replace

        fin = steady(0.20)
        base, _meta = base_year_from(fin)
        a = derive_assumptions(fin).assumptions
        short = replace(a, projection_years=5)
        assert (run_dcf(base, a, with_sensitivity=False).tv_pct_of_ev
                < run_dcf(base, short, with_sensitivity=False).tv_pct_of_ev)


# --- The tax actually converges --------------------------------------------

class TestTheTaxPath:
    def test_it_moves_from_the_effective_rate_to_the_marginal_one(self):
        from dcf import tax_path

        a = derive_assumptions(steady(0.10)).assumptions
        path = tax_path(a)
        assert len(path) == a.projection_years
        assert path[0] == pytest.approx(a.tax_rate)
        assert path[-1] == pytest.approx(MARGINAL_TAX_RATE)
        assert all(b >= x for x, b in zip(path[:-1], path[1:])), path

    def test_a_flat_rate_would_be_worth_real_money(self):
        """The size of the thing being corrected. Without this, an effective
        rate below the statutory one is carried forever - eleven points of
        after-tax profit for a company like Nvidia."""
        from dataclasses import replace

        fin = steady(0.10)
        base, _meta = base_year_from(fin)
        a = derive_assumptions(fin).assumptions
        flat = replace(a, marginal_tax_rate=None)
        assert (run_dcf(base, flat, with_sensitivity=False)
                .intrinsic_value_per_share
                > run_dcf(base, a, with_sensitivity=False)
                .intrinsic_value_per_share * 1.02)


# --- The terminal return, at its edges -------------------------------------

class TestTheTerminalReturnAtItsEdges:
    def test_a_spectacular_return_is_capped(self):
        """Nvidia earns 110% on its capital. Half of that is still a claim
        that a competitive advantage never erodes."""
        d = derive_assumptions(steady(0.10, margin=0.70, latest=10_000e6))
        roic_now = d.diagnostics["current_roic"]
        assert roic_now is not None, "fixture sanity"
        assert (roic_now + d.assumptions.wacc) / 2 > TERMINAL_ROIC_CAP, \
            "fixture sanity: the halfway point must clear the cap"
        assert d.assumptions.terminal_roic == pytest.approx(TERMINAL_ROIC_CAP)
        assert d.provenance["terminal_roic"].source == "derived (clamped)"

    def test_a_poor_return_is_floored_at_the_cost_of_capital(self):
        """Below it, growth destroys value forever - which is not a steady
        state anyone holds, and not something to assume on a company's
        behalf."""
        d = derive_assumptions(steady(0.10, margin=0.01))
        roic_now = d.diagnostics["current_roic"]
        wacc = d.assumptions.wacc
        assert roic_now is not None and roic_now < wacc, "fixture sanity"
        assert d.assumptions.terminal_roic == pytest.approx(wacc)

    def test_an_ordinary_return_lands_halfway(self):
        """Not at the company's own figure, and not at its cost of capital:
        competition is assumed to take about half the excess."""
        d = derive_assumptions(steady(0.10, margin=0.25))
        roic_now = d.diagnostics["current_roic"]
        wacc = d.assumptions.wacc
        assert roic_now is not None and wacc < roic_now < TERMINAL_ROIC_CAP, \
            "fixture sanity"
        assert d.assumptions.terminal_roic == pytest.approx(
            (roic_now + wacc) / 2)
        assert d.assumptions.terminal_roic < roic_now

    def test_the_floor_outranks_the_cap(self):
        """The two limits only disagree above a 25% cost of capital, which
        the build-up cannot reach today - beta is capped at 4.00 and the
        slider at 20%. The order still has to be right. Capping last would
        settle the long-run return BELOW the cost of capital, which says the
        company destroys value by growing, forever: the exact outcome the
        floor exists to prevent, arrived at through the ceiling."""
        wacc = TERMINAL_ROIC_CAP + 0.05
        d = derive_assumptions(steady(0.10, margin=0.70),
                               overrides={"wacc": wacc})
        assert d.assumptions.wacc == pytest.approx(wacc), "fixture sanity"
        assert d.diagnostics["current_roic"] is not None, "fixture sanity"

        assert d.assumptions.terminal_roic == pytest.approx(wacc)
        assert d.assumptions.terminal_roic >= d.assumptions.wacc, \
            "growth must never be assumed to destroy value"
        # Not recorded as clamped: the floor decided this, not the ceiling,
        # and a reader told it was capped at 25% would be reading a figure
        # that is 30%.
        assert d.provenance["terminal_roic"].source == "derived"
