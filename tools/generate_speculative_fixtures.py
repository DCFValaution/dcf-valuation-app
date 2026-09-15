"""
Records real backend responses for the app's speculative flow tests.

The opt-in has to appear for exactly the right refusals and no others, and the
estimate screen has to render what the backend actually sends - a negative
figure, a full disclaimer, warnings about cash burn. Recorded, not imagined.

Run:
    python tools/generate_speculative_fixtures.py

Writes: app/test/fixtures/speculative_cases.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402

OUT = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "test" / "fixtures" / "speculative_cases.json")


def main() -> int:
    client = TestClient(app)
    cases = {}

    def record(name, response, expect_status, check=None):
        body = response.json()
        ok = response.status_code == expect_status and (check is None or check(body))
        print(f"  {'ok' if ok else '!!'} {name}: HTTP {response.status_code}")
        if not ok:
            print(f"     unexpected: {json.dumps(body)[:300]}")
            raise SystemExit(1)
        cases[name] = {"status_code": response.status_code, "body": body}

    # Eligible: refused only for losing money, so the opt-in is offered.
    record("rivn_refusal", client.get("/valuation/RIVN"), 422,
           lambda b: b.get("speculative_estimate_available") is True)
    record("rivn_speculative", client.get("/valuation/RIVN/speculative"), 200,
           lambda b: b.get("speculative_value_per_share", 0) < 0)
    record("rivn_speculative_overridden",
           client.post("/valuation/speculative", json={
               "ticker": "RIVN",
               "overrides": {"target_operating_margin": 0.25,
                             "years_to_profitability": 4}}), 200,
           lambda b: any(a["source"] == "override" for a in b["assumptions"]))

    # Not eligible: refused for more than losses.
    record("qs_refusal", client.get("/valuation/QS"), 422,
           lambda b: b.get("speculative_estimate_available") is False)
    record("axp_refusal", client.get("/valuation/AXP"), 422,
           lambda b: b.get("speculative_estimate_available") is False)

    # Even the speculative path refuses: a no-revenue company.
    record("qs_speculative_refused", client.get("/valuation/QS/speculative"), 422)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
