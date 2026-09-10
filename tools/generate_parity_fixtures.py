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
TICKER = "AAPL"
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
    ("no D&A", {"da_pct": 0.0}),
    ("negative NWC swing", {"nwc_pct": -0.10}),
    ("high tax", {"tax_rate": 0.34}),
    ("short horizon", {"projection_years": 1}),
    ("long horizon", {"projection_years": 15}),
    ("everything moved", {
        "revenue_growth": 0.09, "operating_margin": 0.28, "tax_rate": 0.22,
        "da_pct": 0.05, "capex_pct": 0.07, "nwc_pct": 0.04,
        "wacc": 0.11, "terminal_growth": 0.02, "projection_years": 7,
    }),
]


def post(overrides: dict) -> dict:
    payload = json.dumps({"ticker": TICKER, "overrides": overrides}).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/valuation",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def get() -> dict:
    with urllib.request.urlopen(f"{BASE_URL}/valuation/{TICKER}", timeout=60) as response:
        return json.loads(response.read().decode())


def main() -> int:
    try:
        urllib.request.urlopen(f"{BASE_URL}/health", timeout=10).read()
    except urllib.error.URLError as e:
        print(f"Backend not reachable at {BASE_URL}: {e}")
        print("Start it with:  python -m uvicorn api:app --port 8000")
        return 1

    cases = []
    for label, overrides in CASES:
        try:
            body = post(overrides) if overrides else get()
        except urllib.error.HTTPError as e:
            print(f"  SKIP {label}: HTTP {e.code} {e.read().decode()[:120]}")
            continue

        # Every assumption, flattened - the Dart engine needs the full set,
        # not only the ones that were overridden.
        assumptions = {
            a["name"]: a["value"]
            for a in body.get("assumptions", []) + body.get("global_levers", [])
        }

        cases.append({
            "label": label,
            "overrides": overrides,
            "company_name": body["company"]["company_name"],
            "base_year": body["base_year"],
            "assumptions": assumptions,
            "expected": {
                "intrinsic_value_per_share": body["intrinsic_value_per_share"],
                "current_price": body["current_price"],
                "upside_downside": body["upside_downside"],
                "enterprise_value": body["valuation"]["enterprise_value"],
                "equity_value": body["valuation"]["equity_value"],
                "terminal_value": body["valuation"]["terminal_value"],
                "pv_terminal_value": body["valuation"]["pv_terminal_value"],
                "pv_ufcf_sum": body["valuation"]["pv_ufcf_sum"],
                "tv_pct_of_ev": body["valuation"]["tv_pct_of_ev"],
            },
            "note": body["note"],
        })
        print(f"  captured {label:<32} -> ${body['intrinsic_value_per_share']:,.4f}")

    if not cases:
        print("No cases captured.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"ticker": TICKER, "cases": cases}, indent=2))
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
