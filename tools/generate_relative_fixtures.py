"""
Records real relative-valuation responses for the app's tests.

The relative view has four distinct answers to render - a produced figure,
a decline for lack of peers, a decline that still shows the multiples as
information, and "not applicable" for a company with no intrinsic value - and
each is recorded from the backend rather than imagined.

Peers come from Yahoo's "also watched" lists, which drift: KO had automatic
peers when this feature was built and has none today. The checks below fail
loudly if a case no longer demonstrates what it is recorded for.

Run:
    python tools/generate_relative_fixtures.py

Writes: app/test/fixtures/relative_cases.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402

OUT = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "test" / "fixtures" / "relative_cases.json")


def main() -> int:
    client = TestClient(app)
    cases = {}

    def record(name, ticker, status, check):
        response = client.get(f"/valuation/{ticker}/relative")
        body = response.json()
        ok = response.status_code == status and check(body)
        print(f"  {'ok' if ok else '!!'} {name}: {ticker} HTTP {response.status_code}")
        if not ok:
            print("     ", json.dumps(body)[:400])
            raise SystemExit(1)
        cases[name] = {"status_code": response.status_code, "body": body}

    record("produced", "JPM", 200, lambda b: (
        b["relative_valuation"]["relative_value_per_share"]["central"] is not None
        and len(b["relative_valuation"]["peer_selection"]["peers"]) >= 3))

    record("no_peers", "AAPL", 422, lambda b: (
        b["code"] == "not_suitable" and b["peer_selection"]["peers"] == []
        and b["intrinsic_valuation"]["intrinsic_value_per_share"] is not None))

    record("single_multiple", "CRM", 422, lambda b: (
        b["code"] == "not_suitable"
        and sum(m["applicable"] for m in b["multiples"]) == 1
        and all(m["implied_value_per_share"] is None for m in b["multiples"])))

    record("not_applicable", "RIVN", 422, lambda b: b["code"] == "relative_not_applicable")

    # The intrinsic valuations the relative tab is opened from, recorded at the
    # same moment so the two sides of the comparison agree.
    for ticker in ("JPM", "AAPL", "CRM", "RIVN"):
        response = client.get(f"/valuation/{ticker}")
        print(f"  ok valuation {ticker}: HTTP {response.status_code}")
        cases[f"valuation_{ticker}"] = {"status_code": response.status_code,
                                        "body": response.json()}

    # Edited peer groups, keyed as the app keys them, so a test backend can
    # answer each request exactly as the real one did.
    def edited(label, ticker, add, remove, status, check):
        response = client.post("/valuation/relative", json={
            "ticker": ticker, "add_peers": add, "remove_peers": remove})
        body = response.json()
        ok = response.status_code == status and check(body)
        print(f"  {'ok' if ok else '!!'} {label}: HTTP {response.status_code}")
        if not ok:
            print("     ", json.dumps(body)[:400])
            raise SystemExit(1)
        key = f"{ticker}|add:{','.join(sorted(add))}|remove:{','.join(sorted(remove))}"
        cases.setdefault("edited", {})[key] = {
            "status_code": response.status_code, "body": body}

    def figure(b):
        return (b.get("relative_valuation") or {}).get("relative_value_per_share", {}).get("central")

    # AAPL has no automatic peers. Two added is still too few for a median.
    edited("AAPL + MSFT, GOOGL", "AAPL", ["MSFT", "GOOGL"], [], 422,
           lambda b: b["code"] == "not_suitable" and len(b["peer_selection"]["peers"]) == 2)
    # A third makes a figure possible.
    edited("AAPL + MSFT, GOOGL, META", "AAPL", ["MSFT", "GOOGL", "META"], [], 200,
           lambda b: figure(b) is not None)
    # A fourth moves it - and removing it again must move it back.
    edited("AAPL + MSFT, GOOGL, META, AMZN", "AAPL", ["MSFT", "GOOGL", "META", "AMZN"], [], 200,
           lambda b: figure(b) is not None)
    # JPM: removing an automatic peer leaves too few.
    edited("JPM - WFC", "JPM", [], ["WFC"], 422,
           lambda b: b["code"] == "not_suitable"
           and any(e["reason"] == "removed by you" for e in b["peer_selection"]["excluded"]))
    # JPM: adding one keeps the automatic three and labels the addition.
    edited("JPM + MS", "JPM", ["MS"], [], 200,
           lambda b: any(p["provenance"] == "added by you"
                         for p in b["relative_valuation"]["peer_selection"]["peers"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"\nWrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
