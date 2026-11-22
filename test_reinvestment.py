"""How investment and depreciation move across a forecast.

The model has made both of the obvious mistakes here, and each shipped.

  * Depreciation set EQUAL to investment once mature. Net investment is then
    zero, the assets maintain themselves and growth is free. This is the one
    that made the capital-investment control inert: whatever rate was set,
    the same rate was subtracted and added back, and Tesla's figure did not
    move by a cent between 14.9% and 5%.

  * Depreciation PINNED to today's rate. A company building out at 24% of
    revenue is then assumed to keep depreciating the much smaller asset base
    it has now, forever. Investment looks permanently expensive and a heavy
    spender can never be worth anything: Rivian fell 61% on that alone.

What ships is neither. Investment glides to a level derived from the
company's own figures, depreciation glides to its covered share of that
level, and the gap between them is the net investment that never goes away.

The cover is Damodaran's, per industry, and it is the reciprocal of a single
published ratio - so nothing here divides by a figure that might be near
zero. The level is the company's own, because his two columns are on
different bases (`Cap Ex/Deprecn` is property, plant and equipment;
`Net Cap Ex/Sales` adds net R&D and acquisitions) and combining them gives
Pharmaceuticals a capital intensity of 22% of revenue, which is an artefact
of the mixture rather than a fact about pharma.

Run with:  python -m pytest test_reinvestment.py -v
"""
import pytest

import reinvestment as R
from industry_betas import industry_capex_for
from industry_capex_data import INDUSTRY_CAPEX, IndustryCapex


def industry(capex_to_depreciation, *, net=0.04, name="Test"):
    return IndustryCapex(name, 50, capex_to_depreciation, net, 1.5)


# --- The cover -------------------------------------------------------------

class TestTheCover:
    def test_it_is_the_reciprocal_of_the_published_ratio(self):
        cover, name = R.depreciation_cover(industry(2.0))
        assert cover == pytest.approx(0.50)
        assert name == "Test"

    def test_it_differs_by_industry_rather_than_being_a_constant(self):
        covers = {
            name: R.depreciation_cover(INDUSTRY_CAPEX[name])[0]
            for name in ("Auto & Truck", "Semiconductor", "Beverage (Soft)",
                         "Oil/Gas (Integrated)")
        }
        assert len(set(round(c, 3) for c in covers.values())) == 4, covers
        # Oil and gas replaces nearly all of what it spends; a soft-drinks
        # company replaces well under three quarters of it.
        assert covers["Oil/Gas (Integrated)"] > covers["Beverage (Soft)"]

    def test_depreciation_never_exceeds_investment_at_maturity(self):
        """Pharmaceuticals really does depreciate more than it spends on
        plant, because its value sits in R&D, which is expensed. True of the
        industry and not a steady state for a company: carried through, it
        would have depreciation adding more cash than investment consumes,
        forever."""
        pharma = INDUSTRY_CAPEX["Drugs (Pharmaceutical)"]
        assert 1.0 / pharma.capex_to_depreciation > 1.0, "fixture sanity"

        cover, _name = R.depreciation_cover(pharma)
        assert cover == R.DEPRECIATION_COVER_CLAMP[1]
        assert cover < 1.0

    def test_an_industry_in_a_build_out_cannot_set_a_permanent_floor(self):
        cover, _name = R.depreciation_cover(industry(10.0))
        assert cover == R.DEPRECIATION_COVER_CLAMP[0]

    def test_an_unknown_industry_falls_back_to_the_whole_market(self):
        cover, name = R.depreciation_cover(None)
        assert name == R.FALLBACK_INDUSTRY
        assert cover == pytest.approx(
            1.0 / INDUSTRY_CAPEX[R.FALLBACK_INDUSTRY].capex_to_depreciation,
            abs=1e-9)

    def test_so_does_an_industry_with_no_usable_ratio(self):
        _cover, name = R.depreciation_cover(industry(0.0))
        assert name == R.FALLBACK_INDUSTRY


# --- Where investment settles ----------------------------------------------

class TestTheMatureLevel:
    def test_an_ordinary_spender_keeps_its_own_rate(self):
        assert R.derived_mature_capex(0.08, 0.05) == pytest.approx(0.08)

    def test_a_build_out_is_moderated_to_the_ceiling(self):
        """Lucid spends 72% of revenue on investment. Carrying that to the
        terminal year says it never finishes building the thing."""
        assert R.derived_mature_capex(0.717, 0.30) == pytest.approx(
            R.MATURE_CAPEX_CEILING)

    def test_the_ceiling_sits_above_every_industry_average(self):
        """So it moderates only companies that are outliers against their
        own industry, and leaves every ordinary spender alone. Asserted
        against the published table rather than asserted in prose."""
        worst = max(
            i.net_capex_to_sales / max(1 - 1 / i.capex_to_depreciation, 1e-9)
            for i in INDUSTRY_CAPEX.values()
            if i.capex_to_depreciation > 1.05 and i.net_capex_to_sales > 0)
        assert R.MATURE_CAPEX_CEILING < worst, (
            "this bound is about industry averages; if one now exceeds it, "
            "the ceiling needs revisiting rather than this test relaxing")
        semiconductor = INDUSTRY_CAPEX["Semiconductor"]
        implied = semiconductor.net_capex_to_sales / (
            1 - 1 / semiconductor.capex_to_depreciation)
        assert R.MATURE_CAPEX_CEILING == pytest.approx(0.20)
        assert implied == pytest.approx(0.209, abs=0.01)

    def test_a_company_spending_less_than_it_depreciates_is_floored(self):
        """3M invests 3.8% of revenue and depreciates 5.6%. A business that
        spends less than its assets lose is shrinking, and a forecast cannot
        assume it does so forever."""
        assert R.derived_mature_capex(0.038, 0.056) == pytest.approx(0.056)

    def test_a_negative_rate_is_not_carried_through(self):
        assert R.derived_mature_capex(-0.10, -0.05) == 0.0


# --- The two together ------------------------------------------------------

class TestMatureInvestment:
    def test_depreciation_approaches_investment_without_reaching_it(self):
        m = R.mature_investment(0.20, industry(1.5), chosen_by="a test")
        assert 0 < m.da_pct < m.capex_pct
        assert m.net_capex_pct == pytest.approx(m.capex_pct - m.da_pct)
        assert m.net_capex_pct > 0

    def test_more_investment_costs_more_permanently(self):
        """The property the first attempt at a slider did not have."""
        light = R.mature_investment(0.05, industry(1.5), chosen_by="a test")
        heavy = R.mature_investment(0.25, industry(1.5), chosen_by="a test")
        assert heavy.net_capex_pct > light.net_capex_pct

    def test_it_says_where_its_figures_came_from(self):
        m = R.mature_investment(0.20, INDUSTRY_CAPEX["Auto & Truck"],
                                chosen_by="the company's own current rate")
        assert "20.0% of revenue" in m.detail
        assert "the company's own current rate" in m.detail
        assert "Auto & Truck" in m.detail
        assert "Damodaran" in m.detail
        assert "net investment" in m.detail


# --- The glide -------------------------------------------------------------

class TestTheGlide:
    def test_it_starts_where_the_company_is_and_ends_where_it_settles(self):
        path = R.investment_path(
            capex_start=0.24, da_start=0.08,
            mature=R.mature_investment(0.10, industry(2.0),
                                       chosen_by="a test"),
            years=5)
        assert len(path.capex) == 5 and len(path.da) == 5
        assert path.capex[-1] == pytest.approx(0.10)
        assert path.da[-1] == pytest.approx(0.05)
        # Reached at the last year, not before it.
        assert path.capex[0] != pytest.approx(0.10)

    def test_it_takes_equal_steps(self):
        path = R.investment_path(
            capex_start=0.20, da_start=0.00,
            mature=R.mature_investment(0.10, industry(2.0),
                                       chosen_by="a test"),
            years=4)
        steps = [b - a for a, b in zip(path.capex[:-1], path.capex[1:])]
        assert all(s == pytest.approx(steps[0]) for s in steps)

    def test_depreciation_rises_while_investment_falls(self):
        """Both converge, from opposite directions. A build-out spends less
        over time and depreciates more, because the assets it built are
        still there."""
        path = R.investment_path(
            capex_start=0.24, da_start=0.02,
            mature=R.mature_investment(0.12, industry(1.5),
                                       chosen_by="a test"),
            years=5)
        assert all(b < a for a, b in zip(path.capex[:-1], path.capex[1:]))
        assert all(b > a for a, b in zip(path.da[:-1], path.da[1:]))

    def test_net_investment_stays_positive_the_whole_way(self):
        path = R.investment_path(
            capex_start=0.24, da_start=0.02,
            mature=R.mature_investment(0.12, industry(1.5),
                                       chosen_by="a test"),
            years=5)
        assert all(path.net(t) > 0 for t in range(5))

    def test_a_forecast_of_no_length_is_already_mature(self):
        path = R.investment_path(
            capex_start=0.24, da_start=0.02,
            mature=R.mature_investment(0.12, industry(1.5),
                                       chosen_by="a test"),
            years=0)
        assert path.capex == [] and path.da == []
        assert R.glide(0.24, 0.12, 1, 0) == pytest.approx(0.12)

    def test_the_glide_cannot_overshoot(self):
        assert R.glide(0.0, 1.0, 9, 4) == pytest.approx(1.0)
        assert R.glide(0.0, 1.0, -3, 4) == pytest.approx(0.0)


# --- The industries the app actually values --------------------------------

class TestRealCompanies:
    @pytest.mark.parametrize("ticker, expected", [
        ("TSLA", "Auto & Truck"),
        ("F", "Auto & Truck"),
        ("NVDA", "Semiconductor"),
        ("KO", "Beverage (Soft)"),
        ("XOM", "Oil/Gas (Integrated)"),
    ])
    def test_a_known_ticker_finds_its_industry(self, ticker, expected):
        found = industry_capex_for(ticker)
        assert found is not None and found.name == expected

    def test_an_unknown_ticker_finds_none_rather_than_guessing(self):
        assert industry_capex_for("NOSUCHTICKER") is None
        # And the cover falls back, rather than the caller having to.
        _cover, name = R.depreciation_cover(industry_capex_for("NOSUCHTICKER"))
        assert name == R.FALLBACK_INDUSTRY


# --- The bounds on what a reader may assume --------------------------------

class TestTheBounds:
    """Checked directly, because the HTTP layer rejects out-of-range values
    before the engine sees them - so a test that only goes through the API
    proves the query parameter's bounds and nothing about these."""

    def test_an_absurd_investment_rate_is_refused(self):
        from hypothetical import (CAPEX_PCT_BOUNDS, HypotheticalRefused,
                                  UserAssumptions, validate)

        low, high = CAPEX_PCT_BOUNDS
        for rate in (high + 0.01, low - 0.01, 5.0, -1.0):
            with pytest.raises(HypotheticalRefused) as caught:
                validate(UserAssumptions(0.05, 0.10, 5, capex_pct=rate))
            assert caught.value.code == "hypothetical_out_of_bounds"
            assert "capital investment" in str(caught.value)

    def test_a_rate_inside_them_is_not(self):
        from hypothetical import UserAssumptions, validate

        low, high = R.hypothetical_capex_bounds()
        for rate in (low, high, 0.10, None):
            validate(UserAssumptions(0.05, 0.10, 5, capex_pct=rate))
