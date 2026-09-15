"""
Records real backend responses whose sensitivity grids contain blank cells.

The ordinary fixtures have complete grids, so they cannot show that the app
renders the backend's null cells as blanks rather than as broken numbers.
These are what the backend actually sends at the edges.

Run:
    python tools/generate_sensitivity_fixtures.py

Writes: app/test/fixtures/sensitivity_cases.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402

OUT = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "test" / "fixtures" / "sensitivity_cases.json")


def main() -> int:
    client = TestClient(app)
    cases = {}

    def record(name, response, cell_is_null):
        body = response.json()
        grid = body["sensitivity"]["grid"]
        blanks = sum(cell is None for row in grid for cell in row)
        ok = response.status_code == 200 and blanks > 0 and cell_is_null(grid)
        print(f"  {'ok' if ok else '!!'} {name}: HTTP {response.status_code}, "
              f"{blanks} blank cells")
        if not ok:
            raise SystemExit(1)
        cases[name] = body

    # Growth near WACC: the upper-right cells have g >= WACC.
    record("aapl_growth_near_wacc",
           client.post("/valuation", json={
               "ticker": "AAPL", "overrides": {"wacc": 0.05, "terminal_growth": 0.045}}),
           lambda g: g[0][4] is None)

    # Speculative at its default: the 0% margin row is blank.
    record("rivn_zero_margin_row", client.get("/valuation/RIVN/speculative"),
           lambda g: all(cell is None for cell in g[0]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
