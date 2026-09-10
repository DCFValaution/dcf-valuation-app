"""
Live verification of the Yahoo Finance data layer.

Four things this proves, against the real service:

  1. AAPL reconciles with the FMP figures the model was built against.
  2. Tickers FMP's free tier gated now return real valuations.
  3. The suitability guards still fire for RIVN and JPM.
  4. Unknown, delisted and non-company symbols fail gracefully.

Run with:  python test_market_data_live.py
"""

import sys
import traceback

import market_data as M
from analysis import value_company
from market_data import (DataUnavailableError, MarketDataError,
                         RateLimitedError, TickerNotFoundError,
                         UnsupportedListingError)

# The verified FMP FY2025 figures, in $mm.
FMP_AAPL = {"revenue": 416_161, "total_debt": 98_657, "cash": 132_420,
            "shares": 15_004.7}

# Derived assumptions the model produced on FMP data, for a sanity comparison.
FMP_AAPL_ASSUMPTIONS = {
    "revenue_growth": 0.0328, "operating_margin": 0.3151, "tax_rate": 0.1561,
    "da_pct": 0.0293, "capex_pct": 0.0286, "wacc": 0.1056,
}

# Previously refused by FMP's free tier with HTTP 402 "plan_limited".
PREVIOUSLY_BLOCKED = ["BRK-B", "BRLT", "CROX", "DECK", "ULTA",
                      "WSM", "POOL", "TPR"]

# Foreign listings whose statements are in a different currency from the
# quote. These must be refused, not valued - see _check_currency().
CURRENCY_MISMATCHED = ["TSM", "ASML", "TM", "BABA"]

GUARDED = ["RIVN", "JPM"]

BAD_INPUT = [
    ("ZZZZTESTNOPE", "unknown symbol"),
    ("SAVA", "delisted since the FMP era"),
    ("SPY", "ETF - quotes fine, no statements"),
    ("", "empty"),
    ("AAPL; DROP TABLE", "malformed"),
]

failures: list[str] = []


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def compare(label: str, actual: float, expected: float, unit: str,
            tolerance: float) -> None:
    diff = actual - expected
    ok = abs(diff) <= tolerance
    verdict = "match" if ok else f"{diff:+,.2f}"
    print(f"  {label:<22}{actual:>14,.2f}{unit}  vs FMP {expected:>12,.2f}{unit}   {verdict}")
    if not ok:
        failures.append(f"{label}: {actual} vs {expected}")


def check_aapl() -> None:
    rule("1. AAPL reconciles with the figures the model was verified against")
    report = value_company("AAPL")
    base = report.base

    compare("Revenue ($mm)", base.revenue, FMP_AAPL["revenue"], "", 1)
    compare("Total debt ($mm)", base.total_debt, FMP_AAPL["total_debt"], "", 1)
    compare("Cash & inv ($mm)", base.cash, FMP_AAPL["cash"], "", 1)
    compare("Diluted shares (mm)", base.shares, FMP_AAPL["shares"], "", 1)
    print(f"  {'Live price':<22}{base.current_price:>14,.2f}"
          f"   (moves; not compared)")

    print("\n  derived assumptions vs the FMP-era values:")
    for name, was in FMP_AAPL_ASSUMPTIONS.items():
        now = report.derived.provenance[name].value
        drift = now - was
        flag = "close" if abs(drift) < 0.02 else "DRIFTED"
        print(f"    {name:<20}{now:>9.2%}  was {was:>8.2%}   {drift:+.2%}  {flag}")
        if abs(drift) >= 0.02:
            failures.append(f"assumption {name} drifted {drift:+.2%}")

    print(f"\n  intrinsic value: ${report.result.intrinsic_value_per_share:,.2f}"
          f"   market: ${base.current_price:,.2f}"
          f"   {report.result.upside_downside:+.1%}")


def check_coverage() -> None:
    rule("2. Coverage: tickers FMP's free tier refused with 402")
    for ticker in PREVIOUSLY_BLOCKED:
        try:
            report = value_company(ticker)
        except MarketDataError as e:
            line = str(e).splitlines()[0]
            print(f"  {ticker:<8} FETCH FAILED  {line}")
            failures.append(f"{ticker}: {line}")
            continue
        except Exception as e:
            print(f"  {ticker:<8} CRASH {type(e).__name__}: {e}")
            failures.append(f"{ticker} crashed")
            continue

        if report.suitable:
            r = report.result
            print(f"  {ticker:<8} VALUED        ${r.intrinsic_value_per_share:>9,.2f}/sh"
                  f"   vs ${report.base.current_price:>9,.2f}"
                  f"   {r.upside_downside:+7.1%}   {report.company_name}")
        else:
            reason = report.suitability.reasons[0].splitlines()[0]
            print(f"  {ticker:<8} REFUSED       {reason[:76]}")


def check_currency_guard() -> None:
    rule("2b. Foreign listings with mismatched currencies are refused")
    for ticker in CURRENCY_MISMATCHED:
        try:
            report = value_company(ticker)
            value = (f"${report.result.intrinsic_value_per_share:,.2f}"
                     if report.suitable else "refused for another reason")
            print(f"  {ticker:<8} NOT REFUSED - produced {value}")
            failures.append(f"{ticker} currency mismatch not caught")
        except UnsupportedListingError as e:
            print(f"  {ticker:<8} REFUSED  {str(e).splitlines()[0]}")
        except MarketDataError as e:
            print(f"  {ticker:<8} other error: {str(e).splitlines()[0][:60]}")


def check_guards() -> None:
    rule("3. Guards still fire on real Yahoo data")
    for ticker in GUARDED:
        try:
            report = value_company(ticker)
        except MarketDataError as e:
            print(f"  {ticker:<8} FETCH FAILED  {str(e).splitlines()[0]}")
            failures.append(f"{ticker} could not be fetched")
            continue

        if report.suitable:
            print(f"  {ticker:<8} NOT REFUSED - guard failed to fire!")
            failures.append(f"{ticker} should have been refused")
        else:
            reason = report.suitability.reasons[0].splitlines()[0]
            print(f"  {ticker:<8} REFUSED  ({report.company_name})")
            print(f"           {reason}")


def check_bad_input() -> None:
    rule("4. Bad input fails gracefully")
    for ticker, description in BAD_INPUT:
        try:
            value_company(ticker)
            print(f"  {ticker!r:<20} {description:<32} NO ERROR RAISED")
            failures.append(f"{ticker!r} should have failed")
        except TickerNotFoundError as e:
            print(f"  {ticker!r:<20} {description:<32} not-found: "
                  f"{str(e).splitlines()[0][:40]}")
        except RateLimitedError:
            print(f"  {ticker!r:<20} {description:<32} rate-limited (transient)")
        except DataUnavailableError as e:
            print(f"  {ticker!r:<20} {description:<32} unavailable: "
                  f"{str(e).splitlines()[0][:40]}")
        except Exception as e:
            print(f"  {ticker!r:<20} {description:<32} CRASH "
                  f"{type(e).__name__}: {e}")
            print(traceback.format_exc())
            failures.append(f"{ticker!r} crashed with {type(e).__name__}")


def check_risk_free() -> None:
    rule("5. Risk-free rate")
    result = M.fetch_risk_free_rate("year10")
    if result is None:
        print("  unavailable - the model falls back to its documented default")
    else:
        rate, source = result
        print(f"  {rate:.3%}   {source}")
        if not 0.0 < rate < 0.25:
            failures.append("risk-free rate outside a plausible band")


def check_cache() -> None:
    rule("6. Caching")
    import time
    M.clear_caches()

    start = time.monotonic()
    M.fetch_financials("AAPL")
    cold = time.monotonic() - start

    start = time.monotonic()
    for _ in range(5):
        M.fetch_financials("AAPL")
    warm = (time.monotonic() - start) / 5

    print(f"  first fetch      {cold * 1000:>8.1f} ms")
    print(f"  cached (avg x5)  {warm * 1000:>8.1f} ms")
    if warm > max(0.01, cold / 5):
        failures.append("cache does not appear to be serving repeats")
    else:
        print("  cache is serving repeats")


def main() -> int:
    check_aapl()
    check_coverage()
    check_currency_guard()
    check_guards()
    check_bad_input()
    check_risk_free()
    check_cache()

    rule("RESULT")
    if failures:
        print(f"  {len(failures)} problem(s):")
        for item in failures:
            print(f"    - {item}")
        return 1
    print("  everything verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())

