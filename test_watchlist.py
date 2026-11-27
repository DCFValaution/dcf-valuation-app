"""The watchlist: one shared computation per ticker per trading day.

WHY THIS EXISTS
---------------
A watchlist of ten companies must not be ten valuations. Each one reads
several years of statements, five years of monthly closes, the Treasury
curve and an industry table; ten users watching Nvidia should cost one
computation, not ten.

The design these tests pin is a SPLIT. The intrinsic value is expensive and
barely moves within a day, so it is cached until the next close exists and
shared across every caller. The price is cheap and is the one figure a
reader checks against their broker, so it is never cached beyond the
market's own clock. The upside is recomputed from the pair.

What that buys, and what it costs, are both things a test should state:
it buys one computation per ticker per day; it costs a cached intrinsic
value drifting fractionally from a fresh one as market capitalisation
moves the discount rate's weighting. The second is immaterial at the
precision anyone reads these figures, and these tests do not pretend
otherwise - they pin the sharing, not an equality that was never claimed.

Run with:  python -m pytest test_watchlist.py -v
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

import price_source
import watchlist as W
from price_source import LatestClose


# ---------------------------------------------------------------------------
# Stand-ins
# ---------------------------------------------------------------------------

@dataclass
class FakeSuitability:
    suitable: bool
    reasons: list
    codes: list


@dataclass
class FakeResult:
    intrinsic_value_per_share: float


@dataclass
class FakeReport:
    ticker: str
    company_name: str
    method: str
    result: object
    suitability: FakeSuitability

    @property
    def suitable(self) -> bool:
        return self.suitability.suitable


def valued(ticker="NVDA", value=219.33, name="NVIDIA Corporation"):
    return FakeReport(ticker=ticker, company_name=name, method="dcf",
                      result=FakeResult(value),
                      suitability=FakeSuitability(True, [], []))


def refused(ticker="TSLA", name="Tesla, Inc."):
    return FakeReport(
        ticker=ticker, company_name=name, method="dcf", result=None,
        suitability=FakeSuitability(
            False,
            ["Terminal value would be 3078% of the whole company."],
            ["terminal_value_dominates"]))


class CountingValuer:
    """A valuer that records every ticker it was asked for."""

    def __init__(self, reports=None, raises=None):
        self.reports = reports or {}
        self.raises = raises or {}
        self.calls: list[str] = []

    def __call__(self, ticker, overrides=None):
        self.calls.append(ticker)
        if ticker in self.raises:
            raise self.raises[ticker]
        return self.reports.get(ticker) or valued(ticker)


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    W.clear_caches()
    prices = {"NVDA": 125.53, "KO": 96.99, "TSLA": 431.20, "AMZN": 246.82}

    # The REAL type, not a stand-in. A hand-rolled fake defined as_of_iso
    # as a METHOD where the real class has a PROPERTY, so the fake and the
    # code agreed with each other and both were wrong: every test here
    # passed, and the endpoint returned 500 the first time it met a real
    # price. A fake is only evidence about the real thing while it is
    # built from it.
    def close_for(symbol, now=None):
        return LatestClose(symbol=symbol, price=prices.get(symbol, 100.0),
                           as_of=date(2026, 11, 20))

    monkeypatch.setattr(price_source, "latest_close", close_for)
    monkeypatch.setattr(price_source, "cached_close", lambda symbol: None)
    yield
    W.clear_caches()


# ---------------------------------------------------------------------------
# The list itself
# ---------------------------------------------------------------------------

class TestTheList:
    def test_it_keeps_the_readers_order(self):
        """A watchlist is a list. Returning it sorted would make the app
        sort it back, and the order is the one thing in it the reader
        chose."""
        assert W.normalise(["ko", "NVDA", "amzn"]) == ["KO", "NVDA", "AMZN"]

    def test_duplicates_go_quietly(self):
        """The same company twice is a mistake worth fixing, not one worth
        a 400."""
        assert W.normalise(["NVDA", "nvda", " NVDA "]) == ["NVDA"]

    def test_blanks_are_dropped_rather_than_valued(self):
        assert W.normalise(["NVDA", "", "   ", "KO"]) == ["NVDA", "KO"]

    def test_a_very_long_list_is_capped_not_refused(self):
        """The cap stops one request asking this instance for a thousand
        valuations. It is well above any list a person keeps by hand, so
        truncating is kinder than refusing."""
        asked = [f"T{i}" for i in range(W.MAX_TICKERS + 20)]
        assert len(W.normalise(asked)) == W.MAX_TICKERS


# ---------------------------------------------------------------------------
# The sharing, which is the point
# ---------------------------------------------------------------------------

class TestTheSharing:
    def test_a_second_reader_costs_no_computation(self):
        """The whole reason this module exists."""
        valuer = CountingValuer()
        W.rows_for(["NVDA", "KO"], valuer=valuer)
        W.rows_for(["NVDA", "KO"], valuer=valuer)

        assert valuer.calls == ["NVDA", "KO"], valuer.calls

    def test_the_second_read_says_it_was_shared(self):
        """So the app can tell "nothing has changed" from "nothing has been
        computed yet"."""
        valuer = CountingValuer()
        first = W.rows_for(["NVDA"], valuer=valuer)[0]
        second = W.rows_for(["NVDA"], valuer=valuer)[0]

        assert first.cached is False
        assert second.cached is True
        assert second.intrinsic_value_per_share == first.intrinsic_value_per_share

    def test_overlapping_watchlists_share_the_overlap(self):
        """Two readers, one company in common: it is valued once."""
        valuer = CountingValuer()
        W.rows_for(["NVDA", "KO"], valuer=valuer)
        W.rows_for(["KO", "AMZN"], valuer=valuer)

        assert valuer.calls == ["NVDA", "KO", "AMZN"]

    def test_the_next_close_ends_the_sharing(self):
        """Held until the next close EXISTS, not for a fixed duration.

        Caching "for a day" either serves yesterday's figure after this
        evening's close or recomputes an unchanged one all night. This is
        the same boundary price_source already uses for closes.
        """
        valuer = CountingValuer()
        monday = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
        W.rows_for(["NVDA"], valuer=valuer, now=monday)
        W.rows_for(["NVDA"], valuer=valuer,
                   now=monday + timedelta(hours=2))
        assert valuer.calls == ["NVDA"], "still the same trading day"

        after_the_close = price_source.next_close_after(monday) + timedelta(minutes=1)
        W.rows_for(["NVDA"], valuer=valuer, now=after_the_close)
        assert valuer.calls == ["NVDA", "NVDA"], "a new close exists"


# ---------------------------------------------------------------------------
# The split between the two halves
# ---------------------------------------------------------------------------

class TestTheSplit:
    def test_the_price_is_fetched_every_time_the_value_is_not(self, monkeypatch):
        """The expensive half is shared; the cheap half is current.

        A reader checks the price against their broker and the intrinsic
        value against nothing, so staleness costs very different amounts on
        the two halves of the same row.
        """
        fetches: list[str] = []

        def counting_price(symbol, now=None):
            fetches.append(symbol)
            return LatestClose(symbol=symbol, price=125.53,
                               as_of=date(2026, 11, 20))

        monkeypatch.setattr(price_source, "latest_close", counting_price)

        valuer = CountingValuer()
        W.rows_for(["NVDA"], valuer=valuer)
        W.rows_for(["NVDA"], valuer=valuer)

        assert valuer.calls == ["NVDA"], "valued once"
        assert fetches == ["NVDA", "NVDA"], "priced twice"

    def test_the_upside_is_rebuilt_from_this_rows_own_two_figures(self):
        """Not carried over from the valuation, which priced itself at
        whatever moment it ran. A row whose upside disagreed with the value
        and price printed beside it would be indefensible."""
        valuer = CountingValuer({"NVDA": valued("NVDA", 219.33)})
        row = W.rows_for(["NVDA"], valuer=valuer)[0]

        assert row.intrinsic_value_per_share == pytest.approx(219.33)
        assert row.current_price == pytest.approx(125.53)
        assert row.upside_downside == pytest.approx(219.33 / 125.53 - 1)

    def test_a_row_carries_both_dates(self):
        """A figure without its date is a claim about now that nobody
        checked - and these two figures have different dates by design."""
        row = W.rows_for(["NVDA"], valuer=CountingValuer())[0]
        assert row.valued_for
        assert row.price_as_of == "2026-11-20"

    def test_a_missing_price_costs_the_upside_and_nothing_else(self, monkeypatch):
        monkeypatch.setattr(price_source, "latest_close",
                            lambda symbol, now=None: None)
        row = W.rows_for(["NVDA"], valuer=CountingValuer())[0]

        assert row.intrinsic_value_per_share == pytest.approx(219.33)
        assert row.current_price is None
        assert row.upside_downside is None
        assert row.error is None, "a missing price is not a failed row"

    def test_a_price_source_that_raises_does_not_lose_the_row(self, monkeypatch):
        def exploding(symbol, now=None):
            raise RuntimeError("Yahoo said no")

        monkeypatch.setattr(price_source, "latest_close", exploding)
        monkeypatch.setattr(price_source, "cached_close", lambda symbol: None)

        row = W.rows_for(["NVDA"], valuer=CountingValuer())[0]
        assert row.intrinsic_value_per_share == pytest.approx(219.33)
        assert row.current_price is None


# ---------------------------------------------------------------------------
# Refused and failed, which are different things
# ---------------------------------------------------------------------------

class TestRefusedAndFailed:
    def test_a_refused_company_still_gets_a_row(self):
        """Hiding Tesla would make the watchlist disagree with the
        valuation screen, which says out loud that no figure is shown and
        why. The row carries the same answer, shorter."""
        valuer = CountingValuer({"TSLA": refused()})
        row = W.rows_for(["TSLA"], valuer=valuer)[0]

        assert row.refused is True
        assert row.intrinsic_value_per_share is None
        assert row.refusal_code == "terminal_value_dominates"
        assert "3078%" in row.headline
        assert row.error is None, "a refusal is an answer, not a failure"

    def test_a_refusal_is_shared_like_any_other_answer(self):
        """Recomputing a refusal every time would be the expensive half of
        the work for a row that cannot change until the filings do."""
        valuer = CountingValuer({"TSLA": refused()})
        W.rows_for(["TSLA"], valuer=valuer)
        W.rows_for(["TSLA"], valuer=valuer)
        assert valuer.calls == ["TSLA"]

    def test_a_refused_row_still_carries_its_price(self):
        """The company has no intrinsic value and still has a share price.
        Dropping it would make the row less informative than the ticker."""
        valuer = CountingValuer({"TSLA": refused()})
        row = W.rows_for(["TSLA"], valuer=valuer)[0]
        assert row.current_price == pytest.approx(431.20)
        assert row.upside_downside is None, "no value, so no upside"

    def test_a_failure_is_a_row_too_and_says_so(self):
        valuer = CountingValuer(raises={"ZZZZ": ValueError("no such ticker")})
        row = W.rows_for(["ZZZZ"], valuer=valuer)[0]

        assert row.error == "no such ticker"
        assert row.refused is False, "a failure is not a refusal"
        assert row.intrinsic_value_per_share is None

    def test_one_bad_ticker_does_not_lose_the_others(self):
        """The most important property of the whole endpoint: a watchlist
        is a list of independent companies, and one delisted holding must
        not blank the other nine."""
        valuer = CountingValuer(raises={"ZZZZ": ValueError("no such ticker")})
        rows = W.rows_for(["NVDA", "ZZZZ", "KO"], valuer=valuer)

        assert [r.ticker for r in rows] == ["NVDA", "ZZZZ", "KO"]
        assert rows[0].intrinsic_value_per_share is not None
        assert rows[1].error
        assert rows[2].intrinsic_value_per_share is not None

    def test_a_failure_is_not_retried_on_every_open(self):
        """Asking repeatedly about a delisted ticker is exactly the traffic
        that earns a rate limit."""
        valuer = CountingValuer(raises={"ZZZZ": ValueError("no such ticker")})
        W.rows_for(["ZZZZ"], valuer=valuer)
        W.rows_for(["ZZZZ"], valuer=valuer)
        assert valuer.calls == ["ZZZZ"]

    def test_but_a_failure_is_retried_sooner_than_a_success(self):
        """A delisting is permanent and a Yahoo wobble is not, and the two
        are indistinguishable from here - so the failure is held for
        minutes where an answer is held for the trading day."""
        assert W._FAILURE_SECONDS < 10 * 60


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

class TestTheEndpoint:
    """What the app actually calls."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        import api

        reports = {"NVDA": valued("NVDA", 219.33), "TSLA": refused()}

        def fake_value_company(ticker, overrides=None):
            if ticker == "ZZZZ":
                raise ValueError("no such ticker")
            return reports.get(ticker) or valued(ticker)

        monkeypatch.setattr(api, "value_company", fake_value_company)
        return TestClient(api.app)

    def test_it_returns_a_row_per_ticker_in_order(self, client):
        body = client.get("/watchlist?tickers=NVDA,KO").json()
        assert [r["ticker"] for r in body["rows"]] == ["NVDA", "KO"]

    def test_spacing_and_case_are_forgiven(self, client):
        body = client.get("/watchlist?tickers= nvda , ko ").json()
        assert [r["ticker"] for r in body["rows"]] == ["NVDA", "KO"]

    def test_an_empty_list_is_a_400_not_an_empty_answer(self, client):
        got = client.get("/watchlist?tickers=  ,  ,")
        assert got.status_code == 400
        assert got.json()["code"] == "no_tickers"

    def test_the_cap_is_published_rather_than_guessed_at(self, client):
        """The app has to know where the list stops, and reading it from
        the response means the two can never disagree."""
        body = client.get("/watchlist?tickers=NVDA").json()
        assert body["max_tickers"] == W.MAX_TICKERS

    def test_a_refused_company_is_a_row_with_its_reason(self, client):
        body = client.get("/watchlist?tickers=TSLA").json()
        row = body["rows"][0]
        assert row["refused"] is True
        assert row["intrinsic_value_per_share"] is None
        assert row["refusal_code"] == "terminal_value_dominates"
        assert row["headline"]

    def test_one_bad_ticker_still_returns_200_with_the_rest(self, client):
        """A watchlist is a list of independent companies. Failing the
        whole request over one delisted holding would be the app's problem
        to work around, every time."""
        body = client.get("/watchlist?tickers=NVDA,ZZZZ,KO")
        assert body.status_code == 200
        rows = {r["ticker"]: r for r in body.json()["rows"]}
        assert rows["ZZZZ"]["error"]
        assert rows["NVDA"]["intrinsic_value_per_share"] is not None
        assert rows["KO"]["intrinsic_value_per_share"] is not None

    def test_no_override_reaches_this_endpoint(self, client):
        """It serves the derived model for every company. A saved
        valuation with its own assumptions is a different request, and
        letting overrides in here would make the shared cache wrong for
        everyone else."""
        got = client.get("/watchlist?tickers=NVDA&revenue_growth=0.5")
        assert got.status_code == 200
        assert got.json()["rows"][0]["intrinsic_value_per_share"] == pytest.approx(219.33)
