"""Stage 4 proposal: what switching the statements to EDGAR would do.

    python tools/stage4_switch.py [--tickers A,B,C] [--json out.json]

Values every US 10-K/10-Q filer twice - once on the aggregator's statements,
which is what ships today, and once on EDGAR's - and reports the base year,
the discount rate and the figure a reader sees. 20-F filers (TSM, BP) are
run too and reported separately, because they have no quarterly filings and
so no trailing year on EDGAR; the proposal leaves them on the aggregator.

Everything outside the statements is held identical: the profile, the price,
the beta regression, the risk-free rate, the premium and the industry betas
all come from the same place in both runs. So a difference in the answer is
a difference in the statements and nothing else.

Nothing here switches anything. It is the before/after for a decision.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis as An
import assumptions as A
import edgar_data as E
import market_data as M
from dcf import run_dcf

# 20-F filers: no 10-Q, so no trailing twelve months to rebuild. The
# proposal leaves these on the aggregator and this list is why.
FOREIGN = {"TSM", "BP"}

# Berkshire is separately parked: its investment-gains presentation makes
# revenue a different quantity on the two sources, not a more accurate one.
PARKED = {"BRK-B"}

USUAL = ["NVDA", "AAPL", "GOOGL", "KO", "JNJ", "XOM", "TSLA", "COIN", "JPM",
         "TSM", "BP", "O", "PLD", "BRK-B", "AMZN", "META", "MSFT"]


def valued(fin) -> dict:
    """The production valuation, on one set of statements.

    Routed the way production routes it rather than forced through the DCF.
    The first version of this ran run_dcf on everything, which put JPMorgan
    - a dividend-model company - through a discounted cash flow and
    reported a 60% move that was an artifact of the harness and nothing to
    do with EDGAR. A comparison has to compare what the app would actually
    show.
    """
    out: dict = {}
    original = An.fetch_financials
    try:
        An.fetch_financials = lambda ticker, **kwargs: fin
        report = An.value_company(fin.ticker)
    except Exception as exc:
        return {"error": f"valuation: {type(exc).__name__}: {exc}"}
    finally:
        An.fetch_financials = original

    out["method"] = report.method
    out["suitable"] = report.suitable
    if not report.suitable:
        out["error"] = ("refused: " + "; ".join(
            report.suitability.reasons[:1])[:70])
        return out
    if report.result is not None:
        out["value"] = report.result.intrinsic_value_per_share

    try:
        base, meta = M.base_year_from(fin)
    except Exception as exc:
        out["error"] = f"base year: {type(exc).__name__}: {exc}"
        return out
    out["base_label"] = meta.get("base_label")
    out["revenue"] = base.revenue
    out["debt"] = base.total_debt
    out["cash"] = base.cash
    out["shares"] = base.shares
    out["minority"] = base.minority_interest

    try:
        derived = A.derive_assumptions(fin)
    except Exception as exc:
        out["derivation_error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["wacc"] = derived.assumptions.wacc
    out["wacc_source"] = (derived.provenance.get("wacc").source
                          if derived.provenance.get("wacc") else None)
    out["margin"] = derived.assumptions.operating_margin
    out["growth"] = derived.assumptions.revenue_growth
    out["tax"] = derived.assumptions.tax_rate
    out["capex"] = derived.assumptions.capex_pct
    components = (derived.diagnostics or {}).get("wacc_components") or {}
    out["cost_of_debt"] = components.get("cost_of_debt")
    out["cost_of_debt_source"] = (
        derived.provenance.get("cost_of_debt").source
        if derived.provenance.get("cost_of_debt") else None)

    return out


def compare(ticker: str) -> dict:
    row: dict = {"ticker": ticker,
                 "kind": ("20-F" if ticker in FOREIGN
                          else "parked" if ticker in PARKED else "10-K")}
    try:
        theirs = M.fetch_financials(ticker)
    except Exception as exc:
        row["error"] = f"aggregator: {type(exc).__name__}: {exc}"
        return row
    row["now"] = valued(theirs)

    try:
        # The profile is lent, so the price, sector and beta are identical in
        # both runs and the only thing that changes is the statements.
        ours = E.edgar_financials(ticker, years=5, profile=theirs.profile)
    except Exception as exc:
        row["error"] = f"edgar: {type(exc).__name__}: {exc}"
        return row
    row["edgar"] = valued(ours)
    return row


def move(a, b):
    if a in (None, 0) or b is None:
        return None
    return b / a - 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(USUAL))
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    rows = []
    for ticker in (t.strip().upper() for t in args.tickers.split(",")):
        if not ticker:
            continue
        print(f"  {ticker}...", flush=True)
        rows.append(compare(ticker))

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(rows, handle, indent=1, default=str)

    for heading, kinds in (("US FILERS (10-K / 10-Q) - what Stage 4 switches",
                            {"10-K"}),
                           ("20-F FILERS - left on the aggregator", {"20-F"}),
                           ("PARKED", {"parked"})):
        selected = [r for r in rows if r["kind"] in kinds]
        if not selected:
            continue
        print()
        print("=" * 108)
        print(heading)
        print("=" * 108)
        print(f"{'tkr':<7}{'method':<6}{'base now':<14}{'base EDGAR':<14}"
              f"{'rev now':>12}{'rev EDGAR':>12}{'rev move':>10}"
              f"{'WACC now':>9}{'WACC EDG':>9}{'$ now':>10}{'$ EDGAR':>10}"
              f"{'move':>8}")
        print("-" * 108)
        for r in selected:
            if r.get("error"):
                print(f"{r['ticker']:<7}{r['error'][:96]}")
                continue
            now, edg = r["now"], r["edgar"]
            if now.get("error") or edg.get("error"):
                print(f"{r['ticker']:<7}now: {now.get('error') or 'ok'} | "
                      f"edgar: {edg.get('error') or 'ok'}")
                continue
            v = move(now.get("value"), edg.get("value"))
            print(f"{r['ticker']:<7}{str(now.get('method'))[:5]:<6}"
                  f"{str(now.get('base_label'))[:13]:<14}"
                  f"{str(edg.get('base_label'))[:13]:<14}"
                  f"{now['revenue']:>12,.0f}{edg['revenue']:>12,.0f}"
                  f"{(move(now['revenue'], edg['revenue']) or 0) * 100:>+9.1f}%"
                  f"{now['wacc']:>9.2%}{edg['wacc']:>9.2%}"
                  f"{now.get('value', float('nan')):>10,.2f}"
                  f"{edg.get('value', float('nan')):>10,.2f}"
                  f"{(v or 0) * 100:>+7.1f}%")

    # Everything that moved the headline by more than five per cent, with
    # the inputs that moved with it - which is where the explanation is.
    print()
    print("=" * 108)
    print("MOVED MORE THAN 5% - the inputs behind each")
    print("=" * 108)
    for r in rows:
        if r.get("error"):
            continue
        now, edg = r.get("now") or {}, r.get("edgar") or {}
        if now.get("error") or edg.get("error"):
            continue
        v = move(now.get("value"), edg.get("value"))
        if v is None or abs(v) <= 0.05:
            continue
        print(f"\n{r['ticker']}  value {now['value']:,.2f} -> "
              f"{edg['value']:,.2f}  ({v * 100:+.1f}%)   [{r['kind']}]")
        print(f"   base period  {now.get('base_label')} -> "
              f"{edg.get('base_label')}")
        for field, fmt in (("revenue", ",.0f"), ("debt", ",.0f"),
                           ("cash", ",.0f"), ("shares", ",.1f"),
                           ("minority", ",.0f")):
            a, b = now.get(field), edg.get(field)
            if a is None or b is None:
                continue
            pct = move(a, b)
            flag = "  <-- " if pct is not None and abs(pct) > 0.02 else "      "
            print(f"   {flag}{field:<14}{a:>15{fmt}}{b:>15{fmt}}"
                  f"{'' if pct is None else f'  {pct * 100:+.1f}%'}")
        for field in ("margin", "growth", "tax", "capex", "cost_of_debt",
                      "wacc"):
            a, b = now.get(field), edg.get(field)
            if a is None or b is None:
                continue
            flag = "  <-- " if abs(b - a) > 0.005 else "      "
            print(f"   {flag}{field:<14}{a:>14.2%}{b:>15.2%}"
                  f"  {b - a:+.2%}")
        if now.get("cost_of_debt_source") != edg.get("cost_of_debt_source"):
            print(f"         cost of debt source  "
                  f"{now.get('cost_of_debt_source')} -> "
                  f"{edg.get('cost_of_debt_source')}")
        if now.get("wacc_source") != edg.get("wacc_source"):
            print(f"         WACC source          "
                  f"{now.get('wacc_source')} -> {edg.get('wacc_source')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
