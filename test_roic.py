"""
Tests for the return-on-invested-capital diagnostic.

Two things are being protected. The arithmetic, which is checked against the
definition rather than against a stored answer, so a changed formula fails
here instead of quietly agreeing with itself. And the restraint: the figure
must be absent - not zero, not a dash - everywhere it would mislead, and it
must never touch the valuation it sits beside.

The three company shapes below mirror what the real data showed when the
definition was chosen: a compounder far above its cost of capital, a
regulated utility close enough to its own that neither direction can be
claimed, and a capital-heavy business below it. They are synthetic so the
suite does not depend on a live market, but the shapes are the real ones.

Run with:  python -m pytest test_roic.py -v
"""

import pytest

import analysis
import assumptions as A
import roic
from market_data import CompanyFinancials
from test_api import LOSSMAKING, PROFITABLE, fake_fetch_financials, make_fin


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", fake_fetch_financials)
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def company(*, operating_income, total_assets, current_liabilities,
            short_term_debt=0.0, cash=0.0, short_term_investments=0.0,
            years=3, name="Shape Co"):
    """A company built to a chosen shape, one figure per year repeated."""
    income, balance, cashflow = [], [], []
    for i in range(years):
        income.append({
            "fiscalYear": str(2025 - i),
            "revenue": 10_000.0,
            "operatingIncome": operating_income,
            "incomeBeforeTax": operating_income,
            "incomeTaxExpense": operating_income * 0.2,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalAssets": total_assets,
            "totalCurrentLiabilities": current_liabilities,
            "shortTermDebt": short_term_debt,
            "cashAndCashEquivalents": cash,
            "shortTermInvestments": short_term_investments,
        })
        cashflow.append({"fiscalYear": str(2025 - i)})
    return CompanyFinancials(
        ticker="SHAPE",
        profile={"companyName": name, "beta": 1.0, "marketCap": 1000.0,
                 "price": 10.0, "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


# ---------------------------------------------------------------------------
# Invested capital: what counts as capital someone put in
# ---------------------------------------------------------------------------

def test_payables_are_not_capital_anyone_invested():
    """Suppliers financing the business for free are not investors."""
    bal = {"totalAssets": 1000.0, "totalCurrentLiabilities": 200.0,
           "shortTermDebt": 0.0, "cashAndCashEquivalents": 0.0}
    assert roic.invested_capital(bal) == 800.0


def test_borrowed_money_stays_in_even_when_it_is_current():
    """Short-term debt has a cost, so it is not netted off with the payables."""
    base = {"totalAssets": 1000.0, "totalCurrentLiabilities": 200.0,
            "cashAndCashEquivalents": 0.0}
    without = roic.invested_capital({**base, "shortTermDebt": 0.0})
    with_debt = roic.invested_capital({**base, "shortTermDebt": 150.0})
    assert with_debt == without + 150.0


def test_cash_and_near_cash_are_both_taken_out():
    bal = {"totalAssets": 1000.0, "totalCurrentLiabilities": 0.0,
           "shortTermDebt": 0.0, "cashAndCashEquivalents": 100.0,
           "shortTermInvestments": 50.0}
    assert roic.invested_capital(bal) == 850.0


def test_current_liabilities_below_short_term_debt_do_not_add_capital():
    """The netting is floored at zero: it can only ever remove payables."""
    bal = {"totalAssets": 1000.0, "totalCurrentLiabilities": 50.0,
           "shortTermDebt": 200.0, "cashAndCashEquivalents": 0.0}
    assert roic.invested_capital(bal) == 1000.0


# ---------------------------------------------------------------------------
# NOPAT: taxed at the rate the valuation already uses
# ---------------------------------------------------------------------------

def test_nopat_is_operating_profit_at_the_rate_it_is_given():
    row = {"operatingIncome": 1000.0}
    assert roic.nopat(row, 0.25) == 750.0


def test_nopat_does_not_invent_a_tax_rate_of_its_own():
    """The year's own effective rate is deliberately ignored: a second
    derivation here could drift from the one the valuation uses."""
    row = {"operatingIncome": 1000.0,
           "incomeBeforeTax": 500.0, "incomeTaxExpense": 400.0}  # 80% that year
    assert roic.nopat(row, 0.20) == 800.0


# ---------------------------------------------------------------------------
# The verdict, and the band around it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("roic_value, wacc, expected", [
    (0.70, 0.11, roic.ABOVE),     # Apple's gap, give or take
    (0.118, 0.089, roic.ABOVE),   # Union Pacific's, which is a near thing
    (0.042, 0.055, roic.ABOUT),   # Southern's
    (0.040, 0.054, roic.ABOUT),   # Duke's
    (0.08, 0.08, roic.ABOUT),
    (0.026, 0.09, roic.BELOW),
    (-0.58, 0.124, roic.BELOW),
])
def test_the_band_decides_the_verdict(roic_value, wacc, expected):
    assert roic.verdict_for(roic_value, wacc) == expected


@pytest.mark.parametrize("sign, expected", [(1, roic.ABOUT), (-1, roic.ABOUT)])
def test_a_gap_of_exactly_the_band_is_not_a_claim_either_way(sign, expected):
    """Measured against a zero cost of capital so the subtraction is exact:
    at 0.08 the same gap lands a float-width outside the band, which is not a
    distinction worth asserting about."""
    assert roic.verdict_for(sign * roic.NEUTRAL_BAND, 0.0) == expected


def test_the_band_is_two_percentage_points_either_way():
    assert roic.NEUTRAL_BAND == 0.02


# ---------------------------------------------------------------------------
# The three shapes the real data showed
# ---------------------------------------------------------------------------

def test_a_compounder_reads_above_its_cost_of_capital():
    """Apple's shape: a large return on a small capital base."""
    fin = company(operating_income=800.0, total_assets=1200.0,
                  current_liabilities=200.0, cash=100.0, name="Compounder")
    r = roic.compute(fin, tax_rate=0.20, wacc=0.11)
    assert r is not None
    assert r.roic == pytest.approx(640.0 / 900.0)
    assert r.verdict == roic.ABOVE
    assert "earns more" in r.sentence


def test_a_regulated_utility_reads_about_its_cost_of_capital():
    """Southern and Duke both land inside a point and a half of their WACC,
    which book figures cannot resolve into a direction."""
    fin = company(operating_income=60.0, total_assets=1300.0,
                  current_liabilities=100.0, cash=20.0, name="Utility")
    r = roic.compute(fin, tax_rate=0.20, wacc=0.055)
    assert r is not None
    assert r.roic == pytest.approx(48.0 / 1180.0)   # ~4.07%
    assert r.verdict == roic.ABOUT
    assert "earns about" in r.sentence
    assert abs(r.spread) < roic.NEUTRAL_BAND


def test_a_capital_heavy_business_reads_below_its_cost_of_capital():
    fin = company(operating_income=30.0, total_assets=1300.0,
                  current_liabilities=100.0, cash=20.0, name="Capital Heavy")
    r = roic.compute(fin, tax_rate=0.20, wacc=0.09)
    assert r is not None
    assert r.verdict == roic.BELOW
    assert "earns less" in r.sentence


# ---------------------------------------------------------------------------
# One year does not decide it
# ---------------------------------------------------------------------------

def test_the_median_year_decides_it_not_the_latest():
    """Ford's latest year reads -4.5% against a median of +2.6%. The median
    is what is reported, so one bad year cannot flip the verdict."""
    fin = company(operating_income=100.0, total_assets=1000.0,
                  current_liabilities=0.0, years=3)
    fin.income[0]["operatingIncome"] = -500.0        # a bad latest year

    r = roic.compute(fin, tax_rate=0.20, wacc=0.05)
    assert len(r.years) == 3
    assert r.years[0].roic < 0, "the bad year is still recorded"
    assert r.roic == pytest.approx(80.0 / 1000.0), "but the median is reported"
    assert r.verdict == roic.ABOVE


def test_every_year_in_the_window_shows_its_workings():
    fin = company(operating_income=100.0, total_assets=1000.0,
                  current_liabilities=0.0, years=3)
    r = roic.compute(fin, tax_rate=0.20, wacc=0.05)
    assert [y.fiscal_year for y in r.years] == ["2025", "2024", "2023"]
    for year in r.years:
        assert year.roic == pytest.approx(year.nopat / year.invested_capital)


# ---------------------------------------------------------------------------
# Silence rather than a misleading number
# ---------------------------------------------------------------------------

def test_no_figure_where_invested_capital_is_not_positive():
    """A balance sheet whose cash and payables exceed its assets has no
    capital at work to earn a return on."""
    fin = company(operating_income=100.0, total_assets=100.0,
                  current_liabilities=0.0, cash=500.0)
    assert roic.compute(fin, tax_rate=0.20, wacc=0.08) is None


def test_no_figure_without_a_balance_sheet():
    fin = company(operating_income=100.0, total_assets=0.0,
                  current_liabilities=0.0)
    assert roic.compute(fin, tax_rate=0.20, wacc=0.08) is None


# ---------------------------------------------------------------------------
# Where it reaches, and where it does not
# ---------------------------------------------------------------------------

def test_a_valued_company_carries_the_diagnostic():
    report = analysis.value_company(PROFITABLE)
    assert report.suitable
    assert report.capital_returns is not None
    assert report.capital_returns.verdict in (roic.ABOVE, roic.ABOUT, roic.BELOW)


def test_a_refused_company_carries_none():
    """A loss-maker is refused a valuation; it gets no return on capital
    either, rather than a negative one on a screen explaining there is no
    figure."""
    report = analysis.value_company(LOSSMAKING)
    assert not report.suitable
    assert report.capital_returns is None


def test_the_dividend_discount_model_has_no_such_field_at_all():
    """Banks never reach it, and there is nowhere for it to arrive if they
    did. Their balance sheets do not report current liabilities, so an
    invested capital built from one would quietly treat deposits as capital."""
    assert not hasattr(analysis.DDMReport, "capital_returns")
    assert "capital_returns" in analysis.ValuationReport.__dataclass_fields__


# ---------------------------------------------------------------------------
# It changes nothing
# ---------------------------------------------------------------------------

def test_the_valuation_is_identical_whether_or_not_it_is_computed():
    """The diagnostic reads the same figures the model does and writes none
    of them back."""
    report = analysis.value_company(PROFITABLE)
    before = report.result.intrinsic_value_per_share

    recomputed = roic.compute(
        fake_fetch_financials(PROFITABLE),
        report.derived.assumptions.tax_rate,
        report.derived.assumptions.wacc,
    )
    assert recomputed is not None

    after = analysis.value_company(PROFITABLE).result.intrinsic_value_per_share
    assert after == before
