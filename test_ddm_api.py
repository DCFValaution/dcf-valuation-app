"""
Routing tests: which companies get the dividend discount model, and what the
API returns for each outcome.

Market data is stubbed. The financial companies here are synthetic, built so
every branch of the DDM guard can be reached on demand, and the dividend stub
fails loudly if a non-financial company ever asks for dividends - which is how
"loss-makers are not rerouted" is proved rather than assumed.

Run with:  python -m pytest test_ddm_api.py -v
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import analysis
import api
import assumptions as A
import ddm_assumptions as D
import excel_export as E
from api import app
from ddm import DDMAssumptions, DDMInputs, run_ddm
from market_data import CompanyFinancials, RateLimitedError
from test_api import LOSSMAKING, PROFITABLE, fake_fetch_financials, make_fin
from test_ddm_assumptions import STEADY, TODAY, progressive, quarterly, ts

BANK = "BANK"
NODIV = "NODIV"
LOSSBANK = "LOSSBANK"
OVERPAYER = "OVERPAY"
SUSPENDED = "SUSP"
BUYBACKS = "BUYBACK"
DIVIDENDS_THROTTLED = "DIVTHROTTLE"
INSURER = "INSURER"
VARIABLE = "VARIABLE"      # a Progressive: small regular dividend plus a yearly variable one
ONEOFF = "ONEOFF"          # regular dividends plus a single special
BUYBACKLENDER = "BBLEND"   # a lender whose buybacks exceed its dividends

# Financial-sector companies that are not banks or insurers. None of these
# may ever fetch dividends - fake_dividends raises if they do.
NETWORK = "NETWORK"        # a Visa: fee business, 1.8% interest burden
CARDLENDER = "CARDLEND"    # a Capital One: lender filed as Credit Services
BROKER = "BROKER"          # an Aon: sells insurance, does not underwrite it
MYSTERY = "MYSTERY"        # in the sector, industry unknown
FEE_AND_LENDER = {
    NETWORK: ("Credit Services", 0.018),
    CARDLENDER: ("Credit Services", 0.35),
    BROKER: ("Insurance Brokers", 0.047),
    MYSTERY: (None, 0.005),
}

BANKS = {
    BANK: {},
    NODIV: {},
    LOSSBANK: {"net_income": -500e6},
    OVERPAYER: {"net_income": 200e6, "dividends_paid": -300e6},
    SUSPENDED: {},
    BUYBACKS: {"buybacks": -900e6},
    DIVIDENDS_THROTTLED: {},
    INSURER: {"industry": "Insurance - Property & Casualty"},
    VARIABLE: {"industry": "Insurance - Property & Casualty"},
    ONEOFF: {},
}

DIVIDENDS = {
    BANK: quarterly(STEADY),
    LOSSBANK: quarterly(STEADY),
    OVERPAYER: quarterly(STEADY),
    BUYBACKS: quarterly(STEADY),
    INSURER: quarterly(STEADY),
    VARIABLE: progressive(),
    ONEOFF: quarterly(STEADY) + [(ts(2025, 12, 20), 5.00)],
    NODIV: [],
    SUSPENDED: quarterly({2023: 0.50, 2024: 0.50, 2025: 0.50}, through=date(2025, 3, 1)),
}

# Every field a DCF response carried before the dividend discount model
# existed. A DCF response must still carry exactly these, plus `method`.
DCF_FIELDS_BEFORE_THE_DDM = {
    "company", "suitable", "intrinsic_value_per_share", "current_price",
    "upside_downside", "note", "base_year", "valuation", "assumptions",
    "global_levers", "wacc_build_up", "wacc_inputs", "sensitivity",
    "projection", "warnings",
}


def make_bank(ticker: str, *, net_income=1_000e6, equity=10_000e6,
              dividends_paid=-300e6, buybacks=-100e6, years=4,
              industry="Banks - Diversified") -> CompanyFinancials:
    """ROE 10%, 30% payout, beta 1.0 - so cost of equity 10%, growth capped at 7%."""
    return CompanyFinancials(
        ticker=ticker,
        profile={"companyName": "Test Bank Corp.", "sector": "Financial Services",
                 "industry": industry, "exchange": "NYSE",
                 "beta": 1.0, "price": 50.0},
        income=[{"fiscalYear": str(2025 - i), "netIncomeCommon": net_income}
                for i in range(years)],
        balance=[{"fiscalYear": str(2025 - i), "commonStockEquity": equity}
                 for i in range(years)],
        cashflow=[{"fiscalYear": str(2025 - i), "dividendsPaid": dividends_paid,
                   "stockRepurchased": buybacks} for i in range(years)],
    )


def financial(industry, *, interest_share=0.005, sector="Financial Services",
              ticker="FIN") -> CompanyFinancials:
    """A DCF-capable company in *sector* whose interest expense is a set share of revenue."""
    fin = make_fin(ticker, "Fin Co.", sector=sector)
    fin.profile["industry"] = industry
    for row in fin.income:
        row["interestExpense"] = row["revenue"] * interest_share
    return fin


def fake_financials(ticker: str, years: int = 5) -> CompanyFinancials:
    upper = ticker.upper()
    if upper in BANKS:
        return make_bank(upper, **BANKS[upper])
    if upper in FEE_AND_LENDER:
        industry, share = FEE_AND_LENDER[upper]
        return financial(industry, interest_share=share, ticker=upper)
    if upper == BUYBACKLENDER:
        fin = financial("Capital Markets", interest_share=0.60, ticker=upper)
        for row in fin.cashflow:
            row["dividendsPaid"], row["stockRepurchased"] = -300e6, -900e6
        return fin
    return fake_fetch_financials(ticker, years)


def fake_dividends(ticker: str) -> list[tuple[int, float]]:
    if ticker == DIVIDENDS_THROTTLED:
        raise RateLimitedError("The market data provider is rate-limiting requests right now.")
    if ticker not in DIVIDENDS:
        raise AssertionError(f"dividends were fetched for {ticker}, which is not a financial")
    return DIVIDENDS[ticker]


@pytest.fixture(autouse=True)
def _stubbed_market_data(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", fake_financials)
    monkeypatch.setattr(analysis, "fetch_dividends", fake_dividends)
    real_analyse = D.analyse_dividends
    monkeypatch.setattr(analysis, "analyse_dividends",
                        lambda events: real_analyse(events, today=TODAY))
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))
    api.reset_rate_limits()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def test_a_financial_is_valued_with_the_ddm_instead_of_refused(client):
    r = client.get(f"/valuation/{BANK}")
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "ddm"
    assert body["suitable"] is True
    assert body["intrinsic_value_per_share"] > 0
    assert body["dividend_base"]["current_annual_dividend"] == pytest.approx(2.80)
    assert body["dividend_base"]["payments_per_year"] == 4
    assert "in the Financial Services sector" in body["why_not_dcf"]


def test_a_dcf_company_still_gets_the_dcf_and_is_labelled(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()
    assert body["method"] == "dcf"
    # method_fit_warnings is the one field added since: doubts about whether
    # the method fits at all. An ordinary company has none.
    assert set(body) == DCF_FIELDS_BEFORE_THE_DDM | {"method", "method_fit_warnings"}
    assert body["method_fit_warnings"] == []


def test_a_lossmaking_non_financial_is_still_refused_not_rerouted(client):
    # fake_dividends raises if called, so reaching a 422 proves no reroute.
    r = client.get(f"/valuation/{LOSSMAKING}")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    assert body["method"] == "dcf"
    assert "loss-making" in " ".join(body["reasons"])


# ---------------------------------------------------------------------------
# A DDM valuation
# ---------------------------------------------------------------------------

def test_the_ddm_figure_is_the_engines_figure_and_the_textbook_one(client):
    body = client.get(f"/valuation/{BANK}").json()

    engine = run_ddm(DDMInputs(2.80, 50.0),
                     DDMAssumptions(cost_of_equity=0.10, dividend_growth=0.07,
                                    high_growth_years=5, terminal_growth=0.025))
    dividends = [2.80 * 1.07 ** t for t in range(1, 6)]
    textbook = (sum(d / 1.10 ** t for t, d in enumerate(dividends, 1))
                + dividends[-1] * 1.025 / (0.10 - 0.025) / 1.10 ** 5)

    assert body["intrinsic_value_per_share"] == pytest.approx(engine.intrinsic_value_per_share)
    assert body["intrinsic_value_per_share"] == pytest.approx(textbook)


def test_a_ddm_result_carries_its_own_honesty_note(client):
    note = client.get(f"/valuation/{BANK}").json()["note"]
    assert "not a fact about Test Bank Corp" in note
    assert "cost of equity of 10.00%" in note
    assert "7.00% a year for 5 years" in note
    assert "buybacks is not counted" in note


def test_every_ddm_input_is_labelled_with_its_provenance(client):
    body = client.get(f"/valuation/{BANK}").json()
    assert {a["name"]: a["source"] for a in body["assumptions"]} == {
        "cost_of_equity": "derived",
        "dividend_growth": "derived (clamped)",
        "terminal_growth": "default",
        "high_growth_years": "default",
    }
    assert {a["name"] for a in body["cost_of_equity_inputs"]} == {
        "risk_free_rate", "equity_risk_premium", "beta"}
    assert {a["name"] for a in body["global_levers"]} == {
        "risk_free_rate", "equity_risk_premium", "terminal_growth"}


def test_the_ddm_sensitivity_centre_is_the_headline(client):
    body = client.get(f"/valuation/{BANK}").json()
    s = body["sensitivity"]
    assert s["grid"][s["centre_row"]][s["centre_col"]] == pytest.approx(
        body["intrinsic_value_per_share"])


def test_a_ddm_response_carries_no_dcf_fields(client):
    body = client.get(f"/valuation/{BANK}").json()
    for dcf_only in ("base_year", "wacc_build_up", "wacc_inputs"):
        assert dcf_only not in body


def test_heavy_buybacks_are_disclosed_because_a_ddm_cannot_see_them(client):
    heavy = client.get(f"/valuation/{BUYBACKS}").json()["warnings"]
    light = client.get(f"/valuation/{BANK}").json()["warnings"]
    assert any("buybacks" in w and "understate" in w for w in heavy)
    assert not any("buybacks" in w for w in light)


# ---------------------------------------------------------------------------
# Where a DDM does not apply
# ---------------------------------------------------------------------------

def test_a_financial_paying_no_dividend_is_refused_by_both_methods(client):
    r = client.get(f"/valuation/{NODIV}")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    assert body["method"] == "ddm"
    assert body["intrinsic_value_per_share"] is None
    assert "No valuation method applies" in body["message"]
    # A financial is never offered the speculative opt-in.
    assert body["speculative_estimate_available"] is False
    assert "Financial Services sector" in body["reasons"][0]     # why not a DCF
    assert "paid no dividends" in body["reasons"][1]             # why not a DDM


@pytest.mark.parametrize("ticker, phrase", [
    (LOSSBANK, "reported a loss to shareholders of"),
    (OVERPAYER, "earnings as dividends"),
    (SUSPENDED, "appears to have been suspended"),
])
def test_the_ddm_guard_refuses_where_a_ddm_does_not_apply(client, ticker, phrase):
    r = client.get(f"/valuation/{ticker}")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable" and body["method"] == "ddm"
    assert any(phrase in reason for reason in body["reasons"])


def test_a_throttled_dividend_fetch_is_not_mistaken_for_no_dividend(client):
    r = client.get(f"/valuation/{DIVIDENDS_THROTTLED}")
    assert r.status_code == 429
    assert r.json()["code"] == "upstream_rate_limited"


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def test_ddm_assumptions_can_be_overridden(client):
    base = client.get(f"/valuation/{BANK}").json()["intrinsic_value_per_share"]
    r = client.post("/valuation", json={
        "ticker": BANK, "overrides": {"dividend_growth": 0.03, "terminal_growth": 0.02}})
    assert r.status_code == 200
    body = r.json()
    sources = {a["name"]: a["source"] for a in body["assumptions"]}
    assert sources["dividend_growth"] == "override"
    assert sources["terminal_growth"] == "override"
    assert body["intrinsic_value_per_share"] < base


def test_a_dcf_override_sent_for_a_ddm_company_is_a_clear_400(client):
    r = client.post("/valuation", json={"ticker": BANK, "overrides": {"revenue_growth": 0.05}})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_override"
    assert "applies to the DCF" in r.json()["message"]


def test_a_ddm_override_sent_for_a_dcf_company_is_a_400(client):
    r = client.post("/valuation", json={"ticker": PROFITABLE,
                                        "overrides": {"dividend_growth": 0.05}})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_override"


def test_growth_at_or_above_cost_of_equity_is_a_refusal_not_a_crash(client):
    r = client.post("/valuation", json={"ticker": BANK, "overrides": {"terminal_growth": 0.12}})
    assert r.status_code == 422
    assert any("r > g" in reason for reason in r.json()["reasons"])


# ---------------------------------------------------------------------------
# Excel, schema, discovery
# ---------------------------------------------------------------------------

def test_excel_is_declined_for_a_ddm_valuation_rather_than_faked(client):
    r = client.get(f"/valuation/{BANK}/excel")
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["code"] == "excel_unavailable_for_method"
    assert body["method"] == "ddm"


def test_excel_for_a_financial_no_method_can_value_is_the_refusal(client):
    r = client.get(f"/valuation/{NODIV}/excel")
    assert r.status_code == 422
    assert r.json()["code"] == "not_suitable"


def test_the_workbook_builder_itself_refuses_a_ddm_report():
    report = analysis.value_company(BANK)
    with pytest.raises(ValueError, match="DCF models"):
        E.build_workbook(report)


def test_openapi_describes_both_methods(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert {"ValuationResponse", "DDMValuationResponse"} <= set(schemas)


def test_the_assumptions_endpoint_lists_the_ddm_names(client):
    body = client.get("/assumptions").json()
    assert set(body["ddm_assumptions"]) == {
        "dividend_growth", "high_growth_years", "terminal_growth", "cost_of_equity"}


# ---------------------------------------------------------------------------
# Only genuine banks and insurers get the DDM
#
# The interest-burden figures below are the real ones from probing each
# company, so the tests pin the line against the cases that drew it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("industry, interest_share, kind", [
    ("Banks - Diversified", 0.538, "bank_or_insurer"),              # JPM
    ("Banks - Regional", 0.502, "bank_or_insurer"),                 # USB
    ("Insurance - Property & Casualty", 0.004, "bank_or_insurer"),  # PGR: not debt-funded
    ("Insurance - Life", 0.015, "bank_or_insurer"),                 # MET
    ("Insurance Brokers", 0.047, "fee_based"),                      # AON: sells, does not underwrite
    ("Credit Services", 0.018, "fee_based"),                        # V
    ("Credit Services", 0.023, "fee_based"),                        # MA
    ("Credit Services", 0.345, "balance_sheet"),                    # COF: same industry as Visa
    ("Credit Services", 0.114, "balance_sheet"),                    # AXP, the closest to the line
    ("Capital Markets", 1.344, "balance_sheet"),                    # GS
    ("Financial Data & Stock Exchanges", 0.077, "fee_based"),       # ICE, highest fee business probed
    ("Asset Management", 0.025, "fee_based"),                       # BLK
])
def test_financials_are_classified_by_industry_then_interest_burden(industry, interest_share, kind):
    fin = financial(industry, interest_share=interest_share)
    assert analysis.classify_financial(fin).kind == kind


def test_the_interest_burden_line_sits_at_ten_percent():
    below = financial("Credit Services", interest_share=0.099)
    above = financial("Credit Services", interest_share=0.101)
    assert analysis.MAX_FEE_BUSINESS_INTEREST_BURDEN == 0.10
    assert analysis.classify_financial(below).kind == "fee_based"
    assert analysis.classify_financial(above).kind == "balance_sheet"


def test_search_style_industry_dashes_still_identify_a_bank():
    assert analysis.classify_financial(financial("Banks—Diversified")).kind == "bank_or_insurer"


def test_a_financial_with_no_industry_is_unclassified_not_guessed():
    """Without an industry an insurer and a fee business look the same:
    both have a low interest burden."""
    assert analysis.classify_financial(financial(None)).kind == "unclassified"


def test_a_financial_with_no_interest_data_is_read_from_its_balance_sheet():
    """
    The case that used to refuse Robinhood and T. Rowe Price.

    A missing interest-expense line is not evidence of a loan book. When
    nothing else says the company lends - no loans, no interest income, no
    premiums - it is a fee business, and the valuation carries a warning
    saying the conclusion rests on absent lines.
    """
    fin = financial("Credit Services")
    for row in fin.income:
        row["interestExpense"] = None

    classification = analysis.classify_financial(fin)

    assert classification.kind == "fee_based"
    assert classification.admitted_on_absence is True


def test_a_financial_with_no_balance_sheet_is_unclassified_not_guessed():
    """Without a balance sheet there is nothing to check for lending."""
    fin = financial("Credit Services")
    for row in fin.income:
        row["interestExpense"] = None
    for row in fin.balance:
        row["totalAssets"] = None

    assert analysis.classify_financial(fin).kind == "unclassified"


@pytest.mark.parametrize("sector", [None, "", "Unknown"])
def test_a_company_with_no_sector_is_unclassified_not_assumed_ordinary(sector):
    """
    The reading that produced the TRV bug.

    "Not in the Financial Services sector" and "we do not know the sector"
    are different statements, and only the first justifies a DCF. Treating
    the second as the first valued an insurer as an ordinary company.
    """
    fin = financial("Insurance - Property & Casualty", sector=sector)
    fin.profile["sector"] = sector

    assert analysis.classify_financial(fin).kind == "unclassified"
    assert not analysis.uses_dividend_discount_model(fin)


def test_the_no_sector_refusal_does_not_claim_an_unknown_sector_as_fact():
    """"is in the Unknown sector" would be the model inventing a fact."""
    fin = financial(None, sector=None)
    fin.profile["sector"] = None
    reason = analysis.financial_refusal_reason(fin, analysis.classify_financial(fin))

    assert "Unknown sector" not in reason
    assert "did not report which sector" in reason
    assert "try again" in reason.lower()


def test_a_heavily_indebted_non_financial_is_not_touched():
    """This only sorts financials: a utility's interest burden is irrelevant."""
    fin = financial("Utilities - Regulated Electric", interest_share=0.40, sector="Utilities")
    assert analysis.classify_financial(fin).kind == "not_financial"


def test_a_payments_network_gets_the_dcf_not_the_ddm(client):
    r = client.get(f"/valuation/{NETWORK}")     # fake_dividends raises if the DDM is tried
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "dcf"
    assert body["suitable"] is True
    assert body["intrinsic_value_per_share"] > 0


def test_a_lender_that_is_not_a_bank_is_refused_not_given_a_ddm(client):
    r = client.get(f"/valuation/{CARDLENDER}")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    assert body["method"] == "dcf"
    reason = next(r for r in body["reasons"] if "interest expense of 35% of revenue" in r)
    # The honest reasons: why not a DCF, why not a DDM, and what would be right.
    assert "no economic meaning" in reason
    assert "A dividend discount model is not substituted either" in reason
    assert "excess-return model" in reason and "not available yet" in reason


def test_a_lenders_refusal_cites_its_own_buybacks_where_the_data_has_them(client):
    body = client.get(f"/valuation/{BUYBACKLENDER}").json()
    assert body["code"] == "not_suitable" and body["method"] == "dcf"
    reason = next(r for r in body["reasons"] if "excess-return model" in r)
    assert "returned $0.9bn through buybacks against $0.3bn in dividends in its 2025 financial year" in reason


def test_a_recurring_variable_dividend_is_valued_and_disclosed_honestly(client):
    body = client.get(f"/valuation/{VARIABLE}").json()
    assert body["method"] == "ddm" and body["suitable"] is True

    base = body["dividend_base"]
    assert base["regular_annual_dividend"] == pytest.approx(0.40)
    assert base["variable_annual_dividend"] == pytest.approx(4.03)
    assert base["current_annual_dividend"] == pytest.approx(4.43)
    assert base["special_dividends_excluded"] == []
    assert len(base["variable_dividends_included"]) == 4

    # Regular growth is flat, so: 4.43 a year for 5 years at 10%, then 2.5%.
    textbook = (sum(4.43 / 1.10 ** t for t in range(1, 6))
                + 4.43 * 1.025 / (0.10 - 0.025) / 1.10 ** 5)
    assert body["intrinsic_value_per_share"] == pytest.approx(textbook)

    warning = next(w for w in body["warnings"] if "variable dividend" in w)
    assert "average" in warning and "not a commitment" in warning
    # The run that makes it recurring (3 years) and the payments averaged (4,
    # including one before the 2023 gap) are distinct facts, stated distinctly.
    assert "for the last 3 years running" in warning
    assert ("the payments in that window were $1.40 in Jan 2022, $0.75 in Jan 2024, "
            "$4.50 in Jan 2025, $13.50 in Jan 2026") in warning
    # It recurs: nothing may claim it will not be repeated.
    assert not any("expected to repeat" in w for w in body["warnings"])


def test_a_one_off_special_is_still_excluded_and_says_why(client):
    body = client.get(f"/valuation/{ONEOFF}").json()
    base = body["dividend_base"]
    assert base["current_annual_dividend"] == pytest.approx(2.80)
    assert base["special_dividends_excluded"] == [{"date": "2025-12-20", "amount": 5.0}]
    assert base["variable_dividends_included"] == []
    assert any("do not recur on an annual pattern" in w for w in body["warnings"])


def test_an_insurer_is_valued_with_the_ddm(client):
    body = client.get(f"/valuation/{INSURER}").json()
    assert body["method"] == "ddm"
    assert body["suitable"] is True


def test_an_insurance_broker_is_a_fee_business_valued_with_the_dcf(client):
    body = client.get(f"/valuation/{BROKER}").json()
    assert body["method"] == "dcf"
    assert body["suitable"] is True


def test_a_financial_the_data_cannot_classify_is_refused_rather_than_guessed(client):
    r = client.get(f"/valuation/{MYSTERY}")
    assert r.status_code == 422
    body = r.json()
    assert body["method"] == "dcf"
    assert any("industry is not available" in reason for reason in body["reasons"])


def test_a_payments_network_gets_a_dcf_workbook(client):
    r = client.get(f"/valuation/{NETWORK}/excel")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
