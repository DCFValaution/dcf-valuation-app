"""
Records real backend responses for each valuation method, for the Flutter tests.

The app has to parse two shapes of success - a discounted cash flow response
and a dividend discount model one, which carries no `base_year` at all - plus a
refusal that now says whether a speculative estimate could be asked for.
Hand-written JSON in a test only proves the app can parse what the test author
imagined; these are what the backend actually sends.

Captured in-process rather than over HTTP so no server needs to be running, but
through the real endpoint, so derivation and serialisation are included.

Run:
    python tools/generate_method_fixtures.py

Writes: app/test/fixtures/method_cases.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402

OUT = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "test" / "fixtures" / "method_cases.json")

# One of each thing the app must render: a bank and an insurer on the dividend
# model, an ordinary company on the DCF, and a loss-maker's refusal.
CASES = [
    ("bank", "JPM", "ddm"),
    ("insurer", "TRV", "ddm"),
    ("ordinary company", "AAPL", "dcf"),
    ("loss-making refusal", "RIVN", "refusal"),
]


def main() -> int:
    client = TestClient(app)
    captured = []

    for label, ticker, expected in CASES:
        response = client.get(f"/valuation/{ticker}")
        body = response.json()

        if expected == "refusal":
            got = "refusal" if body.get("code") == "not_suitable" else body.get("method")
        else:
            got = body.get("method")

        if got != expected:
            print(f"  ! {ticker}: expected {expected}, got {got} "
                  f"(HTTP {response.status_code})")
            return 1

        print(f"  {label}: {ticker} -> {expected} (HTTP {response.status_code})")
        captured.append({
            "label": label,
            "ticker": ticker,
            "expected": expected,
            "status_code": response.status_code,
            "body": body,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    print(f"\nWrote {len(captured)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
