"""
Offline tests for the Yahoo Finance data layer.

yfinance is stubbed with synthetic DataFrames, so these run with no network,
no rate limits, and deterministic results. They cover the things that are
hard to trigger against the live service on demand: an unknown symbol, an
ETF, a missing statement line, a rate limit, and the cache.

The field mappings asserted here were verified against real Yahoo responses
for AAPL, and reconcile exactly with the FMP figures the model was originally
built against.

Run with:  python -m pytest test_market_data.py -v
"""

import pandas as pd
import pytest

import market_data as M
from market_data import (DataUnavailableError, MarketDataError,
                         RateLimitedError, TickerNotFoundError,
                         UnsupportedListingError)

PERIODS = [pd.Timestamp("2025-09-30"), pd.Timestamp("2024-09-30"),
           pd.Timestamp("2023-09-30")]

# Real AAPL FY2025 figures, in dollars.
AAPL_INCOME = {
    "Total Revenue": [416_161e6, 391_035e6, 383_285e6],
    "Operating Income": [133_050e6, 123_216e6, 114_301e6],
    "EBIT": [133_050e6, 123_216e6, 114_301e6],
    "Pretax Income": [132_729e6, 123_485e6, 113_736e6],
    "Tax Provision": [20_719e6, 29_749e6, 16_741e6],
    "Diluted Average Shares": [15_004.7e6, 15_408e6, 15_813e6],
    "Basic Average Shares": [14_948.5e6, 15_343e6, 15_744e6],
    "Interest Expense": [0.0, 0.0, 3_933e6],
}
AAPL_BALANCE = {
    "Current Debt": [20_329e6, 20_879e6, 15_807e6],
    "Long Term Debt": [78_328e6, 85_750e6, 95_281e6],
    "Total Debt": [98_657e6, 106_629e6, 111_088e6],
    "Cash And Cash Equivalents": [35_934e6, 29_943e6, 29_965e6],
    "Other Short Term Investments": [18_763e6, 35_228e6, 31_590e6],
    "Investments And Advances": [77_723e6, 91_479e6, 100_544e6],
    "Current Assets": [147_957e6, 152_987e6, 143_566e6],
    "Current Liabilities": [165_631e6, 176_392e6, 145_308e6],
}
AAPL_CASHFLOW = {
    "Depreciation And Amortization": [11_698e6, 11_445e6, 11_519e6],
    "Capital Expenditure": [-12_715e6, -9_447e6, -10_959e6],
    "Change In Working Capital": [-25_000e6, 3_651e6, -6_577e6],
}
AAPL_INFO = {
    "symbol": "AAPL", "quoteType": "EQUITY", "longName": "Apple Inc.",
    "sector": "Technology", "industry": "Consumer Electronics",
    "fullExchangeName": "NasdaqGS", "beta": 1.085, "currentPrice": 315.34,
    "sharesOutstanding": 14_594_180_000, "marketCap": 4_602_128_760_832,
    "currency": "USD", "financialCurrency": "USD",
}


# Captured before the autouse fixture stubs the module attribute, so the
# probe's own behaviour can still be tested directly.
REAL_PROBE_SYMBOL = M.probe_symbol
REAL_FETCH_BETA = M.fetch_beta


def frame(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, index=PERIODS).T


class FakeTicker:
    """Stands in for yf.Ticker."""

    def __init__(self, info=None, income=None, balance=None, cashflow=None,
                 raises=None):
        self._info = info if info is not None else {}
        self._income = income if income is not None else pd.DataFrame()
        self._balance = balance if balance is not None else pd.DataFrame()
        self._cashflow = cashflow if cashflow is not None else pd.DataFrame()
        self._raises = raises

    @property
    def info(self):
        if self._raises:
            raise self._raises
        return self._info

    @property
    def income_stmt(self):
        return self._income

    @property
    def balance_sheet(self):
        return self._balance

    @property
    def cashflow(self):
        return self._cashflow


def install(monkeypatch, ticker: FakeTicker):
    # **kwargs absorbs the session= that market_data now passes.
    monkeypatch.setattr(M.yf, "Ticker", lambda symbol, **kwargs: ticker)


@pytest.fixture(autouse=True)
def _clear_caches():
    M.clear_caches()
    yield
    M.clear_caches()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """
    Keep the suite offline and fast.

    An empty profile now makes market_data ask Yahoo whether the symbol is
    real before calling it missing. Left alone that would be a live request
    from a unit test, so the probe is stubbed to "absent" - Yahoo answering
    normally with no match - which is the case these tests were written
    against. Tests about blocking override it explicitly.

    Retry backoff is zeroed so the failure paths do not each sleep for
    several seconds.
    """
    monkeypatch.setattr(M, "probe_symbol",
                        lambda ticker: ("absent", "stubbed: no match"))
    monkeypatch.setattr(M, "_browser_session", lambda: None)
    monkeypatch.setattr(M, "_RETRY_BACKOFF", (0.0, 0.0))
    # Beta is now computed from five years of chart data when Yahoo does not
    # quote one. Stubbed off by default so no unit test reaches the network;
    # the beta tests below call the real implementation deliberately.
    monkeypatch.setattr(M, "fetch_beta", lambda ticker: None)


@pytest.fixture
def apple(monkeypatch):
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO,
        income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW),
    ))


# ---------------------------------------------------------------------------
# Field mapping - reconciles with the previously verified FMP figures
# ---------------------------------------------------------------------------

def test_base_year_reconciles_with_the_fmp_figures(apple):
    base, meta = M.fetch_with_meta("AAPL")

    assert base.revenue == pytest.approx(416_161, abs=1)
    # Current Debt + Long Term Debt, excluding capital leases.
    assert base.total_debt == pytest.approx(98_657, abs=1)
    # Cash + short-term investments + long-term Investments And Advances.
    assert base.cash == pytest.approx(132_420, abs=1)
    assert base.shares == pytest.approx(15_004.7, abs=0.1)
    assert base.current_price == pytest.approx(315.34)
    assert meta["company_name"] == "Apple Inc."
    assert meta["fiscal_year"] == "2025"


def test_cash_uses_long_term_investments_not_just_short_term(apple):
    """
    Yahoo's "Cash Cash Equivalents And Short Term Investments" would give
    54,697 for AAPL and understate the securities portfolio by ~$78bn.
    """
    base, _ = M.fetch_with_meta("AAPL")
    assert base.cash > 100_000


def test_debt_excludes_capital_leases(apple):
    fin = M.fetch_financials("AAPL")
    row = fin.balance[0]
    assert M.total_debt_of(row) == pytest.approx(98_657e6)


def test_income_rows_carry_canonical_field_names(apple):
    fin = M.fetch_financials("AAPL")
    row = fin.income[0]
    for name in ("revenue", "operatingIncome", "incomeBeforeTax",
                 "incomeTaxExpense", "weightedAverageShsOutDil",
                 "interestExpense", "fiscalYear"):
        assert name in row, f"downstream code reads {name}"
    assert row["operatingIncome"] == pytest.approx(133_050e6)


def test_profile_exposes_beta_sector_and_name(apple):
    fin = M.fetch_financials("AAPL")
    assert fin.profile["beta"] == pytest.approx(1.085)
    assert fin.sector == "Technology"
    assert fin.company_name == "Apple Inc."
    assert fin.exchange == "NasdaqGS"


def test_history_depth_is_limited_to_requested_years(apple):
    fin = M.fetch_financials("AAPL", years=2)
    assert fin.years_available == 2


# ---------------------------------------------------------------------------
# Industry variation: labels differ, and some lines simply do not exist
# ---------------------------------------------------------------------------

def test_operating_income_falls_back_to_ebit(monkeypatch):
    """Insurers publish no "Operating Income" line; Berkshire is the case."""
    income = dict(AAPL_INCOME)
    del income["Operating Income"]
    install(monkeypatch, FakeTicker(
        info={**AAPL_INFO, "symbol": "BRK-B", "longName": "Berkshire"},
        income=frame(income), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("BRK-B")
    assert fin.income[0]["operatingIncome"] == pytest.approx(133_050e6)


def test_operating_income_rebuilt_from_pretax_plus_interest(monkeypatch):
    income = dict(AAPL_INCOME)
    del income["Operating Income"]
    del income["EBIT"]
    income["Interest Expense"] = [5_069e6, 0.0, 0.0]
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(income), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("X")
    assert fin.income[0]["operatingIncome"] == pytest.approx(132_729e6 + 5_069e6)


def test_missing_current_assets_degrades_without_raising(monkeypatch):
    """Insurers present no classified balance sheet - this must not crash."""
    balance = dict(AAPL_BALANCE)
    del balance["Current Assets"]
    del balance["Current Liabilities"]
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME), balance=frame(balance),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("X")
    assert fin.balance[0]["totalCurrentAssets"] is None
    assert M.num(fin.balance[0], "totalCurrentAssets") == 0.0


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_unknown_ticker_is_not_found(monkeypatch):
    """
    Yahoo does not raise for an unknown symbol: it returns a one-key stub and
    empty frames, so emptiness has to be checked explicitly.
    """
    install(monkeypatch, FakeTicker(info={"trailingPegRatio": None}))
    with pytest.raises(TickerNotFoundError, match="No security found"):
        M.fetch_financials("ZZZZTESTNOPE")


def test_empty_info_is_not_found(monkeypatch):
    install(monkeypatch, FakeTicker(info={}))
    with pytest.raises(TickerNotFoundError):
        M.fetch_financials("NOPE")


def test_etf_is_reported_as_having_nothing_to_value(monkeypatch):
    """SPY quotes fine but publishes no income statement."""
    install(monkeypatch, FakeTicker(
        info={"symbol": "SPY", "quoteType": "ETF", "longName": "SPDR S&P 500",
              "currentPrice": 762.4}))
    with pytest.raises(TickerNotFoundError, match="etf"):
        M.fetch_financials("SPY")


def test_equity_without_statements_is_transient_not_not_found(monkeypatch):
    """
    A quoting company whose statements come back empty is reported as a
    temporary upstream problem, not as an unknown ticker.

    This used to raise TickerNotFoundError. That was wrong in the case that
    actually bites in production: when Yahoo throttles the statement
    endpoints, the profile still loads and the statements come back empty,
    and a 404 tells the user their real company does not exist. A genuinely
    uncovered company still surfaces - as a 502 that says "try again", whose
    message names the newly-listed possibility.
    """
    install(monkeypatch, FakeTicker(
        info={"symbol": "NEW", "quoteType": "EQUITY", "longName": "Newly Listed",
              "currentPrice": 10.0}))
    with pytest.raises(DataUnavailableError, match="no financial statements"):
        M.fetch_financials("NEW")
    # Specifically NOT a not-found.
    assert not issubclass(DataUnavailableError, TickerNotFoundError)


# ---------------------------------------------------------------------------
# Blocked is not the same as missing
#
# Yahoo returns an empty profile both for a symbol that does not exist and
# for a request it refuses to serve. Deployed on a shared cloud IP the second
# case is the common one, and reporting it as "no such ticker" tells users
# their real company is unknown. These tests pin the distinction.
# ---------------------------------------------------------------------------

def _empty_profile(monkeypatch):
    """Yahoo's empty-response stub for a refused or unknown symbol."""
    install(monkeypatch, FakeTicker(info={"trailingPegRatio": None}))


def test_blocked_request_is_not_reported_as_a_missing_ticker(monkeypatch):
    _empty_profile(monkeypatch)
    monkeypatch.setattr(M, "probe_symbol",
                        lambda ticker: ("blocked", "HTTP 429"))

    with pytest.raises(RateLimitedError) as excinfo:
        M.fetch_financials("AAPL")

    assert not isinstance(excinfo.value, TickerNotFoundError)
    # The message must not send the user off checking spelling that is fine.
    assert "not a problem with the ticker" in str(excinfo.value)
    # It reaches the screen: the status code is for the log, not the reader.
    assert "HTTP 429" not in str(excinfo.value)


def test_recognised_symbol_with_no_data_is_transient(monkeypatch):
    """Yahoo knows the symbol but withheld the profile - throttling."""
    _empty_profile(monkeypatch)
    monkeypatch.setattr(M, "probe_symbol",
                        lambda ticker: ("found", "1 match(es)"))

    with pytest.raises(DataUnavailableError) as excinfo:
        M.fetch_financials("AAPL")

    assert not isinstance(excinfo.value, TickerNotFoundError)
    assert "not an unknown ticker" in str(excinfo.value)
    assert "match(es)" not in str(excinfo.value)
    assert "upstream" not in str(excinfo.value)


def test_unreachable_yahoo_is_transient(monkeypatch):
    _empty_profile(monkeypatch)
    monkeypatch.setattr(M, "probe_symbol",
                        lambda ticker: ("unreachable", "ConnectionError: boom"))

    with pytest.raises(DataUnavailableError) as excinfo:
        M.fetch_financials("AAPL")
    assert not isinstance(excinfo.value, TickerNotFoundError)
    # A raw exception is for the log; the reader gets a sentence.
    assert "ConnectionError" not in str(excinfo.value)
    assert "boom" not in str(excinfo.value)


def test_genuinely_absent_symbol_is_still_a_404(monkeypatch):
    """The fix must not make every unknown ticker look like an outage."""
    _empty_profile(monkeypatch)
    monkeypatch.setattr(M, "probe_symbol",
                        lambda ticker: ("absent", "HTTP 200 with no matches"))

    with pytest.raises(TickerNotFoundError):
        M.fetch_financials("ZZZZ")


def test_blocked_quote_endpoint_still_produces_a_valuation(monkeypatch):
    """
    The case that actually happens on a cloud IP.

    Yahoo withholds the crumb-authenticated quote endpoint but serves the
    statements, so the profile is rebuilt from the crumb-free chart and
    search endpoints and the valuation completes - without a beta, which
    makes WACC fall back to its documented default.

    Search supplies the sector here, which is the ordinary case and the one
    that keeps this fallback worth having. When it does not, the company
    cannot be routed to a method at all - see the test below.
    """
    # Statements arrive; the profile does not.
    install(monkeypatch, FakeTicker(
        info={"trailingPegRatio": None},
        income=frame(AAPL_INCOME), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))
    monkeypatch.setattr(M, "probe_symbol", lambda ticker: ("found", "1 match(es)"))
    monkeypatch.setattr(M, "crumb_free_profile", lambda ticker: {
        "symbol": "AAPL", "companyName": "Apple Inc.", "sector": "Technology",
        "industry": None, "exchange": "NMS", "beta": None, "marketCap": None,
        "price": 315.34, "sharesOutstanding": None, "currency": "USD",
        "financialCurrency": None, "quoteType": "EQUITY",
    })

    base, meta = M.fetch_with_meta("AAPL")

    assert meta["company_name"] == "Apple Inc."
    assert base.current_price == pytest.approx(315.34)
    assert base.revenue == pytest.approx(416_161, abs=1)
    # The share count is unaffected: it comes from the income statement.
    assert base.shares == pytest.approx(15_004.7, abs=0.1)


def _sectorless_profile(monkeypatch, ticker="TRV", name="The Travelers Companies, Inc."):
    """The crumb-free fallback with search throttled: everything but a sector."""
    install(monkeypatch, FakeTicker(
        info={"trailingPegRatio": None},
        income=frame(AAPL_INCOME), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))
    monkeypatch.setattr(M, "probe_symbol", lambda t: ("found", "1 match(es)"))
    monkeypatch.setattr(M, "crumb_free_profile", lambda t: {
        "symbol": ticker, "companyName": name, "sector": None,
        "industry": None, "exchange": "NYSE", "beta": None, "marketCap": None,
        "price": 375.20, "sharesOutstanding": None, "currency": "USD",
        "financialCurrency": None, "quoteType": "EQUITY",
    })


def test_a_profile_with_no_sector_is_refused_rather_than_valued(monkeypatch):
    """
    The TRV bug: an insurer arrived with no sector during a throttle, was read
    as "not financial", and came back as a DCF at $1,205 against a $375 price.

    Without a sector nothing can be said about which model applies, and the
    unsafe reading is the one that used to be the default.
    """
    _sectorless_profile(monkeypatch)

    with pytest.raises(DataUnavailableError) as excinfo:
        M.fetch_financials("TRV")

    message = str(excinfo.value)
    # Transient and retryable, not a verdict on the company or the ticker.
    assert not isinstance(excinfo.value, TickerNotFoundError)
    assert "sector" in message
    assert "try again" in message.lower()


def test_a_missing_sector_is_not_cached(monkeypatch):
    """
    One throttled search must not cost the ticker a quarter of an hour.

    The financials cache holds entries for fifteen minutes, so caching this
    would turn a single unlucky request into a long run of identical wrong
    answers - which is how the TRV figure survived a retry.
    """
    M.clear_caches()
    _sectorless_profile(monkeypatch)

    with pytest.raises(DataUnavailableError):
        M.fetch_financials("TRV")

    # The very next request retries Yahoo, and succeeds once search answers.
    monkeypatch.setattr(M, "crumb_free_profile", lambda t: {
        "symbol": "TRV", "companyName": "The Travelers Companies, Inc.",
        "sector": "Financial Services", "industry": "Insurance - Property & Casualty",
        "exchange": "NYSE", "beta": None, "marketCap": None, "price": 375.20,
        "sharesOutstanding": None, "currency": "USD", "financialCurrency": None,
        "quoteType": "EQUITY",
    })

    fin = M.fetch_financials("TRV")
    assert fin.sector == "Financial Services"


def test_fallback_profile_is_not_used_for_an_unknown_ticker(monkeypatch):
    """A symbol Yahoo says does not exist must still 404, not be invented."""
    install(monkeypatch, FakeTicker(info={"trailingPegRatio": None}))
    monkeypatch.setattr(M, "probe_symbol", lambda ticker: ("absent", "no match"))

    def must_not_run(ticker):
        raise AssertionError("should not fabricate a profile for a real 404")

    monkeypatch.setattr(M, "crumb_free_profile", must_not_run)
    with pytest.raises(TickerNotFoundError):
        M.fetch_financials("ZZZZ")


def test_unrecognised_quote_type_is_not_mistaken_for_a_fund(monkeypatch):
    """
    Yahoo answers quoteType "NONE" in a degraded response.

    Treating anything that is not "EQUITY" as a fund produced
    "'HOLX' is a none, not a company" for an ordinary listed business during
    a throttle. Only recognised fund-like types justify a 404.
    """
    install(monkeypatch, FakeTicker(
        info={"symbol": "HOLX", "longName": "Hologic", "quoteType": "NONE",
              "currentPrice": 60.0}))

    with pytest.raises(DataUnavailableError) as excinfo:
        M.fetch_financials("HOLX")
    assert not isinstance(excinfo.value, TickerNotFoundError)


def test_a_real_etf_is_still_a_404(monkeypatch):
    """The allow-list must not let funds through as transient errors."""
    install(monkeypatch, FakeTicker(
        info={"symbol": "SPY", "quoteType": "ETF", "longName": "SPDR",
              "currentPrice": 762.4}))
    with pytest.raises(TickerNotFoundError, match="etf"):
        M.fetch_financials("SPY")


def _chart_session(monkeypatch, status, payload):
    class Response:
        status_code = status

        def json(self):
            if payload is None:
                raise ValueError("not JSON")
            return payload

    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    monkeypatch.setattr(M, "_browser_session", lambda: Session())


def test_symbol_probe_uses_an_exact_symbol_lookup(monkeypatch):
    """
    The probe asks the chart endpoint, which describes exactly the symbol
    requested. Search, used before, is fuzzy - ZZZZ returns ZZZZIX, a test
    fund - and not even deterministic: it left BAC out of one response and
    ranked it first in the next.
    """
    _chart_session(monkeypatch, 200, {"chart": {"result": [
        {"meta": {"symbol": "BAC", "instrumentType": "EQUITY"}}], "error": None}})
    assert REAL_PROBE_SYMBOL("BAC")[0] == "found"


def test_absent_requires_yahoos_own_not_found(monkeypatch):
    _chart_session(monkeypatch, 404, {"chart": {"result": None, "error": {
        "code": "Not Found", "description": "No data found, symbol may be delisted"}}})
    assert REAL_PROBE_SYMBOL("ZZZZ")[0] == "absent"


@pytest.mark.parametrize("status, payload", [
    (404, None),     # a bare 404 is not a statement about the symbol
    (429, {"chart": {"result": None, "error": {"code": "Too Many Requests"}}}),
    (500, None),
    (200, None),     # a consent page where JSON belongs
    (200, {"chart": {"result": [{"meta": {"symbol": "OTHER"}}]}}),
])
def test_anything_short_of_a_clear_answer_is_blocked_not_absent(monkeypatch, status, payload):
    _chart_session(monkeypatch, status, payload)
    assert REAL_PROBE_SYMBOL("BAC")[0] == "blocked"


def test_a_network_failure_is_unreachable_not_absent(monkeypatch):
    class Session:
        @staticmethod
        def get(*args, **kwargs):
            raise ConnectionError("boom")

    monkeypatch.setattr(M, "_browser_session", lambda: Session())
    assert REAL_PROBE_SYMBOL("BAC")[0] == "unreachable"


# ---------------------------------------------------------------------------
# Sector on the crumb-free path
# ---------------------------------------------------------------------------

def _profile_session(monkeypatch, search_quotes):
    chart = {"chart": {"result": [{"meta": {
        "symbol": "JPM", "longName": "JPMorgan Chase & Co.",
        "regularMarketPrice": 353.56, "currency": "USD", "exchangeName": "NYQ"}}]}}

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        @staticmethod
        def get(url, **kwargs):
            return Response({"quotes": search_quotes} if "search" in url else chart)

    monkeypatch.setattr(M, "_browser_session", lambda: Session())


def test_crumb_free_profile_carries_sector_so_financials_route_correctly(monkeypatch):
    """
    Sector decides DCF or dividend discount model. It used to be hard-coded
    to None on this path - the one a cloud deployment uses most - so a bank
    arriving through it would have been sent down the DCF.
    """
    _profile_session(monkeypatch, [
        {"symbol": "JPMX"},
        {"symbol": "JPM", "shortname": "JPMorgan Chase & Co.", "quoteType": "EQUITY",
         "sector": "Financial Services", "industry": "Banks—Diversified"},
    ])
    profile = M.crumb_free_profile("JPM")
    assert profile["sector"] == "Financial Services"
    assert profile["industry"] == "Banks - Diversified"
    assert "sector unavailable" not in profile["profileSource"]


def test_crumb_free_profile_says_so_when_sector_is_missing(monkeypatch):
    _profile_session(monkeypatch, [{"symbol": "JPMX", "sector": "Technology"}])
    profile = M.crumb_free_profile("JPM")
    assert profile["sector"] is None
    assert "sector unavailable" in profile["profileSource"]


# ---------------------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------------------

def _dividend_session(monkeypatch, status=200, events=None, calls=None):
    class Response:
        status_code = status

        def json(self):
            dividends = {str(t): {"amount": a, "date": t} for t, a in (events or [])}
            return {"chart": {"result": [{"meta": {}, "events": {"dividends": dividends}}]}}

    class Session:
        @staticmethod
        def get(url, **kwargs):
            if calls is not None:
                calls.append((url, kwargs.get("params")))
            return Response()

    monkeypatch.setattr(M, "_browser_session", lambda: Session())


def test_dividends_come_from_the_crumb_free_chart_oldest_first(monkeypatch):
    calls = []
    _dividend_session(monkeypatch, events=[(1783344600, 1.5), (1775482200, 1.5),
                                           (1767700000, 0.0)], calls=calls)
    assert M.fetch_dividends("JPM") == [(1775482200, 1.5), (1783344600, 1.5)]
    url, params = calls[0]
    assert "/chart/JPM" in url and params["events"] == "div"


def test_dividends_are_cached(monkeypatch):
    calls = []
    _dividend_session(monkeypatch, events=[(1783344600, 1.5)], calls=calls)
    M.fetch_dividends("JPM")
    M.fetch_dividends("JPM")
    assert len(calls) == 1


def test_no_dividend_events_is_a_positive_empty_answer(monkeypatch):
    _dividend_session(monkeypatch, events=None)
    assert M.fetch_dividends("BRK-B") == []


def test_a_throttled_dividend_request_raises_rather_than_reporting_no_dividend(monkeypatch):
    _dividend_session(monkeypatch, status=429)
    with pytest.raises(RateLimitedError):
        M.fetch_dividends("JPM")


def test_a_failed_dividend_request_raises_rather_than_reporting_no_dividend(monkeypatch):
    _dividend_session(monkeypatch, status=503)
    with pytest.raises(DataUnavailableError):
        M.fetch_dividends("JPM")


def test_statement_rows_carry_the_dividend_model_fields(monkeypatch):
    """Row labels as Yahoo actually names them for JPM."""
    income = dict(AAPL_INCOME, **{
        "Net Income Common Stockholders": [55_681e6, 56_868e6, 47_760e6],
        "Diluted EPS": [20.02, 19.75, 16.23]})
    balance = dict(AAPL_BALANCE, **{
        "Common Stock Equity": [342_393e6, 324_708e6, 300_474e6]})
    cashflow = dict(AAPL_CASHFLOW, **{
        "Cash Dividends Paid": [-16_625e6, -14_783e6, -13_463e6],
        "Repurchase Of Capital Stock": [-34_591e6, -28_680e6, -9_824e6]})
    install(monkeypatch, FakeTicker(info=AAPL_INFO, income=frame(income),
                                    balance=frame(balance), cashflow=frame(cashflow)))

    fin = M.fetch_financials("AAPL")
    assert fin.income[0]["netIncomeCommon"] == pytest.approx(55_681e6)
    assert fin.income[0]["dilutedEPS"] == pytest.approx(20.02)
    assert fin.balance[0]["commonStockEquity"] == pytest.approx(342_393e6)
    assert fin.cashflow[0]["dividendsPaid"] == pytest.approx(-16_625e6)
    assert fin.cashflow[0]["stockRepurchased"] == pytest.approx(-34_591e6)


# ---------------------------------------------------------------------------
# Peer classification
# ---------------------------------------------------------------------------

def _search_session(monkeypatch, payloads, calls=None):
    responses = iter(payloads)

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        @staticmethod
        def get(url, **kwargs):
            if calls is not None:
                calls.append(url)
            return Response(next(responses))

    monkeypatch.setattr(M, "_browser_session", lambda: Session())


def test_classification_retries_a_search_that_leaves_the_symbol_out(monkeypatch):
    """Search dropped BAC from one response and included it in the next - costing JPM a peer."""
    _search_session(monkeypatch, [
        {"quotes": [{"symbol": "BAC-PQ"}, {"symbol": "BLZE"}]},
        {"quotes": [{"symbol": "BAC", "quoteType": "EQUITY", "industry": "Banks—Diversified",
                     "shortname": "Bank of America Corporation"}]},
    ])
    result = M.fetch_classification("BAC")
    assert result["industry"] == "Banks - Diversified"
    assert result["name"] == "Bank of America Corporation"


def test_a_classification_miss_is_not_cached(monkeypatch):
    calls = []
    empty = {"quotes": []}
    _search_session(monkeypatch, [empty] * (2 * M._CLASSIFICATION_ATTEMPTS), calls=calls)
    assert M.fetch_classification("BAC")["industry"] is None
    assert M.fetch_classification("BAC")["industry"] is None
    assert len(calls) == 2 * M._CLASSIFICATION_ATTEMPTS, "a miss must be asked again, not remembered"


def test_empty_statements_are_retried_not_reported_as_missing(monkeypatch):
    """
    A throttled statement request returns an empty DataFrame, not an error.

    That is the commonest cloud failure, and because it raises nothing the
    exception retry never saw it: one empty frame became "this company files
    no statements". Emptiness must itself trigger a retry.
    """
    attempts = {"n": 0}

    class SometimesEmpty(FakeTicker):
        @property
        def income_stmt(self):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return pd.DataFrame()      # throttled: empty, no exception
            return frame(AAPL_INCOME)

    monkeypatch.setattr(M.yf, "Ticker", lambda symbol, **kwargs: SometimesEmpty(
        info=AAPL_INFO, balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("AAPL")

    assert fin.years_available > 0
    assert attempts["n"] == 3, "should have retried past the empty frames"


def test_statements_that_stay_empty_are_transient_not_missing(monkeypatch):
    """When retries do not rescue it, the error still must not read as a 404."""
    install(monkeypatch, FakeTicker(info=AAPL_INFO))  # quotes fine, no statements

    with pytest.raises(DataUnavailableError) as excinfo:
        M.fetch_financials("AAPL")
    assert not isinstance(excinfo.value, TickerNotFoundError)


def test_transient_failures_are_retried(monkeypatch):
    """One blip must not fail a valuation outright."""
    real = FakeTicker(info=AAPL_INFO, income=frame(AAPL_INCOME),
                      balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW))
    attempts = {"n": 0}

    class Flaky:
        @property
        def info(self):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("connection reset")
            return AAPL_INFO

        income_stmt = real.income_stmt
        balance_sheet = real.balance_sheet
        cashflow = real.cashflow

    monkeypatch.setattr(M.yf, "Ticker", lambda symbol, **kwargs: Flaky())

    fin = M.fetch_financials("AAPL")
    assert fin.company_name == "Apple Inc."
    assert attempts["n"] == 2, "should have retried once and then succeeded"


def test_rate_limiting_is_its_own_error(monkeypatch):
    # yfinance's own YFRateLimitError takes no message argument.
    install(monkeypatch, FakeTicker(raises=M.YFRateLimitError()))
    with pytest.raises(RateLimitedError, match="rate-limiting"):
        M.fetch_financials("AAPL")


def test_rate_limit_detected_from_message_text(monkeypatch):
    """Older yfinance versions surface throttling as a plain exception."""
    install(monkeypatch, FakeTicker(raises=RuntimeError("429 Too Many Requests")))
    with pytest.raises(RateLimitedError):
        M.fetch_financials("AAPL")


def test_other_upstream_failures_are_transient_not_not_found(monkeypatch):
    install(monkeypatch, FakeTicker(raises=RuntimeError("connection reset")))
    with pytest.raises(DataUnavailableError) as excinfo:
        M.fetch_financials("AAPL")
    assert not isinstance(excinfo.value, TickerNotFoundError)


def test_missing_price_is_reported_clearly(monkeypatch):
    info = {k: v for k, v in AAPL_INFO.items() if k != "currentPrice"}
    install(monkeypatch, FakeTicker(
        info=info, income=frame(AAPL_INCOME), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))
    with pytest.raises(DataUnavailableError, match="current price"):
        M.fetch_with_meta("AAPL")


def test_missing_revenue_is_reported_clearly(monkeypatch):
    income = {k: v for k, v in AAPL_INCOME.items() if k != "Total Revenue"}
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(income), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))
    with pytest.raises(DataUnavailableError, match="revenue"):
        M.fetch_with_meta("AAPL")


@pytest.mark.parametrize("bad", ["", "   ", "AAPL; DROP TABLE"])
def test_malformed_tickers_never_reach_the_network(monkeypatch, bad):
    def explode(symbol, **kwargs):
        raise AssertionError("should not have called Yahoo")

    monkeypatch.setattr(M.yf, "Ticker", explode)
    with pytest.raises(TickerNotFoundError):
        M.fetch_financials(bad)


def test_every_failure_is_a_market_data_error(monkeypatch):
    """The API layer catches MarketDataError; nothing may escape it."""
    for error in (M.YFRateLimitError(), RuntimeError("y")):
        M.clear_caches()
        install(monkeypatch, FakeTicker(raises=error))
        with pytest.raises(MarketDataError):
            M.fetch_financials("AAPL")


# ---------------------------------------------------------------------------
# Period alignment - Yahoo pads and truncates inconsistently
# ---------------------------------------------------------------------------

def test_periods_without_revenue_are_dropped(monkeypatch):
    """
    Yahoo pads its oldest column with NaN. AAPL's fifth income column carries
    no revenue, which made the CAGR undefined and silently fell the growth
    assumption back to its default.
    """
    income = {k: list(v) for k, v in AAPL_INCOME.items()}
    income["Total Revenue"] = [416_161e6, 391_035e6, float("nan")]
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(income), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("AAPL")
    assert fin.years_available == 2
    assert all(r["revenue"] is not None for r in fin.income)


def test_statements_stay_aligned_when_one_is_shorter(monkeypatch):
    """
    Berkshire returns five income columns but four balance-sheet columns.
    The derivation zips them positionally, so an offset would pair one year's
    cash flow with another year's revenue.
    """
    short_balance = pd.DataFrame(
        {k: v[:2] for k, v in AAPL_BALANCE.items()},
        index=PERIODS[:2],
    ).T
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME), balance=short_balance,
        cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("AAPL")
    assert len(fin.income) == len(fin.balance) == len(fin.cashflow)
    # The period that the balance sheet lacks contributes an empty row rather
    # than shifting the newer years up by one.
    assert fin.balance[0]["fiscalYear"] == fin.income[0]["fiscalYear"]
    assert fin.balance[1]["fiscalYear"] == fin.income[1]["fiscalYear"]
    assert fin.balance[2] == {}
    assert M.num(fin.balance[2], "shortTermDebt") == 0.0


# ---------------------------------------------------------------------------
# Currency mismatch
# ---------------------------------------------------------------------------

def test_foreign_listing_reporting_in_another_currency_is_refused(monkeypatch):
    """
    TSM reports in TWD and trades in USD. Mixing them valued it 809% above
    the market price - a confident, meaningless number.
    """
    install(monkeypatch, FakeTicker(
        info={**AAPL_INFO, "symbol": "TSM", "currency": "USD",
              "financialCurrency": "TWD"},
        income=frame(AAPL_INCOME), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))

    with pytest.raises(UnsupportedListingError) as excinfo:
        M.fetch_financials("TSM")
    message = str(excinfo.value)
    assert "TWD" in message and "USD" in message


def test_matching_currencies_are_valued_normally(monkeypatch, apple):
    fin = M.fetch_financials("AAPL")
    assert fin.profile["currency"] == fin.profile["financialCurrency"] == "USD"


def test_unknown_currency_does_not_block_a_valuation(monkeypatch):
    """A missing field must not refuse an otherwise valuable company."""
    info = {k: v for k, v in AAPL_INFO.items() if k != "currency"}
    install(monkeypatch, FakeTicker(
        info=info, income=frame(AAPL_INCOME), balance=frame(AAPL_BALANCE),
        cashflow=frame(AAPL_CASHFLOW)))
    assert M.fetch_financials("X").years_available == 3


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_repeat_fetches_hit_yahoo_once(monkeypatch):
    calls = {"n": 0}
    real = FakeTicker(info=AAPL_INFO, income=frame(AAPL_INCOME),
                      balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW))

    def counting(symbol, **kwargs):
        calls["n"] += 1
        return real

    monkeypatch.setattr(M.yf, "Ticker", counting)

    M.fetch_financials("AAPL")
    M.fetch_financials("AAPL")
    M.fetch_financials("AAPL")
    assert calls["n"] == 1, "the cache should have served the repeats"


def test_failures_are_cached_too(monkeypatch):
    """Repeatedly asking about a delisted ticker is how you earn a rate limit."""
    calls = {"n": 0}

    def counting(symbol, **kwargs):
        calls["n"] += 1
        return FakeTicker(info={"trailingPegRatio": None})

    monkeypatch.setattr(M.yf, "Ticker", counting)

    for _ in range(3):
        with pytest.raises(TickerNotFoundError):
            M.fetch_financials("ZZZZ")
    assert calls["n"] == 1


def test_clearing_the_cache_forces_a_refetch(monkeypatch):
    calls = {"n": 0}
    real = FakeTicker(info=AAPL_INFO, income=frame(AAPL_INCOME),
                      balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW))

    def counting(symbol, **kwargs):
        calls["n"] += 1
        return real

    monkeypatch.setattr(M.yf, "Ticker", counting)

    M.fetch_financials("AAPL")
    M.clear_caches()
    M.fetch_financials("AAPL")
    assert calls["n"] == 2


def test_cache_expires(monkeypatch):
    cache = M._TTLCache(ttl_seconds=0.0)
    cache.put("k", "v")
    assert cache.get("k") is None


def test_cache_evicts_when_full():
    cache = M._TTLCache(ttl_seconds=60, max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert len(cache._data) <= 2


# ---------------------------------------------------------------------------
# Beta, computed from price history
#
# Yahoo publishes beta only through the quote endpoint it will not serve a
# datacentre IP. Computing it from the chart endpoint keeps WACC both
# available and - the point of the exercise - identical between requests.
# ---------------------------------------------------------------------------

_MONTH = 30 * 24 * 3600


def _chart_payload(returns: list[float], start: float = 100.0) -> dict:
    """A chart response whose adjusted closes produce exactly *returns*."""
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
    stamps = [1_600_000_000 + i * _MONTH for i in range(len(prices))]
    return {"chart": {"result": [{
        "timestamp": stamps,
        "indicators": {"adjclose": [{"adjclose": prices}]},
    }]}}


def _install_chart(monkeypatch, by_symbol: dict[str, dict], status=200):
    class Response:
        def __init__(self, payload):
            self.status_code = status
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    class Session:
        @staticmethod
        def get(url, **kwargs):
            for symbol, payload in by_symbol.items():
                if f"/{symbol}" in url:
                    return Response(payload)
            raise AssertionError(f"unexpected chart request: {url}")

    monkeypatch.setattr(M, "_browser_session", lambda: Session())


def test_beta_matches_the_textbook_covariance_ratio(monkeypatch):
    """
    A series built to move exactly twice the market must return beta 2.

    Pins the arithmetic itself - covariance over variance on overlapping
    months - independently of any live data.
    """
    market = [0.03, -0.02, 0.05, -0.01, 0.04, -0.03, 0.02, 0.01,
              -0.04, 0.06, -0.02, 0.03] * 3          # 36 months
    stock = [2.0 * r for r in market]

    _install_chart(monkeypatch, {
        M.MARKET_INDEX: _chart_payload(market),
        "TWOX": _chart_payload(stock),
    })

    beta, source = REAL_FETCH_BETA("TWOX")
    assert beta == pytest.approx(2.0, abs=1e-9)
    assert "monthly returns" in source and M.MARKET_INDEX in source


def test_beta_is_identical_across_repeated_calls(monkeypatch):
    """
    The instability this fixes: the same ticker must not value differently
    between two requests because one of them got a beta and the other did not.
    """
    market = [0.03, -0.02, 0.05, -0.01, 0.04, -0.03] * 6
    stock = [0.6 * r + 0.001 for r in market]
    _install_chart(monkeypatch, {
        M.MARKET_INDEX: _chart_payload(market),
        "STEADY": _chart_payload(stock),
    })

    values = {REAL_FETCH_BETA("STEADY")[0] for _ in range(5)}
    assert len(values) == 1, "beta must be deterministic across requests"


def test_beta_needs_enough_history(monkeypatch):
    """A company with a year of trading has no meaningful five-year beta."""
    market = [0.03, -0.02, 0.05, -0.01, 0.04, -0.03]      # 6 months
    _install_chart(monkeypatch, {
        M.MARKET_INDEX: _chart_payload(market),
        "NEWCO": _chart_payload([0.01] * 6),
    })
    assert REAL_FETCH_BETA("NEWCO") is None


def test_beta_returns_none_when_the_chart_is_refused(monkeypatch):
    """A blocked chart endpoint must fall back, not raise."""
    _install_chart(monkeypatch, {
        M.MARKET_INDEX: _chart_payload([0.01] * 30),
        "BLOCKED": _chart_payload([0.01] * 30),
    }, status=429)
    assert REAL_FETCH_BETA("BLOCKED") is None


def test_computed_beta_reaches_the_profile_and_says_so(monkeypatch):
    """The valuation must be able to report which path produced its beta."""
    profile = {"beta": None}
    monkeypatch.setattr(M, "fetch_beta",
                        lambda ticker: (1.23, "computed from 59 monthly returns"))

    M._with_computed_beta(profile, "AAPL")

    assert profile["beta"] == pytest.approx(1.23)
    assert "computed" in profile["betaSource"]


def test_the_computed_beta_wins_over_a_quoted_one(monkeypatch):
    """
    Determinism beats deference to Yahoo's published figure.

    The quote endpoint answers only when the crumb happens to mint, so
    preferring it would leave beta depending on which endpoint Yahoo felt
    like serving - the very instability being removed. AAPL returned 1.085
    quoted and 1.088 computed on the same afternoon.
    """
    monkeypatch.setattr(M, "fetch_beta",
                        lambda ticker: (1.088, "computed from 59 monthly returns"))
    profile = {"beta": 1.085}

    M._with_computed_beta(profile, "AAPL")

    assert profile["beta"] == pytest.approx(1.088)
    assert "computed" in profile["betaSource"]


def test_a_quoted_beta_is_used_when_the_computation_fails(monkeypatch):
    """Yahoo's number still beats falling back to a market beta of 1.0."""
    monkeypatch.setattr(M, "fetch_beta", lambda ticker: None)
    profile = {"beta": 1.085}

    M._with_computed_beta(profile, "AAPL")

    assert profile["beta"] == pytest.approx(1.085)
    assert "quoted" in profile["betaSource"]


def test_beta_falls_back_to_the_default_when_uncomputable(monkeypatch):
    """With no beta at all, WACC still has to produce a number."""
    profile = {"beta": None}
    monkeypatch.setattr(M, "fetch_beta", lambda ticker: None)

    M._with_computed_beta(profile, "AAPL")

    assert profile["beta"] is None
    assert "unavailable" in profile["betaSource"]


def test_the_market_series_is_fetched_once_for_many_tickers(monkeypatch):
    """Thirty valuations must not mean thirty index downloads."""
    market = [0.03, -0.02, 0.05, -0.01, 0.04, -0.03] * 6
    fetches = {"n": 0}
    real_closes = M._monthly_closes

    def counting(symbol):
        if symbol == M.MARKET_INDEX:
            fetches["n"] += 1
        return real_closes(symbol)

    _install_chart(monkeypatch, {
        M.MARKET_INDEX: _chart_payload(market),
        "AAA": _chart_payload([0.5 * r for r in market]),
        "BBB": _chart_payload([1.5 * r for r in market]),
    })
    monkeypatch.setattr(M, "_monthly_closes", counting)

    REAL_FETCH_BETA("AAA")
    REAL_FETCH_BETA("BBB")

    assert fetches["n"] == 1, "the market series should be cached across tickers"


# ---------------------------------------------------------------------------
# Risk-free rate
# ---------------------------------------------------------------------------

class FakeRateTicker:
    def __init__(self, close=None, info=None, raises=None):
        self._close = close
        self._info = info or {}
        self._raises = raises

    def history(self, period="5d"):
        if self._raises:
            raise self._raises
        if self._close is None:
            return pd.DataFrame()
        return pd.DataFrame({"Close": [self._close]},
                            index=[pd.Timestamp("2026-09-09")])

    @property
    def info(self):
        return self._info


def test_risk_free_rate_converts_percent_to_decimal(monkeypatch):
    monkeypatch.setattr(M.yf, "Ticker", lambda s, **kwargs: FakeRateTicker(close=4.837))
    rate, source = M.fetch_risk_free_rate("year10")
    assert rate == pytest.approx(0.04837)
    assert "10-year" in source and "^TNX" in source


def test_risk_free_rate_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(M.yf, "Ticker", lambda s, **kwargs: FakeRateTicker(raises=RuntimeError()))
    assert M.fetch_risk_free_rate("year10") is None


def test_absurd_risk_free_rate_is_rejected(monkeypatch):
    """A bad quote must not silently become a 90% discount rate."""
    monkeypatch.setattr(M.yf, "Ticker", lambda s, **kwargs: FakeRateTicker(close=9000.0))
    assert M.fetch_risk_free_rate("year10") is None


def test_risk_free_rate_is_cached(monkeypatch):
    calls = {"n": 0}

    def counting(symbol, **kwargs):
        calls["n"] += 1
        return FakeRateTicker(close=4.5)

    monkeypatch.setattr(M.yf, "Ticker", counting)
    M.fetch_risk_free_rate("year10")
    M.fetch_risk_free_rate("year10")
    assert calls["n"] == 1


def test_unknown_tenor_returns_none():
    assert M.fetch_risk_free_rate("year7") is None


# ---------------------------------------------------------------------------
# No credentials
# ---------------------------------------------------------------------------

def test_no_api_key_required():
    assert M.requires_api_key() is False
    assert "Yahoo" in M.data_source_name()
