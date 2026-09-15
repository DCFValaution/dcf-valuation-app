"""
Selection, overrides, gating and labelling tests for the relative valuation.

Market data is stubbed. The co-watched lists reproduce what probing found: a
candidate pool that mixes genuine peers with other industries, a second share
class, an ADR reporting in another currency, and a company far too small to
compare. The standard, DDM and speculative paths are proved untouched by making
the relative data fetchers explode if any of them is called there.

Run with:  python -m pytest test_relative_api.py -v
"""

import json

import pytest
from fastapi.testclient import TestClient

import analysis
import api
import assumptions as A
import ddm_assumptions as D
from api import app
from market_data import MarketFigures, RateLimitedError, TickerNotFoundError
from test_api import LOSSMAKING, PROFITABLE, fake_fetch_financials, make_fin
from test_ddm_api import DCF_FIELDS_BEFORE_THE_DDM, make_bank
from test_ddm_assumptions import STEADY, TODAY, quarterly

SOFTWARE = "Software - Application"
BANKS = "Banks - Diversified"
BANKX = "BANKX"
FEW = "FEWCO"


def figs(ticker, name, *, price, shares=1_000.0, revenue=None, ebitda=None, net_income=None,
         debt=0.0, cash=0.0, equity=None, report="USD", as_of="2026-06-30") -> MarketFigures:
    return MarketFigures(ticker=ticker, name=name, trading_currency="USD", reporting_currency=report,
                         price=price, revenue=revenue, ebitda=ebitda, net_income=net_income,
                         total_debt=debt, cash=cash, shares=shares, common_equity=equity, as_of=as_of)


FIGURES = {
    # The company: P/E 50, EV/EBITDA 33.5, P/S 10.
    PROFITABLE: figs(PROFITABLE, "Profitable Corp.", price=100, revenue=10_000, ebitda=3_000,
                     net_income=2_000, debt=1_000, cash=500, equity=5_000),
    # Peers: P/E 20/25/30, EV/EBITDA 10/12.5/15, P/S 4/5/6.
    "P1": figs("P1", "Peer One Inc.", price=50, revenue=12_500, ebitda=5_000, net_income=2_500),
    "P2": figs("P2", "Peer Two Inc.", price=80, revenue=16_000, ebitda=6_400, net_income=3_200),
    "P3": figs("P3", "Peer Three Inc.", price=120, revenue=20_000, ebitda=8_000, net_income=4_000),
    "P1B": figs("P1B", "Peer One Inc.", price=51, revenue=12_500, ebitda=5_000, net_income=2_500),
    "ADRX": figs("ADRX", "Overseas Software Ltd", price=90, revenue=15_000, ebitda=5_000,
                 net_income=3_000, report="CNY"),
    "TINYCO": figs("TINYCO", "Tiny Software Co.", price=1, revenue=200, ebitda=80, net_income=40),
    "OTHER": figs("OTHER", "Other Bank Corp.", price=90, revenue=18_000, ebitda=6_000, net_income=3_000),
    # Unprofitable: only its price/sales (6x) is meaningful.
    "LOSSPEER": figs("LOSSPEER", "Loss Peer Inc.", price=60, revenue=10_000, ebitda=-500, net_income=-800),
    # A bank peer with no book value reported: only its P/E is meaningful.
    "NOEQ": figs("NOEQ", "No Book Bank Corp.", price=70, net_income=5_000, equity=None),
    FEW: figs(FEW, "Few Peers Inc.", price=100, revenue=10_000, ebitda=3_000, net_income=2_000),
    # A bank: P/E 12.5, P/B 1.25. Its peers: P/E 12/14/16, P/B 1.2/1.4/1.6.
    BANKX: figs(BANKX, "Test Bank Corp.", price=50, net_income=4_000, equity=40_000),
    "B1": figs("B1", "Bank One Corp.", price=60, net_income=5_000, equity=50_000),
    "B2": figs("B2", "Bank Two Corp.", price=70, net_income=5_000, equity=50_000),
    "B3": figs("B3", "Bank Three Corp.", price=80, net_income=5_000, equity=50_000),
}

ALSO_WATCHED = {
    PROFITABLE: ["P1", "P2", "OTHER", "P3", "P1B", "ADRX", "TINYCO"],
    FEW: ["P1", "OTHER"],
    BANKX: ["B1", "B2", "B3"],
}

INDUSTRY = {"P1": SOFTWARE, "P2": SOFTWARE, "P3": SOFTWARE, "P1B": SOFTWARE, "ADRX": SOFTWARE,
            "TINYCO": SOFTWARE, "OTHER": BANKS, "B1": BANKS, "B2": BANKS, "B3": BANKS,
            "LOSSPEER": SOFTWARE, "NOEQ": BANKS}


def fake_financials(ticker, years=5):
    upper = ticker.upper()
    if upper == BANKX:
        return make_bank(BANKX)
    if upper in (PROFITABLE, FEW):
        fin = make_fin(upper, "Profitable Corp." if upper == PROFITABLE else "Few Peers Inc.")
        fin.profile["industry"] = SOFTWARE
        return fin
    return fake_fetch_financials(ticker, years)


def fake_figures(ticker):
    if ticker == "NOSUCH":
        raise TickerNotFoundError("No security found for 'NOSUCH' on Yahoo Finance.")
    return FIGURES[ticker]


def fake_classification(ticker):
    return {"sector": None, "industry": INDUSTRY.get(ticker), "quote_type": "EQUITY",
            "name": FIGURES[ticker].name if ticker in FIGURES else None}


@pytest.fixture(autouse=True)
def _stubbed(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", fake_financials)
    monkeypatch.setattr(analysis, "fetch_dividends", lambda t: quarterly(STEADY))
    real = D.analyse_dividends
    monkeypatch.setattr(analysis, "analyse_dividends", lambda events: real(events, today=TODAY))
    monkeypatch.setattr(analysis, "fetch_also_watched", lambda t: ALSO_WATCHED.get(t, []))
    monkeypatch.setattr(analysis, "fetch_classification", fake_classification)
    monkeypatch.setattr(analysis, "fetch_market_figures", fake_figures)
    monkeypatch.setattr(analysis, "_today", lambda: TODAY)
    monkeypatch.setattr(A, "fetch_risk_free_rate", lambda tenor="year10": (0.045, "pinned for test"))
    api.reset_rate_limits()


@pytest.fixture
def client():
    return TestClient(app)


def _explode_relative_fetchers(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("relative valuation data was fetched where it must not be")
    for name in ("fetch_also_watched", "fetch_classification", "fetch_market_figures"):
        monkeypatch.setattr(analysis, name, never)


def block(body):
    return body["relative_valuation"]


# ---------------------------------------------------------------------------
# Never reached from anywhere else
# ---------------------------------------------------------------------------

def test_the_standard_valuation_is_unchanged_and_never_fetches_peers(client, monkeypatch):
    _explode_relative_fetchers(monkeypatch)
    body = client.get(f"/valuation/{PROFITABLE}").json()
    assert body["method"] == "dcf"
    assert set(body) == DCF_FIELDS_BEFORE_THE_DDM | {"method"}


def test_the_ddm_valuation_never_fetches_peers(client, monkeypatch):
    _explode_relative_fetchers(monkeypatch)
    body = client.get(f"/valuation/{BANKX}").json()
    assert body["method"] == "ddm"
    assert "relative_valuation" not in body


def test_the_speculative_estimate_never_fetches_peers(client, monkeypatch):
    _explode_relative_fetchers(monkeypatch)
    r = client.get(f"/valuation/{LOSSMAKING}/speculative")
    assert r.status_code == 200
    assert "relative_valuation" not in r.json()


def test_no_other_response_schema_can_carry_a_relative_block(client):
    schema = client.get("/openapi.json").json()
    for path, verb in [("/valuation/{ticker}", "get"), ("/valuation", "post"),
                       ("/valuation/{ticker}/speculative", "get")]:
        assert "Relative" not in json.dumps(schema["paths"][path][verb]["responses"]["200"])
    assert "RelativeValuationResponse" in schema["components"]["schemas"]


def test_a_company_the_intrinsic_valuation_refuses_gets_no_relative_figure(client, monkeypatch):
    """Otherwise peer multiples would be a back door to a number the guards withhold."""
    _explode_relative_fetchers(monkeypatch)
    r = client.get(f"/valuation/{LOSSMAKING}/relative")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "relative_not_applicable"
    assert "second opinion, never a substitute" in body["message"]
    assert "loss-making" in " ".join(body["reasons"])


# ---------------------------------------------------------------------------
# A relative figure, clearly labelled
# ---------------------------------------------------------------------------

def test_the_relative_block_cannot_be_mistaken_for_an_intrinsic_value(client):
    body = client.get(f"/valuation/{PROFITABLE}/relative").json()
    rel = block(body)
    assert rel["method"] == "relative"
    assert rel["basis"] == "market"
    assert rel["is_intrinsic_valuation"] is False
    assert rel["framing"] == "How the market prices similar companies right now"
    assert "intrinsic_value_per_share" not in rel
    note = rel["note"]
    for phrase in ("not an estimate of intrinsic value", "inherits the market's own mispricing",
                   "change the peer group and the figure changes", "second opinion"):
        assert phrase in note, phrase


def test_the_intrinsic_value_sits_beside_it_unchanged(client):
    standard = client.get(f"/valuation/{PROFITABLE}").json()
    body = client.get(f"/valuation/{PROFITABLE}/relative").json()
    intrinsic = body["intrinsic_valuation"]
    assert intrinsic["method"] == "dcf"
    assert intrinsic["intrinsic_value_per_share"] == standard["intrinsic_value_per_share"]
    assert intrinsic["note"] == standard["note"]
    assert intrinsic["full_valuation"] == f"/valuation/{PROFITABLE}"


def test_automatic_peers_are_named_and_every_exclusion_says_why(client):
    selection = block(client.get(f"/valuation/{PROFITABLE}/relative").json())["peer_selection"]
    assert selection["mode"] == "automatic"
    assert [(p["ticker"], p["name"], p["provenance"]) for p in selection["peers"]] == [
        ("P1", "Peer One Inc.", "selected"),
        ("P2", "Peer Two Inc.", "selected"),
        ("P3", "Peer Three Inc.", "selected"),
    ]
    reasons = {e["ticker"]: e["reason"] for e in selection["excluded"]}
    assert "in Banks - Diversified, not Software - Application" in reasons["OTHER"]
    assert reasons["P1B"] == "another share class of a company already in the group"
    assert "reports its results in CNY but its shares trade in USD" in reasons["ADRX"]
    assert "too different in size to compare fairly" in reasons["TINYCO"]


def test_multiples_medians_and_implied_values_match_a_hand_calculation(client):
    rel = block(client.get(f"/valuation/{PROFITABLE}/relative").json())
    by_name = {m["name"]: m for m in rel["multiples"]}
    assert by_name["pe"]["company_value"] == pytest.approx(50.0)
    assert by_name["pe"]["peer_median"] == pytest.approx(25.0)
    assert by_name["ev_ebitda"]["peer_median"] == pytest.approx(12.5)
    assert by_name["ps"]["peer_median"] == pytest.approx(5.0)
    assert by_name["pe"]["premium_to_median"] == pytest.approx(1.0)
    assert by_name["pe"]["implied_value_per_share"] == pytest.approx(50.0)
    assert by_name["ev_ebitda"]["implied_value_per_share"] == pytest.approx(37.0)
    assert by_name["ps"]["implied_value_per_share"] == pytest.approx(50.0)
    value = rel["relative_value_per_share"]
    assert (value["central"], value["low"], value["high"]) == pytest.approx((50.0, 37.0, 50.0))
    assert value["multiples_applied"] == ["P/E", "EV/EBITDA", "Price/Sales"]


def test_the_gap_to_the_intrinsic_value_is_stated_without_taking_sides(client):
    body = client.get(f"/valuation/{PROFITABLE}/relative").json()
    comparison = block(body)["comparison_with_intrinsic"]
    intrinsic = body["intrinsic_valuation"]["intrinsic_value_per_share"]
    assert comparison["gap"] == pytest.approx(50.0 / intrinsic - 1)
    assert "information, not an error in either" in comparison["statement"]


def test_a_bank_is_compared_on_earnings_and_book_value(client):
    rel = block(client.get(f"/valuation/{BANKX}/relative").json())
    by_name = {m["name"]: m for m in rel["multiples"]}
    assert set(by_name) == {"pe", "pb"}
    assert by_name["pe"]["peer_median"] == pytest.approx(14.0)
    assert by_name["pb"]["peer_median"] == pytest.approx(1.4)
    assert rel["relative_value_per_share"]["central"] == pytest.approx(56.0)
    assert rel["comparison_with_intrinsic"]["intrinsic_method"] == "ddm"


def test_a_thin_group_is_flagged(client):
    warnings = block(client.get(f"/valuation/{PROFITABLE}/relative").json())["warnings"]
    assert any("With only 3 peers" in w for w in warnings)


# ---------------------------------------------------------------------------
# Declining where peers cannot support a figure
# ---------------------------------------------------------------------------

def test_too_few_peers_declines_and_says_who_was_found_and_why_not_more(client):
    r = client.get(f"/valuation/{FEW}/relative")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    assert body["method"] == "relative"
    assert "relative_value_per_share" not in json.dumps(body)
    reason = body["reasons"][0]
    assert "Only 1 comparable company could be found: P1 (Peer One Inc.)" in reason
    # Automatic selection came up short, so the reason explains it and offers
    # the remedy.
    assert "Peers are chosen automatically" in reason
    assert "You can add your own peers" in reason
    assert any(e["ticker"] == "OTHER" for e in body["peer_selection"]["excluded"])
    # The intrinsic valuation is unaffected by the relative lens declining.
    assert body["intrinsic_valuation"]["intrinsic_value_per_share"] > 0


def test_no_peers_at_all_is_said_plainly(client):
    r = client.post("/valuation/relative", json={"ticker": BANKX, "remove_peers": ["B1", "B2", "B3"]})
    assert r.status_code == 422
    # Every peer was removed by the user, so the plain statement is that none
    # are left - not that none could be found.
    assert r.json()["reasons"][0].startswith("No companies are left to compare.")


def test_the_decline_message_keeps_the_companys_full_name(client):
    body = client.get(f"/valuation/{FEW}/relative").json()
    assert body["message"].startswith("No relative valuation is produced for Few Peers Inc.: ")


def test_one_multiple_is_information_not_a_relative_valuation(client):
    """
    Salesforce's real case: an unprofitable peer leaves only price-to-sales with
    three values, and that median was pulled up by Snowflake at 21x sales. The
    comparison is shown; no figure is drawn from it.
    """
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE,
                                                 "peers": ["P1", "P2", "LOSSPEER"]})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable" and body["method"] == "relative"

    reason = body["reasons"][0]
    assert "Only one of the multiples" in reason and "Price/Sales" in reason
    assert "too fragile to report" in reason
    assert "shown as information instead" in reason

    # The multiple itself is visible - median, peers and all - but no value is
    # drawn from it, here or anywhere else in the response.
    by_name = {m["name"]: m for m in body["multiples"]}
    assert by_name["ps"]["applicable"] is True
    assert by_name["ps"]["peer_median"] == pytest.approx(5.0)
    assert by_name["ps"]["company_value"] == pytest.approx(10.0)
    assert [p["value"] for p in by_name["ps"]["peers"]] == pytest.approx([4.0, 5.0, 6.0])
    assert by_name["ps"]["implied_value_per_share"] is None
    assert by_name["pe"]["applicable"] is False
    assert "relative_value_per_share" not in json.dumps(body)
    assert body["company_figures"]["price"] == pytest.approx(100.0)

    # And the intrinsic valuation is shown exactly as it would be anyway.
    standard = client.get(f"/valuation/{PROFITABLE}").json()
    assert body["intrinsic_valuation"]["intrinsic_value_per_share"] == \
        standard["intrinsic_value_per_share"]


def test_a_bank_with_only_one_usable_multiple_is_declined_too(client):
    """Two multiples apply to a bank at most, so one missing book value is enough."""
    r = client.post("/valuation/relative", json={"ticker": BANKX,
                                                 "peers": ["B1", "B2", "NOEQ"]})
    assert r.status_code == 422
    body = r.json()
    assert "Only one of the multiples" in body["reasons"][0]
    by_name = {m["name"]: m for m in body["multiples"]}
    assert by_name["pe"]["applicable"] is True and by_name["pe"]["implied_value_per_share"] is None
    assert by_name["pb"]["applicable"] is False


def test_two_multiples_are_enough_for_a_figure(client):
    """The threshold is two, not three: a bank's P/E and P/B suffice."""
    rel = block(client.get(f"/valuation/{BANKX}/relative").json())
    assert rel["relative_value_per_share"]["multiples_applied"] == ["P/E", "Price/Book"]
    assert rel["relative_value_per_share"]["central"] == pytest.approx(56.0)


def test_a_throttled_peer_fetch_is_a_429_not_a_claim_that_peers_are_missing(client, monkeypatch):
    def throttled(ticker):
        if ticker == "P3":
            raise RateLimitedError("Yahoo Finance is rate-limiting requests right now.")
        return FIGURES[ticker]
    monkeypatch.setattr(analysis, "fetch_market_figures", throttled)
    r = client.get(f"/valuation/{PROFITABLE}/relative")
    assert r.status_code == 429
    assert r.json()["code"] == "upstream_rate_limited"


# ---------------------------------------------------------------------------
# The peer group is overridable
# ---------------------------------------------------------------------------

def test_a_supplied_peer_group_replaces_automatic_selection(client, monkeypatch):
    monkeypatch.setattr(analysis, "fetch_also_watched",
                        lambda t: (_ for _ in ()).throw(AssertionError("should not discover peers")))
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE, "peers": ["P1", "P2", "P3"]})
    assert r.status_code == 200
    selection = block(r.json())["peer_selection"]
    assert selection["mode"] == "your list"
    assert {p["provenance"] for p in selection["peers"]} == {"chosen by you"}


def test_an_added_peer_is_labelled_and_a_different_industry_is_flagged(client):
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE, "add_peers": ["OTHER"]})
    assert r.status_code == 200
    rel = block(r.json())
    assert rel["peer_selection"]["mode"] == "automatic, edited by you"
    added = next(p for p in rel["peer_selection"]["peers"] if p["ticker"] == "OTHER")
    assert added["provenance"] == "added by you"
    assert any("OTHER (Other Bank Corp.) is in Banks - Diversified" in w for w in rel["warnings"])


def test_a_removed_peer_is_recorded_and_can_leave_too_few(client):
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE, "remove_peers": ["P3"]})
    assert r.status_code == 422
    body = r.json()
    assert {"ticker": "P3", "name": None, "reason": "removed by you"} in body["peer_selection"]["excluded"]
    # The user removed a peer, so the group is theirs: nothing was "found".
    assert "Only 2 of these companies could be compared" in body["reasons"][0]
    assert "could be found" not in body["reasons"][0]


def test_a_decline_explains_the_peer_group_the_way_it_was_actually_made(client):
    """Automatic, automatic with the user's edits, or the user's own list: the
    remedy differs, so the explanation must too - and must never describe
    automatic selection for peers the user picked."""
    automatic = client.get(f"/valuation/{FEW}/relative").json()["reasons"][0]
    edited = client.post("/valuation/relative", json={
        "ticker": FEW, "add_peers": ["NOSUCH"]}).json()["reasons"][0]
    supplied = client.post("/valuation/relative", json={
        "ticker": FEW, "peers": ["P1"]}).json()["reasons"][0]

    assert "Peers are chosen automatically" in automatic

    assert "automatic selection with your changes" in edited
    assert "Peers are chosen automatically" not in edited
    assert "people often look at" not in edited

    assert "These are the companies you chose" in supplied
    assert "Peers are chosen automatically" not in supplied
    assert "automatic selection" not in supplied


def test_a_peer_the_user_chose_is_not_held_to_the_size_band_but_is_flagged(client):
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE, "peers": ["P1", "P2", "TINYCO"]})
    assert r.status_code == 200
    rel = block(r.json())
    assert "TINYCO" in [p["ticker"] for p in rel["peer_selection"]["peers"]]
    assert any("TINYCO is less than 1/10 the size" in w and "You chose it" in w
               for w in rel["warnings"])


def test_an_unknown_supplied_peer_is_excluded_not_fatal(client):
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE,
                                                 "peers": ["P1", "P2", "P3", "NOSUCH"]})
    assert r.status_code == 200
    excluded = block(r.json())["peer_selection"]["excluded"]
    assert {"ticker": "NOSUCH", "name": None, "reason": "not found on Yahoo Finance"} in excluded


def test_a_malformed_peer_is_a_400(client):
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE, "peers": ["NOT A TICKER"]})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_override"


def test_the_peer_list_is_kept_modest(client):
    r = client.post("/valuation/relative", json={"ticker": PROFITABLE,
                                                 "peers": [f"P{i}" for i in range(9)]})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
