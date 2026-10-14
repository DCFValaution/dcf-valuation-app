"""
Minority interest in the equity bridge.

Enterprise value is built from fully consolidated cash flows: a subsidiary
the parent owns 60% of contributes 100% of its revenue and margin. The 40%
belonging to everyone else is a claim on that, and it leaves the bridge
alongside debt. Omitting it values cash flows the parent does not own.

Three things are protected here.

The arithmetic, at both bridge sites and in the sensitivity table's own
inline copy of the bridge - the centre cell has to reproduce the headline,
so a subtraction missing from one of them shows up as the table disagreeing
with the figure above it.

The currency. Minority interest is a balance-sheet amount in the reporting
currency, and it is subtracted from an enterprise value in the trading
currency. That is exactly the mix that made a yen reporter's debt count 158
times over in the capital-structure weights, so it gets its own test.

And the difference between reading a figure and assuming one. An absent
line is treated as nil, which was checked across thirty-two companies rather
than assumed - but a reader looking at a holding company should be able to
tell which of the two they got.

Run with:  python -m pytest test_minority_interest.py -v
"""

import pytest

import market_data as M
from dcf import Assumptions, BaseYearData, run_dcf
from speculative import SpeculativeInputs
from test_market_data import (AAPL_BALANCE, AAPL_CASHFLOW, AAPL_INCOME,
                              AAPL_INFO, FakeTicker, frame, install)


@pytest.fixture(autouse=True)
def _clear_caches():
    M.clear_caches()
    yield
    M.clear_caches()


ASSUMPTIONS = Assumptions(
    revenue_growth=0.05, operating_margin=0.30, tax_rate=0.20,
    da_pct=0.03, capex_pct=0.04, nwc_pct=0.02, wacc=0.09,
    terminal_growth=0.025,
)


def base(**overrides):
    fields = dict(revenue=100_000.0, total_debt=20_000.0, cash=10_000.0,
                  shares=1_000.0, current_price=50.0)
    fields.update(overrides)
    return BaseYearData(**fields)


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------

def test_minority_interest_leaves_the_equity_value():
    without = run_dcf(base(), ASSUMPTIONS)
    with_mi = run_dcf(base(minority_interest=5_000.0), ASSUMPTIONS)

    assert with_mi.enterprise_value == without.enterprise_value, \
        "it is an equity-bridge item, not an operating one"
    assert with_mi.equity_value == without.equity_value - 5_000.0
    assert with_mi.intrinsic_value_per_share < without.intrinsic_value_per_share


def test_it_subtracts_exactly_like_debt():
    """Both are claims on the consolidated business that are not the parent's."""
    as_debt = run_dcf(base(total_debt=25_000.0), ASSUMPTIONS)
    as_minority = run_dcf(base(minority_interest=5_000.0), ASSUMPTIONS)
    assert as_debt.equity_value == pytest.approx(as_minority.equity_value)


def test_no_minority_interest_is_the_default_and_changes_nothing():
    assert base().minority_interest == 0.0
    explicit = run_dcf(base(minority_interest=0.0), ASSUMPTIONS)
    defaulted = run_dcf(base(), ASSUMPTIONS)
    assert (explicit.intrinsic_value_per_share
            == defaulted.intrinsic_value_per_share)


def test_the_sensitivity_centre_cell_still_reproduces_the_headline():
    """The inline bridge inside the sensitivity table must match the real one."""
    result = run_dcf(base(minority_interest=5_000.0), ASSUMPTIONS)
    centre = len(result.sensitivity) // 2
    assert result.sensitivity[centre][centre] == pytest.approx(
        result.intrinsic_value_per_share, rel=1e-12)


def test_the_speculative_bridge_subtracts_it_too():
    fields = dict(revenue=1_000.0, operating_margin=-0.30, da_pct=0.05,
                  capex_pct=0.10, nwc_pct=0.02, total_debt=500.0,
                  cash=800.0, shares=100.0, current_price=10.0)
    assert SpeculativeInputs(**fields).minority_interest == 0.0
    with_mi = SpeculativeInputs(**fields, minority_interest=50.0)
    assert with_mi.minority_interest == 50.0


# ---------------------------------------------------------------------------
# Reading it off the balance sheet
# ---------------------------------------------------------------------------

def balance_with(**extra):
    rows = dict(AAPL_BALANCE)
    rows.update(extra)
    return rows


def test_it_is_read_from_the_balance_sheet(monkeypatch):
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME),
        balance=frame(balance_with(**{"Minority Interest": [3_000e6] * 3})),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("X")
    base_year, meta = M.base_year_from(fin)

    assert base_year.minority_interest == 3_000.0
    assert meta["minority_interest_reported"] is True
    assert meta["minority_interest_source"] == "balance sheet: Minority Interest"


def test_an_absent_line_is_nil_and_says_it_was_inferred(monkeypatch):
    """Absent genuinely means zero - but the provenance must not pretend it
    read a figure. A holding company's reader needs to tell them apart."""
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    base_year, meta = M.base_year_from(M.fetch_financials("X"))

    assert base_year.minority_interest == 0.0
    assert meta["minority_interest_reported"] is False
    assert "inferred" in meta["minority_interest_source"]
    assert "nil" in meta["minority_interest_source"]


@pytest.mark.parametrize("lookalike", [
    "Total Equity Gross Minority Interest",
    "Total Liabilities Net Minority Interest",
    "Total Non Current Liabilities Net Minority Interest",
])
def test_the_lookalike_labels_are_not_mistaken_for_it(monkeypatch, lookalike):
    """Yahoo publishes three lines whose names contain these words and mean
    something else entirely. Only the exact label counts."""
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME),
        balance=frame(balance_with(**{lookalike: [500_000e6] * 3})),
        cashflow=frame(AAPL_CASHFLOW)))

    base_year, meta = M.base_year_from(M.fetch_financials("X"))
    assert base_year.minority_interest == 0.0
    assert meta["minority_interest_reported"] is False


# ---------------------------------------------------------------------------
# It is money, so it travels with the currency conversion
# ---------------------------------------------------------------------------

def test_it_is_converted_into_the_trading_currency(monkeypatch):
    """The bridge must not mix currencies.

    Minority interest comes off an enterprise value already restated into
    the trading currency. Left in the reporting currency it would be
    subtracted at roughly thirty-two times its true size for a Taiwanese
    company.
    """
    rate = 0.03134403
    monkeypatch.setattr(M, "_fx_close", lambda symbol: {
        "TWDUSD=X": (rate, "2026-10-12"),
        "USDTWD=X": (1.0 / rate, "2026-10-12"),
    }.get(symbol))

    info = dict(AAPL_INFO)
    info["financialCurrency"] = "TWD"
    install(monkeypatch, FakeTicker(
        info=info, income=frame(AAPL_INCOME),
        balance=frame(balance_with(**{"Minority Interest": [3_000e6] * 3})),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("TSM")
    base_year, _ = M.base_year_from(fin)

    assert fin.fx["pair"] == "TWDUSD=X"
    assert base_year.minority_interest == pytest.approx(3_000.0 * rate)
    # And it is in the same currency as the figures it is subtracted from.
    assert base_year.minority_interest / base_year.total_debt == pytest.approx(
        3_000e6 / (20_329e6 + 78_328e6))


def test_a_usd_company_leaves_it_untouched(monkeypatch):
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME),
        balance=frame(balance_with(**{"Minority Interest": [3_000e6] * 3})),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("X")
    assert fin.fx is None
    assert fin.balance[0]["minorityInterest"] == 3_000e6
