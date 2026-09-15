"""
Company search: the filtering, the caching, the endpoint, and its rate bucket.

Every fixture row below is copied from a live Yahoo search response (see the
probe in the commit that added this), so the rules are tested against the
shapes Yahoo actually sends - including the ones that motivated them, like
Bank of America's preferreds arriving typed as EQUITY on NYSE.
"""

import time

import pytest
from fastapi.testclient import TestClient

import api
import market_data as M
from api import app
from market_data import DataUnavailableError, RateLimitedError, TickerNotFoundError


def q(symbol, quote_type="EQUITY", exchange="NYQ", disp="NYSE", short=None, long=None,
      type_disp="Equity"):
    row = {"symbol": symbol, "quoteType": quote_type, "exchange": exchange,
           "exchDisp": disp, "typeDisp": type_disp, "isYahooFinance": True}
    if short is not None:
        row["shortname"] = short
    if long is not None:
        row["longname"] = long
    return row


# Recorded from Yahoo for "bank of am": the company, a future, and preferreds.
BANK_OF_AM = [
    q("BAC", short="Bank of America Corporation", long="Bank of America Corporation"),
    q("XBAC0=F", "FUTURE", "CME", "Chicago Mercantile Exchange",
      short="Micro Bank of America Corp Stoc", type_disp="Futures"),
    q("BAC-PQ", short="Bank of America Corporation Dep"),
    q("BAC-PL", short="Bank of America Corporation Non"),
    q("BAC-PB", short="Bank of America Corporation Dep"),
]

# Recorded for "apple": futures, a foreign CDR, a leveraged ETF, a German line.
APPLE = [
    q("AAPL", exchange="NMS", disp="NASDAQ", short="Apple Inc.", long="Apple Inc."),
    q("SAAPL=F", "FUTURE", "CME", short="Apple Inc Stock Futures,Sep-202"),
    q("APLE", short="Apple Hospitality REIT, Inc.", long="Apple Hospitality REIT, Inc."),
    q("AAPL.TO", exchange="TOR", disp="Toronto", short="APPLE CDR (CAD HEDGED)"),
    q("AAPX", "ETF", "BTS", "BATS Trading", short="T-Rex 2X Long Apple Daily Targe"),
    q("APC.DE", exchange="GER", disp="XETRA", short="Apple Inc.                    R"),
]


@pytest.fixture(autouse=True)
def _clean():
    M.clear_caches()
    api.reset_rate_limits()
    yield
    M.clear_caches()
    api.reset_rate_limits()


class FakeYahoo:
    """Stands in for _get_json, recording what was asked."""

    def __init__(self, quotes=None, error=None):
        self.quotes = quotes or []
        self.error = error
        self.calls = []

    def __call__(self, url, params, what, ticker):
        self.calls.append(params["q"])
        if self.error is not None:
            raise self.error
        return {"quotes": self.quotes, "count": len(self.quotes)}


@pytest.fixture
def yahoo(monkeypatch):
    fake = FakeYahoo()
    monkeypatch.setattr(M, "_get_json", fake)
    return fake


# ---------------------------------------------------------------------------
# What is offered
# ---------------------------------------------------------------------------

def test_preferred_shares_are_never_offered(yahoo):
    """Valuing BAC-PQ would pair BAC's statements with a preferred's price."""
    yahoo.quotes = BANK_OF_AM

    tickers = [r.ticker for r in M.search_companies("bank of am")]

    assert tickers == ["BAC"]


def test_class_shares_are_kept_though_they_look_like_preferreds(yahoo):
    yahoo.quotes = [q("BRK-B", long="Berkshire Hathaway Inc."),
                    q("BRK-A", long="Berkshire Hathaway Inc."),
                    q("XYZ-WT", long="Xyz Corp Warrant"),
                    q("XYZ-UN", long="Xyz Corp Unit")]

    assert [r.ticker for r in M.search_companies("berkshire")] == ["BRK-B", "BRK-A"]


@pytest.mark.parametrize("quote_type", ["ETF", "MUTUALFUND", "FUTURE", "OPTION",
                                        "CRYPTOCURRENCY", "INDEX", "CURRENCY"])
def test_listings_the_model_cannot_value_are_left_out(yahoo, quote_type):
    yahoo.quotes = [q("JUNK", quote_type, long="Something unvaluable"),
                    q("KO", long="The Coca-Cola Company")]

    assert [r.ticker for r in M.search_companies("x")] == ["KO"]


def test_foreign_and_otc_listings_are_left_out(yahoo):
    yahoo.quotes = APPLE + [q("TOYOF", exchange="PNK", disp="OTC Markets",
                              long="Toyota Motor Corp.")]

    tickers = [r.ticker for r in M.search_companies("apple")]

    assert tickers == ["AAPL", "APLE"]


def test_yahoos_relevance_order_is_kept(yahoo):
    """For the typo APPL, Yahoo ranks Apple first. Re-sorting would lose that."""
    yahoo.quotes = [q("AAPL", exchange="NMS", long="Apple Inc."),
                    q("APP", exchange="NMS", long="AppLovin Corporation"),
                    q("AMAT", exchange="NMS", long="Applied Materials, Inc.")]

    assert [r.ticker for r in M.search_companies("APPL")] == ["AAPL", "APP", "AMAT"]


def test_at_most_eight_results(yahoo):
    yahoo.quotes = [q(f"CO{letter}", long=f"Company {letter}") for letter in "ABCDEFGHIJKL"]

    assert len(M.search_companies("company")) == M.SEARCH_RESULT_LIMIT == 8


def test_more_results_are_fetched_than_shown(yahoo):
    """Filtering removes most of a typical response, so asking for eight would
    leave a near-empty dropdown."""
    captured = {}

    def fake(url, params, what, ticker):
        captured.update(params)
        return {"quotes": []}

    M._get_json = fake
    try:
        M.search_companies("apple")
    finally:
        M._get_json = yahoo
    assert captured["quotesCount"] > M.SEARCH_RESULT_LIMIT


def test_duplicates_are_removed(yahoo):
    yahoo.quotes = [q("KO", long="The Coca-Cola Company"), q("KO", long="The Coca-Cola Company")]

    assert [r.ticker for r in M.search_companies("coca")] == ["KO"]


def test_the_full_name_is_preferred_and_padding_removed(yahoo):
    yahoo.quotes = [q("CCEP", exchange="NMS", short="Coca-Cola Europacific Partners ",
                      long="Coca-Cola Europacific Partners PLC"),
                    q("PAD", short="Padded   Name   Inc.")]

    names = [r.name for r in M.search_companies("coca")]

    assert names == ["Coca-Cola Europacific Partners PLC", "Padded Name Inc."]


def test_each_result_carries_name_ticker_and_exchange(yahoo):
    yahoo.quotes = BANK_OF_AM

    result = M.search_companies("bank of am")[0]

    assert (result.ticker, result.name, result.exchange, result.type) == \
        ("BAC", "Bank of America Corporation", "NYSE", "Equity")


def test_a_dotted_class_share_is_searched_as_yahoo_spells_it(yahoo):
    """BRK.B found only options and an ETF on Yahoo; BRK-B is the listing."""
    yahoo.quotes = [q("BRK-B", long="Berkshire Hathaway Inc.")]

    results = M.search_companies("BRK.B")

    assert yahoo.calls == ["BRK-B"]
    assert [r.ticker for r in results] == ["BRK-B"]


def test_no_matches_is_an_empty_list_not_an_error(yahoo):
    assert M.search_companies("zzzzqqq") == []


def test_a_blank_query_asks_yahoo_nothing(yahoo):
    assert M.search_companies("   ") == []
    assert yahoo.calls == []


# ---------------------------------------------------------------------------
# Caching: the backend's half of not spamming Yahoo
# ---------------------------------------------------------------------------

def test_a_repeated_query_is_served_from_cache(yahoo):
    yahoo.quotes = BANK_OF_AM

    M.search_companies("bank of am")
    M.search_companies("Bank  of AM")     # case and spacing do not make it new

    assert len(yahoo.calls) == 1


def test_an_empty_answer_is_cached_too(yahoo):
    """'nothing is called zzzzqqq' will not change within the hour."""
    M.search_companies("zzzzqqq")
    M.search_companies("zzzzqqq")

    assert len(yahoo.calls) == 1


def test_a_failed_search_is_not_cached(yahoo):
    """A throttle is not an answer. Remembering it would hide results for an hour."""
    yahoo.error = RateLimitedError("throttled")
    with pytest.raises(RateLimitedError):
        M.search_companies("bank of am")

    yahoo.error, yahoo.quotes = None, BANK_OF_AM
    assert [r.ticker for r in M.search_companies("bank of am")] == ["BAC"]
    assert len(yahoo.calls) == 2


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


def test_the_endpoint_returns_name_ticker_and_exchange(client, yahoo):
    yahoo.quotes = BANK_OF_AM

    r = client.get("/search", params={"q": "bank of am"})

    assert r.status_code == 200
    assert r.json() == {
        "query": "bank of am",
        "results": [{"ticker": "BAC", "name": "Bank of America Corporation",
                     "exchange": "NYSE", "type": "Equity"}],
    }


def test_no_matches_is_a_200_with_no_results(client, yahoo):
    r = client.get("/search", params={"q": "zzzzqqq"})

    assert r.status_code == 200
    assert r.json()["results"] == []


def test_yahoo_throttling_is_a_429_not_an_empty_list(client, yahoo):
    """'No companies match' and 'could not ask' must never look the same."""
    yahoo.error = RateLimitedError("throttled")

    r = client.get("/search", params={"q": "apple"})

    assert r.status_code == 429
    assert r.json()["code"] == "upstream_rate_limited"


@pytest.mark.parametrize("error", [DataUnavailableError("down"),
                                   TickerNotFoundError("404 from the search endpoint")])
def test_an_unavailable_search_is_a_502_that_points_to_direct_entry(client, yahoo, error):
    """A 404 from Yahoo's search is the search misbehaving, not a missing company."""
    yahoo.error = error

    r = client.get("/search", params={"q": "apple"})

    assert r.status_code == 502
    assert r.json()["code"] == "upstream_error"
    assert "ticker directly" in r.json()["message"]


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "x" * 51}])
def test_a_missing_or_oversized_query_is_rejected(client, yahoo, params):
    r = client.get("/search", params=params)

    assert r.status_code == 422
    assert yahoo.calls == []


# ---------------------------------------------------------------------------
# Search has its own rate bucket
# ---------------------------------------------------------------------------

def test_searching_does_not_spend_the_valuation_budget(client, yahoo, monkeypatch):
    """Looking up names must never lock someone out of valuing what they found."""
    for i in range(api.RATE_LIMIT_REQUESTS + 5):
        assert client.get("/search", params={"q": f"co{i}"}).status_code == 200

    r = client.get("/health")                      # exempt: proves nothing
    assert r.status_code == 200
    r = client.get("/assumptions")                 # counted in the valuation bucket
    assert r.status_code != 429


def test_valuations_do_not_spend_the_search_budget(client, yahoo):
    now = time.monotonic()
    for _ in range(api.RATE_LIMIT_REQUESTS):
        api._rate_limited("testclient", now)

    assert client.get("/assumptions").status_code == 429
    assert client.get("/search", params={"q": "apple"}).status_code == 200


def test_search_has_a_limit_of_its_own(client, yahoo):
    for i in range(api.SEARCH_RATE_LIMIT_REQUESTS):
        assert client.get("/search", params={"q": f"co{i}"}).status_code == 200

    r = client.get("/search", params={"q": "one too many"})

    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    assert str(api.SEARCH_RATE_LIMIT_REQUESTS) in r.json()["message"]
