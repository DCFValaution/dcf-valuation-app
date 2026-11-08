"""Every money field on the income statement travels with the others.

Three did not. A converted company had its revenue restated into the
trading currency while its net income stayed in the reporting one, so any
ratio drawn across the two was wrong by the exchange rate - and the
dividend model draws several.
"""

import pytest

from market_data import (_MONEY_FIELDS_BALANCE, _MONEY_FIELDS_CASHFLOW,
                         _MONEY_FIELDS_INCOME, convert_statements)


def statements():
    income = [{"revenue": 1000.0, "operatingIncome": 200.0,
               "netIncomeCommon": 150.0, "dilutedEPS": 1.50,
               "grossProfit": 400.0, "weightedAverageShsOutDil": 100.0,
               "fiscalYear": 2026}]
    balance = [{"commonStockEquity": 900.0, "totalAssets": 2000.0}]
    cashflow = [{"dividendsPaid": -50.0}]
    return income, balance, cashflow


class TestTheThreeThatWereMissing:
    @pytest.mark.parametrize("field", ["netIncomeCommon", "dilutedEPS",
                                       "grossProfit"])
    def test_it_is_converted(self, field):
        income, balance, cashflow = statements()
        before = income[0][field]
        convert_statements(income, balance, cashflow, 0.15)
        assert income[0][field] == pytest.approx(before * 0.15)

    def test_earnings_per_share_is_money_not_a_count(self):
        """The distinction the omission turned on.

        A per-share figure left in one currency beside a price in another
        is a price/earnings ratio that means nothing - Alibaba's read 2.5
        where it should have read 16.6.
        """
        assert "dilutedEPS" in _MONEY_FIELDS_INCOME
        assert "weightedAverageShsOutDil" not in _MONEY_FIELDS_INCOME, (
            "a share count is not money and must not be scaled")


class TestRatiosSurviveConversion:
    def test_margins_are_unchanged_by_the_rate(self):
        """A ratio of two money figures is currency-neutral - once both of
        them are actually in the same currency."""
        income, balance, cashflow = statements()
        before = income[0]["netIncomeCommon"] / income[0]["revenue"]
        convert_statements(income, balance, cashflow, 0.0063)
        assert income[0]["netIncomeCommon"] / income[0]["revenue"] == (
            pytest.approx(before))

    def test_return_on_equity_crosses_the_two_statements(self):
        """The one that produced 1726% for Mitsubishi UFJ: net income from
        the income statement over equity from the balance sheet."""
        income, balance, cashflow = statements()
        before = income[0]["netIncomeCommon"] / balance[0]["commonStockEquity"]
        convert_statements(income, balance, cashflow, 0.0063)
        after = income[0]["netIncomeCommon"] / balance[0]["commonStockEquity"]
        assert after == pytest.approx(before)
        assert after < 1.0, "a plausible ROE, not a hundredfold one"

    def test_payout_crosses_income_and_cash_flow(self):
        income, balance, cashflow = statements()
        before = abs(cashflow[0]["dividendsPaid"]) / income[0]["netIncomeCommon"]
        convert_statements(income, balance, cashflow, 0.0063)
        assert abs(cashflow[0]["dividendsPaid"]) / income[0]["netIncomeCommon"] \
            == pytest.approx(before)


def test_a_rate_of_one_changes_nothing():
    """What a US company gets: conversion is a no-op, and must stay one."""
    income, balance, cashflow = statements()
    original = dict(income[0])
    convert_statements(income, balance, cashflow, 1.0)
    for k, v in original.items():
        assert income[0][k] == pytest.approx(v)


def test_no_money_field_is_left_out_of_a_conversion_list():
    """A guard against the next one.

    Every field the statements carry is either money, and converted, or
    explicitly named here as not money. Adding a money field without
    adding it to a list is what caused this.
    """
    not_money = {
        "date", "fiscalYear",
        "weightedAverageShsOut", "weightedAverageShsOutDil",  # counts
    }
    from market_data import _balance_row, _cashflow_row, _income_row
    import inspect
    for fn, converted in ((_income_row, _MONEY_FIELDS_INCOME),
                          (_balance_row, _MONEY_FIELDS_BALANCE),
                          (_cashflow_row, _MONEY_FIELDS_CASHFLOW)):
        source = inspect.getsource(fn)
        keys = {line.split('"')[1] for line in source.splitlines()
                if line.strip().startswith('"') and '":' in line}
        missed = keys - set(converted) - not_money
        assert not missed, (
            f"{fn.__name__} returns {sorted(missed)}, which is neither "
            "converted nor declared a non-money field")
