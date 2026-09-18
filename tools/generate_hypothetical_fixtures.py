"""
Records real backend responses for the app's user-built hypothetical tests.

The screen's whole claim is that nothing on it is derived and that the
company's own figures travel with every assumption. Tests that assert those
things against invented JSON would prove nothing about the backend, so every
case here is what the live API actually returned for a real shrinking
loss-maker.

Run (no server needed; the API is exercised in-process):
    python tools/generate_hypothetical_fixtures.py

Writes: app/test/fixtures/hypothetical_cases.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402

OUT = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "test" / "fixtures" / "hypothetical_cases.json")

# Intel: loses money and is shrinking, so the speculative engine refuses it.
SHRINKER = "INTC"
ASSUMED = {"revenue_growth": 0.08, "target_operating_margin": 0.15,
           "years_to_target": 5}
NEUTRAL = {"revenue_growth": 0.0, "target_operating_margin": 0.0,
           "years_to_target": 5}


def main() -> int:
    client = TestClient(app)
    cases = {}

    def record(name, response, expect_status, check=None):
        body = response.json()
        ok = response.status_code == expect_status and (check is None or check(body))
        print(f"  {'ok' if ok else '!!'} {name}: HTTP {response.status_code}")
        if not ok:
            print(f"     unexpected: {json.dumps(body)[:400]}")
            raise SystemExit(1)
        cases[name] = {"status_code": response.status_code, "body": body}

    def hypothetical(ticker, params):
        return client.get(f"/valuation/{ticker}/hypothetical", params=params)

    # The refusal that offers the opt-in, and the one that does not.
    record("intc_speculative_refused", client.get(f"/valuation/{SHRINKER}/speculative"), 422,
           lambda b: b.get("hypothetical_available") is True
           and b.get("hypothetical_placeholders") is not None)
    record("qs_speculative_refused", client.get("/valuation/QS/speculative"), 422,
           lambda b: b.get("hypothetical_available") is False)

    # The neutral starting point: no profit assumed, so no figure.
    record("intc_neutral", hypothetical(SHRINKER, NEUTRAL), 422,
           lambda b: b.get("code") == "hypothetical_incomplete")

    # A hypothetical the company's own figures contradict.
    record("intc_hypothetical", hypothetical(SHRINKER, ASSUMED), 200,
           lambda b: b.get("is_valuation") is False
           and b.get("derived_from_company_data") is False
           and any(c["contradicts"] for c in b["reality"]))

    # Assuming the decline continues contradicts nothing.
    record("intc_pessimistic",
           hypothetical(SHRINKER, {**ASSUMED, "revenue_growth": -0.10}), 200)

    # Refused even here: nothing to grow from.
    record("qs_hypothetical_refused", hypothetical("QS", ASSUMED), 422,
           lambda b: b.get("code") == "hypothetical_not_applicable")

    # A company the speculative estimate already serves is sent back to it.
    record("rivn_hypothetical_refused", hypothetical("RIVN", ASSUMED), 422)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
