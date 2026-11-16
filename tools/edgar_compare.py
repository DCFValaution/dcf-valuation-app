"""Compare the EDGAR shadow fetcher against the aggregator, field by field.

Run:
    PUBLIC_DATA_CONTACT='Name you@example.com' python tools/edgar_compare.py
    ... --tickers NVDA,AAPL --years 5 --json out.json

What it does, and the order matters:

  1. Fetches the same company from both sources.
  2. Aligns them on fiscal period, not on position. The two can disagree
     about how many years they hold and about which year a period belongs
     to, and comparing row 0 against row 0 would then compare two different
     years and call every field wrong.
  3. Compares every field both sources carry, and reports every field only
     one of them carries.
  4. Classifies each difference, conservatively. A difference it cannot
     place is reported as UNCLASSIFIED rather than guessed at - the point of
     the first run is to find out what the categories are, so a harness that
     forces everything into three buckets would hide exactly what is being
     looked for.
  5. Runs the derivation on both and compares the assumptions and the final
     per-share figure, which is the only number a reader sees.

Nothing here writes to the model or to production. It reads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assumptions as A
import edgar_data as E
import market_data as M
from analysis import value_company
from market_data import base_year_from

USUAL = ["NVDA", "AAPL", "GOOGL", "KO", "JNJ", "XOM", "TSLA", "COIN", "JPM",
         "TSM", "BP", "O", "PLD", "BRK-B", "AMZN", "META", "MSFT"]

# Fields compared per statement. Taken from the row builders rather than
# retyped, so a field added to one and not the other shows up as missing
# instead of silently going uncompared.
def _fields(*groups) -> tuple[str, ...]:
    """Flatten and de-duplicate, order preserved.

    Not cosmetic: shortTermInvestments is both a tag chain and a presented
    line, so the first version of this listed it twice and reported every
    disagreement about it twice, inflating the counts by forty-six.
    """
    seen, out = set(), []
    for group in groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return tuple(out)


INCOME_FIELDS = _fields(E.INCOME_TAGS,
                        ("depreciationAndAmortization", "effectiveTaxRate"))
BALANCE_FIELDS = _fields(
    (k for k in E.BALANCE_TAGS if k != "longTermInvestments"),
    ("shortTermInvestments", "totalInvestments"))
CASHFLOW_FIELDS = _fields(E.CASHFLOW_TAGS)

# Per-share and per-count fields. An ADR is a receipt for some number of
# ordinary shares - five for TSM - so the aggregator's per-ADR figures and
# the filer's per-ordinary-share figures differ by that ratio and by
# nothing else. Comparing them without saying so reads as a 5x error.
PER_SHARE_FIELDS = ("dilutedEPS", "weightedAverageShsOut",
                    "weightedAverageShsOutDil")

# Below this, the two agree. Rounding in the aggregator's own pipeline is
# worth a few parts in ten thousand and is not a finding.
TOLERANCE = 0.005


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Each rule is (field, predicate, category, why). The first match wins, so
# specific rules come before general ones. CATEGORIES are the three the
# review asked for, plus the honest fourth.
TAG_WRONG = "tag chain wrong"
DEFINITIONAL = "genuine definitional difference"
EDGAR_BETTER = "EDGAR more correct"
UNCLASSIFIED = "UNCLASSIFIED - needs review"

# Fields where the aggregator publishes a line no filer reports. A gap here
# is a known consequence of using primary data, not a tagging mistake.
NORMALISED_ONLY = {
    "effectiveTaxRate": "the aggregator's normalised rate; no filer reports one",
}

# Fields where the two sources are known to mean different things.
KNOWN_DEFINITIONAL = {
    "shortTermDebt": "the aggregator's Current Debt may include capital "
                     "lease obligations; DebtCurrent does not",
    "longTermDebt": "same lease question, on the non-current line",
    "changeInWorkingCapital": "a reconciling subtotal with no single tag; "
                              "filers present the components",
    "capitalExpenditure": "gross purchases against a net-of-disposals figure, "
                          "and property companies tag purchases of real "
                          "estate rather than of PPE",
    "minorityInterest": "the equity-attributable and the "
                        "including-NCI tags differ by the parent's own equity",
    "interestExpense": "gross interest against interest net of interest income",
    "totalInvestments": "assembled from two tags here and published as one "
                        "line there; the constituents are a judgement",
}


def classify(field: str, ours: float | None, theirs: float | None,
             ratio: float | None, *, adr_ratio: float | None = None
             ) -> tuple[str, str]:
    """(category, why) for one disagreement."""
    if field in NORMALISED_ONLY and ours is None:
        return DEFINITIONAL, NORMALISED_ONLY[field]

    if (field in PER_SHARE_FIELDS and adr_ratio is not None and ratio
            and abs(ratio / adr_ratio - 1) < 0.02):
        return DEFINITIONAL, (
            f"per ordinary share against per ADR; this ADR is "
            f"{adr_ratio:.3g} ordinary shares")

    if ours is None and theirs is not None:
        # The aggregator has it and we do not. Either the chain is missing
        # the tag this filer used, or the filer genuinely did not tag it.
        return TAG_WRONG, ("no tag in the chain answered for this period; "
                           "the aggregator has a figure")

    # A field assembled from constituents that were all absent sums to zero
    # rather than to None, so a zero against a real figure is the same
    # finding as a missing one and must not fall through to UNCLASSIFIED.
    if ours == 0 and theirs not in (None, 0):
        return TAG_WRONG, ("every tag in the chain was absent, so the line "
                           "summed to zero; the aggregator has a figure")

    if ours is not None and theirs is None:
        # We have it and the aggregator does not. For capex and debt this is
        # the whole point - reading an absent line as nil was the bug class
        # that produced REITs valued with zero capital spending.
        return EDGAR_BETTER, ("filed and tagged, but absent from the "
                              "aggregator, which reads as nil downstream")

    if theirs == 0 and ours not in (None, 0):
        # Worse than absent. A nil reads as a fact: a zero on `loans` is how
        # the router decides a company is not a lender.
        return EDGAR_BETTER, ("filed and tagged, but nil in the aggregator, "
                              "which reads downstream as a statement about "
                              "the company rather than a gap")

    if field in KNOWN_DEFINITIONAL:
        return DEFINITIONAL, KNOWN_DEFINITIONAL[field]

    if ratio is not None:
        # A clean factor of a thousand is a units mistake, not a definition.
        for factor, name in ((1000.0, "thousands"), (1e6, "millions"),
                             (0.001, "thousands"), (1e-6, "millions")):
            if abs(ratio / factor - 1) < 0.01:
                return TAG_WRONG, f"out by a factor of {name}; wrong unit"
        if abs(ratio + 1) < 0.01:
            return TAG_WRONG, "same magnitude, opposite sign; sign convention"

    return UNCLASSIFIED, f"both present, ratio {ratio:.4g}" if ratio else \
        "both present, no ratio"


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _by_period(rows: list[dict]) -> dict[str, dict]:
    return {str(r.get("date")): r for r in rows if r.get("date")}


# Searching four hundred tags for one that happens to carry a given figure
# finds coincidences. The first version of this reported that Prologis
# "tagged gross profit us-gaap:OtherLiabilities" and that Coca-Cola tagged
# its short-term investments "us-gaap:DeferredTaxAssetsGross" - both true as
# arithmetic and worthless as findings. A matched tag only counts if its
# name has something to do with the field, which is what separates "the
# chain is missing this tag" from numerology.
FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales", "turnover"),
    "operatingIncome": ("operating", "profitloss"),
    "incomeBeforeTax": ("beforeincometax", "beforetax", "pretax"),
    "incomeTaxExpense": ("incometax", "taxexpense"),
    "interestExpense": ("interestexpense", "financecost", "interestpaid",
                        "interestincomeexpense"),
    "interestIncome": ("interestincome", "interestanddividend",
                       "investmentincome", "interestother"),
    "grossProfit": ("grossprofit", "grossmargin"),
    "netIncomeCommon": ("netincome", "profitloss", "netearnings"),
    "dilutedEPS": ("pershare", "earningspershare"),
    "weightedAverageShsOut": ("weightedaverage", "sharesoutstanding"),
    "weightedAverageShsOutDil": ("weightedaverage", "sharesoutstanding"),
    "depreciationAndAmortization": ("deprecia", "amorti", "depletion"),
    "capitalExpenditure": ("propertyplant", "productiveassets",
                           "capitalexpenditure", "acquirereal",
                           "purchaseofproperty"),
    "changeInWorkingCapital": ("workingcapital", "operatingcapital",
                               "operatingassetsandliabilities"),
    "dividendsPaid": ("dividend",),
    "stockRepurchased": ("repurchase", "ownshares", "treasurystock"),
    "shortTermDebt": ("debt", "borrow", "notespayable", "commercialpaper",
                      "lease"),
    "longTermDebt": ("debt", "borrow", "notespayable", "lease"),
    "cashAndCashEquivalents": ("cash",),
    "shortTermInvestments": ("investment", "securities", "marketable"),
    "totalInvestments": ("investment", "securities", "marketable"),
    "totalCurrentAssets": ("assetscurrent", "currentassets"),
    "totalCurrentLiabilities": ("liabilitiescurrent", "currentliabilities"),
    "commonStockEquity": ("equity", "stockholders", "shareholders"),
    "loans": ("loan", "financingreceivable", "notesreceivable", "lease"),
    "totalAssets": ("assets",),
    "minorityInterest": ("minority", "noncontrolling"),
    "premiumsEarned": ("premium",),
    "policyholderBenefits": ("policyholder", "benefit", "claim"),
}


def _plausible(field: str, tag: str) -> bool:
    """Does this tag's name have anything to do with this field?"""
    keywords = FIELD_KEYWORDS.get(field)
    if not keywords:
        return True
    name = tag.split(":")[-1].lower()
    return any(word in name for word in keywords)


def matching_tag(facts: dict, target: float, period: str, field: str,
                 tolerance: float = 0.005) -> str | None:
    """A tag in *facts* whose figure for *period* is *target*.

    This is what turns "tag chain wrong" from a complaint into an
    instruction. The aggregator has a number and our chain does not; if some
    tag the filer used carries exactly that number, that tag is the one the
    chain is missing, and the harness can say which rather than leaving a
    human to search four hundred tags by hand.
    """
    if not target:
        return None
    best = None
    for taxonomy, tags in (facts.get("facts") or {}).items():
        for tag, entry in tags.items():
            for unit, points in (entry.get("units") or {}).items():
                for point in points:
                    if point.get("end") != period:
                        continue
                    if point.get("form") not in E.ANNUAL_FORMS:
                        continue
                    try:
                        value = float(point["val"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if abs(value - target) / max(abs(target), 1e-9) <= tolerance:
                        candidate = f"{taxonomy}:{tag}"
                        if not _plausible(field, candidate):
                            continue
                        # Shortest name wins: of several tags carrying the
                        # same figure the plainest is almost always the
                        # presented line rather than a disclosure detail.
                        if best is None or len(candidate) < len(best):
                            best = candidate
    return best


def compare_statement(label: str, ours: list[dict], theirs: list[dict],
                      fields, *, facts: dict | None = None,
                      rate: float = 1.0,
                      adr_ratio: float | None = None) -> list[dict]:
    mine, yours = _by_period(ours), _by_period(theirs)
    shared = sorted(set(mine) & set(yours), reverse=True)
    findings = []

    for period in shared:
        for field in fields:
            a, b = mine[period].get(field), yours[period].get(field)
            # The aggregator's statements have already been restated into
            # the trading currency by the time they get here - fetch_
            # financials converts them in place - so EDGAR's figures, which
            # are as filed, have to be put on the same footing. Without this
            # every TSM field disagreed by 32.969, which is the TWD/USD
            # rate and not a finding about anything.
            if a is not None and field not in PER_SHARE_FIELDS \
                    and field != "effectiveTaxRate":
                a = a * rate
            if a is None and b is None:
                continue
            if a is not None and b is not None:
                if b == 0 and a == 0:
                    continue
                scale = max(abs(a), abs(b))
                if scale == 0 or abs(a - b) / scale <= TOLERANCE:
                    continue
                ratio = (a / b) if b else None
            else:
                ratio = None
            category, why = classify(field, a, b, ratio, adr_ratio=adr_ratio)
            # Run the tag finder for every difference, not only the missing
            # ones. Where both sources have a figure, knowing which tag
            # carries the aggregator's is what decides whether this is our
            # chain reaching for the wrong line or the two genuinely meaning
            # different things - and it is the difference between a finding
            # and a to-do.
            # Not for the fields no filer reports. Searching a filing for a
            # tag carrying the aggregator's normalised tax rate finds one -
            # some ratio in some disclosure equals 0.1561 - and calling that
            # the tag we should have used would be exactly backwards.
            if facts is not None and b and field not in NORMALISED_ONLY:
                found = matching_tag(facts, b / rate if rate else b, period,
                                     field)
                if found:
                    # Some tag in this filing carries exactly the
                    # aggregator's figure, under a name that belongs to this
                    # field. That is our chain reaching for the wrong line,
                    # and the fix is the tag's name.
                    category = TAG_WRONG
                    why = f"the filer tagged that figure {found}"
                else:
                    # No tag in the filing carries it. Then the aggregator
                    # did not read it off the filing either - it computed or
                    # normalised it - and there is no tag to add. That is a
                    # difference in what the two mean, not a mistake here.
                    category = DEFINITIONAL
                    why = (f"{why}; but no tag in the filing carries the "
                           "aggregator's figure, so it is normalised or "
                           "computed rather than reported")
            findings.append({
                "statement": label, "period": period, "field": field,
                "edgar": a, "aggregator": b, "ratio": ratio,
                "category": category, "why": why,
            })
    return findings


def _assumption_values(result) -> dict:
    d = result.derived
    out = {}
    for name, prov in (d.provenance or {}).items():
        out[name] = getattr(prov, "value", None)
    for key in ("wacc", "terminal_growth", "revenue_growth",
                "operating_margin", "tax_rate"):
        value = getattr(d.assumptions, key, None)
        if value is not None:
            out.setdefault(key, value)
    return out


def compare_ticker(ticker: str, years: int) -> dict:
    out: dict = {"ticker": ticker}
    try:
        theirs = M.fetch_financials(ticker)
    except Exception as exc:
        out["error"] = f"aggregator: {type(exc).__name__}: {exc}"
        return out
    try:
        # The aggregator's profile is lent so the derivation can run at all:
        # EDGAR has no price and no sector, and without a sector the router
        # cannot pick a method. The statements are entirely EDGAR's.
        ours = E.edgar_financials(ticker, years=years, profile=theirs.profile)
    except Exception as exc:
        out["error"] = f"edgar: {type(exc).__name__}: {exc}"
        return out

    out["cik"] = ours.profile.get("cik")
    out["currency"] = {"edgar": ours.reporting_currency,
                       "aggregator": theirs.reporting_currency,
                       "aggregator_now_in": theirs.trading_currency}
    out["periods"] = {
        "edgar": [r["date"] for r in ours.income],
        "aggregator": [r.get("date") for r in theirs.income],
    }

    # The rate the aggregator itself used, so the two are compared on one
    # footing and any residual difference is about the figures.
    rate = float((theirs.fx or {}).get("rate") or 1.0)
    out["fx_applied_to_edgar"] = rate

    # How many ordinary shares one of the aggregator's shares represents.
    # Measured from the share counts rather than looked up: for an ordinary
    # US listing it comes out at 1 and costs nothing, and for an ADR it is
    # the only place the ratio appears anywhere in either source.
    adr_ratio = None
    try:
        theirs_shares = theirs.income[0].get("weightedAverageShsOutDil")
        ours_shares = ours.income[0].get("weightedAverageShsOutDil")
        if theirs_shares and ours_shares:
            measured = ours_shares / theirs_shares
            # Only a ratio that is actually an ADR ratio. A plain US
            # listing measures 1. Tesla measured 0.94 - a basic-against-
            # diluted mismatch, not a depositary ratio - and labelling that
            # an ADR ratio would have explained away a real finding.
            for candidate in (1, 2, 3, 4, 5, 6, 8, 10, 20, 0.5, 0.25, 0.2):
                if abs(measured / candidate - 1) < 0.01:
                    adr_ratio = float(candidate)
                    break
            out["measured_share_ratio"] = measured
    except (IndexError, TypeError, ZeroDivisionError):
        pass
    out["adr_ratio"] = adr_ratio

    facts = E.company_facts(out["cik"]) if out["cik"] else None
    common = {"facts": facts, "rate": rate, "adr_ratio": adr_ratio}
    out["findings"] = (
        compare_statement("income", ours.income, theirs.income,
                          INCOME_FIELDS, **common)
        + compare_statement("balance", ours.balance, theirs.balance,
                            BALANCE_FIELDS, **common)
        + compare_statement("cashflow", ours.cashflow, theirs.cashflow,
                            CASHFLOW_FIELDS, **common))

    # And the only number a reader sees.
    try:
        base_theirs, _ = base_year_from(theirs)
        base_ours, _ = base_year_from(ours)
        out["base"] = {
            "revenue": {"edgar": base_ours.revenue,
                        "aggregator": base_theirs.revenue},
            "shares": {"edgar": base_ours.shares,
                       "aggregator": base_theirs.shares},
        }
    except Exception as exc:
        out["base_error"] = f"{type(exc).__name__}: {exc}"

    try:
        their_value = value_company(ticker)
        out["value"] = {
            "aggregator": (their_value.result.intrinsic_value_per_share
                           if their_value.result else None),
            "method": their_value.method,
        }
    except Exception as exc:
        out["value_error"] = f"{type(exc).__name__}: {exc}"

    try:
        derived = A.derive_assumptions(ours)
        out["edgar_assumptions"] = {
            k: getattr(v, "value", v)
            for k, v in (derived.provenance or {}).items()}
    except Exception as exc:
        out["edgar_assumptions_error"] = f"{type(exc).__name__}: {exc}"

    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(results: list[dict]) -> None:
    print("\n" + "=" * 100)
    print("COVERAGE")
    print("=" * 100)
    print(f"{'tkr':<8}{'CIK':<12}{'filed in':<10}{'fx used':>10}"
          f"{'ADR':>6}{'EDGAR yrs':>11}{'agg yrs':>9}{'diffs':>7}")
    for r in results:
        if "error" in r:
            print(f"{r['ticker']:<8}{r['error'][:90]}")
            continue
        adr = r.get("adr_ratio")
        print(f"{r['ticker']:<8}{str(r.get('cik') or '-'):<12}"
              f"{r['currency']['edgar'] or '?':<10}"
              f"{r.get('fx_applied_to_edgar', 1.0):>10.6g}"
              f"{('-' if adr is None else f'{adr:.2f}'):>6}"
              f"{len(r['periods']['edgar']):>11}"
              f"{len(r['periods']['aggregator']):>9}"
              f"{len(r['findings']):>7}")

    counts: dict[str, int] = {}
    per_field: dict[tuple[str, str], int] = {}
    for r in results:
        for f in r.get("findings", []):
            counts[f["category"]] = counts.get(f["category"], 0) + 1
            key = (f["field"], f["category"])
            per_field[key] = per_field.get(key, 0) + 1

    print("\n" + "=" * 100)
    print("DIFFERENCES BY CATEGORY")
    print("=" * 100)
    for category in (TAG_WRONG, DEFINITIONAL, EDGAR_BETTER, UNCLASSIFIED):
        print(f"  {counts.get(category, 0):>5}  {category}")

    print("\n" + "=" * 100)
    print("DIFFERENCES BY FIELD")
    print("=" * 100)
    print(f"{'field':<34}{'category':<34}{'count':>6}")
    for (field, category), n in sorted(per_field.items(),
                                       key=lambda kv: -kv[1]):
        print(f"{field:<34}{category:<34}{n:>6}")

    print("\n" + "=" * 100)
    print("EVERY DIFFERENCE")
    print("=" * 100)
    for r in results:
        findings = r.get("findings", [])
        if not findings:
            continue
        print(f"\n--- {r['ticker']} ---")
        print(f"{'stmt':<10}{'period':<12}{'field':<32}"
              f"{'EDGAR':>18}{'aggregator':>18}{'ratio':>10}")
        for f in findings:
            e = "-" if f["edgar"] is None else f"{f['edgar']:,.0f}"
            a = "-" if f["aggregator"] is None else f"{f['aggregator']:,.0f}"
            ratio = "-" if f["ratio"] is None else f"{f['ratio']:.4f}"
            print(f"{f['statement']:<10}{f['period']:<12}{f['field']:<32}"
                  f"{e:>18}{a:>18}{ratio:>10}")
            print(f"            {f['category']}: {f['why']}")

    print("\n" + "=" * 100)
    print("BASE YEAR AND HEADLINE")
    print("=" * 100)
    print(f"{'tkr':<8}{'rev EDGAR':>16}{'rev agg':>16}{'move':>9}"
          f"{'shares E':>12}{'shares A':>12}{'$ agg':>11}{'method':>8}")
    for r in results:
        base = r.get("base")
        if not base:
            continue
        re_, ra = base["revenue"]["edgar"], base["revenue"]["aggregator"]
        se, sa = base["shares"]["edgar"], base["shares"]["aggregator"]
        value = (r.get("value") or {}).get("aggregator")
        method = (r.get("value") or {}).get("method") or "-"
        move = (re_ / ra - 1) * 100 if ra else float("nan")
        print(f"{r['ticker']:<8}{re_:>16,.0f}{ra:>16,.0f}{move:>+8.2f}%"
              f"{se:>12,.1f}{sa:>12,.1f}"
              f"{(value or 0):>11,.2f}{method:>8}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(USUAL))
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    if not E.public_data_contact():
        print("PUBLIC_DATA_CONTACT is not set.\n"
              "  The SEC refuses an undeclared client: it serves a "
              "User-Agent that looks like a contact address and returns 403 "
              "to one that looks like a crawler.\n"
              "  Set it to the address the operator wants the SEC to use:\n"
              "    PUBLIC_DATA_CONTACT='Intrinsic ops@example.com'",
              file=sys.stderr)
        return 2

    if E.bulk_facts_path():
        print(f"reading facts from the bulk archive at "
              f"{E.bulk_facts_path()}")
    else:
        print("no EDGAR_BULK_FACTS set; using the per-company endpoint "
              "(one request per ticker, throttled to "
              f"{E.SEC_RATE_LIMIT_PER_SECOND}/s)")

    results = []
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        print(f"  {ticker} ...", end="", flush=True)
        try:
            results.append(compare_ticker(ticker, args.years))
            print(" done")
        except Exception:
            traceback.print_exc()
            results.append({"ticker": ticker, "error": "harness crashed"})
            print(" CRASHED")

    report(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
