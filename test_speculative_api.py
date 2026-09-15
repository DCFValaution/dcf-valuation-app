"""
Gating, derivation and guard tests for the opt-in speculative estimate.

The first group matters most: a loss-making company must be refused by default
exactly as before, and nothing on the standard endpoints may ever reach the
speculative path - proved by making that path explode if it is touched.

Market data is stubbed with synthetic companies shaped like the real ones
probed: a Rivian-like grower, and companies shaped like QuantumScape (no
revenue), Beyond Meat (shrinking), Lucid (losses too deep) and a business
that loses money on every sale.

Run with:  python -m pytest test_speculative_api.py -v
"""

import json

import pytest
from fastapi.testclient import TestClient

import analysis
import api
import assumptions as A
import speculative_assumptions as S
from api import app
from market_data import base_year_from
from test_api import PROFITABLE, fake_fetch_financials, make_fin
from test_ddm_api import financial, make_bank

GROWER = "GROWER"          # Rivian-like: growing, -67% margin, 3% gross margin
NOREV = "NOREV"            # QuantumScape-like: no revenue
TINY = "TINY"              # a few $mm of revenue
SHRINKER = "SHRINK"        # Beyond Meat-like: revenue falling
DEEPLOSS = "DEEP"          # Lucid-like depth: -200% margin
UNDERWATER = "UNDERW"      # loses money on every sale
BANK = "BANKX"             # valued with the DDM
LENDERLOSS = "LENDLOSS"    # a loss-making lender: refused for more than losses


def reshape(fin, *, revenues=None, margin=None, gross=None, operating_income=None):
    for i, row in enumerate(fin.income):
        if revenues is not None:
            row["revenue"] = revenues[i]
        if margin is not None:
            row["operatingIncome"] = row["revenue"] * margin
        if operating_income is not None:
            row["operatingIncome"] = operating_income
        if gross is not None:
            row["grossProfit"] = row["revenue"] * gross
    return fin


def grower(ticker=GROWER, *, margin=-0.67, gross=0.03, revenues=None, capex_share=None):
    fin = make_fin(ticker, "Grower Motors Inc.", sector="Consumer Cyclical", operating_margin=margin)
    fin.profile["industry"] = "Auto Manufacturers"
    reshape(fin, revenues=revenues, margin=margin, gross=gross)
    if capex_share is not None:
        for cashflow, income in zip(fin.cashflow, fin.income):
            cashflow["capitalExpenditure"] = -income["revenue"] * capex_share
    return fin


BUILDERS = {
    GROWER: lambda: grower(),
    NOREV: lambda: reshape(grower(NOREV, gross=None), revenues=[0.0] * 4,
                           operating_income=-500e6),
    TINY: lambda: grower(TINY, revenues=[5e6 * 1.05 ** (3 - i) for i in range(4)], margin=-0.5),
    SHRINKER: lambda: grower(SHRINKER, revenues=[10_000e6 * 1.10 ** i for i in range(4)],
                             margin=-0.30, gross=0.30),
    DEEPLOSS: lambda: grower(DEEPLOSS, margin=-2.00, gross=0.20),
    UNDERWATER: lambda: grower(UNDERWATER, margin=-0.40, gross=-0.10),
    BANK: lambda: make_bank(BANK),
    LENDERLOSS: lambda: reshape(financial("Credit Services", interest_share=0.35,
                                          ticker=LENDERLOSS), margin=-0.30),
}


def fake_financials(ticker, years=5):
    upper = ticker.upper()
    if upper in BUILDERS:
        return BUILDERS[upper]()
    return fake_fetch_financials(ticker, years)


def _no_dividends(ticker):
    raise AssertionError(f"dividends fetched for {ticker}: the speculative path must not use the DDM")


@pytest.fixture(autouse=True)
def _stubbed(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", fake_financials)
    monkeypatch.setattr(analysis, "fetch_dividends", _no_dividends)
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))
    api.reset_rate_limits()


@pytest.fixture
def client():
    return TestClient(app)


def _explode_if_speculative_runs(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the speculative path ran without being asked for")
    monkeypatch.setattr(analysis, "run_speculative", never)
    monkeypatch.setattr(analysis, "derive_speculative_assumptions", never)
    monkeypatch.setattr(analysis, "value_company_speculatively", never)


# ---------------------------------------------------------------------------
# Never automatic
# ---------------------------------------------------------------------------

def test_a_loss_maker_is_still_refused_by_default_exactly_as_before(client, monkeypatch):
    _explode_if_speculative_runs(monkeypatch)
    r = client.get(f"/valuation/{GROWER}")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    assert body["method"] == "dcf"
    assert body["intrinsic_value_per_share"] is None
    # The standard refusal's shape, and no speculative figure smuggled into it -
    # only the flag saying one could be asked for.
    assert set(body) == {"code", "method", "company", "suitable", "intrinsic_value_per_share",
                         "message", "reasons", "assumptions", "speculative_estimate_available"}


@pytest.mark.parametrize("ticker, available, why", [
    (GROWER, True, "refused only for losing money"),
    (NOREV, False, "also refused for having no revenue: the opt-in would refuse it again"),
    (LENDERLOSS, False, "also refused as a lender, which no projection of profits addresses"),
    (DEEPLOSS, True, "refused only for losing money, however deeply"),
])
def test_a_refusal_says_whether_a_speculative_estimate_can_be_asked_for(client, ticker, available, why):
    body = client.get(f"/valuation/{ticker}").json()
    assert body["speculative_estimate_available"] is available, why


def test_overrides_on_the_standard_endpoint_cannot_reach_the_speculative_path(client, monkeypatch):
    _explode_if_speculative_runs(monkeypatch)
    r = client.post("/valuation", json={"ticker": GROWER,
                                        "overrides": {"operating_margin": 0.10}})
    assert r.status_code == 422
    assert r.json()["method"] == "dcf"


def test_the_standard_endpoint_does_not_even_accept_speculative_assumptions(client):
    r = client.post("/valuation", json={"ticker": GROWER,
                                        "overrides": {"target_operating_margin": 0.10}})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_the_excel_export_never_produces_a_speculative_workbook(client, monkeypatch):
    _explode_if_speculative_runs(monkeypatch)
    r = client.get(f"/valuation/{GROWER}/excel")
    assert r.status_code == 422
    assert r.json()["method"] == "dcf"


def test_no_standard_response_schema_can_carry_a_speculative_result(client):
    schema = client.get("/openapi.json").json()
    for path, verb in [("/valuation/{ticker}", "get"), ("/valuation", "post")]:
        ok = schema["paths"][path][verb]["responses"]["200"]
        assert "Speculative" not in json.dumps(ok)
    assert "SpeculativeValuationResponse" in schema["components"]["schemas"]


def test_valuable_companies_are_untouched_by_default(client, monkeypatch):
    _explode_if_speculative_runs(monkeypatch)
    body = client.get(f"/valuation/{PROFITABLE}").json()
    assert body["method"] == "dcf" and body["suitable"] is True


# ---------------------------------------------------------------------------
# Only on request
# ---------------------------------------------------------------------------

def test_a_speculative_estimate_is_produced_when_explicitly_requested(client):
    r = client.get(f"/valuation/{GROWER}/speculative")
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "speculative"
    assert body["is_valuation"] is False
    assert isinstance(body["speculative_value_per_share"], float)
    assert "intrinsic_value_per_share" not in body, \
        "a speculative figure must never wear a valuation's name"
    assert "loss-making" in body["why_standard_valuation_refused"][0]
    assert len(body["path_to_profitability"]) == 6


def test_the_disclaimer_is_unmistakable_and_names_its_assumptions(client):
    body = client.get(f"/valuation/{GROWER}/speculative").json()
    assert body["disclaimer_headline"] == "SPECULATIVE ESTIMATE - NOT A VALUATION"

    disclaimer = body["disclaimer"]
    assert disclaimer.startswith("Grower Motors Inc. loses money")
    for phrase in ("not a valuation and must not be treated as one",
                   "explicitly requested",
                   "a future that has not happened",
                   "never reach sustained profitability",
                   "dilute existing shareholders",
                   "lose everything",
                   "by multiples, not percentages",
                   "not investment advice"):
        assert phrase in disclaimer, phrase
    # Specific, not boilerplate: the actual assumptions it rests on.
    assert "5.0% a year" in disclaimer
    assert "from -67% to 10% within 6 years" in disclaimer
    assert "bn of losses" in disclaimer


def test_the_disclaimer_is_stronger_than_the_standard_note(client):
    speculative = client.get(f"/valuation/{GROWER}/speculative").json()["disclaimer"]
    standard = client.get(f"/valuation/{PROFITABLE}").json()["note"]
    assert "not a valuation" in speculative and "not a valuation" not in standard
    assert len(speculative) > 2 * len(standard)


def test_every_speculative_assumption_is_labelled_with_its_source(client):
    body = client.get(f"/valuation/{GROWER}/speculative").json()
    by_name = {a["name"]: a for a in body["assumptions"]}
    assert set(by_name) == set(S.SPECULATIVE_ASSUMPTION_NAMES)
    assert by_name["target_operating_margin"]["source"] == "default"
    assert by_name["tax_rate"]["source"] == "default"
    assert by_name["years_to_profitability"]["source"] == "derived"
    assert by_name["years_to_profitability"]["value"] == 6
    assert by_name["speculative_revenue_growth"]["source"] == "derived"
    assert by_name["speculative_revenue_growth"]["value"] == pytest.approx(0.05)
    assert all(a["detail"] for a in body["assumptions"])
    assert {a["name"] for a in body["wacc_inputs"]} >= {"risk_free_rate", "equity_risk_premium", "beta"}


def test_the_estimate_is_the_engines_figure(client):
    body = client.get(f"/valuation/{GROWER}/speculative").json()
    report = analysis.value_company_speculatively(GROWER)
    assert body["speculative_value_per_share"] == pytest.approx(report.result.value_per_share)
    s = body["sensitivity"]
    assert s["grid"][s["centre_row"]][s["centre_col"]] == pytest.approx(
        body["speculative_value_per_share"], rel=1e-9)


def test_speculative_assumptions_can_be_overridden(client):
    base = client.get(f"/valuation/{GROWER}/speculative").json()["speculative_value_per_share"]
    r = client.post("/valuation/speculative", json={
        "ticker": GROWER, "overrides": {"target_operating_margin": 0.20, "years_to_profitability": 4}})
    assert r.status_code == 200
    body = r.json()
    sources = {a["name"]: a["source"] for a in body["assumptions"]}
    assert sources["target_operating_margin"] == "override"
    assert sources["years_to_profitability"] == "override"
    assert body["speculative_value_per_share"] > base


def test_the_cost_of_getting_there_is_disclosed(client):
    warnings = client.get(f"/valuation/{GROWER}/speculative").json()["warnings"]
    assert any("burns" in w and "dilute" in w for w in warnings)
    assert any("gross margin was 3%" in w for w in warnings)


def test_a_positive_estimate_says_it_rests_on_the_future(client):
    body = client.post("/valuation/speculative", json={"ticker": GROWER, "overrides": {
        "target_operating_margin": 0.40, "speculative_revenue_growth": 0.25}}).json()
    assert body["speculative_value_per_share"] > 0
    assert any("after the assumed turn to profit" in w for w in body["warnings"])


def test_a_negative_estimate_says_so_and_claims_nothing_about_shares_of_it(client):
    """With a 1% target the losses on the way outweigh the business at the end."""
    body = client.post("/valuation/speculative", json={"ticker": GROWER, "overrides": {
        "target_operating_margin": 0.01}}).json()
    assert body["speculative_value_per_share"] < 0
    assert body["valuation"]["share_from_after_profitability"] is None
    assert any("the estimate is negative" in w for w in body["warnings"])
    assert not any("comes from after the assumed turn to profit" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# Not offered to companies that can be valued, or were refused for other reasons
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker, phrase", [
    (PROFITABLE, "can be valued with a standard DCF"),
    (BANK, "valued with the dividend discount model"),
    (LENDERLOSS, "reasons other than losing money"),
])
def test_it_is_never_offered_where_a_valuation_applies_or_other_guards_refuse(client, ticker, phrase):
    r = client.get(f"/valuation/{ticker}/speculative")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "speculative_not_applicable"
    assert phrase in body["message"]
    assert "speculative_value_per_share" not in body
    assert body["standard_valuation"] == f"/valuation/{ticker}"


# ---------------------------------------------------------------------------
# Refused where even a speculative path is meaningless
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker, phrase", [
    (NOREV, "reported no revenue"),
    (TINY, "too small to anchor a projection"),
    (SHRINKER, "revenue has fallen"),
    (DEEPLOSS, "years to break even"),
    (UNDERWATER, "before any operating costs"),
])
def test_where_even_a_speculative_path_is_meaningless_it_is_refused(client, ticker, phrase):
    r = client.get(f"/valuation/{ticker}/speculative")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    assert body["method"] == "speculative"
    assert body["intrinsic_value_per_share"] is None
    assert "speculative_value_per_share" not in body
    assert any(phrase in reason for reason in body["reasons"]), body["reasons"]


@pytest.mark.parametrize("overrides, phrase", [
    ({"target_operating_margin": 0.0}, "not a path to profitability"),
    ({"terminal_growth": 0.12, "wacc": 0.11}, "is not below WACC"),
])
def test_overrides_that_break_the_model_are_refused_not_crashed(client, overrides, phrase):
    r = client.post("/valuation/speculative", json={"ticker": GROWER, "overrides": overrides})
    assert r.status_code == 422
    body = r.json()
    assert body["method"] == "speculative"
    assert any(phrase in reason for reason in body["reasons"])


def test_an_unknown_speculative_override_is_rejected(client):
    r = client.post("/valuation/speculative", json={"ticker": GROWER, "overrides": {"nonsense": 1}})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def derive(fin, overrides=None):
    base, _ = base_year_from(fin)
    return S.derive_speculative_assumptions(fin, base, overrides=overrides)


def test_growth_is_the_lower_of_the_latest_year_and_the_cagr():
    """Rivian's real revenue: a 48% CAGR from a 2022 ramp, 8.4% in the latest year."""
    fin = grower(revenues=[5387e6, 4970e6, 4434e6, 1658e6])
    prov = derive(fin).provenance["speculative_revenue_growth"]
    assert prov.value == pytest.approx(5387 / 4970 - 1)
    assert prov.source == "derived"
    assert "not extrapolated" in prov.detail


def test_years_to_profitability_come_from_the_distance_to_the_target():
    prov = derive(grower()).provenance["years_to_profitability"]
    assert prov.value == 6                      # -67% to 10% at 15 points a year
    assert prov.source == "derived"
    assert "15 percentage points a year" in prov.detail

    near = derive(grower(margin=-0.02)).provenance["years_to_profitability"]
    assert near.value == S.YEARS_TO_PROFITABILITY_BOUNDS[0]
    assert near.source == "derived (clamped)"


def test_todays_build_out_capex_is_not_assumed_to_last():
    heavy = derive(grower(capex_share=0.23)).provenance["mature_capex_pct"]
    assert heavy.value == pytest.approx(S.DEFAULT_MATURE_CAPEX_PCT)
    assert heavy.source == "default"
    assert "not assumed to last" in heavy.detail

    light = derive(grower()).provenance["mature_capex_pct"]   # already spends 4%
    assert light.value == pytest.approx(0.04)
    assert light.source == "derived"


def test_a_low_discount_rate_is_floored_for_a_company_never_profitable(client):
    body = client.post("/valuation/speculative", json={
        "ticker": GROWER, "overrides": {"beta": 0.2}}).json()
    wacc = next(a for a in body["assumptions"] if a["name"] == "wacc")
    assert wacc["value"] == pytest.approx(S.MIN_SPECULATIVE_WACC)
    assert wacc["source"] == "derived (clamped)"
    assert "floor" in wacc["detail"]
