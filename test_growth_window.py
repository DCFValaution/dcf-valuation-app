"""The window the growth assumption is measured over.

It used to be "as many years as the source gave", which made the assumption
a property of the data provider rather than of the company. Moving the
statements to SEC EDGAR, which files five years where the aggregator returns
four, changed Exxon's growth from -6.7% to +4.68% and Coinbase's from +31%
to -2.2% - because the extra year was 2021, Exxon's post-COVID trough and
Coinbase's crypto-boom peak. Neither figure was wrong. The window was.

So it is fixed at three years, FY-3 to the latest fiscal year, and a company
with less history than that says so.
"""
import pytest

import assumptions as A
from assumptions import GROWTH_WINDOW_YEARS, derive_assumptions
from market_data import CompanyFinancials


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(revenues, *, year_ends=None, beta=1.0):
    """A company with *revenues* newest-first, one row per year."""
    income, balance, cashflow = [], [], []
    for i, revenue in enumerate(revenues):
        year = 2025 - i
        income.append({
            "fiscalYear": str(year),
            "date": (year_ends[i] if year_ends else f"{year}-12-31"),
            "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
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
        profile={"companyName": "Test Co", "beta": beta,
                 "marketCap": 1_000_000e6, "price": 100.0,
                 "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow)


def growth(revenues):
    return derive_assumptions(make_fin(revenues)).provenance["revenue_growth"]


# --- The window is fixed ---------------------------------------------------

class TestTheWindowIsFixed:
    def test_three_years_is_three_periods_of_growth(self):
        """Four observations, FY-3 to the latest."""
        prov = growth([1331.0, 1210.0, 1100.0, 1000.0])
        assert prov.value == pytest.approx(0.10, abs=1e-9)
        assert "3-year revenue CAGR" in prov.detail
        assert "FY2022-FY2025" in prov.detail

    def test_a_fifth_year_changes_nothing(self):
        """The whole point. The same company with an extra year of history
        must get the same assumption."""
        four = growth([1331.0, 1210.0, 1100.0, 1000.0])
        five = growth([1331.0, 1210.0, 1100.0, 1000.0, 500.0])
        assert five.value == pytest.approx(four.value)
        assert five.detail == four.detail

    def test_a_tenth_year_changes_nothing_either(self):
        long_history = [1331.0, 1210.0, 1100.0, 1000.0] + [10.0] * 6
        assert growth(long_history).value == pytest.approx(
            growth([1331.0, 1210.0, 1100.0, 1000.0]).value)

    def test_the_2021_case_that_found_this(self):
        """Exxon's shape: a trough four years back that reverses the trend.

        With the old whole-window rule the extra year took the growth from
        a decline to a rise. With a fixed window the trough is simply
        outside it.
        """
        without = growth([332.0, 349.0, 344.0, 413.0])
        with_trough = growth([332.0, 349.0, 344.0, 413.0, 276.0])
        assert with_trough.value == pytest.approx(without.value)
        # And the figure is a decline, clamped to the floor, not a rise.
        assert with_trough.value == pytest.approx(A.CLAMP_REVENUE_GROWTH[0])
        assert "raw -7" in with_trough.detail

    def test_the_window_is_the_newest_years_not_the_oldest(self):
        """FY-3 to the LATEST, so recent growth is what is measured.

        Asserted on the raw figure: 2000 from 800 over three years is 35.7%,
        which the clamp holds at 15%, and a test that only looked at the
        clamped value could not tell this window from any other fast one.
        """
        prov = growth([2000.0, 1000.0, 900.0, 800.0, 10.0])
        assert prov.raw == pytest.approx((2000.0 / 800.0) ** (1 / 3) - 1,
                                         abs=1e-9)


# --- A short history says so ----------------------------------------------

class TestAShortHistory:
    def test_two_years_measures_one_period_and_says_so(self):
        prov = growth([1100.0, 1000.0])
        assert prov.value == pytest.approx(0.10)
        assert "1-year revenue CAGR" in prov.detail
        assert "only 2 years of revenue history" in prov.detail

    def test_three_years_measures_two_periods_and_says_so(self):
        prov = growth([1210.0, 1100.0, 1000.0])
        assert prov.value == pytest.approx(0.10)
        assert "2-year revenue CAGR" in prov.detail
        assert "only 3 years" in prov.detail

    def test_a_full_window_does_not_apologise(self):
        assert "only" not in growth([1331.0, 1210.0, 1100.0, 1000.0]).detail

    def test_one_year_cannot_measure_growth_at_all(self):
        prov = growth([1000.0])
        assert prov.source == "default"
        assert "insufficient revenue history" in prov.detail


# --- The arithmetic --------------------------------------------------------

class TestTheArithmetic:
    def test_it_is_a_compound_rate_not_an_average_of_annual_ones(self):
        """A single strong year must not set the trajectory.

        The mean of the annual rates here is 33.3% (100%, 0%, 0%); the
        compound rate over the same window is 26.0%.
        """
        prov = growth([1000.0, 1000.0, 1000.0, 500.0])
        assert prov.raw == pytest.approx((1000.0 / 500.0) ** (1 / 3) - 1,
                                         abs=1e-9)
        assert prov.raw != pytest.approx((1.0 + 0.0 + 0.0) / 3, abs=1e-3)

    def test_a_decline_is_reported_as_a_clamp_not_as_zero_growth(self):
        prov = growth([800.0, 900.0, 950.0, 1000.0])
        assert prov.source == "derived (clamped)"
        assert prov.raw is not None and prov.raw < 0

    def test_a_zero_in_the_window_is_not_divided_by(self):
        prov = growth([1000.0, 900.0, 800.0, 0.0])
        assert prov.source == "default"

    def test_the_window_matches_the_ratio_window(self):
        """The margin, tax rate and capex ratios are medians over
        RATIO_WINDOW years. A growth rate measured over a different span
        from the margins it multiplies is harder to reason about than one
        measured over the same span."""
        assert GROWTH_WINDOW_YEARS == A.RATIO_WINDOW
