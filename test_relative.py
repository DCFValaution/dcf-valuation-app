"""
Tests for the relative valuation arithmetic (relative.py).

Worked figures throughout: a company with market value 100,000 and peers built
so every median and implied value can be checked by hand.

Run with:  python -m pytest test_relative.py -v
"""

from datetime import date

import pytest

import relative as R
from market_data import MarketFigures

TODAY = date(2026, 9, 11)


def figs(ticker="CO", *, name=None, price=100.0, shares=1_000.0, revenue=10_000.0,
         ebitda=3_000.0, net_income=2_000.0, debt=1_000.0, cash=500.0, equity=5_000.0,
         trade="USD", report="USD", as_of="2026-06-30") -> MarketFigures:
    return MarketFigures(ticker=ticker, name=name or f"{ticker} Inc.", trading_currency=trade,
                         reporting_currency=report, price=price, revenue=revenue, ebitda=ebitda,
                         net_income=net_income, total_debt=debt, cash=cash, shares=shares,
                         common_equity=equity, as_of=as_of)


# Market value 100,000; enterprise value 100,500.
COMPANY = figs("CO")
# P/E 20, 25, 30   EV/EBITDA 10, 12.5, 15   Price/Sales 4, 5, 6
PEERS = [
    figs("P1", price=50, net_income=2_500, ebitda=5_000, revenue=12_500, debt=0, cash=0),
    figs("P2", price=80, net_income=3_200, ebitda=6_400, revenue=16_000, debt=0, cash=0),
    figs("P3", price=120, net_income=4_000, ebitda=8_000, revenue=20_000, debt=0, cash=0),
]


# ---------------------------------------------------------------------------
# Multiples
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [("pe", 50.0), ("ev_ebitda", 33.5), ("ps", 10.0), ("pb", 20.0)])
def test_multiples_are_computed_from_market_value(name, expected):
    value, reason = R.multiple_of(COMPANY, name)
    assert value == pytest.approx(expected)
    assert reason is None


@pytest.mark.parametrize("overrides, name, phrase", [
    ({"net_income": -500.0}, "pe", "not profitable"),
    ({"net_income": None}, "pe", "no recent earnings figures"),
    ({"ebitda": 0.0}, "ev_ebitda", "EBITDA was not positive"),
    ({"revenue": None}, "ps", "no recent revenue figures"),
    ({"equity": -1.0}, "pb", "book value is not positive"),
    ({"shares": None}, "pe", "market value is unknown"),
])
def test_a_meaningless_multiple_is_skipped_with_its_reason(overrides, name, phrase):
    value, reason = R.multiple_of(figs(**overrides), name)
    assert value is None
    assert phrase in reason


def test_a_multiple_above_its_ceiling_is_skipped_not_averaged_in():
    """CrowdStrike at 4,752x earnings describes near-zero profit, not pricing."""
    value, reason = R.multiple_of(figs(net_income=50.0), "pe")      # 2,000x
    assert value is None
    assert "2,000x" in reason and "far outside the normal range" in reason
    assert "earnings are too small" in reason


def test_negative_enterprise_value_is_not_a_multiple():
    value, reason = R.multiple_of(figs(cash=500_000.0), "ev_ebitda")
    assert value is None
    assert "more cash than its debt and market value combined" in reason


@pytest.mark.parametrize("name, multiple, expected", [
    ("pe", 25.0, 50.0),              # 25 x 2,000 / 1,000
    ("ps", 5.0, 50.0),               # 5 x 10,000 / 1,000
    ("pb", 2.0, 10.0),               # 2 x 5,000 / 1,000
    ("ev_ebitda", 12.5, 37.0),       # (12.5 x 3,000 - 1,000 + 500) / 1,000
])
def test_implied_values_per_share(name, multiple, expected):
    assert R.implied_value_per_share(COMPANY, name, multiple) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

def test_peer_medians_premiums_and_implied_values_match_a_hand_calculation():
    result = R.run_relative(COMPANY, PEERS, R.STANDARD_MULTIPLES)
    by_name = {m.name: m for m in result.multiples}

    assert [m.name for m in result.multiples] == ["pe", "ev_ebitda", "ps"]
    assert by_name["pe"].peer_median == pytest.approx(25.0)
    assert by_name["ev_ebitda"].peer_median == pytest.approx(12.5)
    assert by_name["ps"].peer_median == pytest.approx(5.0)

    assert by_name["pe"].premium_to_median == pytest.approx(1.0)          # 50 vs 25
    assert by_name["ev_ebitda"].premium_to_median == pytest.approx(1.68)  # 33.5 vs 12.5
    assert by_name["pe"].implied_value_per_share == pytest.approx(50.0)
    assert by_name["ev_ebitda"].implied_value_per_share == pytest.approx(37.0)
    assert by_name["ps"].implied_value_per_share == pytest.approx(50.0)

    assert result.central_value_per_share == pytest.approx(50.0)
    assert result.low_value_per_share == pytest.approx(37.0)
    assert result.high_value_per_share == pytest.approx(50.0)
    assert result.central_vs_price == pytest.approx(-0.5)


def test_banks_and_insurers_are_compared_on_earnings_and_book_only():
    result = R.run_relative(COMPANY, PEERS, R.FINANCIAL_MULTIPLES)
    assert [m.name for m in result.multiples] == ["pe", "pb"]


def test_a_median_needs_three_peers_with_a_meaningful_value():
    peers = [PEERS[0], PEERS[1], figs("P3", price=120, net_income=-100, ebitda=8_000,
                                      revenue=20_000, debt=0, cash=0)]
    pe = next(m for m in R.run_relative(COMPANY, peers, R.STANDARD_MULTIPLES).multiples
              if m.name == "pe")
    assert pe.applicable is False
    assert pe.peer_count == 2
    assert "at least 3" in pe.reason
    assert next(p for p in pe.peers if p.ticker == "P3").excluded_reason == \
        "not profitable over the last twelve months"


def test_a_multiple_meaningless_for_the_company_is_not_applied():
    result = R.run_relative(figs(net_income=-2_000.0), PEERS, R.STANDARD_MULTIPLES)
    pe = next(m for m in result.multiples if m.name == "pe")
    assert pe.applicable is False
    assert "not meaningful for CO" in pe.reason
    assert pe.peer_median == pytest.approx(25.0), "the peers' median is still reported"
    assert result.central_value_per_share == pytest.approx((37.0 + 50.0) / 2)


def test_a_negative_implied_value_is_not_applied():
    """Debt larger than the enterprise value the peer median implies."""
    heavy = figs(debt=50_000.0)     # its own EV/EBITDA 49.8x: meaningful
    ev = next(m for m in R.run_relative(heavy, PEERS, R.STANDARD_MULTIPLES).multiples
              if m.name == "ev_ebitda")
    assert ev.applicable is False
    assert "no positive value per share" in ev.reason


def test_nothing_applicable_gives_no_central_figure():
    result = R.run_relative(figs(net_income=-1.0, ebitda=-1.0, revenue=None), PEERS,
                            R.STANDARD_MULTIPLES)
    assert result.central_value_per_share is None
    assert result.low_value_per_share is None and result.high_value_per_share is None


def test_the_median_is_robust_to_one_outlying_peer():
    outlier = figs("P3", price=900, net_income=10_000, ebitda=8_000, revenue=20_000, debt=0, cash=0)
    pe = next(m for m in R.run_relative(COMPANY, [PEERS[0], PEERS[1], outlier],
                                        R.STANDARD_MULTIPLES).multiples if m.name == "pe")
    assert pe.peer_median == pytest.approx(25.0)      # 20, 25, 90


# ---------------------------------------------------------------------------
# Which peers can be compared at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("peer, phrase", [
    (figs("CO"), "the company itself"),
    (figs("COB", name="CO Inc."), "another share class"),
    (figs("ADR", report="CNY"), "reports its results in CNY but its shares trade in USD"),
    (figs("OLD", as_of="2024-12-31"), "too old"),
    (figs("NOPRICE", price=None), "no current price"),
    (figs("TINY", price=0.5), "less than 1/10 the size of CO - too different in size"),
    (figs("HUGE", price=2_000.0), "more than 10 times the size of CO - too different in size"),
])
def test_peers_that_cannot_be_compared_are_excluded_with_a_reason(peer, phrase):
    assert phrase in R.peer_exclusion(COMPANY, peer, TODAY)


def test_a_comparable_peer_is_not_excluded():
    assert R.peer_exclusion(COMPANY, PEERS[0], TODAY) is None


def test_the_size_band_is_not_imposed_on_a_peer_the_user_chose():
    assert R.peer_exclusion(COMPANY, figs("TINY", price=0.5), TODAY, enforce_size=False) is None
