"""The reference card beside the valuation, and the gaps in it.

Most of what matters here is what the card refuses to print. A dash is a
statement - "this figure does not exist for this company" - and these
tests are mostly about keeping it honest.
"""

import pytest

from market_data import CompanyFinancials
from market_snapshot import market_snapshot


def _fin(sector="Technology", industry="Consumer Electronics",
         income=None, balance=None, cashflow=None, **profile):
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "sector": sector,
                 "industry": industry, "currency": "USD", **profile},
        income=[income if income is not None else {
            "revenue": 1000.0, "netIncomeCommon": 100.0, "dilutedEPS": 2.0,
            "weightedAverageShsOutDil": 50.0}],
        balance=[balance if balance is not None else {
            "commonStockEquity": 400.0, "totalCurrentAssets": 300.0,
            "totalCurrentLiabilities": 150.0}],
        cashflow=[cashflow if cashflow is not None else {}],
        trading_currency="USD",
    )


class TestCurrentRatio:
    """Current assets over current liabilities, where that division means
    something."""

    def test_an_ordinary_company_gets_the_ratio(self):
        snapshot = market_snapshot(_fin(), price=40.0)
        assert snapshot.current_ratio == pytest.approx(2.0)

    def test_a_bank_gets_nothing_even_with_both_figures_present(self):
        """The gate is what the company is, not whether the data arrived.

        A bank's balance sheet is not split current and non-current, so a
        pair of figures inferred for one is not a liquidity measure. This
        is the case the data source cannot protect us from: if it ever
        starts reporting the two lines for a lender, the ratio must still
        be withheld.
        """
        snapshot = market_snapshot(
            _fin(sector="Financial Services", industry="Banks - Diversified",
                 balance={"commonStockEquity": 400.0,
                          "totalCurrentAssets": 300.0,
                          "totalCurrentLiabilities": 150.0}),
            price=40.0)
        assert snapshot.current_ratio is None

    def test_an_insurer_gets_nothing(self):
        snapshot = market_snapshot(
            _fin(sector="Financial Services",
                 industry="Insurance - Property & Casualty"),
            price=40.0)
        assert snapshot.current_ratio is None

    def test_a_fee_based_financial_keeps_it(self):
        """Visa and the asset managers do present a classified balance
        sheet; being filed under Financial Services is not the test."""
        snapshot = market_snapshot(
            _fin(sector="Financial Services", industry="Credit Services",
                 income={"revenue": 1000.0, "netIncomeCommon": 100.0,
                         "dilutedEPS": 2.0, "interestExpense": 1.0,
                         "weightedAverageShsOutDil": 50.0},
                 balance={"commonStockEquity": 400.0, "totalAssets": 900.0,
                          "totalCurrentAssets": 300.0,
                          "totalCurrentLiabilities": 150.0}),
            price=40.0)
        assert snapshot.current_ratio == pytest.approx(2.0)

    def test_an_unclassified_financial_gets_nothing(self):
        """In the sector and the data cannot say which kind. The dash says
        the ratio may not apply, which is the honest reading."""
        snapshot = market_snapshot(
            _fin(sector="Financial Services", industry="Unknown"), price=40.0)
        assert snapshot.current_ratio is None

    def test_missing_figures_give_a_dash_not_a_crash(self):
        snapshot = market_snapshot(
            _fin(balance={"commonStockEquity": 400.0}), price=40.0)
        assert snapshot.current_ratio is None

    def test_zero_liabilities_give_a_dash_not_an_infinity(self):
        snapshot = market_snapshot(
            _fin(balance={"commonStockEquity": 400.0,
                          "totalCurrentAssets": 300.0,
                          "totalCurrentLiabilities": 0.0}),
            price=40.0)
        assert snapshot.current_ratio is None

    def test_the_rate_cancels(self):
        """It is a ratio of two fields converted by the same scalar, so a
        foreign listing's ratio is the same number in either currency."""
        plain = market_snapshot(_fin(), price=40.0)
        converted = market_snapshot(
            _fin(balance={"commonStockEquity": 400.0 * 7.2,
                          "totalCurrentAssets": 300.0 * 7.2,
                          "totalCurrentLiabilities": 150.0 * 7.2}),
            price=40.0)
        assert converted.current_ratio == pytest.approx(plain.current_ratio)


class TestTheCardSurvivesOnTheRatioAlone:
    def test_a_snapshot_of_nothing_but_a_current_ratio_is_still_a_snapshot(self):
        """has_anything must count every field it carries. A field left out
        of that check is a field that can silently suppress the card."""
        snapshot = market_snapshot(
            _fin(income={"revenue": None},
                 balance={"totalCurrentAssets": 300.0,
                          "totalCurrentLiabilities": 150.0}),
            price=None)
        assert snapshot is not None
        assert snapshot.current_ratio == pytest.approx(2.0)
