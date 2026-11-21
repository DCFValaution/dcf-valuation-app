"""The one place a share price comes from.

Prices are the last input still bought from a commercial aggregator, and the
one with no free licensed alternative. The app launches on Yahoo and is
meant to move to Twelve Data, so the thing worth testing hardest is not the
Yahoo parsing: it is that nothing outside this module reaches a price
endpoint, and that the cache boundary is the market's clock rather than a
duration.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import price_source as P


@pytest.fixture(autouse=True)
def _clean():
    P.clear_caches()
    P.reset_source()
    yield
    P.clear_caches()
    P.reset_source()


def utc(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# --- Choosing an implementation -------------------------------------------

class TestTheConfiguredSource:
    def test_yahoo_is_the_default(self, monkeypatch):
        monkeypatch.delenv(P.PRICE_SOURCE_VAR, raising=False)
        assert isinstance(P.source(), P.YahooPriceSource)

    def test_the_setting_chooses_it(self, monkeypatch):
        class Other:
            name = "other"

        monkeypatch.setitem(P._SOURCES, "other", Other)
        monkeypatch.setenv(P.PRICE_SOURCE_VAR, "other")
        P.reset_source()
        assert isinstance(P.source(), Other)

    def test_the_setting_is_read_without_regard_to_case_or_space(
            self, monkeypatch):
        """Asserted against a NON-default source on purpose.

        The first version of this used "  YAHOO ", which normalises to the
        default - so it passed whether the normalising happened or the
        lookup missed and fell back. Mutation testing found that; the
        assertion now distinguishes the two.
        """
        class Other:
            name = "other"

        monkeypatch.setitem(P._SOURCES, "other", Other)
        monkeypatch.setenv(P.PRICE_SOURCE_VAR, "  OTHER ")
        P.reset_source()
        assert isinstance(P.source(), Other), (
            "the setting was not normalised, so the lookup missed and fell "
            "back to Yahoo")

    def test_an_unknown_setting_falls_back_rather_than_crashing(
            self, monkeypatch):
        """A typo in an environment variable must not take the app down."""
        monkeypatch.setenv(P.PRICE_SOURCE_VAR, "twelvedata-typo")
        P.reset_source()
        assert isinstance(P.source(), P.YahooPriceSource)

    def test_the_source_is_built_once(self, monkeypatch):
        built = []

        class Counting:
            name = "counting"

            def __init__(self):
                built.append(1)

        monkeypatch.setitem(P._SOURCES, "counting", Counting)
        monkeypatch.setenv(P.PRICE_SOURCE_VAR, "counting")
        P.reset_source()
        P.source()
        P.source()
        assert len(built) == 1


# --- The cache boundary ---------------------------------------------------

class TestWhenACloseStopsBeingCurrent:
    """A close is good until the next one exists, so the boundary is the
    market's clock. Caching "for an hour" either serves a stale price after
    the bell or re-fetches an unchanged one all evening."""

    def test_the_boundary_is_after_the_bell_not_on_it(self):
        """Settling time: the official close is not served at 16:00:00."""
        morning = utc(2026, 11, 20, 14)        # 09:00 New York
        boundary = P.next_close_after(morning)
        assert boundary.astimezone(P.NYSE).date() == date(2026, 11, 20)
        assert boundary.astimezone(P.NYSE).hour == 16
        assert boundary.astimezone(P.NYSE).minute == 30

    def test_after_the_close_it_rolls_to_the_next_day(self):
        evening = utc(2026, 11, 20, 23)        # 18:00 New York, Friday
        boundary = P.next_close_after(evening)
        # Friday evening, so the next close is Monday's.
        assert boundary.astimezone(P.NYSE).date() == date(2026, 11, 23)

    def test_a_weekend_is_skipped(self):
        saturday = utc(2026, 11, 21, 18)
        monday = date(2026, 11, 23)
        assert P.next_close_after(saturday).astimezone(P.NYSE).date() == monday
        sunday = utc(2026, 11, 22, 18)
        assert P.next_close_after(sunday).astimezone(P.NYSE).date() == monday

    def test_it_is_always_in_the_future(self):
        for hour in range(0, 24):
            moment = utc(2026, 11, 20, hour)
            assert P.next_close_after(moment) > moment, hour


class TestTheCloseCache:
    def _close(self, price=100.0):
        return P.LatestClose(symbol="AAPL", price=price,
                             as_of=date(2026, 11, 20))

    def test_a_price_is_served_again_before_the_next_close(self):
        morning = utc(2026, 11, 20, 14)
        P._close_cache.put("AAPL", self._close(), now=morning)
        later = utc(2026, 11, 20, 20)           # 15:00 New York, still open
        assert P._close_cache.get("AAPL", now=later) is not None

    def test_it_is_dropped_once_the_next_close_exists(self):
        morning = utc(2026, 11, 20, 14)
        P._close_cache.put("AAPL", self._close(), now=morning)
        after = utc(2026, 11, 20, 22)           # 17:00 New York
        assert P._close_cache.get("AAPL", now=after) is None

    def test_hits_and_misses_are_counted_for_the_diagnostics(self):
        morning = utc(2026, 11, 20, 14)
        P._close_cache.get("AAPL", now=morning)          # miss
        P._close_cache.put("AAPL", self._close(), now=morning)
        P._close_cache.get("AAPL", now=morning)          # hit
        stats = P.cache_statistics()["latest_close"]
        assert stats["hits"] == 1 and stats["misses"] == 1

    def test_it_does_not_grow_without_bound(self):
        cache = P._ClosePriceCache(max_entries=4)
        now = utc(2026, 11, 20, 14)
        for i in range(10):
            cache.put(f"T{i}", self._close(), now=now)
        assert len(cache._data) <= 4


class TestTheDailyCache:
    def test_it_serves_within_the_day(self):
        P._monthly_cache.put("AAPL", [(1, 2.0)])
        assert P._monthly_cache.get("AAPL") == [(1, 2.0)]

    def test_it_expires(self, monkeypatch):
        cache = P._DailyCache(ttl_seconds=0.0)
        cache.put("AAPL", [(1, 2.0)])
        assert cache.get("AAPL") is None

    def test_a_caller_cannot_mutate_what_is_cached(self):
        """monthly_closes hands out a list, and the beta regression is
        entitled to do what it likes with it."""
        P._monthly_cache.put("AAPL", [(1, 2.0)])
        got = P.monthly_closes("AAPL")
        got.append((2, 3.0))
        assert len(P.monthly_closes("AAPL")) == 1


# --- The front door -------------------------------------------------------

class Fake:
    name = "fake"

    def __init__(self, close=None, monthly=None, dividends=None, raises=None):
        self._close = close
        self._monthly = monthly or []
        self._dividends = dividends or []
        self._raises = raises
        self.calls = 0

    def latest_close(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._close

    def monthly_closes(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return list(self._monthly)

    def dividend_events(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return list(self._dividends)

    def daily_close(self, symbol):
        found = self.latest_close(symbol)
        return None if found is None else (found.price, found.as_of_iso)


def use(monkeypatch, fake):
    monkeypatch.setitem(P._SOURCES, "fake", lambda: fake)
    monkeypatch.setenv(P.PRICE_SOURCE_VAR, "fake")
    P.reset_source()
    return fake


class TestTheFrontDoor:
    def test_a_close_is_fetched_once_and_then_served(self, monkeypatch):
        fake = use(monkeypatch, Fake(close=P.LatestClose(
            "AAPL", 100.0, date(2026, 11, 20))))
        P.latest_close("AAPL")
        P.latest_close("AAPL")
        P.latest_close("AAPL")
        assert fake.calls == 1

    def test_a_monthly_series_is_fetched_once_and_then_served(self,
                                                              monkeypatch):
        fake = use(monkeypatch, Fake(monthly=[(1, 2.0), (2, 3.0)]))
        P.monthly_closes("AAPL")
        P.monthly_closes("AAPL")
        assert fake.calls == 1

    def test_different_symbols_are_cached_apart(self, monkeypatch):
        fake = use(monkeypatch, Fake(monthly=[(1, 2.0)]))
        P.monthly_closes("AAPL")
        P.monthly_closes("^GSPC")
        assert fake.calls == 2

    def test_nothing_is_cached_when_nothing_came_back(self, monkeypatch):
        """A failure must not be remembered as "this company has no price"
        until the next market close.

        The cache is inspected directly as well as counted. Storing None is
        invisible from the outside - `get` returns the stored value, and a
        stored None reads as a miss - so a test that only counted calls
        passed whether the guard was there or not. Mutation testing found
        that.
        """
        fake = use(monkeypatch, Fake(close=None))
        assert P.latest_close("AAPL") is None
        assert "AAPL" not in P._close_cache._data, (
            "a failure was written into the cache")
        assert P.latest_close("AAPL") is None
        assert fake.calls == 2

    def test_an_empty_monthly_series_is_not_cached_either(self, monkeypatch):
        fake = use(monkeypatch, Fake(monthly=[]))
        P.monthly_closes("AAPL")
        assert "AAPL" not in P._monthly_cache._data
        P.monthly_closes("AAPL")
        assert fake.calls == 2

    def test_no_dividends_IS_cached_because_it_is_an_answer(self,
                                                            monkeypatch):
        """An empty dividend list is a positive statement - the provider
        answered and recorded none - unlike an empty price series."""
        fake = use(monkeypatch, Fake(dividends=[]))
        P.dividend_events("AAPL")
        P.dividend_events("AAPL")
        assert fake.calls == 1

    def test_a_failure_propagates_rather_than_becoming_no_dividends(
            self, monkeypatch):
        """Reading a blocked request as "pays no dividend" would refuse a
        bank for a reason that is simply untrue."""
        use(monkeypatch, Fake(raises=P.PriceUnavailable("blocked")))
        with pytest.raises(P.PriceUnavailable):
            P.dividend_events("JPM")

    def test_rate_limiting_keeps_its_own_type(self, monkeypatch):
        """The API turns it into an HTTP 429 so a client can back off."""
        use(monkeypatch, Fake(raises=P.PriceRateLimited("slow down")))
        with pytest.raises(P.PriceRateLimited):
            P.latest_close("AAPL")
        assert issubclass(P.PriceRateLimited, P.PriceUnavailable)

    def test_cached_close_never_fetches(self, monkeypatch):
        """It is for a serialiser, which must not make a request."""
        fake = use(monkeypatch, Fake(close=P.LatestClose(
            "AAPL", 100.0, date(2026, 11, 20))))
        assert P.cached_close("AAPL") is None
        assert fake.calls == 0
        P.latest_close("AAPL")
        assert P.cached_close("AAPL") is not None


# --- What a price says about itself ---------------------------------------

class TestTheDate:
    def test_it_reads_as_a_sentence(self):
        close = P.LatestClose("AAPL", 100.0, date(2026, 11, 20))
        assert close.as_of_text == "close as of 20 November 2026"
        assert close.as_of_iso == "2026-11-20"

    def test_a_price_from_an_open_session_is_not_called_a_close(self):
        close = P.LatestClose("AAPL", 100.0, date(2026, 11, 20),
                              is_close=False)
        assert close.as_of_text == "price as of 20 November 2026"

    def test_a_missing_date_says_so_rather_than_guessing_today(self):
        """The one mistake this field exists to prevent. Defaulting to
        today would relabel a stale price as current, which is worse than
        having no date at all."""
        close = P.LatestClose("AAPL", 100.0, None)
        assert close.as_of_text == "close date unavailable"
        assert close.as_of_iso is None
        assert str(date.today()) not in close.as_of_text


# --- Nothing else may reach a price endpoint ------------------------------

def test_no_other_module_fetches_a_price():
    """The whole point of this module.

    Switching to Twelve Data has to be writing one class and changing one
    setting, and that is only true while every price request goes through
    here. A chart URL anywhere else is a second implementation waiting to
    be forgotten.

    probe_symbol and endpoint_report are allowed: the first asks whether a
    symbol exists and the second is a diagnostic that deliberately pokes
    raw endpoints. Neither reads a price.
    """
    import inspect

    import market_data

    allowed = {"probe_symbol", "endpoint_report", "_search_exact",
               "search_companies"}
    source = inspect.getsource(market_data)

    offenders = []
    for name, obj in vars(market_data).items():
        if not inspect.isfunction(obj) or name in allowed:
            continue
        try:
            body = inspect.getsource(obj)
        except (OSError, TypeError):
            continue
        for marker in ("_CHART_URL", "finance/chart", "regularMarketPrice",
                       ".history(", "adjclose"):
            if marker in body:
                offenders.append(f"{name} uses {marker}")
    assert offenders == [], offenders
