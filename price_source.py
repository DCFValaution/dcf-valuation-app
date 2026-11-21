"""Every share price this app uses, behind one interface.

The statements come from the filings, the risk-free rate from the Treasury
and exchange rates from the ECB. Prices are the one input still bought from
a commercial aggregator, and the one with no free licensed alternative - see
SOURCES.md section 6. The app launches with Yahoo; Twelve Data is the
intended replacement.

So prices are the dependency most likely to be swapped, and this module
exists so that swapping it is writing one new class and changing one
setting. Nothing outside here may reach a price endpoint directly.

    PriceSource          what a provider has to answer
    YahooPriceSource     the only implementation today
    source()             the configured one, by PRICE_SOURCE_VAR

Four questions, which is all any provider is asked:

    latest_close(symbol)    one close, dated
    monthly_closes(symbol)  five years of month-end closes
    dividend_events(symbol) cash dividends per share
    daily_close(symbol)     one close for a non-equity symbol

The last is for an FX pair or a yield index, and exists only because both
have a quoted fallback behind a licensed primary source. A provider that
does not carry them returns None and the fallback simply does not fire.

WHAT IS DELIBERATELY NOT HERE. Symbol search, "also watched" peer
suggestions and the fundamentals timeseries are the same provider's
endpoints but not prices, and folding them in would make this module "the
Yahoo module" rather than "the price module". They are listed in SOURCES.md
as the rest of the aggregator dependency, and a switch has to deal with
them separately.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as time_of_day, timedelta, timezone
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PRICE_SOURCE_VAR = "PRICE_SOURCE"
DEFAULT_PRICE_SOURCE = "yahoo"


class PriceUnavailable(Exception):
    """The provider was asked and did not answer.

    Distinct from "this symbol does not exist", which is a fact about the
    world rather than about the provider, and is reported by returning None.
    Callers are expected to degrade rather than fail where they can.
    """


class PriceRateLimited(PriceUnavailable):
    """The provider is throttling us.

    Its own subclass because the right response differs: "try again in a
    moment" is true and actionable, where a generic failure is neither. The
    API turns this into an HTTP 429 so a client can back off, and collapsing
    it into PriceUnavailable lost that - caught by a test that had been
    asserting the distinction since before this module existed.
    """


# ---------------------------------------------------------------------------
# What a price is
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LatestClose:
    """One price, and the date it closed on.

    The date is not decoration. A cached price and a live one are
    indistinguishable to a reader, and a market holiday makes Friday's close
    today's price with nothing on screen to say so. Everything that shows a
    price shows this date beside it.
    """

    symbol: str
    price: float
    # None when the provider sent a price and no date for it. The price is
    # still worth having - refusing it would lose a whole valuation over a
    # missing field - but it must not then be presented as current, so
    # `as_of_text` says the date is unknown rather than guessing today's.
    as_of: date | None
    currency: str | None = None
    name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    # True when the figure is the last regular-session close rather than a
    # price from a session now open. It changes the wording, not the number.
    is_close: bool = True

    @property
    def as_of_text(self) -> str:
        """"close as of 20 November 2026", for putting next to the number."""
        what = "close" if self.is_close else "price"
        if self.as_of is None:
            return f"{what} date unavailable"
        return f"{what} as of {self.as_of.day} {self.as_of:%B %Y}"

    @property
    def as_of_iso(self) -> str | None:
        return self.as_of.isoformat() if self.as_of else None


class PriceSource(Protocol):
    """What a price provider has to be able to answer."""

    name: str

    def latest_close(self, symbol: str) -> LatestClose | None:
        ...

    def monthly_closes(self, symbol: str) -> list[tuple[int, float]]:
        ...

    def dividend_events(self, symbol: str) -> list[tuple[int, float]]:
        ...

    def daily_close(self, symbol: str) -> tuple[float, str] | None:
        ...


# ---------------------------------------------------------------------------
# When a price stops being current
# ---------------------------------------------------------------------------
#
# A close is good until the next one exists, so the cache is keyed on the
# market's clock rather than on a duration. Caching "for an hour" either
# serves a stale price after the close or re-fetches an unchanged one all
# evening; caching "until the next close" does neither.

NYSE = ZoneInfo("America/New_York")
MARKET_CLOSE = time_of_day(16, 0)
# Settling time after the bell before the official close is served.
CLOSE_SETTLE = timedelta(minutes=30)


def next_close_after(moment: datetime) -> datetime:
    """When the next US regular-session close will have been published.

    Weekends are skipped. Exchange holidays are not: this is a cache
    boundary, and the cost of being wrong about Thanksgiving is one
    unnecessary request, not a wrong price. The date on the price itself
    comes from the provider, so a holiday shows the real close date whatever
    this returns.
    """
    local = moment.astimezone(NYSE)
    boundary = local.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute,
                             second=0, microsecond=0) + CLOSE_SETTLE
    if local >= boundary:
        boundary += timedelta(days=1)
    while boundary.weekday() >= 5:          # Saturday, Sunday
        boundary += timedelta(days=1)
    return boundary


class _ClosePriceCache:
    """Latest closes, held until the next close exists.

    Thread-safe, in-memory, and that is on purpose: the free Render instance
    restarts whenever it feels like it, so a disk or Redis cache would add a
    dependency to buy back something the platform takes away anyway.
    """

    def __init__(self, max_entries: int = 512):
        self.max_entries = max_entries
        self._data: dict[str, tuple[datetime, LatestClose]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, symbol: str, *, now: datetime | None = None
            ) -> LatestClose | None:
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            entry = self._data.get(symbol)
            if entry is None:
                self.misses += 1
                return None
            expires, value = entry
            if moment >= expires:
                self._data.pop(symbol, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def put(self, symbol: str, value: LatestClose, *,
            now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            if len(self._data) >= self.max_entries:
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
            self._data[symbol] = (next_close_after(moment), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0


class _DailyCache:
    """Anything worth keeping for a day. Monthly closes and dividends.

    Five years of month-end closes cannot move materially in a day, and the
    monthly series is the heaviest request this app makes - sixty bars per
    company, plus sixty for the index.
    """

    def __init__(self, ttl_seconds: float = 24 * 60 * 60,
                 max_entries: int = 512):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            stored_at, value = entry
            if time.monotonic() - stored_at > self.ttl:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def put(self, key: str, value: object) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries:
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0


_close_cache = _ClosePriceCache()
_monthly_cache = _DailyCache()
_dividend_cache = _DailyCache()


def clear_caches() -> None:
    _close_cache.clear()
    _monthly_cache.clear()
    _dividend_cache.clear()


def cache_statistics() -> dict:
    """Hits and misses per cache, for the diagnostics endpoint."""
    return {
        "latest_close": {"hits": _close_cache.hits,
                         "misses": _close_cache.misses,
                         "expires": "next US market close"},
        "monthly_closes": {"hits": _monthly_cache.hits,
                           "misses": _monthly_cache.misses,
                           "expires": "24 hours"},
        "dividends": {"hits": _dividend_cache.hits,
                      "misses": _dividend_cache.misses,
                      "expires": "24 hours"},
    }


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------

class YahooPriceSource:
    """Prices from Yahoo's chart endpoint.

    The chart endpoint rather than the quote endpoint, which needs a crumb
    Yahoo mints for a datacentre IP roughly once in fourteen attempts. Every
    figure here is served unauthenticated, which is why the whole app is
    built on it.
    """

    name = "Yahoo Finance"

    _CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

    def _chart(self, symbol: str, params: dict, *, timeout: int = 30) -> dict:
        # Imported here, not at module scope: market_data imports this
        # module, so importing it back at the top would be a cycle. The
        # session is market_data's because it carries the TLS impersonation
        # that gets Yahoo to answer at all.
        from market_data import _browser_session

        try:
            response = _browser_session().get(self._CHART.format(symbol),
                                              params=params, timeout=timeout)
        except Exception as exc:
            raise PriceUnavailable(
                f"Could not reach the price source for {symbol}: {exc}"
            ) from exc
        if response.status_code == 404:
            return {}
        if response.status_code == 429:
            raise PriceRateLimited(
                "The price source is rate-limiting requests right now. "
                "Wait a moment and try again.")
        if response.status_code != 200:
            raise PriceUnavailable(
                f"The price source returned HTTP {response.status_code} for "
                f"{symbol}.")
        try:
            return response.json()
        except Exception as exc:
            raise PriceUnavailable(
                f"The price source returned an unreadable response for "
                f"{symbol}.") from exc

    def latest_close(self, symbol: str) -> LatestClose | None:
        body = self._chart(symbol, {"range": "1d", "interval": "1d"},
                           timeout=20)
        try:
            meta = body["chart"]["result"][0]["meta"]
        except (KeyError, IndexError, TypeError):
            return None

        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if not price:
            return None

        # The stamp Yahoo gives is the last trade in the session the price
        # belongs to, which is the date to show. Falling back to today would
        # be the one mistake this field exists to prevent: it would relabel
        # a stale price as current.
        stamp = (meta.get("regularMarketTime")
                 or meta.get("currentTradingPeriod", {})
                 .get("regular", {}).get("end"))
        as_of = None
        if not stamp:
            bars = (body["chart"]["result"][0].get("timestamp") or [])
            stamp = bars[-1] if bars else None
        if stamp:
            try:
                as_of = datetime.fromtimestamp(int(stamp), NYSE).date()
            except (TypeError, ValueError, OSError, OverflowError):
                as_of = None

        state = (meta.get("marketState") or "").upper()
        return LatestClose(
            symbol=meta.get("symbol") or symbol,
            price=float(price),
            as_of=as_of,
            currency=meta.get("currency"),
            name=meta.get("longName") or meta.get("shortName"),
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName"),
            quote_type=meta.get("instrumentType"),
            is_close=state not in {"REGULAR"},
        )

    def monthly_closes(self, symbol: str) -> list[tuple[int, float]]:
        body = self._chart(symbol, {"range": "5y", "interval": "1mo"})
        try:
            result = body["chart"]["result"][0]
        except (KeyError, IndexError, TypeError):
            return []
        stamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}

        # Adjusted closes where available: dividends and splits are returns
        # to the holder, and a raw split would otherwise read as a -50%
        # month.
        adjusted = indicators.get("adjclose") or []
        if adjusted and adjusted[0].get("adjclose"):
            series = adjusted[0]["adjclose"]
        else:
            series = (indicators.get("quote") or [{}])[0].get("close")

        bars = [(int(t), float(v)) for t, v in zip(stamps, series or [])
                if v is not None]

        # Yahoo returns sixty monthly bars and then repeats the last one,
        # stamped with the moment of the request and carrying the same
        # close. That duplicate is not a month: it is a 0.000000% return,
        # and a month of zero return that never happened belongs in no
        # series. It is dropped here because every caller wants months and
        # none of them wants this.
        if len(bars) >= 2:
            previous = datetime.fromtimestamp(bars[-2][0], timezone.utc)
            last = datetime.fromtimestamp(bars[-1][0], timezone.utc)
            if (previous.year, previous.month) == (last.year, last.month):
                bars = bars[:-1]
        return bars

    def dividend_events(self, symbol: str) -> list[tuple[int, float]]:
        body = self._chart(symbol, {"range": "10y", "interval": "1mo",
                                    "events": "div"})
        try:
            result = body["chart"]["result"][0]
        except (KeyError, IndexError, TypeError):
            raise PriceUnavailable(
                f"The price source returned no dividend history for {symbol}.")
        events = ((result.get("events") or {}).get("dividends") or {})
        out = []
        for entry in events.values():
            stamp, amount = entry.get("date"), entry.get("amount")
            if stamp is not None and amount is not None:
                out.append((int(stamp), float(amount)))
        return sorted(out)

    def daily_close(self, symbol: str) -> tuple[float, str] | None:
        """One close for a symbol that is not a company.

        An FX pair or a yield index. Both have a licensed primary source
        now - the ECB and the US Treasury - so this is only ever the
        fallback behind one.
        """
        found = self.latest_close(symbol)
        if found is None:
            return None
        return found.price, found.as_of_iso or date.today().isoformat()



def close_from_quote(info: dict, symbol: str) -> LatestClose | None:
    """A price out of Yahoo's quote payload, when one is already in hand.

    Not a fetch. `fetch_financials` gets the whole quote in one request on
    the path where Yahoo authenticates it, and the price is in there; asking
    the chart endpoint again for a figure already received would be a second
    request for the same thing.

    It lives here rather than in market_data so that Yahoo's field names
    stay inside the Yahoo module - the guard in test_price_source asserts
    exactly that. The price source is still consulted FIRST, because two
    sources for one number is how AAPL came back 1.085 and 1.088 on the same
    afternoon; this is the fallback for when it has nothing.
    """
    price = (info.get("currentPrice")
             or info.get("regularMarketPrice")
             or info.get("previousClose")
             or info.get("regularMarketPreviousClose"))
    if not price:
        return None
    as_of = None
    stamp = info.get("regularMarketTime")
    if stamp:
        try:
            as_of = datetime.fromtimestamp(int(stamp), NYSE).date()
        except (TypeError, ValueError, OSError, OverflowError):
            as_of = None
    return LatestClose(
        symbol=info.get("symbol") or symbol,
        price=float(price),
        as_of=as_of,
        currency=info.get("currency"),
        name=info.get("longName") or info.get("shortName"),
        exchange=info.get("fullExchangeName") or info.get("exchange"),
        quote_type=info.get("quoteType"),
        is_close=(info.get("marketState") or "").upper() != "REGULAR",
    )

_SOURCES = {"yahoo": YahooPriceSource}
_lock = threading.Lock()
_configured: PriceSource | None = None


def configured_name() -> str:
    return (os.environ.get(PRICE_SOURCE_VAR) or DEFAULT_PRICE_SOURCE).strip().lower()


def source() -> PriceSource:
    """The configured price source.

    One setting, one class. Adding Twelve Data is writing the class, adding
    it to _SOURCES, and setting PRICE_SOURCE on Render.
    """
    global _configured
    with _lock:
        if _configured is not None:
            return _configured
        wanted = configured_name()
        implementation = _SOURCES.get(wanted)
        if implementation is None:
            log.warning("%s=%r is not a price source I have; using %s",
                        PRICE_SOURCE_VAR, wanted, DEFAULT_PRICE_SOURCE)
            implementation = _SOURCES[DEFAULT_PRICE_SOURCE]
        _configured = implementation()
        return _configured


def reset_source() -> None:
    """Forget the configured source. Used by tests and after a setting change."""
    global _configured
    with _lock:
        _configured = None


# ---------------------------------------------------------------------------
# The cached front door - what the rest of the app calls
# ---------------------------------------------------------------------------

def latest_close(symbol: str, *, now: datetime | None = None
                 ) -> LatestClose | None:
    """The newest close for *symbol*, cached until the next one exists."""
    cached = _close_cache.get(symbol, now=now)
    if cached is not None:
        return cached
    found = source().latest_close(symbol)
    if found is not None:
        _close_cache.put(symbol, found, now=now)
    return found


def cached_close(symbol: str) -> LatestClose | None:
    """What is already cached for *symbol*, without fetching anything.

    For a serialiser that wants the price's date and must not make a
    request to get it. Every valuation path has fetched the price moments
    earlier, so the cache has it; if it does not, the caller says the date
    is unavailable rather than going and finding out.
    """
    return _close_cache.get(symbol)


def monthly_closes(symbol: str) -> list[tuple[int, float]]:
    """Five years of month-end closes for *symbol*, cached for a day."""
    cached = _monthly_cache.get(symbol)
    if cached is not None:
        return list(cached)                     # callers may mutate
    found = source().monthly_closes(symbol)
    # Only a real series. An empty one means the provider would not answer,
    # and remembering that for a day would turn a blocked request into "this
    # company has no price history" - which reaches the beta regression as
    # an absence of evidence rather than as a failure.
    if found:
        _monthly_cache.put(symbol, list(found))
    return found


def dividend_events(symbol: str) -> list[tuple[int, float]]:
    """Cash dividends per share for *symbol*, cached for a day.

    An empty list is a positive statement: the provider answered and
    recorded no dividends. A failure raises PriceUnavailable instead, so a
    company that pays dividends is never valued as one that does not.
    """
    cached = _dividend_cache.get(symbol)
    if cached is not None:
        return list(cached)
    found = source().dividend_events(symbol)
    _dividend_cache.put(symbol, list(found))
    return found


def daily_close(symbol: str) -> tuple[float, str] | None:
    """One close for an FX pair or a yield index, cached as a close."""
    found = latest_close(symbol)
    if found is None:
        return None
    return found.price, found.as_of_iso or date.today().isoformat()
