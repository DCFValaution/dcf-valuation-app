"""
Records real backend responses for the method-fit warning tests.

Robinhood is the case the warning exists for: valued with a DCF, with lending
income the data cannot see. Apple is the control - an ordinary company whose
caveats are all about assumptions, which must render exactly as before.

Run (the API is exercised in-process):
    python tools/generate_method_fit_fixtures.py

Writes: app/test/fixtures/method_fit_cases.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402

OUT = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "test" / "fixtures" / "method_fit_cases.json")


def main() -> int:
    client = TestClient(app)
    cases = {}

    def record(name, ticker, check):
        response = client.get(f"/valuation/{ticker}")
        body = response.json()
        ok = response.status_code == 200 and check(body)
        print(f"  {'ok' if ok else '!!'} {name}: HTTP {response.status_code}")
        if not ok:
            print(f"     unexpected: {json.dumps(body)[:400]}")
            raise SystemExit(1)
        cases[name] = {"status_code": response.status_code, "body": body}

    record("hood", "HOOD",
           lambda b: b.get("method") == "dcf" and len(b.get("method_fit_warnings", [])) == 1)
    record("aapl", "AAPL",
           lambda b: b.get("method") == "dcf" and b.get("method_fit_warnings") == [])

    OUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
