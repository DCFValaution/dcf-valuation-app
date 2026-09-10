"""
Live check of derived assumptions + the suitability guard.

AAPL should value cleanly from its own history; RIVN should be refused.

Run with:  python test_analysis_live.py [TICKER ...]
"""

import sys

from analysis import format_report, value_company
from market_data import MarketDataError

DEFAULT_TICKERS = ["AAPL", "RIVN"]


def main() -> int:
    tickers = sys.argv[1:] or DEFAULT_TICKERS
    failures = []

    for ticker in tickers:
        try:
            report = value_company(ticker)
        except MarketDataError as e:
            print("=" * 78)
            print(f"{ticker}: could not fetch")
            print("=" * 78)
            print(f"  {e}\n")
            continue
        except Exception as e:  # noqa: BLE001 - this is the thing we're testing for
            print(f"{ticker}: UNHANDLED {type(e).__name__}: {e}")
            failures.append(ticker)
            continue

        print(format_report(report))
        print()

    if failures:
        print(f"UNHANDLED CRASHES: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
