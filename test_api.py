"""
API tests using FastAPI's TestClient.

All FMP access is mocked, so these run offline, need no API key, and consume
no quota. The mocks replace the two functions that reach the network:
analysis.fetch_financials and assumptions.fetch_risk_free_rate.

Run with:  python -m pytest test_api.py -v
"""

import pytest
from fastapi.testclient import TestClient

import analysis
import assumptions as A
from api import app
from market_data import (CompanyFinancials, DataUnavailableError,
                         RateLimitedError, TickerNotFoundError)


# ---------------------------------------------------------------------------
# Synthetic companies
# ---------------------------------------------------------------------------

def make_fin(ticker="TEST", name="Test Co", sector="Technology",
             *, operating_margin=0.25, years=4) -> CompanyFinancials:
    """
    A synthetic company. A negative `operating_margin` produces a loss-making
    business, which is what the suitability guard should refuse.
    """
    income, balance, cashflow = [], [], []
    for i in range(years):
        revenue = 10_000e6 * (1.05 ** (years - 1 - i))
        operating_income = revenue * operating_margin
        pre_tax = operating_income - 50e6
        income.append({
            "fiscalYear": str(2025 - i),
            "date": f"{2025 - i}-09-30",
            "revenue": revenue,
            "operatingIncome": operating_income,
            "incomeBeforeTax": pre_tax,
            "incomeTaxExpense": pre_tax * 0.20 if pre_tax > 0 else 0.0,
            "interestExpense": 50e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0,
            "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0,
            "longTermDebt": 1_000e6,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker=ticker,
        profile={"companyName": name, "beta": 1.1, "marketCap": 100_000e6,
                 "price": 100.0, "sector": sector, "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


PROFITABLE = "GOOD"
LOSSMAKING = "LOSS"
UNKNOWN = "ZZZZ"
BROKEN = "BROKEN"
THROTTLED = "THROTTLED"


def fake_fetch_financials(ticker: str, years: int = 5) -> CompanyFinancials:
    ticker = ticker.upper()
    if ticker == PROFITABLE:
        return make_fin(PROFITABLE, "Profitable Corp.")
    if ticker == LOSSMAKING:
        return make_fin(LOSSMAKING, "Lossmaking Inc.", operating_margin=-0.60)
    if ticker == THROTTLED:
        raise RateLimitedError(
            "Yahoo Finance is rate-limiting requests right now. "
            "Wait a moment and try again."
        )
    if ticker == BROKEN:
        raise DataUnavailableError("Yahoo Finance did not return a usable response.")
    raise TickerNotFoundError(
        f"No security found for '{ticker}' on Yahoo Finance.\n"
        "  Check the spelling. Delisted companies are not covered."
    )


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", fake_fetch_financials)
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Successful valuation
# ---------------------------------------------------------------------------

def test_valuation_returns_200_with_core_fields(client):
    r = client.get(f"/valuation/{PROFITABLE}")
    assert r.status_code == 200
    body = r.json()

    assert body["suitable"] is True
    assert body["company"]["ticker"] == PROFITABLE
    assert body["intrinsic_value_per_share"] > 0
    assert body["current_price"] == 100.0
    assert "upside_downside" in body


def test_valuation_includes_honesty_note(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()
    note = body["note"]
    assert "not a fact" in note
    assert "equity risk premium" in note


def test_every_assumption_carries_a_source_label(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()
    names = {a["name"] for a in body["assumptions"]}
    assert {"revenue_growth", "operating_margin", "wacc", "terminal_growth"} <= names
    for a in body["assumptions"]:
        assert a["source"] in {"derived", "derived (clamped)", "default", "override"}


def test_global_levers_are_reported(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()
    names = [lever["name"] for lever in body["global_levers"]]
    assert names == ["risk_free_rate", "equity_risk_premium", "terminal_growth"]


def test_sensitivity_is_centred_on_the_headline_value(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()
    sens = body["sensitivity"]

    assert len(sens["waccs"]) == 5
    assert len(sens["grid"]) == 5
    assert sens["waccs"][sens["centre_row"]] == pytest.approx(sens["centre_wacc"])

    centre_value = sens["grid"][sens["centre_row"]][sens["centre_col"]]
    assert centre_value == pytest.approx(body["intrinsic_value_per_share"])


def test_projection_and_valuation_blocks_present(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()
    assert len(body["projection"]) == 5
    assert body["projection"][0]["period"] == 1
    assert body["valuation"]["enterprise_value"] > 0
    assert body["wacc_build_up"]["cost_of_equity"] > 0


# ---------------------------------------------------------------------------
# Not-suitable case
# ---------------------------------------------------------------------------

def test_lossmaking_company_is_refused_with_422(client):
    r = client.get(f"/valuation/{LOSSMAKING}")
    assert r.status_code == 422
    body = r.json()

    assert body["code"] == "not_suitable"
    assert body["suitable"] is False
    assert body["intrinsic_value_per_share"] is None
    assert body["reasons"], "a refusal must explain itself"
    assert "loss-making" in " ".join(body["reasons"]).lower()


def test_not_suitable_response_carries_no_valuation_figure(client):
    body = client.get(f"/valuation/{LOSSMAKING}").json()
    for banned in ("valuation", "sensitivity", "projection"):
        assert banned not in body, f"{banned} must not appear when a DCF was refused"


def test_not_suitable_is_distinguishable_from_validation_error(client):
    """Both are 422; the `code` field separates them."""
    refusal = client.get(f"/valuation/{LOSSMAKING}").json()
    invalid = client.post("/valuation", json={"ticker": PROFITABLE,
                                              "overrides": {"nonsense": 1}}).json()
    assert refusal["code"] == "not_suitable"
    assert invalid["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

def test_unknown_ticker_returns_404(client):
    r = client.get(f"/valuation/{UNKNOWN}")
    assert r.status_code == 404
    assert r.json()["code"] == "ticker_not_found"


def test_rate_limit_returns_429(client):
    r = client.get(f"/valuation/{THROTTLED}")
    assert r.status_code == 429
    assert r.json()["code"] == "upstream_rate_limited"


def test_other_upstream_failure_returns_502(client):
    r = client.get(f"/valuation/{BROKEN}")
    assert r.status_code == 502
    assert r.json()["code"] == "upstream_error"


def test_unknown_override_name_reaching_the_engine_returns_400(client, monkeypatch):
    """The Pydantic schema blocks unknown names first; this covers the
    defensive path if the two lists ever drift apart."""
    def raise_value_error(ticker, overrides=None):
        raise ValueError("Unknown assumption override(s): drifted")

    monkeypatch.setattr("api.value_company", raise_value_error)
    r = client.get(f"/valuation/{PROFITABLE}")
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_override"


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def test_override_is_applied_and_labelled(client):
    r = client.post("/valuation", json={"ticker": PROFITABLE,
                                        "overrides": {"wacc": 0.12}})
    assert r.status_code == 200
    body = r.json()

    wacc = next(a for a in body["assumptions"] if a["name"] == "wacc")
    assert wacc["value"] == pytest.approx(0.12)
    assert wacc["source"] == "override"
    assert body["sensitivity"]["centre_wacc"] == pytest.approx(0.12)


def test_override_changes_the_valuation(client):
    base = client.get(f"/valuation/{PROFITABLE}").json()
    higher_discount = client.post(
        "/valuation", json={"ticker": PROFITABLE, "overrides": {"wacc": 0.15}}
    ).json()
    assert higher_discount["intrinsic_value_per_share"] < base["intrinsic_value_per_share"]


def test_omitted_overrides_keep_derived_values(client):
    body = client.post("/valuation", json={"ticker": PROFITABLE,
                                           "overrides": {"wacc": 0.12}}).json()
    margin = next(a for a in body["assumptions"] if a["name"] == "operating_margin")
    assert margin["source"] == "derived"


def test_wacc_input_overrides_are_accepted(client):
    body = client.post("/valuation", json={
        "ticker": PROFITABLE,
        "overrides": {"equity_risk_premium": 0.04, "risk_free_rate": 0.03},
    }).json()
    levers = {lever["name"]: lever for lever in body["global_levers"]}
    assert levers["equity_risk_premium"]["source"] == "override"
    assert levers["risk_free_rate"]["value"] == pytest.approx(0.03)


def test_unknown_override_field_is_rejected(client):
    r = client.post("/valuation", json={"ticker": PROFITABLE,
                                        "overrides": {"made_up": 0.5}})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Meta endpoints and secret handling
# ---------------------------------------------------------------------------

def test_health_reports_the_data_source_and_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "Yahoo" in body["data_source"]
    # Yahoo needs no credentials, so there is nothing to leak.
    assert body["requires_api_key"] is False


def test_health_touches_no_upstream_so_it_can_wake_a_sleeping_instance(
        client, monkeypatch):
    """
    The app pings /health on launch to wake the free-tier instance.

    If it called Yahoo it would be slow exactly when it matters most - on a
    cold start - and would report the service unhealthy whenever Yahoo was
    throttling, which Render would answer by restarting a healthy process.
    """
    import market_data as M

    def must_not_run(*args, **kwargs):
        raise AssertionError("/health must not touch the data source")

    monkeypatch.setattr(M, "fetch_financials", must_not_run)
    monkeypatch.setattr(M, "probe_symbol", must_not_run)
    monkeypatch.setattr(M, "_browser_session", must_not_run)
    monkeypatch.setattr(M, "fetch_risk_free_rate", must_not_run)
    monkeypatch.setattr(M, "fetch_beta", must_not_run)

    assert client.get("/health").status_code == 200


def test_assumptions_endpoint_lists_override_names(client):
    body = client.get("/assumptions").json()
    assert "wacc" in body["assumptions"]
    assert "equity_risk_premium" in body["wacc_inputs"]


@pytest.mark.parametrize("path", [
    f"/valuation/{PROFITABLE}", f"/valuation/{LOSSMAKING}",
    f"/valuation/{UNKNOWN}", f"/valuation/{BROKEN}",
    f"/valuation/{THROTTLED}", "/health", "/assumptions",
])
def test_no_credentials_or_internal_paths_leak(client, path):
    """
    Yahoo needs no key, so there is no secret to redact - but a response
    should still never expose local filesystem paths or tracebacks.
    """
    text = client.get(path).text
    assert "Traceback" not in text
    assert "C:\\Users" not in text


def test_openapi_schema_builds(client):
    """A broken response model would only surface when /docs is opened."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/valuation/{ticker}" in r.json()["paths"]
