"""
Generates the cross-engine parity fixtures used by the Flutter test suite.

The Dart engine in app/lib/dcf_engine.dart duplicates the arithmetic in
dcf.py. Duplicated logic drifts, so this records what the real backend
produces for a range of assumption combinations, and the Dart test asserts
its own engine reproduces each one.

Fixtures are captured through the HTTP API rather than by importing dcf.py
directly, so the whole backend path - derivation, overrides, serialisation -
is what the Dart side is measured against.

Run with the backend up:
    python -m uvicorn api:app --port 8000
    python tools/generate_parity_fixtures.py

Writes: app/test/fixtures/parity_cases.json
"""

import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8000"

# Two companies, not one, and the second is the point of having two.
#
# AAPL reports in the currency it trades in, so its honesty note has no
# exchange-rate sentence and never exercised that branch. NVO reports in
# Danish kroner and trades in dollars, so its note ends with the rate the
# figure rests on - and the Dart copy of the note simply dropped that
# sentence the moment a slider moved, for as long as the app had existed,
# because no recorded case had one to drop.
TICKERS = ["AAPL", "NVO"]
OUT = pathlib.Path(__file__).resolve().parents[1] / "app" / "test" / "fixtures" / "parity_cases.json"

# Combinations chosen to move each lever independently and then together,
# including values well away from the derived ones, so a sign error or a
# misplaced term in the Dart port cannot pass unnoticed.
CASES: list[tuple[str, dict]] = [
    ("derived (no overrides)", {}),
    ("high growth", {"revenue_growth": 0.12}),
    ("zero growth", {"revenue_growth": 0.0}),
    ("low WACC", {"wacc": 0.05}),
    ("high WACC", {"wacc": 0.18}),
    ("thin margin", {"operating_margin": 0.05}),
    ("fat margin", {"operating_margin": 0.55}),
    ("terminal growth near zero", {"terminal_growth": 0.001}),
    ("terminal growth close to WACC", {"wacc": 0.09, "terminal_growth": 0.085}),
    ("heavy capex", {"capex_pct": 0.35}),
    # The capex fade is on by default, so every case above already exercises
    # a glide. These move its two ends independently, and then flatten it.
    ("capex ramping down hard", {"capex_pct": 0.40, "capex_terminal_pct": 0.05}),
    ("capex ramping up", {"capex_pct": 0.05, "capex_terminal_pct": 0.30}),
    ("capex flat by coincidence", {"capex_pct": 0.12, "capex_terminal_pct": 0.12}),
    ("capex above revenue, as Oracle's is",
     {"capex_pct": 1.05, "capex_terminal_pct": 0.40}),
    ("capex fade over one year", {"capex_pct": 0.30, "capex_terminal_pct": 0.10,
                                  "projection_years": 1}),
    ("no D&A", {"da_pct": 0.0}),
    ("negative NWC swing", {"nwc_pct": -0.10}),
    ("high tax", {"tax_rate": 0.34}),
    ("short horizon", {"projection_years": 1}),
    ("long horizon", {"projection_years": 15}),
    ("everything moved", {
        "revenue_growth": 0.09, "operating_margin": 0.28, "tax_rate": 0.22,
        "da_pct": 0.05, "capex_pct": 0.07, "capex_terminal_pct": 0.03,
        "nwc_pct": 0.04,
        "wacc": 0.11, "terminal_growth": 0.02, "projection_years": 7,
    }),
]


def post(ticker: str, overrides: dict) -> dict:
    payload = json.dumps({"ticker": ticker, "overrides": overrides}).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/valuation",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def get(ticker: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}/valuation/{ticker}",
                                timeout=60) as response:
        return json.loads(response.read().decode())


def main() -> int:
    try:
        urllib.request.urlopen(f"{BASE_URL}/health", timeout=10).read()
    except urllib.error.URLError as e:
        print(f"Backend not reachable at {BASE_URL}: {e}")
        print("Start it with:  python -m uvicorn api:app --port 8000")
        return 1

    cases = []
    for ticker in TICKERS:
        # The whole grid for the first company; a handful for the second.
        # The arithmetic is already covered by the grid, and what the second
        # company is here to cover is the one sentence that only it has.
        wanted = CASES if ticker == TICKERS[0] else [
            c for c in CASES
            if c[0] in ("derived (no overrides)", "low WACC", "high WACC",
                        "everything moved")]
        for label, overrides in wanted:
            label = f"{ticker}: {label}"
            try:
                body = post(ticker, overrides) if overrides else get(ticker)
            except urllib.error.HTTPError as e:
                print(f"  SKIP {label}: HTTP {e.code} "
                      f"{e.read().decode()[:120]}")
                continue

            # Every assumption, flattened - the Dart engine needs the full
            # set, not only the ones that were overridden.
            assumptions = {
                a["name"]: a["value"]
                for a in body.get("assumptions", [])
                + body.get("global_levers", [])
            }

            cases.append({
                "label": label,
                "ticker": ticker,
                "overrides": overrides,
                "company_name": body["company"]["company_name"],
                "base_year": body["base_year"],
                "assumptions": assumptions,
                # The honesty note names the month the premium was
                # published, so the Dart copy needs it to reproduce the
                # sentence.
                "erp_as_of": next(
                    (a.get("as_of") for a in body.get("global_levers", [])
                     + body.get("assumptions", [])
                     if a["name"] == "equity_risk_premium"), None),
                # Null for a company that reports in its trading currency.
                "fx_clause": body.get("fx_clause"),
                "expected": {
                    "intrinsic_value_per_share":
                        body["intrinsic_value_per_share"],
                    "current_price": body["current_price"],
                    "upside_downside": body["upside_downside"],
                    "enterprise_value": body["valuation"]["enterprise_value"],
                    "equity_value": body["valuation"]["equity_value"],
                    "terminal_value": body["valuation"]["terminal_value"],
                    "pv_terminal_value":
                        body["valuation"]["pv_terminal_value"],
                    "pv_ufcf_sum": body["valuation"]["pv_ufcf_sum"],
                    "tv_pct_of_ev": body["valuation"]["tv_pct_of_ev"],
                },
                "note": body["note"],
            })
            print(f"  captured {label:<44} -> "
                  f"${body['intrinsic_value_per_share']:,.4f}")

    if not cases:
        print("No cases captured.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    converted = sum(1 for c in cases if c["fx_clause"])
    if not converted:
        print("No converted company captured. The Dart note's exchange-rate "
              "branch would go untested, which is how it came to be missing.")
        return 1
    OUT.write_text(json.dumps({"tickers": TICKERS, "cases": cases}, indent=2))
    print(f"  {converted} of them exercise the exchange-rate sentence")
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
