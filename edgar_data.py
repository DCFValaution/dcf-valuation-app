"""A second opinion on the fundamentals, built from SEC EDGAR.

**Nothing in production calls this.** It is a shadow fetcher: it builds the
same `CompanyFinancials` object `market_data.fetch_financials()` builds, from
companies' own XBRL filings rather than from an aggregator, so the two can be
compared figure by figure before anything is switched over. The switch is a
later stage and a separate decision; this stage is for finding out what the
switch would do.

Why bother. Every statement figure the model uses today comes from an
aggregator whose terms prohibit automated collection, which is the single
blocker on a public release (see `SOURCES.md`). EDGAR is the primary source
those figures are derived from, is public domain, and is explicitly open to
automated access on one condition - that the client says who it is.

What EDGAR cannot supply, and why this is a shadow rather than a replacement:

  * **No prices.** No market cap, no beta, no price history. Those need
    their own source, licensed separately.
  * **No sector or industry label** in a form the router can use. EDGAR
    carries an SIC code, which is not the aggregator's taxonomy.
  * **Annual only, for foreign private issuers.** A 20-F filer files once a
    year, so there are no quarterly facts to build a trailing-twelve-month
    base from. For those companies the base year is the latest annual period
    and that is the best that exists.

Three further things are different in kind rather than in quality, and the
comparison harness classifies them rather than calling them errors:

  * **As filed, including restatements.** Where a figure was restated, EDGAR
    holds both and this module takes the most recently filed. The aggregator
    sometimes holds the original.
  * **Company-chosen tags.** Two companies reporting the same thing may tag
    it differently; revenue alone has four plausible tags in current US
    GAAP. The chains below are ordered, and a wrong order is a bug in this
    file rather than a difference between the sources.
  * **No normalisation.** The aggregator computes lines no filer reports -
    "Tax Rate For Calcs", "Reconciled Depreciation", a single "Total Debt".
    EDGAR has only what was filed, so some of those have to be assembled
    here, and some cannot be.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import zipfile
from datetime import date, datetime
from typing import Any, Iterable

from market_data import CompanyFinancials
from public_data import _get, public_data_contact, public_user_agent

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The SEC's one condition
# ---------------------------------------------------------------------------

class EdgarNotConfigured(RuntimeError):
    """Raised when the SEC's declared-client requirement is not met."""


# Measured, not assumed. The SEC serves a request whose User-Agent looks like
# a contact ("Company Name admin@company.com", their own documented example)
# and returns 403 to one that looks like a crawler - our project string,
# "dcf-valuation-app/1.0 (+https://github.com/...)", is refused with "Your
# Request Originates from an Undeclared Automated Tool" even over an
# otherwise ordinary connection. So the contact address is not a courtesy
# here as it is for Treasury and the ECB: without one there is no data.
#
# It is still not hard-coded. See public_data for why.
SEC_RATE_LIMIT_PER_SECOND = 10
_rate_lock = threading.Lock()
_last_request = 0.0


def sec_user_agent() -> str:
    """The User-Agent in the shape the SEC serves, or raise if we cannot."""
    contact = public_data_contact()
    if not contact:
        raise EdgarNotConfigured(
            "The SEC requires an automated client to declare a contact "
            "address and refuses one that does not.\n"
            "  Set PUBLIC_DATA_CONTACT to the address the operator wants "
            "the SEC to use, e.g.\n"
            "    PUBLIC_DATA_CONTACT='Intrinsic ops@example.com'\n"
            "  It is deliberately not committed to this repository.")
    return contact if "@" in contact else f"{public_user_agent()} {contact}"


def _throttle() -> None:
    """Keep under the SEC's published ten requests a second, process-wide."""
    global _last_request
    with _rate_lock:
        gap = 1.0 / SEC_RATE_LIMIT_PER_SECOND
        wait = gap - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _fetch(url: str, *, timeout: int = 60) -> str | None:
    """GET *url* from the SEC, declared and throttled."""
    agent = sec_user_agent()
    _throttle()
    return _get(url, timeout=timeout, agent=agent)


# ---------------------------------------------------------------------------
# Ticker to CIK
# ---------------------------------------------------------------------------

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_cik_lock = threading.Lock()
_cik_index: dict[str, tuple[str, str]] | None = None


def _load_cik_index() -> dict[str, tuple[str, str]]:
    """{TICKER: (zero-padded CIK, registrant name)} from the SEC's own list."""
    global _cik_index
    with _cik_lock:
        if _cik_index is not None:
            return _cik_index
        body = _fetch(_TICKERS_URL)
        if not body:
            raise EdgarNotConfigured(
                "The SEC's ticker-to-CIK list could not be fetched.")
        index: dict[str, tuple[str, str]] = {}
        for entry in json.loads(body).values():
            ticker = str(entry.get("ticker") or "").upper()
            cik = entry.get("cik_str")
            if ticker and cik is not None:
                index[ticker] = (f"{int(cik):010d}",
                                 str(entry.get("title") or ""))
        _cik_index = index
        return index


# The SEC's ticker list holds one registrant per ticker, and after a
# holding-company reorganisation that registrant can be the new shell rather
# than the entity with the filing history. XOM is the case that found this:
# the list maps it to "ExxonMobil Holdings Corporation" (CIK 2115436), which
# carries 98 tags and not one annual revenue figure, while fifty years of
# accounts sit under "Exxon Mobil Corporation" (CIK 34088).
#
# There is no machine-readable link from a successor to its predecessor, so
# this is a hand-maintained map with a date and a reason per entry, and
# edgar_financials() refuses loudly rather than silently returning an empty
# company when a CIK has no annual history - which is how the next one gets
# found rather than shipped.
CIK_OVERRIDES: dict[str, tuple[str, str]] = {
    # ticker: (CIK, why)
    "XOM": ("0000034088",
            "the SEC's list points at ExxonMobil Holdings Corporation, a "
            "2026 holding company with no annual filings; the operating "
            "history is under Exxon Mobil Corporation (checked 16 Nov 2026)"),
}


def cik_for(ticker: str) -> tuple[str, str] | None:
    """The CIK and registrant name for *ticker*, or None if unlisted.

    The aggregator's symbol and the SEC's are not always the same string.
    A class suffix is written with a hyphen by the aggregator (BRK-B) and
    with neither separator nor at all by the SEC, so both spellings are
    tried before giving up.
    """
    index = _load_cik_index()
    override = CIK_OVERRIDES.get((ticker or "").upper().strip())
    if override is not None:
        cik, _why = override
        # The name still comes from the SEC's list where it has one, so the
        # override carries a CIK and not a second source of truth.
        for _, (listed_cik, name) in index.items():
            if listed_cik == cik:
                return cik, name
        return cik, ""
    wanted = (ticker or "").upper().strip()
    for candidate in (wanted, wanted.replace("-", "."),
                      wanted.replace("-", ""), wanted.split("-")[0]):
        if candidate in index:
            return index[candidate]
    return None


# ---------------------------------------------------------------------------
# Company facts: bulk file if there is one, the API if not
# ---------------------------------------------------------------------------

_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# The SEC publishes every company's facts in one nightly archive. Pointing
# this at a local copy is the right way to run the comparison harness: the
# harness reads dozens of companies at once and would otherwise make dozens
# of requests for data that arrives in one file, which is precisely the
# traffic the SEC's fair-access guidance asks callers to avoid.
#
# It is opt-in rather than automatic because the archive is large. For a
# handful of companies the per-company endpoint is the lighter choice, which
# is why both paths exist rather than one.
BULK_FACTS_VAR = "EDGAR_BULK_FACTS"

_bulk_lock = threading.Lock()
_bulk_zip: Any = None


def bulk_facts_path() -> str:
    return (os.environ.get(BULK_FACTS_VAR) or "").strip()


def _bulk_archive() -> Any:
    """The opened companyfacts archive, or None when none is configured."""
    global _bulk_zip
    path = bulk_facts_path()
    if not path:
        return None
    with _bulk_lock:
        if _bulk_zip is None:
            if not os.path.exists(path):
                log.warning("%s points at %s, which does not exist; falling "
                            "back to the per-company endpoint",
                            BULK_FACTS_VAR, path)
                return None
            _bulk_zip = zipfile.ZipFile(path)
        return _bulk_zip


def reset_bulk() -> None:
    """Close the archive. Used by tests."""
    global _bulk_zip
    with _bulk_lock:
        if _bulk_zip is not None:
            try:
                _bulk_zip.close()
            except Exception:
                pass
        _bulk_zip = None


def company_facts(cik: str) -> dict | None:
    """Every XBRL fact the SEC holds for *cik*, from the bulk file or the API."""
    archive = _bulk_archive()
    if archive is not None:
        name = f"CIK{cik}.json"
        try:
            with archive.open(name) as handle:
                return json.loads(handle.read().decode("utf-8"))
        except KeyError:
            # A company in the ticker list but not the archive is ordinary:
            # the archive is a nightly snapshot and a new registrant can be
            # in one and not the other. Fall through rather than fail.
            log.info("%s not in the bulk archive; asking the API", name)
        except Exception as exc:
            log.warning("reading %s from the bulk archive failed: %s",
                        name, exc)

    body = _fetch(_FACTS_URL.format(cik=cik))
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tag chains
# ---------------------------------------------------------------------------

# One ordered chain per field the model reads, in the taxonomies filers
# actually use. Order matters: the first tag that yields a figure for a
# period wins, so a more specific tag must come before a broader one.
#
# `us-gaap` for domestic filers, `ifrs-full` for the foreign private issuers
# that report under IFRS, `dei` for the entity-level facts that are not in
# either. A single chain spans all three rather than one chain per
# taxonomy, because what matters is which tag answers, not whose book it
# came from.
DURATION = "duration"
INSTANT = "instant"

INCOME_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        # The current US GAAP revenue tag, then the ASC-605 era one, then
        # the catch-alls. "Revenues" is last because a filer that uses a
        # specific tag and also files "Revenues" usually means a different,
        # broader total by it.
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "Revenues",
        "RevenueFromSaleOfGoods",      # ifrs-full
        "Revenue",                     # ifrs-full
    ),
    "operatingIncome": (
        "OperatingIncomeLoss",
        "ProfitLossFromOperatingActivities",   # ifrs-full
    ),
    "incomeBeforeTax": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign",
        "ProfitLossBeforeTax",                 # ifrs-full
    ),
    "incomeTaxExpense": (
        "IncomeTaxExpenseBenefit",
        "IncomeTaxExpenseContinuingOperations",  # ifrs-full
    ),
    "interestExpense": (
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestExpenseNonoperating",
        "InterestIncomeExpenseNet",
        "FinanceCosts",                        # ifrs-full
    ),
    "weightedAverageShsOutDil": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted",
        "WeightedAverageNumberOfDilutedSharesOutstandingIfrs",  # ifrs-full
    ),
    "weightedAverageShsOut": (
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted",
    ),
    "netIncomeCommon": (
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLoss",
        "ProfitLossAttributableToOwnersOfParent",   # ifrs-full
    ),
    "dilutedEPS": (
        "EarningsPerShareDiluted",
        "EarningsPerShareBasicAndDiluted",
        "DilutedEarningsLossPerShare",              # ifrs-full
    ),
    "grossProfit": (
        "GrossProfit",
    ),
    "interestIncome": (
        "InterestAndDividendIncomeOperating",
        "InterestIncomeOperating",
        "InvestmentIncomeInterest",
        "RevenueFromInterest",                      # ifrs-full
    ),
    "premiumsEarned": (
        "PremiumsEarnedNet",
        "PremiumsWrittenNet",
        "InsuranceRevenue",                         # ifrs-full
    ),
    "policyholderBenefits": (
        "PolicyholderBenefitsAndClaimsIncurredNet",
        "BenefitsLossesAndExpenses",
        "InsuranceServiceExpenseFromInsuranceContractsIssued",  # ifrs-full
    ),
}

BALANCE_TAGS: dict[str, tuple[str, ...]] = {
    "shortTermDebt": (
        "DebtCurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
        "CurrentPortionOfLongTermBorrowings",       # ifrs-full
    ),
    "longTermDebt": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "NoncurrentPortionOfLongTermBorrowings",    # ifrs-full
    ),
    "cashAndCashEquivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalents",                   # ifrs-full
    ),
    "shortTermInvestments": (
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "MarketableSecuritiesCurrent",
    ),
    "longTermInvestments": (
        "LongTermInvestments",
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
        "MarketableSecuritiesNoncurrent",
    ),
    "totalCurrentAssets": (
        "AssetsCurrent",
        "CurrentAssets",                            # ifrs-full
    ),
    "totalCurrentLiabilities": (
        "LiabilitiesCurrent",
        "CurrentLiabilities",                       # ifrs-full
    ),
    "commonStockEquity": (
        "StockholdersEquity",
        "EquityAttributableToOwnersOfParent",       # ifrs-full
    ),
    "loans": (
        "LoansAndLeasesReceivableNetReportedAmount",
        "NotesReceivableNet",
        "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
    ),
    "totalAssets": (
        "Assets",
    ),
    "minorityInterest": (
        "MinorityInterest",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "NoncontrollingInterests",                  # ifrs-full
    ),
}

CASHFLOW_TAGS: dict[str, tuple[str, ...]] = {
    "depreciationAndAmortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
        "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",  # ifrs-full
    ),
    "capitalExpenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
        # The property companies. Realty Income and Prologis file no capital
        # expenditure line at all, which read as nil capex and valued both
        # as though the portfolio cost nothing to maintain.
        "PaymentsToAcquireRealEstate",
        "PaymentsToAcquireCommercialRealEstate",
        "PurchaseOfPropertyPlantAndEquipment",      # ifrs-full
    ),
    "changeInWorkingCapital": (
        "IncreaseDecreaseInOperatingCapital",
        "OtherOperatingActivitiesCashFlowStatement",
        "AdjustmentsForDecreaseIncreaseInWorkingCapital",  # ifrs-full
    ),
    "dividendsPaid": (
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
        "DividendsPaidClassifiedAsFinancingActivities",    # ifrs-full
    ),
    "stockRepurchased": (
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
        "PaymentsToAcquireOrRedeemOwnShares",              # ifrs-full
    ),
}

# The forms an annual figure may come from. 10-K for domestic filers, 20-F
# for foreign private issuers, 40-F for the Canadian MJDS filers. The
# amendments count: an amended annual report is still the annual report, and
# is usually the one that corrected something.
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A",
                          "20-F", "20-F/A", "40-F", "40-F/A"})

# A fiscal year is not 365 days. A 52/53-week retailer runs 364 or 371; a
# transition period runs anything. This band accepts a year and rejects a
# quarter, a half and a two-year comparative without needing to know which
# calendar the filer keeps.
ANNUAL_DAYS = (330, 400)


# ---------------------------------------------------------------------------
# Reading facts
# ---------------------------------------------------------------------------

def _points(facts: dict, tag: str) -> list[dict]:
    """Every annual point for *tag*, in any taxonomy, with its unit attached.

    Flattened across taxonomies on purpose: a filer uses one of them and the
    caller does not care which. The unit is carried through because for a
    foreign filer it is the only statement of the reporting currency that
    comes from the filing itself.
    """
    out: list[dict] = []
    for taxonomy, tags in (facts.get("facts") or {}).items():
        entry = tags.get(tag)
        if not entry:
            continue
        for unit, points in (entry.get("units") or {}).items():
            for point in points:
                if point.get("form") not in ANNUAL_FORMS:
                    continue
                end = point.get("end")
                if not end:
                    continue
                start = point.get("start")
                if start is not None:
                    try:
                        span = (date.fromisoformat(end)
                                - date.fromisoformat(start)).days
                    except (TypeError, ValueError):
                        continue
                    if not ANNUAL_DAYS[0] <= span <= ANNUAL_DAYS[1]:
                        continue
                out.append({**point, "unit": unit, "taxonomy": taxonomy,
                            "tag": tag})
    return out


def _latest_per_period(points: Iterable[dict]) -> dict[str, dict]:
    """{period end: the most recently filed point for it}.

    A 10-K carries two or three years of comparatives, so the same period
    appears under several accession numbers, and a restatement appears under
    a later one with a different value. The most recently filed figure is
    the company's current view of its own past, which is what a valuation
    should use.
    """
    best: dict[str, dict] = {}
    for point in points:
        end = point["end"]
        current = best.get(end)
        if current is None or str(point.get("filed") or "") > str(
                current.get("filed") or ""):
            best[end] = point
    return best


def _series(facts: dict, chain: tuple[str, ...]) -> dict[str, dict]:
    """{period end: point} for the first tag in *chain* that answers.

    Per period rather than per chain: a filer that changed tags mid-history -
    the ASC 606 transition did this to almost everyone - has one tag covering
    the early years and another the late ones. Falling back per period
    stitches those together; falling back per chain would throw away
    whichever half came second.
    """
    merged: dict[str, dict] = {}
    for tag in chain:
        for end, point in _latest_per_period(_points(facts, tag)).items():
            merged.setdefault(end, point)
    return merged


def _money(point: dict | None) -> float | None:
    """The value of a monetary point, or None. Shares and EPS pass through."""
    if point is None:
        return None
    try:
        return float(point["val"])
    except (KeyError, TypeError, ValueError):
        return None


def reporting_currency_of(facts: dict) -> str | None:
    """The currency the filing is in, from the units on its own revenue.

    Unlike the aggregator, which carries a separate currency field that could
    disagree with the numbers, this is read off the figures themselves: an
    XBRL monetary fact names its unit, so a EUR filing cannot be mistaken for
    a USD one.
    """
    counts: dict[str, int] = {}
    for key in ("revenue", "operatingIncome", "netIncomeCommon"):
        for point in _series(facts, INCOME_TAGS[key]).values():
            unit = str(point.get("unit") or "")
            if len(unit) == 3 and unit.isalpha():
                counts[unit.upper()] = counts.get(unit.upper(), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# Building the statements
# ---------------------------------------------------------------------------

def _row(facts: dict, tags: dict[str, tuple[str, ...]], end: str,
         series: dict[str, dict[str, dict]]) -> dict:
    """One statement row for the period ending *end*."""
    row: dict[str, Any] = {}
    for key in tags:
        row[key] = _money(series[key].get(end))
    return row


def _instant_near(series: dict[str, dict], end: str,
                  tolerance: int = 10) -> dict | None:
    """A balance-sheet point at *end*, or within a few days of it.

    The balance sheet is dated the last day of the period and the income
    statement's period ends on the same day, so an exact match is the normal
    case. It is not the only one: a filer whose fiscal year ends on the
    Saturday nearest 31 December can date the two a day or two apart, and an
    exact-match-only lookup silently produced a company with an income
    statement and no balance sheet.
    """
    exact = series.get(end)
    if exact is not None:
        return exact
    try:
        target = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    nearest, best = None, tolerance + 1
    for stamp, point in series.items():
        try:
            gap = abs((date.fromisoformat(stamp) - target).days)
        except (TypeError, ValueError):
            continue
        if gap < best:
            nearest, best = point, gap
    return nearest


def edgar_financials(ticker: str, years: int = 5, *,
                     profile: dict | None = None) -> CompanyFinancials:
    """Build a `CompanyFinancials` for *ticker* from EDGAR alone.

    *profile* is accepted so the comparison harness can lend the fields EDGAR
    cannot supply - price, sector, industry, beta. Passing nothing yields a
    profile carrying only what the filings state, which is enough to compare
    statements and not enough to route a company to a method. That asymmetry
    is the honest shape of this stage: the statements are replaceable today
    and the profile is not.
    """
    found = cik_for(ticker)
    if found is None:
        raise LookupError(
            f"'{ticker}' is not in the SEC's ticker list. Foreign issuers "
            "without a US listing, and symbols the aggregator spells "
            "differently, both land here.")
    cik, registrant = found

    facts = company_facts(cik)
    if facts is None:
        raise LookupError(f"The SEC returned no facts for CIK {cik} "
                          f"('{ticker}').")

    income_series = {k: _series(facts, v) for k, v in INCOME_TAGS.items()}
    balance_series = {k: _series(facts, v) for k, v in BALANCE_TAGS.items()}
    cashflow_series = {k: _series(facts, v) for k, v in CASHFLOW_TAGS.items()}

    # Revenue decides which periods exist. Every other line is aligned to it,
    # the same rule market_data's _aligned_rows() follows and for the same
    # reason: a period with no revenue cannot be a base year or a growth
    # observation, so carrying it would only let it be mistaken for a zero.
    periods = sorted((end for end, point in income_series["revenue"].items()
                      if _money(point) is not None), reverse=True)[:years]

    if not periods:
        # A CIK with facts but no annual revenue is almost never a company
        # with no revenue. It is the wrong CIK - a holding company created
        # by a reorganisation, or a registrant that files only the parts of
        # a filing that are not the accounts. Refusing here is the point:
        # the first version of this returned an empty CompanyFinancials, the
        # derivation read every absent figure as zero, and XOM came out with
        # a 0% operating margin and no complaint from anything.
        raise LookupError(
            f"CIK {cik} ('{registrant}') has SEC facts but no annual revenue "
            f"for '{ticker}'.\n"
            "  That usually means the ticker maps to the wrong registrant - "
            "a holding company created by a reorganisation, for instance.\n"
            "  Check whether the filing history sits under a different CIK "
            "and add it to edgar_data.CIK_OVERRIDES with a reason.")

    income, balance, cashflow = [], [], []
    for end in periods:
        fiscal_year = int(end[:4])
        point = income_series["revenue"][end]
        # The filer's own fiscal-year label where it gave one; a December
        # year-end makes these agree, and Apple's September one does not.
        if point.get("fy") and point.get("fp") == "FY":
            fiscal_year = int(point["fy"])

        row = _row(facts, INCOME_TAGS, end, income_series)
        row["fiscalYear"] = fiscal_year
        row["date"] = end
        # The aggregator publishes a normalised effective rate beside the
        # reported provision. No filer reports one, so this is None and the
        # tax-rate source chain falls to its next source - which is the
        # behaviour change this stage exists to measure, not to hide.
        row["effectiveTaxRate"] = None
        interest = row.get("interestExpense")
        row["interestExpense"] = abs(interest) if interest is not None else None
        if row.get("operatingIncome") is None \
                and row.get("incomeBeforeTax") is not None:
            # Same reconstruction market_data does for insurers and
            # conglomerates that publish no operating income line.
            row["operatingIncome"] = (row["incomeBeforeTax"]
                                      + (row.get("interestExpense") or 0.0))
        # The aggregator carries D&A on both statements - "Reconciled
        # Depreciation" on the income statement and the cash-flow line
        # itself. Filers report it once, in the cash-flow statement, so it
        # is copied across to keep the row shapes identical.
        row["depreciationAndAmortization"] = _money(
            cashflow_series["depreciationAndAmortization"].get(end))
        income.append(row)

        brow: dict[str, Any] = {"fiscalYear": fiscal_year, "date": end}
        for key in BALANCE_TAGS:
            brow[key] = _money(_instant_near(balance_series[key], end))
        short = brow.pop("shortTermInvestments") or 0.0
        long_ = brow.pop("longTermInvestments") or 0.0
        brow["shortTermInvestments"] = short
        brow["totalInvestments"] = short + long_
        balance.append(brow)

        crow = _row(facts, CASHFLOW_TAGS, end, cashflow_series)
        crow["fiscalYear"] = fiscal_year
        crow["date"] = end
        # Filers tag cash *paid* as a positive number; the aggregator
        # publishes the same figures as negative outflows and every consumer
        # downstream takes an absolute value or expects a negative. Negating
        # here keeps the shape identical rather than making the comparison
        # harness compare two sign conventions.
        for key in ("capitalExpenditure", "dividendsPaid", "stockRepurchased"):
            value = crow.get(key)
            if value is not None and value > 0:
                crow[key] = -value
        cashflow.append(crow)

    currency = reporting_currency_of(facts) or ""
    merged = {
        "companyName": registrant or ticker,
        "cik": cik,
        "sector": "",
        "industry": "",
        "exchange": "",
        "financialCurrency": currency,
        "source": "edgar",
    }
    if profile:
        # The lent fields win, because they are the ones EDGAR does not have.
        merged.update({k: v for k, v in profile.items() if v is not None})
        merged["source"] = "edgar+lent-profile"
        merged["cik"] = cik

    return CompanyFinancials(
        ticker=ticker,
        profile=merged,
        income=income,
        balance=balance,
        cashflow=cashflow,
        reporting_currency=currency,
        trading_currency=(profile or {}).get("currency") or currency,
        fx=None,
        # A 20-F filer files once a year, so there is nothing to build a
        # trailing-twelve-month base from. Left None for every company at
        # this stage rather than built for some: a base year that is TTM for
        # domestic filers and annual for foreign ones would make the
        # comparison against the aggregator incomparable.
        ttm=None,
    )
