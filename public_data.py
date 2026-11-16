"""Official, citable sources for the two market inputs that have one.

Almost everything this model reads is a company's own filing, fetched from a
commercial aggregator. Two inputs are different: the risk-free rate and the
exchange rate are published by the institutions that set or observe them,
free, for anyone, with a licence that says so in writing. Taking them from an
aggregator instead meant the valuation rested on a redistribution of a public
number - no faster to the truth, impossible to cite, and covered by terms
that prohibit automated collection.

So they come from the primary sources:

  * The **risk-free rate** from the US Treasury's Daily Treasury Par Yield
    Curve Rates, which is the curve every textbook means by "the Treasury
    yield". Works of the US government carry no copyright (17 U.S.C. 105),
    and Treasury states its data is "offered free, without restriction, and
    available to copy, adapt, redistribute, or otherwise use for
    non-commercial or commercial purposes".

  * The **exchange rate** from the European Central Bank's euro foreign
    exchange reference rates. The ECB's copyright terms permit free use of
    information obtained from its site on three conditions, all of which bind
    this file: the information "must appear accurately and the ECB must be
    cited as the source"; if it is ever incorporated in something sold,
    buyers must be told it is available free from the ECB; and "if the
    information is modified by the user (e.g. by seasonal adjustment of
    statistical data or calculation of growth rates) this must be stated
    explicitly". Every rate this module returns for a pair that does not
    involve the euro IS modified - it is a cross-rate computed from two ECB
    quotes - so the provenance it hands back says so, and the app repeats it.

Neither source replaces the aggregator outright. Both are wrapped so that a
failure, or a currency the ECB does not quote, falls through to the previous
behaviour rather than failing a valuation. The ECB publishes 29 currencies
and the New Taiwan dollar is not among them, so that fall-through is a live
path, not a theoretical one.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import threading
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Who we say we are
# ---------------------------------------------------------------------------

# The SEC requires an automated client to declare itself with a contact
# address and will refuse one that does not. Treasury and the ECB require
# nothing, but a request that names itself is the difference between a polite
# client and an anonymous one, and costs nothing.
#
# The address is read from the environment and never hard-coded. It is the
# operator's address to give: baking one in would publish somebody's email in
# a public repository and, worse, name a stranger to a regulator as the
# responsible contact for this traffic. With nothing set, the agent names the
# project and omits the contact.
PUBLIC_DATA_CONTACT_VAR = "PUBLIC_DATA_CONTACT"
_AGENT_BASE = ("dcf-valuation-app/1.0 "
               "(+https://github.com/DCFValaution/dcf-valuation-app)")


def public_data_contact() -> str:
    """The operator's contact address, or "" when none is configured."""
    return (os.environ.get(PUBLIC_DATA_CONTACT_VAR) or "").strip()


def public_user_agent() -> str:
    """A User-Agent that names the client and, when known, who to contact."""
    contact = public_data_contact()
    return f"{_AGENT_BASE} ({contact})" if contact else _AGENT_BASE


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

# A plain session, not the Chrome-impersonating one market_data uses. That
# session exists because Yahoo refuses anything that looks automated;
# Treasury and the ECB serve a declared client happily, and pretending to be
# a browser to a public-data site would be both unnecessary and dishonest
# about who is asking.
#
# curl_cffi rather than urllib because it ships its own certificate bundle.
# urllib leans on the host's trust store, which on a developer Windows box
# does not have the ECB's issuer: the fetch fails locally and works on the
# server, which is the worst shape of bug to debug.
_session_lock = threading.Lock()
_session: Any = None


def _plain_session() -> Any:
    """The shared session for public-data sources, created on first use."""
    global _session
    with _session_lock:
        if _session is None:
            from curl_cffi import requests as _creq
            _session = _creq.Session()
        return _session


def reset_session() -> None:
    """Drop the shared session. Used by tests and after a transport failure."""
    global _session
    with _session_lock:
        _session = None


def _get(url: str, *, timeout: int = 20, agent: str | None = None
         ) -> str | None:
    """GET *url* as text, or None on any failure.

    *agent* overrides the declared User-Agent. The SEC is the reason it
    exists: it serves a string that looks like a contact and refuses one
    that looks like a crawler, so that caller has to say who it is in the
    SEC's own shape rather than this module's.
    """
    try:
        response = _plain_session().get(
            url, headers={"User-Agent": agent or public_user_agent()},
            timeout=timeout)
    except Exception as exc:
        log.warning("public-data fetch of %s failed: %s", url, exc)
        return None
    if response.status_code != 200:
        log.warning("public-data fetch of %s returned %s",
                    url, response.status_code)
        return None
    return response.text


# ---------------------------------------------------------------------------
# US Treasury par yield curve
# ---------------------------------------------------------------------------

_TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
    "/daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
    "&page&_format=csv"
)

# The tenors this model asks for, mapped to the column headings Treasury
# publishes them under. Deliberately a subset: the curve carries fourteen
# points and the model has a use for four.
#
# One convention note, because it shows up as a real difference rather than
# rounding. This curve is quoted on a coupon-equivalent (bond-equivalent)
# basis at every point. The aggregator's short-bill index, ^IRX, is quoted on
# a bank-discount basis, which always reads lower - 4.122% against 4.32% for
# the same day in November 2026, a gap of twenty basis points. The par yield
# is the right one here: CAPM adds a risk premium to a yield, and the premium
# is measured on a yield basis too. Only the ten-year point is used in
# production, so the difference is visible in the other three rather than
# priced into anything.
TREASURY_COLUMNS = {
    "month3": ("3 Mo", "3-month"),
    "year5": ("5 Yr", "5-year"),
    "year10": ("10 Yr", "10-year"),
    "year30": ("30 Yr", "30-year"),
}

# A par yield outside this band is not a Treasury yield; it is a parse error
# wearing one's clothes. The same band the previous implementation used.
SANE_YIELD = (0.0, 0.25)


def _parse_treasury(body: str, column: str) -> tuple[float, str] | None:
    """The newest non-blank value of *column*, as (percent, iso date)."""
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows or column not in rows[0]:
        return None
    # Treasury serves newest first, but that is a property of their page
    # rather than a promise, so the rows are sorted rather than trusted.
    dated: list[tuple[date, str]] = []
    for row in rows:
        stamp, cell = row.get("Date"), (row.get(column) or "").strip()
        if not stamp or not cell:
            # A blank cell is normal rather than broken: the 20-year point
            # was not published between 1987 and 1993, nor the 30-year
            # between 2002 and 2006.
            continue
        try:
            month, day, year = (int(part) for part in stamp.split("/"))
            dated.append((date(year, month, day), cell))
        except (TypeError, ValueError):
            continue
    if not dated:
        return None
    when, cell = max(dated)
    try:
        return float(cell), when.isoformat()
    except ValueError:
        return None


def treasury_par_yield(tenor: str, *, today: date | None = None
                       ) -> tuple[float, str] | None:
    """
    Latest published par yield for *tenor* as a decimal, with a source line.

    Returns (rate, description) or None. Two calendar years are tried: in the
    first days of January the new year's file exists but is empty, and a
    valuation on 2 January should use 31 December's curve rather than fall
    back to a static default.
    """
    mapped = TREASURY_COLUMNS.get(tenor)
    if mapped is None:
        return None
    column, label = mapped

    year = (today or date.today()).year
    for candidate in (year, year - 1):
        body = _get(_TREASURY_CSV.format(year=candidate))
        if not body:
            continue
        parsed = _parse_treasury(body, column)
        if parsed is None:
            continue
        percent, as_of = parsed
        rate = percent / 100.0
        if not SANE_YIELD[0] < rate < SANE_YIELD[1]:
            log.warning("Treasury %s par yield of %s%% is outside the sane "
                        "band; refusing it", tenor, percent)
            return None
        return rate, (f"US Treasury {label} par yield as of {as_of} "
                      f"(Daily Treasury Par Yield Curve Rates)")
    return None


# ---------------------------------------------------------------------------
# ECB euro foreign exchange reference rates
# ---------------------------------------------------------------------------

_ECB_DAILY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

_ECB_DATE_RE = re.compile(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]")
_ECB_RATE_RE = re.compile(
    r"<Cube\s+currency=['\"]([A-Za-z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]")

ECB_ATTRIBUTION = "European Central Bank euro foreign exchange reference rates"


def ecb_reference_rates() -> tuple[dict[str, float], str] | None:
    """
    The published table as {currency: units per 1 EUR}, with its date.

    The euro itself is included at exactly 1.0, which is not in the feed but
    is what the feed means: every quote is denominated per one euro, so the
    euro's own entry makes the cross-rate arithmetic below uniform instead of
    special-casing the base currency.
    """
    body = _get(_ECB_DAILY)
    if not body:
        return None
    stamp = _ECB_DATE_RE.search(body)
    rates: dict[str, float] = {"EUR": 1.0}
    for code, value in _ECB_RATE_RE.findall(body):
        try:
            rate = float(value)
        except ValueError:
            continue
        if rate > 0:
            rates[code.upper()] = rate
    # One entry means nothing was parsed but the euro this function supplied
    # itself, which is a changed feed format rather than a quiet euro-only
    # day: the ECB has never published a table without the dollar in it.
    if len(rates) < 2 or stamp is None:
        log.warning("ECB reference-rate feed parsed to %d rates; ignoring it",
                    len(rates))
        return None
    return rates, stamp.group(1)


def ecb_fx_rate(frm: str, to: str) -> dict | None:
    """
    Units of *to* per one unit of *frm* from the ECB table, or None.

    None has two meanings and the caller treats them alike: the feed was
    unavailable, or the ECB does not quote one of these currencies. The
    second is ordinary. The ECB publishes 29 currencies against the euro, and
    the New Taiwan dollar, the Saudi riyal and the UAE dirham are all absent,
    so any company reporting in one of those needs the fallback.

    Both quotes are per euro, so a pair that does not involve the euro is a
    cross-rate: a number the ECB did not publish, computed from two it did.
    The ECB's terms require that modification to be stated, so the returned
    provenance says which arithmetic produced the figure, and the app repeats
    it beside the valuation rather than leaving it buried here.
    """
    frm, to = (frm or "").upper(), (to or "").upper()
    if not frm or not to:
        return None

    table = ecb_reference_rates()
    if table is None:
        return None
    rates, as_of = table
    if frm not in rates or to not in rates:
        return None

    rate = rates[to] / rates[frm]
    if not rate > 0:
        return None

    if "EUR" in (frm, to):
        basis = f"published {ECB_ATTRIBUTION}"
    else:
        basis = (f"cross-rate computed from the EUR/{frm} and EUR/{to} "
                 f"{ECB_ATTRIBUTION}")

    return {
        "rate": rate,
        "pair": f"{frm}/{to}",
        "as_of": as_of,
        # Nothing to cross-check: the forward and inverse quotes the previous
        # implementation compared came from two separate requests to a source
        # that could serve a pair the wrong way round. One ECB table cannot
        # disagree with itself, so the check has no work to do here, and
        # reporting a drift of zero would overstate what was verified.
        "cross_check": None,
        "source": "ecb",
        "basis": basis,
    }
