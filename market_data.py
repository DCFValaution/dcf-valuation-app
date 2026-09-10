"""
Market data via Yahoo Finance (yfinance).

Replaces the previous Financial Modeling Prep fetcher. Yahoo is free and
covers far more tickers - companies that FMP's free tier gated behind a paid
plan now return real data.

WHY THIS MODULE LOOKS LIKE THE OLD ONE
--------------------------------------
Everything downstream - assumption derivation, the suitability guard, the DCF
engine, the API and the Excel export - reads statement rows as dicts with
FMP's field names (`revenue`, `operatingIncome`, `shortTermDebt`, ...). Rather
than rewrite all of that, this module normalises Yahoo's DataFrames into
exactly that shape. The mapping is the only thing that changed; the contract
is identical, which is why no other module needed touching.

FIELD MAPPING, VERIFIED NOT GUESSED
-----------------------------------
Yahoo labels rows differently from FMP, and its labels are not stable across
companies. Every mapping below was checked against real responses, and the
AAPL FY2025 figures reconcile exactly with the FMP numbers the model was
previously verified against:

    Total Revenue                                416,161mm   exact
    Operating Income                             133,050mm   exact
    Pretax Income / Tax Provision        132,729 / 20,719mm  exact
    Diluted Average Shares                      15,004.7mm   exact
    Current Debt + Long Term Debt                 98,657mm   exact
    Cash + Other STI + Investments And Advances  132,420mm   exact
    Depreciation And Amortization                 11,698mm   exact

Two mappings remain judgement calls, kept identical to the treatment the
model was built and tested against:

  total_debt = Current Debt + Long Term Debt
      Excludes capital leases. Yahoo also publishes "Total Debt" and
      "... And Capital Lease Obligation" variants; the unqualified pair is
      the lease-free figure.

  cash = Cash And Cash Equivalents + Other Short Term Investments
         + Investments And Advances
      Includes LONG-term marketable securities. Yahoo's
      "Cash Cash Equivalents And Short Term Investments" (54,697 for AAPL)
      omits the long-term portfolio and badly understates the cash a DCF
      should credit.

LABELS VARY BY INDUSTRY
-----------------------
Berkshire publishes no "Operating Income" line at all, and no current
assets/liabilities, because insurers do not present a classified balance
sheet. Every lookup is therefore a fallback chain rather than a single name,
and a missing line degrades to a documented default instead of an exception.

NO API KEY
----------
Yahoo needs no credentials, so the FMP_API_KEY dependency is gone entirely.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable, Sequence

from dcf import BaseYearData

# yfinance chats to stderr about 404s and retries. In a server that is noise
# on someone else's terminal, and it is not how this module reports failure.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

import yfinance as yf  # noqa: E402  (import after the logger is quieted)

try:  # pragma: no cover - depends on the installed yfinance version
    from yfinance.exceptions import YFRateLimitError
except Exception:  # pragma: no cover
    class YFRateLimitError(Exception):
        """Placeholder when the installed yfinance predates this exception."""

DEFAULT_HISTORY_YEARS = 5


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MarketDataError(Exception):
    """Any data-source failure, carrying a human-readable message."""


class TickerNotFoundError(MarketDataError):
    """
    The symbol is unknown, delisted, or has no financial statements to value.

    Maps to HTTP 404. Covers ETFs and funds too: they quote fine but publish
    no income statement, so there is nothing to build a DCF from.
    """


class DataUnavailableError(MarketDataError):
    """
    Yahoo is reachable but did not return usable data right now.

    Distinct from TickerNotFoundError because it is transient: the same
    request may well succeed a minute later. Maps to HTTP 502.
    """


class RateLimitedError(DataUnavailableError):
    """Yahoo is throttling us. Maps to HTTP 429."""


class UnsupportedListingError(MarketDataError):
    """
    The data is real but cannot be used to value this listing correctly.

    Raised for foreign listings whose financial statements are reported in a
    different currency from the quote - see _check_currency(). Permanent for
    that listing rather than transient, so retrying will not help.
    """


# Kept so `except FMPError` in any older caller still works. New code should
# use MarketDataError.
FMPError = MarketDataError


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class _TTLCache:
    """
    Small thread-safe TTL cache.

    Yahoo is rate-limited and flaky, and a single valuation triggers several
    calls; re-fetching the same ticker for every slider confirmation would be
    both slow and a good way to get throttled. Failures are cached too, for a
    shorter period - repeatedly asking about a delisted ticker is exactly the
    traffic that earns a rate limit.
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if time.monotonic() - stored_at > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries:
                # Cheap eviction: drop the oldest entry.
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Fundamentals change quarterly; the price inside them is the only fast-moving
# part, and a few minutes of staleness is irrelevant to a DCF.
_FINANCIALS_TTL = 15 * 60
_FAILURE_TTL = 60
_RATE_TTL = 30 * 60

# Beta is built from five years of monthly closes. Another hour of trading
# cannot move it materially, and caching it for that long keeps the chart
# endpoint - the one Yahoo still serves us - from being hammered.
_BETA_TTL = 6 * 60 * 60

_financials_cache = _TTLCache(_FINANCIALS_TTL)
_failure_cache = _TTLCache(_FAILURE_TTL)
_rate_cache = _TTLCache(_RATE_TTL)
_beta_cache = _TTLCache(_BETA_TTL)
# The market series is identical for every ticker, so it is cached apart from
# them: valuing thirty companies costs one index fetch, not thirty.
_market_cache = _TTLCache(_BETA_TTL)


def clear_caches() -> None:
    """Drop everything cached. Used by tests and after a deliberate refresh."""
    _financials_cache.clear()
    _failure_cache.clear()
    _rate_cache.clear()
    _beta_cache.clear()
    _market_cache.clear()


# ---------------------------------------------------------------------------
# Statement rows
# ---------------------------------------------------------------------------

@dataclass
class CompanyFinancials:
    """A company's profile plus aligned multi-year statements, newest first."""
    ticker: str
    profile: dict
    income: list[dict] = field(default_factory=list)
    balance: list[dict] = field(default_factory=list)
    cashflow: list[dict] = field(default_factory=list)

    @property
    def company_name(self) -> str:
        return self.profile.get("companyName") or self.ticker

    @property
    def sector(self) -> str:
        return self.profile.get("sector") or "Unknown"

    @property
    def industry(self) -> str:
        return self.profile.get("industry") or "Unknown"

    @property
    def exchange(self) -> str:
        return self.profile.get("exchange") or "?"

    @property
    def years_available(self) -> int:
        return len(self.income)


def num(row: dict, name: str, default: float = 0.0) -> float:
    """Read a numeric field that may legitimately be absent or null."""
    value = row.get(name)
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if result != result else result  # NaN guard


def total_debt_of(balance_row: dict) -> float:
    """
    Interest-bearing debt, matching the base-year mapping.

    Excludes capital leases, and stays consistent with base_year_from()
    because this figure is the denominator of the cost-of-debt calculation
    and the D in the WACC weighting.
    """
    return num(balance_row, "shortTermDebt") + num(balance_row, "longTermDebt")


# ---------------------------------------------------------------------------
# Yahoo -> canonical field mapping
# ---------------------------------------------------------------------------

def _frame_value(frame, names: Sequence[str], column: int) -> float | None:
    """
    First usable value among `names` in the given period column.

    Yahoo's row labels differ by industry, so every lookup is a chain. Returns
    None when nothing matches, so callers can decide between a default and an
    error rather than silently receiving a zero.
    """
    if frame is None or getattr(frame, "empty", True):
        return None
    if column >= frame.shape[1]:
        return None

    for name in names:
        if name not in frame.index:
            continue
        try:
            value = frame.iloc[:, column].loc[name]
        except Exception:
            continue
        # A duplicated label yields a Series; take its first usable entry.
        if hasattr(value, "iloc"):
            candidates: Iterable = value.tolist()
        else:
            candidates = [value]
        for candidate in candidates:
            try:
                number = float(candidate)
            except (TypeError, ValueError):
                continue
            if number == number:  # not NaN
                return number
    return None


def _period_label(frame, column: int) -> tuple[str, str]:
    """(fiscalYear, date) for a period column."""
    try:
        stamp = frame.columns[column]
    except Exception:
        return "?", "?"
    try:
        as_date = stamp.date() if hasattr(stamp, "date") else stamp
        # Yahoo labels the period by its END date. A fiscal year ending in
        # early-calendar months is therefore labelled a year later than a
        # 10-K would call it; harmless for the model, which only uses the
        # ordering, but worth knowing when reading the output.
        return str(as_date.year), str(as_date)
    except Exception:
        return "?", str(stamp)


def _income_row(frame, column: int) -> dict:
    fiscal_year, period_end = _period_label(frame, column)

    revenue = _frame_value(frame, ("Total Revenue", "Operating Revenue"), column)
    pretax = _frame_value(frame, ("Pretax Income",), column)
    interest = _frame_value(
        frame, ("Interest Expense", "Interest Expense Non Operating"), column)

    # Insurers and some conglomerates publish no "Operating Income" line at
    # all (Berkshire is the case that surfaced this). EBIT is the closest
    # equivalent; failing that, rebuild it from pre-tax income by adding back
    # interest.
    operating_income = _frame_value(
        frame,
        ("Operating Income", "Total Operating Income As Reported", "EBIT"),
        column,
    )
    if operating_income is None and pretax is not None:
        operating_income = pretax + (interest or 0.0)

    return {
        "fiscalYear": fiscal_year,
        "date": period_end,
        "revenue": revenue,
        "operatingIncome": operating_income,
        "incomeBeforeTax": pretax,
        "incomeTaxExpense": _frame_value(frame, ("Tax Provision",), column),
        "interestExpense": abs(interest) if interest is not None else None,
        "weightedAverageShsOutDil": _frame_value(
            frame, ("Diluted Average Shares", "Basic Average Shares"), column),
        "weightedAverageShsOut": _frame_value(
            frame, ("Basic Average Shares",), column),
        "depreciationAndAmortization": _frame_value(
            frame, ("Reconciled Depreciation",), column),
    }


def _balance_row(frame, column: int) -> dict:
    fiscal_year, period_end = _period_label(frame, column)

    short_investments = _frame_value(
        frame, ("Other Short Term Investments",), column) or 0.0
    long_investments = _frame_value(
        frame, ("Investments And Advances", "Long Term Equity Investment"),
        column) or 0.0

    return {
        "fiscalYear": fiscal_year,
        "date": period_end,
        # Unqualified debt lines exclude capital leases; the
        # "... And Capital Lease Obligation" variants include them.
        "shortTermDebt": _frame_value(
            frame, ("Current Debt", "Current Debt And Capital Lease Obligation"),
            column),
        "longTermDebt": _frame_value(
            frame, ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
            column),
        "cashAndCashEquivalents": _frame_value(
            frame,
            ("Cash And Cash Equivalents",
             "Cash Cash Equivalents And Short Term Investments"),
            column),
        "shortTermInvestments": short_investments,
        "totalInvestments": short_investments + long_investments,
        # Absent for insurers, which do not present a classified balance
        # sheet. The NWC derivation already treats a missing pair as "no
        # working-capital investment" rather than failing.
        "totalCurrentAssets": _frame_value(frame, ("Current Assets",), column),
        "totalCurrentLiabilities": _frame_value(
            frame, ("Current Liabilities",), column),
    }


def _cashflow_row(frame, column: int) -> dict:
    fiscal_year, period_end = _period_label(frame, column)
    return {
        "fiscalYear": fiscal_year,
        "date": period_end,
        "depreciationAndAmortization": _frame_value(
            frame,
            ("Depreciation And Amortization", "Depreciation Amortization Depletion",
             "Depreciation"),
            column),
        # Yahoo reports capex as a negative outflow, as FMP did; the
        # derivation takes its absolute value.
        "capitalExpenditure": _frame_value(
            frame, ("Capital Expenditure", "Purchase Of PPE"), column),
        "changeInWorkingCapital": _frame_value(
            frame, ("Change In Working Capital",), column),
    }


def _column_index(frame) -> dict:
    """Period end -> column position, for aligning the three statements."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    return {stamp: i for i, stamp in enumerate(frame.columns)}


def _aligned_rows(income_frame, balance_frame, cashflow_frame,
                  years: int) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Build the three statements as period-aligned lists, newest first.

    Two problems this solves, both of which produced quietly wrong numbers
    when the statements were simply truncated to `years` independently:

    1. Yahoo pads the oldest column with NaN when it has partial coverage -
       AAPL's fifth income column carries no revenue at all. Treating that as
       a zero made the revenue CAGR undefined, and the derivation silently
       fell back to its default growth rate. Periods without revenue are not
       usable periods, so they are dropped.

    2. The three statements do not always share the same periods (Berkshire
       returns five income columns but four balance-sheet columns). The
       derivation zips them positionally, so an offset would have paired one
       year's cash flow with another year's revenue. Rows are matched on the
       period end date instead, and a statement missing a period contributes
       an empty row rather than shifting everything up.
    """
    if income_frame is None or getattr(income_frame, "empty", True):
        return [], [], []

    balance_by_period = _column_index(balance_frame)
    cashflow_by_period = _column_index(cashflow_frame)

    income: list[dict] = []
    balance: list[dict] = []
    cashflow: list[dict] = []

    for position, stamp in enumerate(income_frame.columns):
        if len(income) >= years:
            break

        row = _income_row(income_frame, position)
        if row.get("revenue") is None:
            continue  # a period Yahoo padded with NaN

        income.append(row)

        balance_position = balance_by_period.get(stamp)
        balance.append(_balance_row(balance_frame, balance_position)
                       if balance_position is not None else {})

        cashflow_position = cashflow_by_period.get(stamp)
        cashflow.append(_cashflow_row(cashflow_frame, cashflow_position)
                        if cashflow_position is not None else {})

    return income, balance, cashflow


def _check_currency(info: dict, ticker: str) -> None:
    """
    Refuse a listing whose statements and quote are in different currencies.

    Yahoo's wider coverage brings in many foreign listings and ADRs, and it
    reports their financials in the home currency while quoting the share
    price in the listing currency. TSM is the clearest case: revenue of
    3.8 trillion TWD against a $435 USD price. Dividing a TWD equity value by
    the share count and comparing it to a USD price produced a valuation
    809% above the market - a confident, meaningless number.

    Conversion is deliberately not attempted here. Every derived assumption
    is a ratio and so is currency-neutral, which means a single spot rate
    would in principle be enough for the level figures - but that is a
    modelling decision with its own error, and guessing at it silently is
    exactly the failure this guard exists to prevent.
    """
    quote_currency = (info.get("currency") or "").upper()
    financial_currency = (info.get("financialCurrency") or "").upper()

    if not quote_currency or not financial_currency:
        return  # nothing to compare; let the valuation proceed
    if quote_currency == financial_currency:
        return

    raise UnsupportedListingError(
        f"{ticker} reports its financial statements in {financial_currency} "
        f"but its shares trade in {quote_currency}.\n"
        "  Valuing it would mean dividing a "
        f"{financial_currency} equity value by the share count and comparing "
        f"the result to a {quote_currency} price, which produces a "
        "meaningless figure.\n"
        "  Currency conversion is not implemented, so this listing is "
        "refused rather than valued incorrectly."
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Talking to Yahoo like a browser
#
# Yahoo does not serve its finance API to anything that looks automated. It
# fingerprints the TLS handshake as well as the headers, so a plain requests
# Session with a spoofed User-Agent is still recognisable and still refused.
# curl_cffi reproduces a real Chrome handshake, which is what gets us served
# from a datacentre IP at all.
#
# The session is shared and reused: Yahoo issues a cookie and a crumb on first
# contact, and carrying them across requests means one handshake per process
# instead of one per ticker - both faster and far less likely to be throttled.
# ---------------------------------------------------------------------------

_IMPERSONATE = "chrome"

_session_lock = threading.Lock()
_session: Any = None


def _browser_session() -> Any:
    """The shared impersonating session, created on first use."""
    global _session
    with _session_lock:
        if _session is None:
            from curl_cffi import requests as _creq
            _session = _creq.Session(impersonate=_IMPERSONATE)
        return _session


def reset_session() -> None:
    """
    Drop the shared session so the next call starts a fresh handshake.

    Used after a block or a rate limit: the cookie and crumb we hold may have
    been invalidated, and reusing them just repeats the failure.
    """
    global _session
    with _session_lock:
        old, _session = _session, None
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"


def probe_symbol(ticker: str) -> tuple[str, str]:
    """
    Ask Yahoo whether *ticker* exists, via an endpoint that needs no crumb.

    This is what separates "no such company" from "Yahoo refused to talk to
    us". The distinction matters: an empty `info` dict is produced by BOTH,
    so believing it on its own turns every blocked request into a confident,
    wrong 404.

    Returns (verdict, detail) where verdict is one of:

      "found"      - Yahoo returned a match. The symbol is real.
      "absent"     - Yahoo answered normally and had no match. A true 404.
      "blocked"    - Yahoo refused or answered with something other than JSON.
      "unreachable"- the request itself failed.

    Only "absent" is a genuine not-found, and only because Yahoo answered
    with HTTP 200 and an empty result list - a positive statement that the
    symbol does not exist, rather than an absence of evidence.
    """
    try:
        response = _browser_session().get(
            _SEARCH_URL,
            # More than one, so the exact match is not crowded out of the
            # results by Yahoo's fuzzy near-misses.
            params={"q": ticker, "quotesCount": 8, "newsCount": 0},
            timeout=20,
        )
    except Exception as e:
        return "unreachable", f"{type(e).__name__}: {e}"

    code = response.status_code
    if code in (401, 403, 429) or code >= 500:
        return "blocked", f"HTTP {code}"

    if code != 200:
        return "blocked", f"HTTP {code}"

    try:
        payload = response.json()
    except Exception:
        # A consent interstitial or a CAPTCHA page - HTML where JSON belongs.
        return "blocked", "HTTP 200 but the body was not JSON"

    # The search is fuzzy: querying ZZZZ returns ZZZZIX, a Nasdaq test fund.
    # Accepting any match would call an unknown symbol "real" and downgrade a
    # legitimate 404 into a confusing upstream error, so the symbol has to
    # come back exactly. Yahoo writes it in upper case; _validate_ticker has
    # already upper-cased what we send.
    wanted = ticker.upper()
    for quote in payload.get("quotes") or []:
        if (quote.get("symbol") or "").upper() == wanted:
            return "found", f"exact match ({quote.get('quoteType') or 'unknown type'})"

    return "absent", "HTTP 200 with no exact match"


def endpoint_report(ticker: str) -> dict:
    """
    Raw status of each Yahoo endpoint the fetch depends on, from this machine.

    Yahoo does not refuse everything equally: the public search endpoint is
    served freely while the crumb-authenticated ones are withheld. Knowing
    which is which is the difference between "try a different header" and
    "this data source is not available from this IP", so the diagnosis is
    measured rather than assumed.
    """
    session = _browser_session()
    out: dict[str, str] = {}

    def check(name: str, url: str, **kwargs) -> None:
        try:
            r = session.get(url, timeout=20, **kwargs)
            body = (r.text or "")[:80].replace("\n", " ")
            out[name] = f"HTTP {r.status_code} len={len(r.text or '')} {body!r}"
        except Exception as e:
            out[name] = f"EXC {type(e).__name__}: {e}"

    check("search (no crumb needed)", _SEARCH_URL,
          params={"q": ticker, "quotesCount": 1, "newsCount": 0})
    check("getcrumb", "https://query2.finance.yahoo.com/v1/test/getcrumb")
    check("chart (no crumb needed)",
          f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
          params={"range": "5d", "interval": "1d"})
    check("quoteSummary (crumb required)",
          f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
          params={"modules": "price"})
    # Does yfinance's own statement path work when .info does not? This is
    # the difference between "the data source is unusable here" and "only
    # the profile call is blocked".
    try:
        handle = yf.Ticker(ticker, session=session)
        frame = handle.income_stmt
        rows = 0 if frame is None or getattr(frame, "empty", True) else len(frame.columns)
        out["yfinance income_stmt"] = f"{rows} period(s) returned"
    except Exception as e:
        out["yfinance income_stmt"] = f"EXC {type(e).__name__}: {e}"

    check("fundamentals-timeseries (crumb required)",
          "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/"
          f"finance/timeseries/{ticker}",
          params={"symbol": ticker, "type": "annualTotalRevenue",
                  "period1": 0, "period2": 9999999999})
    return out


def _validate_ticker(ticker: str) -> str:
    ticker = (ticker or "").upper().strip()
    if not ticker:
        raise TickerNotFoundError("Ticker cannot be empty.")
    if not ticker.replace(".", "").replace("-", "").isalnum():
        raise TickerNotFoundError(
            f"'{ticker}' does not look like a valid ticker symbol.")
    return ticker


_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = (0.75, 2.0)  # seconds before the 2nd and 3rd attempts


def _is_rate_limit(e: Exception) -> bool:
    if isinstance(e, YFRateLimitError):
        return True
    message = str(e).lower()
    return ("too many requests" in message
            or "rate limit" in message
            or "429" in message)


def _call(description: str, ticker: str, fn: Callable[[], Any]) -> Any:
    """
    Run a yfinance call, translating its failure modes into ours.

    Retried with backoff. Yahoo's refusals from a datacentre IP are often
    intermittent - the same request a second later succeeds - so a single
    attempt reports a hard failure for what is frequently a blip. The session
    is reset between attempts on a throttle, since a stale crumb will
    otherwise fail identically however many times we retry.
    """
    last: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt == _RETRY_ATTEMPTS - 1:
                break
            if _is_rate_limit(e):
                reset_session()
            time.sleep(_RETRY_BACKOFF[attempt])

    assert last is not None
    if _is_rate_limit(last):
        raise RateLimitedError(
            "Yahoo Finance is rate-limiting requests right now. "
            "Wait a moment and try again."
        ) from last
    raise DataUnavailableError(
        f"Yahoo Finance did not return {description} for '{ticker}': {last}"
    ) from last


# Quote types that genuinely publish no income statement. An allow-list
# rather than "anything that is not EQUITY", because Yahoo puts arbitrary
# values here in a degraded response and an unrecognised one must not be
# mistaken for a fund.
_NON_COMPANY_QUOTE_TYPES = frozenset({
    "ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY",
    "FUTURE", "OPTION", "ECNQUOTE",
})


def _is_empty_frame(frame) -> bool:
    return frame is None or bool(getattr(frame, "empty", True))


def _fetch_statement(description: str, ticker: str, attribute: str,
                     handle: Any, required: bool = False) -> Any:
    """
    Fetch one statement, retrying while it comes back empty.

    A throttled statement request is not an error in yfinance's eyes: it
    returns an empty DataFrame and no exception. _call() only retries on
    exceptions, so without this the commonest cloud failure got exactly one
    attempt and was reported as "no financial statements" - which reads to
    the user as though the company files none.

    Each retry builds a fresh Ticker on a fresh session. Both matter: a
    Ticker memoises the frame it fetched, so re-reading the same handle
    would return the cached empty result without asking Yahoo again, and the
    old session carries the cookie that was just refused.

    `required` marks the income statement, the one the model cannot proceed
    without. The balance sheet and cash flow are fetched once and allowed to
    stay empty - _aligned_rows() already contributes empty rows for a period
    a statement does not cover, and retrying them would triple the request
    volume that provoked the throttling in the first place.
    """
    frame = _call(description, ticker, lambda: getattr(handle, attribute))
    if not _is_empty_frame(frame) or not required:
        return frame

    for delay in _RETRY_BACKOFF:
        if delay:
            time.sleep(delay)
        reset_session()
        fresh = yf.Ticker(ticker, session=_browser_session())
        frame = _call(description, ticker, lambda: getattr(fresh, attribute))
        if not _is_empty_frame(frame):
            logging.getLogger(__name__).info(
                "%s: %s arrived on retry", ticker, description)
            return frame

    return frame


def _looks_like_a_real_quote(info: dict) -> bool:
    """
    Is this a real security, or Yahoo's empty-response stub?

    An unknown symbol does not raise. yfinance returns a dict with a single
    'trailingPegRatio' key, having logged a 404. A genuine quote carries a
    symbol and a quote type.
    """
    if not info or len(info) <= 3:
        return False
    return bool(info.get("symbol") or info.get("quoteType")
                or info.get("longName") or info.get("shortName"))


_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


def crumb_free_profile(ticker: str) -> dict | None:
    """
    Build a profile from endpoints that need no crumb, or None if that fails.

    Yahoo mints the crumb that authenticates its quote endpoint only rarely
    for a datacentre IP - measured at roughly one attempt in fourteen from
    Render - while serving the chart, search and fundamentals endpoints
    normally. Since the financial statements arrive over the unauthenticated
    path, the profile is the only thing standing between a cloud deployment
    and a working valuation, so it is rebuilt from what is actually served.

    What is recoverable: price, currency, exchange, company name, quote type.
    What is not: beta, sector, industry, marketCap and sharesOutstanding.

    Beta's absence is the one with teeth - it drives the CAPM cost of equity,
    and its loss makes WACC fall back to the documented global default. That
    is reported in the valuation's assumption sourcing rather than hidden,
    and it is a far better outcome than refusing to value the company. The
    share count is unaffected: it comes from the income statement's diluted
    average, which the statements carry.
    """
    session = _browser_session()

    name = quote_type = None
    try:
        found = session.get(
            _SEARCH_URL,
            params={"q": ticker, "quotesCount": 8, "newsCount": 0},
            timeout=20)
        if found.status_code == 200:
            wanted = ticker.upper()
            for quote in found.json().get("quotes") or []:
                # Exact only - the search is fuzzy, and naming a company
                # after a near-miss would be worse than leaving it blank.
                if (quote.get("symbol") or "").upper() == wanted:
                    name = quote.get("shortname") or quote.get("longname")
                    quote_type = quote.get("quoteType")
                    break
    except Exception:
        pass

    try:
        chart = session.get(_CHART_URL.format(ticker),
                            params={"range": "1d", "interval": "1d"},
                            timeout=20)
        if chart.status_code != 200:
            return None
        meta = chart.json()["chart"]["result"][0]["meta"]
    except Exception:
        return None

    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    if not price:
        return None

    return {
        "symbol": meta.get("symbol") or ticker,
        "companyName": meta.get("longName") or meta.get("shortName")
                       or name or ticker,
        "sector": None,
        "industry": None,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        # Filled in by _with_computed_beta() below, from the chart endpoint.
        "beta": None,
        "marketCap": None,
        "price": float(price),
        "sharesOutstanding": None,
        "currency": meta.get("currency"),
        # Unknown here. _check_currency() treats an unknown reporting
        # currency as "do not block", which is the existing behaviour.
        "financialCurrency": None,
        "quoteType": quote_type or "EQUITY",
        "profileSource": "crumb-free fallback (chart + search); "
                         "sector unavailable",
    }


# ---------------------------------------------------------------------------
# Beta, computed rather than quoted
#
# Yahoo publishes a beta, but only through the quote endpoint it will not
# authenticate for a datacentre IP. Losing it meant WACC silently fell back
# to a market beta of 1.0 - and worse, inconsistently: on the rare request
# where the crumb happened to mint, the same company came back with a real
# beta and a materially different valuation.
#
# Computing it from the chart endpoint, which Yahoo does serve us, makes it
# both available and deterministic. The convention matches Yahoo's own -
# five years of monthly returns against the S&P 500 - and reproduces their
# published figure to a mean absolute difference of 0.04 across a
# fifteen-ticker check, so the number is not a different quantity wearing
# the same name.
# ---------------------------------------------------------------------------

MARKET_INDEX = "^GSPC"

# Below this many overlapping months the estimate is too noisy to trust; a
# company listed for under two years does not have a meaningful five-year
# beta, and pretending otherwise is worse than admitting the default.
_MIN_BETA_MONTHS = 24


def _monthly_closes(symbol: str) -> list[tuple[int, float]]:
    """Five years of monthly closes as (timestamp, price), oldest first."""
    response = _browser_session().get(
        _CHART_URL.format(symbol),
        params={"range": "5y", "interval": "1mo"},
        timeout=30,
    )
    if response.status_code != 200:
        raise DataUnavailableError(
            f"chart endpoint returned HTTP {response.status_code} for {symbol}")

    result = response.json()["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}

    # Adjusted closes where available: dividends and splits are returns to
    # the holder, and a raw split would otherwise read as a -50% month.
    adjusted = indicators.get("adjclose") or []
    series = None
    if adjusted and adjusted[0].get("adjclose"):
        series = adjusted[0]["adjclose"]
    else:
        quote = (indicators.get("quote") or [{}])[0]
        series = quote.get("close")

    return [(t, float(v)) for t, v in zip(stamps, series or [])
            if v is not None]


def _returns(closes: Sequence[tuple[int, float]]) -> dict[int, float]:
    """Period-over-period returns, keyed by the closing timestamp."""
    return {later: (end / start) - 1.0
            for (_, start), (later, end) in zip(closes, closes[1:])
            if start}


def _market_returns() -> dict[int, float]:
    cached = _market_cache.get(MARKET_INDEX)
    if cached is not None:
        return cached
    returns = _returns(_monthly_closes(MARKET_INDEX))
    _market_cache.put(MARKET_INDEX, returns)
    return returns


def fetch_beta(ticker: str) -> tuple[float, str] | None:
    """
    Beta of *ticker* against the S&P 500, or None if it cannot be computed.

    Returns (beta, source_description). Cached, and the market series is
    cached separately so a batch of valuations costs one index fetch.
    """
    cached = _beta_cache.get(ticker)
    if cached is not None:
        return cached

    try:
        market = _market_returns()
        stock = _returns(_monthly_closes(ticker))
    except Exception:
        return None

    # Only months both series cover, so a mismatched calendar cannot pair a
    # company's return with the wrong month of the index.
    months = sorted(set(stock) & set(market))
    if len(months) < _MIN_BETA_MONTHS:
        return None

    xs = [market[m] for m in months]
    ys = [stock[m] for m in months]
    n = len(months)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    variance = sum((x - mean_x) ** 2 for x in xs) / (n - 1)
    if variance <= 0:
        return None
    covariance = sum((x - mean_x) * (y - mean_y)
                     for x, y in zip(xs, ys)) / (n - 1)

    beta = covariance / variance
    if beta != beta:  # NaN
        return None

    result = (beta, f"computed from {n} monthly returns vs {MARKET_INDEX} "
                    f"(5y), matching Yahoo's convention")
    _beta_cache.put(ticker, result)
    return result


def _empty_quote_error(ticker: str) -> MarketDataError:
    """
    Decide what an empty profile for *ticker* actually means.

    Yahoo returns an empty dict both for a symbol that does not exist and for
    a request it has decided not to serve. Guessing "not found" is the more
    damaging error of the two: it tells the user their perfectly real ticker
    is unknown, and invites them to correct spelling that was never wrong.
    So we corroborate against an endpoint that answers without a crumb.
    """
    verdict, detail = probe_symbol(ticker)

    if verdict == "found":
        # Yahoo knows the symbol but would not give us its profile. That is a
        # fault on the data source's side, not a missing company.
        reset_session()
        return DataUnavailableError(
            f"Yahoo Finance recognises '{ticker}' but returned no data for it "
            f"just now ({detail}).\n"
            "  This is a temporary upstream problem, not an unknown ticker. "
            "Try again shortly."
        )

    if verdict == "blocked":
        reset_session()
        return RateLimitedError(
            f"Yahoo Finance is refusing requests from this server right now "
            f"({detail}).\n"
            "  This is throttling, not a problem with the ticker. Try again "
            "shortly."
        )

    if verdict == "unreachable":
        reset_session()
        return DataUnavailableError(
            f"Could not reach Yahoo Finance to look up '{ticker}' ({detail}).\n"
            "  Try again shortly."
        )

    # "absent": Yahoo answered normally and positively had no such symbol.
    return TickerNotFoundError(
        f"No security found for '{ticker}' on Yahoo Finance.\n"
        "  Check the spelling. Delisted companies and some foreign "
        "listings are not covered."
    )


def _profile_from(info: dict, ticker: str) -> dict:
    price = (info.get("currentPrice")
             or info.get("regularMarketPrice")
             or info.get("previousClose")
             or info.get("regularMarketPreviousClose"))

    return {
        "symbol": info.get("symbol") or ticker,
        "companyName": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "beta": info.get("beta"),
        "marketCap": info.get("marketCap"),
        "price": price,
        "sharesOutstanding": info.get("sharesOutstanding")
                             or info.get("impliedSharesOutstanding"),
        "currency": info.get("currency"),
        "financialCurrency": info.get("financialCurrency"),
        "quoteType": info.get("quoteType"),
    }


def _with_computed_beta(profile: dict, ticker: str) -> None:
    """
    Ensure *profile* carries a beta, computed from price history where possible.

    Mutates the profile in place and records which path produced the number,
    so the valuation can say where its beta came from instead of presenting a
    quoted figure and a computed one as though they were the same thing.

    The computed beta is preferred even when Yahoo quotes one, and that is
    deliberate. The quote endpoint answers only when the crumb happens to
    mint, so trusting it first would leave the beta - and the valuation -
    depending on which endpoint Yahoo felt like serving: AAPL came back 1.085
    quoted and 1.088 computed on the same afternoon. A small difference, but
    an unpredictable one, and unpredictability is the whole complaint. The
    chart endpoint is served consistently, so preferring it makes the number
    the same on every request.

    The two agree closely - a mean absolute difference of 0.037 across a
    fifteen-ticker check against Yahoo's published figure - so this buys
    determinism at no real cost in accuracy. A quoted beta is still used if
    the computation fails, and only then does the default apply.
    """
    computed = fetch_beta(ticker)
    if computed is not None:
        profile["beta"], profile["betaSource"] = computed
        return

    quoted = profile.get("beta")
    if isinstance(quoted, (int, float)) and quoted == quoted and quoted != 0:
        profile["betaSource"] = (
            "quoted by Yahoo; price history was unavailable to compute one")
        return

    profile["beta"] = None
    profile["betaSource"] = (
        "unavailable - neither computable from price history nor quoted")


def fetch_financials(ticker: str,
                     years: int = DEFAULT_HISTORY_YEARS) -> CompanyFinancials:
    """
    Fetch profile plus `years` of annual statements for *ticker*.

    Cached, so repeated valuations of the same company during a session make
    one round trip rather than many.
    """
    ticker = _validate_ticker(ticker)
    key = (ticker, years)

    cached = _financials_cache.get(key)
    if cached is not None:
        return cached

    # Re-raise a recent failure rather than hammering Yahoo with a request we
    # already know fails.
    failure = _failure_cache.get(key)
    if failure is not None:
        raise failure

    try:
        handle = yf.Ticker(ticker, session=_browser_session())
        info = _call("a company profile", ticker, lambda: handle.info) or {}

        profile: dict | None = None
        quote_ok = _looks_like_a_real_quote(info)

        if quote_ok:
            _check_currency(info, ticker)
            profile = _profile_from(info, ticker)
        else:
            # An empty profile is ambiguous - an unknown symbol and a refused
            # request look identical here. Ask Yahoo directly before deciding,
            # so a block is never reported as a missing company.
            error = _empty_quote_error(ticker)

            if isinstance(error, TickerNotFoundError):
                raise error

            # The symbol is real; Yahoo simply would not authenticate the
            # quote endpoint. The statements come over a path that needs no
            # crumb, so rebuild the profile from what is served rather than
            # failing a valuation we can very nearly complete.
            profile = crumb_free_profile(ticker)
            if profile is None:
                raise error
            logging.getLogger(__name__).warning(
                "%s: quote endpoint unavailable, using crumb-free profile",
                ticker)

        _with_computed_beta(profile, ticker)

        # Throttled statement requests do not raise - yfinance hands back an
        # empty DataFrame. The exception retry above therefore never fires
        # for the single most common cloud failure mode, so emptiness is
        # retried explicitly here.
        income_frame = _fetch_statement(
            "an income statement", ticker, "income_stmt", handle, required=True)
        balance_frame = _fetch_statement(
            "a balance sheet", ticker, "balance_sheet", handle)
        cashflow_frame = _fetch_statement(
            "a cash flow statement", ticker, "cashflow", handle)

        income, balance, cashflow = _aligned_rows(
            income_frame, balance_frame, cashflow_frame, years)

        if not income and income_frame is not None \
                and not getattr(income_frame, "empty", True):
            # Statements came back, but no period carried revenue - so there
            # is nothing to project, and saying "no statements" would be wrong.
            raise DataUnavailableError(
                f"Yahoo Finance returned an income statement for '{ticker}' "
                "with no revenue in any period, so there is nothing to "
                "project from."
            )

        if not income:
            quote_type = (info.get("quoteType") or "").upper()
            # Only a type we positively recognise as not-a-company justifies a
            # 404. Yahoo answers "NONE" - and other junk - in a degraded
            # response, and treating anything that merely is not "EQUITY" as
            # a fund produced the memorable "'HOLX' is a none, not a company"
            # for a perfectly ordinary listed business during a throttle.
            if quote_type in _NON_COMPANY_QUOTE_TYPES:
                raise TickerNotFoundError(
                    f"'{ticker}' is a {quote_type.lower()}, not a company.\n"
                    "  ETFs and funds publish no income statement, so there "
                    "is nothing to build a discounted cash flow from."
                )
            # The profile loaded, so the symbol is real and Yahoo is talking
            # to us - but the statement endpoints came back empty. For a
            # listed company that is far more often throttling of those
            # heavier endpoints than a genuine absence of filings, so report
            # it as transient rather than as an unknown ticker.
            raise DataUnavailableError(
                f"Yahoo Finance returned no financial statements for '{ticker}' "
                "just now.\n"
                "  The symbol is valid and quoting, so this is most likely a "
                "temporary upstream limit. Try again shortly - if it persists, "
                "the company may be newly listed or not covered."
            )

        financials = CompanyFinancials(
            ticker=ticker,
            profile=profile,
            income=income,
            balance=balance,
            cashflow=cashflow,
        )
    except MarketDataError as e:
        # Cache the refusal briefly; transient problems get a short TTL so a
        # retry is still possible soon.
        _failure_cache.put(key, e)
        raise

    _financials_cache.put(key, financials)
    return financials


def base_year_from(fin: CompanyFinancials) -> tuple[BaseYearData, dict]:
    """Map the most recent fiscal year of *fin* onto BaseYearData."""
    ticker = fin.ticker
    if not fin.income:
        raise TickerNotFoundError(f"No income statement available for '{ticker}'.")

    income = fin.income[0]
    balance = fin.balance[0] if fin.balance else {}

    revenue = income.get("revenue")
    if revenue is None:
        raise DataUnavailableError(
            f"Yahoo Finance did not report revenue for '{ticker}', which the "
            "model requires."
        )
    revenue_mm = float(revenue) / 1e6

    price = fin.profile.get("price")
    if price is None:
        raise DataUnavailableError(
            f"Yahoo Finance did not return a current price for '{ticker}'."
        )
    current_price = float(price)

    fiscal_year = income.get("fiscalYear", "?")
    period_end = income.get("date", "?")

    # See the module docstring for why leases are excluded and long-term
    # investments are included.
    total_debt_mm = total_debt_of(balance) / 1e6
    cash_mm = (num(balance, "cashAndCashEquivalents")
               + num(balance, "totalInvestments")) / 1e6

    balance_year = balance.get("fiscalYear", "?")
    if balance_year != fiscal_year:
        period_end = f"{period_end} (balance sheet FY{balance_year})"

    shares_mm, shares_source = _resolve_shares(fin, current_price)

    base = BaseYearData(
        revenue=revenue_mm,
        total_debt=total_debt_mm,
        cash=cash_mm,
        shares=shares_mm,
        current_price=current_price,
    )
    meta = {
        "ticker": ticker,
        "company_name": fin.company_name,
        "exchange": fin.exchange,
        "sector": fin.sector,
        "industry": fin.industry,
        "fiscal_year": fiscal_year,
        "period_end": period_end,
        "revenue_source": "income statement: Total Revenue",
        "debt_source": "balance sheet: Current Debt + Long Term Debt "
                       "(excl. capital leases)",
        "cash_source": "balance sheet: Cash And Cash Equivalents + "
                       "Other Short Term Investments + Investments And Advances",
        "shares_source": shares_source,
        "price_source": "Yahoo Finance quote (live)",
    }
    return base, meta


def _resolve_shares(fin: CompanyFinancials, price: float) -> tuple[float, str]:
    """
    Resolve the share count (in millions) used to divide equity value.

    WHY DILUTED
    -----------
    A DCF values the entire equity claim. In-the-money options, RSUs and
    convertibles are real claims that will become shares before the projected
    cash flows are all earned, so dividing by a basic count spreads value over
    too few shares and overstates value per share. Diluted is the conservative
    and conventional denominator.

    The fallbacks are point-in-time rather than weighted averages, and say so,
    because the two measure slightly different moments.
    """
    income = fin.income[0] if fin.income else {}

    diluted = num(income, "weightedAverageShsOutDil")
    if diluted > 0:
        return diluted / 1e6, "income statement: Diluted Average Shares"

    basic = num(income, "weightedAverageShsOut")
    if basic > 0:
        return (basic / 1e6,
                "income statement: Basic Average Shares (diluted unavailable)")

    outstanding = fin.profile.get("sharesOutstanding")
    if outstanding:
        return (float(outstanding) / 1e6,
                "quote: sharesOutstanding (point-in-time, no weighted average)")

    market_cap = fin.profile.get("marketCap")
    if market_cap and price > 0:
        return (float(market_cap) / price / 1e6,
                "derived from marketCap / price (last resort)")

    raise DataUnavailableError(
        f"Could not determine a share count for '{fin.ticker}'. Without one "
        "the model cannot produce a per-share value."
    )


# ---------------------------------------------------------------------------
# Risk-free rate
# ---------------------------------------------------------------------------

# Yahoo publishes Treasury yields as index quotes, in percent.
_TREASURY_SYMBOLS = {
    "month3": ("^IRX", "13-week"),
    "year5": ("^FVX", "5-year"),
    "year10": ("^TNX", "10-year"),
    "year30": ("^TYX", "30-year"),
}


def fetch_risk_free_rate(tenor: str = "year10") -> tuple[float, str] | None:
    """
    Current Treasury yield for *tenor* as a decimal (0.0484 = 4.84%).

    Returns (rate, source_description), or None when unavailable - callers
    fall back to a documented static default rather than failing a valuation
    because a rate lookup did.
    """
    symbol_and_label = _TREASURY_SYMBOLS.get(tenor)
    if symbol_and_label is None:
        return None
    symbol, label = symbol_and_label

    cached = _rate_cache.get(tenor)
    if cached is not None:
        return cached

    rate: float | None = None
    as_of = date.today().isoformat()

    try:
        handle = yf.Ticker(symbol, session=_browser_session())
        history = handle.history(period="5d")
        if history is not None and not history.empty and "Close" in history:
            rate = float(history["Close"].iloc[-1])
            stamp = history.index[-1]
            as_of = str(stamp.date() if hasattr(stamp, "date") else stamp)
        else:
            quote = handle.info or {}
            candidate = (quote.get("regularMarketPrice")
                         or quote.get("previousClose"))
            if candidate is not None:
                rate = float(candidate)
    except Exception:
        return None

    if rate is None or rate != rate:
        return None

    rate /= 100.0  # quoted in percent
    if not 0.0 < rate < 0.25:  # a Treasury yield outside this band is not real
        return None

    result = (rate, f"US Treasury {label} ({symbol}) as of {as_of}")
    _rate_cache.put(tenor, result)
    return result


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def data_source_name() -> str:
    return "Yahoo Finance (yfinance)"


def requires_api_key() -> bool:
    """Yahoo needs no credentials. Kept so /health can state this plainly."""
    return False


def fetch_with_meta(ticker: str) -> tuple[BaseYearData, dict]:
    """Fetch base-year inputs plus provenance metadata for *ticker*."""
    return base_year_from(fetch_financials(ticker))


def fetch_base_year_data(ticker: str) -> BaseYearData:
    """Convenience wrapper returning just the BaseYearData."""
    base, _ = fetch_with_meta(ticker)
    return base
