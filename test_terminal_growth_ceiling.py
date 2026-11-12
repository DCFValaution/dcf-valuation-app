"""The three limits on long-run growth, and which one binds.

The ceiling used to be the lower of the risk-free rate and WACC less 1.5
points. At a 5.07% ten-year that let every company compound at 5.07% in
perpetuity - above the 4.0% the Fed projects for long-run US nominal growth,
and above the 3.2% it projects for the policy rate itself. A company growing
faster than its economy forever eventually becomes the economy, so the
figure was indefensible regardless of how much room sat under WACC.

The 1.5-point margin had its own problem. The Gordon denominator is
(WACC - g), and the low-beta names were clearing it by a point and a half
or less - Exxon 1.64, Johnson & Johnson 1.68 - while the terminal value was
over nine tenths of the answer. A half-point error in WACC moved the result
by about a fifth. Those companies came out at 1.5 to 2.2 times their market
price.

So: the lowest of the risk-free rate, long-run nominal growth, and WACC less
three points. The first two are observed figures with sources; the third is
a modelling judgement, and says so wherever it appears.

Run with:  python -m pytest test_terminal_growth_ceiling.py -v
"""

from datetime import date, timedelta

import pytest

import assumptions as A
from assumptions import (LONG_RUN_GROWTH_AS_OF, LONG_RUN_NOMINAL_GROWTH,
                         SEP_STALE_AFTER_DAYS, TERMINAL_GROWTH_SAFETY_MARGIN,
                         derive_assumptions, terminal_growth_ceiling)
from market_data import CompanyFinancials


def make_fin(beta=1.0) -> CompanyFinancials:
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 10_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "effectiveTaxRate": 0.21, "interestExpense": 40e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": beta, "marketCap": 1_000_000e6,
                 "price": 100.0, "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


@pytest.fixture(autouse=True)
def _fixed_risk_free(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.0507, "pinned for test"))


# --- Which limit binds ------------------------------------------------

class TestTheThreeLimits:
    def test_the_rate_binds_when_it_is_the_lowest(self):
        assert terminal_growth_ceiling(0.030, 0.12) == pytest.approx(0.030)

    def test_long_run_growth_binds_at_todays_rates(self):
        """A 5.07% ten-year against 4.0% long-run growth: the economy is
        what holds the figure down, for any company with room under WACC."""
        assert terminal_growth_ceiling(0.0507, 0.1335) == pytest.approx(
            LONG_RUN_NOMINAL_GROWTH)

    def test_the_discount_rate_binds_for_a_low_wacc_company(self):
        """Coca-Cola's 6.82% leaves less than four points above the margin."""
        assert terminal_growth_ceiling(0.0507, 0.0682) == pytest.approx(
            0.0682 - TERMINAL_GROWTH_SAFETY_MARGIN)

    def test_it_is_always_the_lowest_of_the_three(self):
        for rf in (0.0, 0.02, 0.0507, 0.12):
            for wacc in (0.04, 0.0682, 0.10, 0.1335, 0.25):
                assert terminal_growth_ceiling(rf, wacc) == pytest.approx(
                    min(rf, LONG_RUN_NOMINAL_GROWTH,
                        wacc - TERMINAL_GROWTH_SAFETY_MARGIN))

    def test_the_margin_is_three_points(self):
        assert TERMINAL_GROWTH_SAFETY_MARGIN == pytest.approx(0.030)

    def test_the_spread_under_wacc_is_never_less_than_the_margin(self):
        for beta in (0.15, 0.3, 0.5, 1.0, 1.5, 2.5, 4.0):
            a = derive_assumptions(make_fin(beta=beta)).assumptions
            assert a.wacc - a.terminal_growth >= TERMINAL_GROWTH_SAFETY_MARGIN - 1e-9, \
                f"beta={beta}"


# --- The provenance says which --------------------------------------

class TestItSaysWhichLimitBound:
    def _prov(self, beta):
        d = derive_assumptions(make_fin(beta=beta))
        return d.provenance["terminal_growth"], d.diagnostics

    def test_a_high_beta_company_is_held_by_the_economy(self):
        prov, diag = self._prov(2.5)
        assert diag["terminal_growth_binding"] == "long_run_growth"
        assert "outgrows its own economy" in prov.detail
        assert "Fed SEP" in prov.detail

    def test_a_low_beta_company_is_held_by_its_discount_rate(self):
        prov, diag = self._prov(0.15)
        assert diag["terminal_growth_binding"] == "wacc_margin"
        assert "3 percentage points below this company" in prov.detail, (
            "points, not percent: 'held 3% below' reads as a relative cut")

    def test_the_judgement_is_named_as_one(self):
        """The three-point spread is not a published standard and the
        provenance must not imply it is."""
        prov, _ = self._prov(0.15)
        assert "modelling judgement" in prov.detail
        assert "rather than a published standard" in prov.detail

    def test_the_observed_figures_carry_their_source(self):
        prov, _ = self._prov(2.5)
        assert "2.0% real GDP" in prov.detail
        assert "2.0% PCE inflation" in prov.detail

    def test_the_rate_path_names_the_treasury(self, monkeypatch):
        monkeypatch.setattr(A, "fetch_risk_free_rate",
                            lambda tenor="year10": (0.025, "pinned low"))
        prov, diag = self._prov(1.0)
        assert diag["terminal_growth_binding"] == "risk_free_rate"
        assert "10-year Treasury" in prov.detail


# --- The slider still rests at the ceiling ---------------------------

class TestTheSlider:
    def test_it_rests_at_the_ceiling_and_only_moves_down(self):
        d = derive_assumptions(make_fin(beta=1.0))
        prov = d.provenance["terminal_growth"]
        assert prov.value == pytest.approx(prov.allowed_max)
        assert prov.allowed_min == 0.0

    def test_the_ceiling_is_company_specific_where_wacc_binds(self):
        low = derive_assumptions(make_fin(beta=0.15)).provenance["terminal_growth"]
        high = derive_assumptions(make_fin(beta=2.5)).provenance["terminal_growth"]
        assert low.allowed_max < high.allowed_max
        assert high.allowed_max == pytest.approx(LONG_RUN_NOMINAL_GROWTH)

    def test_the_ceiling_never_goes_negative(self):
        """A company whose WACC is below the margin would otherwise be
        offered a negative ceiling, and a slider that cannot be moved."""
        d = derive_assumptions(make_fin(beta=1.0), overrides={"wacc": 0.02})
        prov = d.provenance["terminal_growth"]
        assert prov.allowed_max >= 0.0
        assert prov.value >= 0.0


# --- The figure has a date on it -------------------------------------

class TestTheProjectionsGoStale:
    def test_it_is_the_fed_september_2026_figure(self):
        assert LONG_RUN_NOMINAL_GROWTH == pytest.approx(0.040)
        assert LONG_RUN_GROWTH_AS_OF == date(2026, 9, 16)

    def test_a_fresh_set_says_nothing_about_its_age(self):
        fresh = LONG_RUN_GROWTH_AS_OF + timedelta(days=30)
        assert not A.sep_is_stale(fresh)
        assert "due" not in A.long_run_growth_source(fresh)

    def test_an_old_set_says_a_newer_one_is_due(self):
        """The SEP is quarterly, so past a hundred days one was missed."""
        old = LONG_RUN_GROWTH_AS_OF + timedelta(days=SEP_STALE_AFTER_DAYS + 1)
        assert A.sep_is_stale(old)
        assert "newer set is due" in A.long_run_growth_source(old)

    def test_the_boundary_is_not_stale_until_passed(self):
        on_the_day = LONG_RUN_GROWTH_AS_OF + timedelta(days=SEP_STALE_AFTER_DAYS)
        assert not A.sep_is_stale(on_the_day)
        assert A.sep_is_stale(on_the_day + timedelta(days=1))


# --- What it did not touch -------------------------------------------

def test_the_dividend_model_keeps_its_own_terminal_growth():
    """DEFAULT_TERMINAL_GROWTH serves five unrelated purposes and this rule
    is only one of them. The dividend model's terminal growth is not the
    DCF's ceiling and was deliberately left where it was.
    """
    assert A.DEFAULT_TERMINAL_GROWTH == pytest.approx(0.025)
    assert A.DEFAULT_TERMINAL_GROWTH != LONG_RUN_NOMINAL_GROWTH

    import ddm_assumptions as D
    assert D.DEFAULT_TERMINAL_GROWTH == pytest.approx(0.025)


def test_the_revenue_growth_fallback_is_not_the_ceiling_either():
    import inspect
    source = inspect.getsource(A.derive_assumptions)
    assert "insufficient revenue history" in source
    assert "DEFAULT_TERMINAL_GROWTH" in source, (
        "the revenue-growth fallback still uses the conservative default, "
        "not the long-run growth figure")


# --- The sentence the app repeats ------------------------------------

def test_the_honesty_note_describes_the_rule_it_actually_uses():
    """The note is duplicated in Dart so it can update mid-drag, and the
    parity fixtures pin the Dart copy against a recorded backend string.
    Nothing pinned the backend string itself, so a revert here would have
    gone unnoticed until someone read it.
    """
    import analysis
    import market_data
    from dcf import BaseYearData, run_dcf

    fin = make_fin(beta=1.2)
    base, meta = market_data.base_year_from(fin)
    d = derive_assumptions(fin)
    report = analysis.ValuationReport(
        ticker="TEST", company_name="Test Co", sector="Technology",
        exchange="NASDAQ", fiscal_year="2025",
        suitability=analysis.assess_suitability(fin, d, base),
        base=base, derived=d, meta=meta, result=run_dcf(base, d.assumptions))
    note = analysis.honesty_note(report)

    assert "lowest of the risk-free rate" in note
    assert "long-run US nominal growth" in note
    assert "discount rate less three points" in note
    assert "moved down but not up" in note
    assert "defaults to the current risk-free rate" not in note
