"""The watchlist's rows: one shared computation per ticker per trading day.

WHAT THIS IS FOR
----------------
A watchlist of ten companies must not be ten valuations. Each one reads
several years of statements, five years of monthly closes, the Treasury
curve and an industry table, and on a cold free instance the first of them
also pays the container's start-up. Ten users watching Nvidia should cost
one computation, not ten.

THE SPLIT, WHICH IS THE WHOLE DESIGN
------------------------------------
A row has two halves that move at completely different speeds:

  the intrinsic value   expensive, and a discounted cash flow is not a
                        tape - it is built from filings that change once a
                        quarter and a discount rate that moves in basis
                        points. Cached until the next close exists.

  the price             cheap, one request, and the only figure on the row
                        a reader will check against their broker. Never
                        cached beyond the market's own clock.

So the expensive half is shared across every user of this instance for the
trading day, and the cheap half is current. The upside is recomputed from
the pair, which means a row is never internally inconsistent even though
its two numbers came from different moments.

The one thing this costs: market capitalisation feeds the debt-to-equity
weighting in the discount rate, so a cached intrinsic value drifts very
slightly from what a fresh computation would give as the price moves
through the day. That is immaterial at the precision anyone reads these
figures, and it is the price of not recomputing a DCF because a share moved
forty cents. Opening the company itself always recomputes.

WHAT IT DELIBERATELY IS NOT
---------------------------
Not a persistent cache. The free Render instance stops when idle and loses
this with the rest of its memory, exactly as `price_source` already accepts
for closes. A disk or Redis cache would add a dependency to buy back
something the platform takes away anyway; what it would buy is one slow
first request per instance per day.

Not a figure anyone's device trusts. The app shows each row's own last
known value from the user's own stored watchlist first, then replaces it
with what this returns. Nothing here is on the path to a first paint.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import price_source
from price_source import next_close_after

# A watchlist is a reading list, not a screen of the market. The cap exists
# so one request cannot ask this instance for a thousand valuations; it is
# well above any list a person maintains by hand.
MAX_TICKERS = 50

# A ticker that failed is retried sooner than one that succeeded: a delisting
# is permanent and a Yahoo wobble is not, and the two are indistinguishable
# from here. Short enough that a transient failure clears on the next pull,
# long enough that a genuinely dead ticker in someone's list is not retried
# on every open.
_FAILURE_SECONDS = 120


@dataclass(frozen=True)
class WatchlistRow:
    """One line of the watchlist."""

    ticker: str
    name: str | None = None
    method: str | None = None

    # None whenever the company was refused a valuation, or the data could
    # not be fetched. The row still exists in both cases: a watchlist that
    # silently drops Tesla disagrees with the valuation screen, which says
    # out loud that no figure is being shown and why.
    intrinsic_value_per_share: float | None = None
    current_price: float | None = None
    upside_downside: float | None = None

    # The trading day the intrinsic value was computed for, and the date the
    # price is from. Shown on the row, because a figure without its date is
    # a claim about now that nobody checked.
    valued_for: str | None = None
    price_as_of: str | None = None

    # Refused, as opposed to failed. A refusal is an answer.
    refused: bool = False
    refusal_code: str | None = None
    headline: str | None = None

    # Failed, as opposed to refused. Transient, and worth retrying.
    error: str | None = None

    # True when this row came from the day cache rather than a computation.
    # Reported so the app can tell "nothing has changed" from "nothing has
    # been computed yet", and so a test can prove the sharing works.
    cached: bool = False


@dataclass(frozen=True)
class _Valued:
    """The expensive half, held for the trading day."""

    name: str | None
    method: str | None
    intrinsic_value_per_share: float | None
    valued_for: str
    refused: bool
    refusal_code: str | None
    headline: str | None


class _DayCache:
    """Valuations, held until the next close exists.

    The same boundary `price_source` uses for closes, and for the same
    reason: caching "for a day" either serves yesterday's figure after this
    evening's close or recomputes an unchanged one all night, and caching
    "until the next close" does neither.
    """

    def __init__(self, max_entries: int = 512):
        self.max_entries = max_entries
        self._data: dict[str, tuple[datetime, _Valued]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, ticker: str, *, now: datetime | None = None) -> _Valued | None:
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            entry = self._data.get(ticker)
            if entry is None:
                self.misses += 1
                return None
            expires, value = entry
            if moment >= expires:
                self._data.pop(ticker, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def put(self, ticker: str, value: _Valued, *,
            now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            if len(self._data) >= self.max_entries:
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
            self._data[ticker] = (next_close_after(moment), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0


class _FailureCache:
    """Tickers that would not value, held briefly."""

    def __init__(self, seconds: float = _FAILURE_SECONDS, max_entries: int = 256):
        self.seconds = seconds
        self.max_entries = max_entries
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, ticker: str, *, monotonic: float) -> str | None:
        with self._lock:
            entry = self._data.get(ticker)
            if entry is None:
                return None
            stored_at, message = entry
            if monotonic - stored_at > self.seconds:
                self._data.pop(ticker, None)
                return None
            return message

    def put(self, ticker: str, message: str, *, monotonic: float) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries:
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
            self._data[ticker] = (monotonic, message)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_valuations = _DayCache()
_failures = _FailureCache()


def clear_caches() -> None:
    """Drop everything cached. Used by tests and after a deliberate refresh."""
    _valuations.clear()
    _failures.clear()


def cache_statistics() -> dict:
    """How well the sharing is working, for /health and for tests."""
    return {
        "valuations_cached": len(_valuations._data),
        "hits": _valuations.hits,
        "misses": _valuations.misses,
    }


# ---------------------------------------------------------------------------
# Building the rows
# ---------------------------------------------------------------------------

def normalise(tickers: list[str]) -> list[str]:
    """Upper-cased, blanks dropped, duplicates removed, order kept, capped.

    Order is kept because it is the user's: a watchlist is a list, and
    returning it shuffled would make the app sort it back. Duplicates are
    dropped rather than refused - the same company twice is a mistake worth
    fixing silently, not an error worth a 400.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in tickers:
        ticker = (raw or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
        if len(out) >= MAX_TICKERS:
            break
    return out


def _valued_from(report) -> _Valued:
    """The expensive half of a row, from a valuation report."""
    valued_for = datetime.now(timezone.utc).date().isoformat()

    if not report.suitable:
        # A refusal is an answer, and the row says so rather than going
        # blank. The first reason is the one the refusal card leads with.
        reasons = list(getattr(report.suitability, "reasons", None) or [])
        codes = list(getattr(report.suitability, "codes", None) or [])
        return _Valued(
            name=getattr(report, "company_name", None),
            method=getattr(report, "method", None),
            intrinsic_value_per_share=None,
            valued_for=valued_for,
            refused=True,
            refusal_code=codes[0] if codes else None,
            headline=reasons[0] if reasons else None,
        )

    return _Valued(
        name=getattr(report, "company_name", None),
        method=getattr(report, "method", None),
        intrinsic_value_per_share=report.result.intrinsic_value_per_share,
        valued_for=valued_for,
        refused=False,
        refusal_code=None,
        headline=None,
    )


def _price_for(ticker: str, now: datetime | None) -> tuple[float | None, str | None]:
    """The cheap half: a current price and its date, or nothing.

    Never raises. A price that cannot be fetched costs this row its upside
    and nothing else - the intrinsic value is still the answer to the
    question the app asked, and the row still shows it.
    """
    try:
        close = price_source.latest_close(ticker, now=now)
    except Exception:
        close = price_source.cached_close(ticker)
    if close is None:
        return None, None
    # A property, not a method. Calling it returned a str that the row then
    # tried to call, which the stand-in in the tests hid by defining it as
    # a method - so the fake was wrong and agreed with the code about it.
    # The tests build real LatestClose objects now.
    return close.price, close.as_of_iso


def rows_for(tickers: list[str], *, valuer=None,
             now: datetime | None = None) -> list[WatchlistRow]:
    """One row per ticker, sharing the day's computation across callers.

    `valuer` is injected rather than imported at module level so this stays
    testable without the network, and so the API layer keeps deciding which
    function every standard request goes through.
    """
    import time as _time

    if valuer is None:                       # pragma: no cover - wiring
        from analysis import value_company as valuer   # noqa: N813

    moment = now or datetime.now(timezone.utc)
    monotonic = _time.monotonic()
    rows: list[WatchlistRow] = []

    for ticker in normalise(tickers):
        failed = _failures.get(ticker, monotonic=monotonic)
        if failed is not None:
            rows.append(WatchlistRow(ticker=ticker, error=failed, cached=True))
            continue

        valued = _valuations.get(ticker, now=moment)
        cached = valued is not None
        if valued is None:
            try:
                valued = _valued_from(valuer(ticker, overrides=None))
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                _failures.put(ticker, message, monotonic=monotonic)
                rows.append(WatchlistRow(ticker=ticker, error=message))
                continue
            _valuations.put(ticker, valued, now=moment)

        price, price_as_of = _price_for(ticker, now)

        # Recomputed from this row's own two halves rather than carried over
        # from the valuation, which priced itself at whatever moment it ran.
        upside = None
        value = valued.intrinsic_value_per_share
        if value is not None and price:
            upside = value / price - 1.0

        rows.append(WatchlistRow(
            ticker=ticker,
            name=valued.name,
            method=valued.method,
            intrinsic_value_per_share=value,
            current_price=price,
            upside_downside=upside,
            valued_for=valued.valued_for,
            price_as_of=price_as_of,
            refused=valued.refused,
            refusal_code=valued.refusal_code,
            headline=valued.headline,
            cached=cached,
        ))

    return rows
