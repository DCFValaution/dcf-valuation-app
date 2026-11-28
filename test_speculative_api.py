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
import reinvestment as R
import speculative_assumptions as S
from speculative import run_speculative
from api import XLSX_MEDIA_TYPE, app
from market_data import base_year_from
from test_api import PROFITABLE, fake_fetch_financials, make_fin
from test_ddm_api import financial, make_bank

GROWER = "GROWER"          # Rivian-like: growing, -67% margin, 3% gross margin
NOREV = "NOREV"            # QuantumScape-like: no revenue
TINY = "TINY"              # a few $mm of revenue
SHRINKER = "SHRINK"        # Beyond Meat-like: revenue falling
DEEPLOSS = "DEEP"          # Lucid-like depth: -200% margin
UNDERWATER = "UNDERW"      # loses money on every sale
OUTSPENDER = "OUTSPEND"    # Tesla-like: profitable, but capex outruns the profit
BANK = "BANKX"             # valued with the DDM
LENDERLOSS = "LENDLOSS"    # a loss-making lender: refused for more than losses
LENDER = "LENDPROF"        # a profitable lender: refused for nothing BUT its shape


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
    # Shaped like Tesla: a real profit (6% operating margin, 18% gross) on
    # growing revenue, and capital spending heavy enough that the answer
    # stops being about the forecast. Refused for terminal dominance, which
    # is the refusal no earlier tier serves - the speculative estimate
    # exists for losses, and this company is not making one.
    #
    # Capex was 10% and is 11.5% because the figure it has to produce
    # moved, not because the case did. Unifying return on capital onto the
    # assets-side measure raised this company's long-run return, which
    # raised its terminal value - and for a company whose forecast is worth
    # less than nothing, a BIGGER terminal value makes the terminal SHARE
    # smaller, because the share divides by a total the forecast is
    # dragging down. It fell from 185% to 144% and slipped under the 150%
    # line while being, in every way that matters, the same company.
    #
    # Tesla as measured today is capex 14.9% of revenue on a 5.1% margin,
    # which is heavier than this. Those figures are not used because this
    # synthetic pipeline has no capex glide and no depreciation catching
    # up, so at that weight its LAST forecast year turns negative and
    # `negative_fcf` claims it first - which is the refusal this fixture
    # exists NOT to be.
    OUTSPENDER: lambda: grower(OUTSPENDER, margin=0.06, gross=0.18,
                               revenues=[90_000e6 / 1.05 ** i for i in range(4)],
                               capex_share=0.115),
    BANK: lambda: make_bank(BANK),
    LENDERLOSS: lambda: reshape(financial("Credit Services", interest_share=0.35,
                                          ticker=LENDERLOSS), margin=-0.30),
    # Profitable, and still refused: interest is 35% of its revenue, so it
    # earns through borrowing rather than being financed by it. The only
    # refusal code is `financial`, which makes it the case that separates "no
    # loss to project a way out of" from "the model does not fit at all".
    LENDER: lambda: financial("Credit Services", interest_share=0.35,
                              ticker=LENDER),
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
    # only the flags saying which onward step, if any, could be asked for.
    #
    # `hypothetical_available` and `hypothetical_placeholders` were added when
    # Tesla turned up a refusal with nothing after it: profitable, so no
    # speculative estimate, and refused for cash flow, so the screen ended the
    # conversation. The assertion is kept exact rather than loosened, because
    # its job is to catch a figure appearing in a refusal - and it still would.
    assert set(body) == {"code", "method", "company", "suitable", "intrinsic_value_per_share",
                         "message", "reasons", "assumptions", "speculative_estimate_available",
                         "hypothetical_available", "hypothetical_placeholders",
                         "hypothetical_framing"}
    # Nothing speculative or hypothetical was computed to say so.
    assert body["hypothetical_available"] is False
    assert body["hypothetical_placeholders"] is None
    assert body["hypothetical_framing"] is None


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
    # `years_to_profitability` rather than `target_operating_margin`: the
    # latter is now a DCF assumption too - the optional margin target - so
    # the standard endpoint accepts it, correctly. This one is the
    # speculative engine's alone and always will be, because the standard
    # model has no concept of a year profitability arrives.
    r = client.post("/valuation", json={"ticker": GROWER,
                                        "overrides": {"years_to_profitability": 4}})
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
    """The speculative screen must not read like an ordinary valuation.

    Measured on what the two say rather than on how long they are. A length
    ratio was the original check and it was the wrong instrument: it broke
    when the standard note gained a sentence explaining that terminal growth
    tracks the risk-free rate, which makes the standard note longer without
    making the speculative one any less emphatic.
    """
    speculative = client.get(f"/valuation/{GROWER}/speculative").json()["disclaimer"]
    standard = client.get(f"/valuation/{PROFITABLE}").json()["note"]

    # The refusal, and what it rests on, are said outright.
    assert "not a valuation" in speculative and "not a valuation" not in standard
    for phrase in ("must not be treated as one",
                   "explicitly requested",
                   "lose everything",
                   "not investment advice"):
        assert phrase in speculative, phrase
        assert phrase not in standard, f"{phrase!r} belongs only to the speculative note"

    # And it is still the substantially longer document of the two.
    assert len(speculative) > len(standard)


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
    ({"terminal_growth": 0.12, "wacc": 0.11}, "is not below the discount rate"),
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
    """A build-out rate is moderated, but to the ceiling - not to a flat 5%.

    The flat rate was the bug: it handed Lucid 67 points of revenue a year
    and Rivian 19, silently, and was most of their figures. The ceiling is
    above the highest industry AVERAGE Damodaran publishes, so it moderates
    only companies that are outliers against their own industry.
    """
    heavy = derive(grower(capex_share=0.23)).provenance["mature_capex_pct"]
    assert heavy.value == pytest.approx(R.MATURE_CAPEX_CEILING)
    assert heavy.source == "derived"
    assert "moderated down from today's 23.0%" in heavy.detail

    # This one spends 4% and depreciates 5%, so it is floored at its own
    # depreciation: a company that invests less than its assets lose is
    # shrinking, and a forecast cannot assume that forever.
    light = derive(grower()).provenance["mature_capex_pct"]
    assert light.value == pytest.approx(0.05)
    assert light.source == "derived"
    assert "its own depreciation" in light.detail

    # A company already spending between the two keeps its own rate.
    middling = derive(grower(capex_share=0.08)).provenance["mature_capex_pct"]
    assert middling.value == pytest.approx(0.08)
    assert "own current rate" in middling.detail


def test_depreciation_rises_toward_investment_without_reaching_it():
    """The gap between them is net investment, and it does not go away.

    Setting them equal was the other bug: it made investment free, so the
    rate could be anything and the figure never moved.
    """
    derived = derive(grower(capex_share=0.23))
    capex = derived.provenance["mature_capex_pct"].value
    da = derived.provenance["mature_da_pct"].value

    assert 0 < da < capex, (da, capex)
    assert derived.provenance["mature_da_pct"].source == "derived"
    assert "cover out of depreciation" in derived.provenance["mature_da_pct"].detail
    # And the cover is the industry's own published ratio, not a constant.
    cover = derived.diagnostics["mature_cover"]
    assert da == pytest.approx(capex * cover)


def test_a_low_discount_rate_is_floored_for_a_company_never_profitable(client):
    body = client.post("/valuation/speculative", json={
        "ticker": GROWER, "overrides": {"beta": 0.2}}).json()
    wacc = next(a for a in body["assumptions"] if a["name"] == "wacc")
    assert wacc["value"] == pytest.approx(S.MIN_SPECULATIVE_WACC)
    assert wacc["source"] == "derived (clamped)"
    assert "floor" in wacc["detail"]


# ---------------------------------------------------------------------------
# The workbook endpoints
#
# The file outlives the screen, so the refusal has to survive the trip too: a
# company that cannot carry even a speculative path must get the same two-part
# refusal here as it gets on screen, not a spreadsheet and not a crash.
# ---------------------------------------------------------------------------

def test_the_speculative_workbook_is_served_as_a_spreadsheet(client):
    r = client.get(f"/valuation/{GROWER}/speculative/excel")
    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX_MEDIA_TYPE
    assert r.headers["content-disposition"] == \
        f'attachment; filename="{GROWER}_Speculative.xlsx"'
    assert r.content[:2] == b"PK"


def test_the_filename_never_calls_it_a_valuation(client):
    r = client.get(f"/valuation/{GROWER}/speculative/excel")
    assert "valuation" not in r.headers["content-disposition"].lower()


@pytest.mark.parametrize("ticker", [NOREV, SHRINKER, UNDERWATER])
def test_declining_the_speculative_workbook_gives_the_screens_refusal(client, ticker):
    r = client.get(f"/valuation/{ticker}/speculative/excel")
    assert r.status_code == 422
    body = r.json()
    assert body["method"] == "speculative"
    assert body["reasons"]
    assert "spreadsheetml" not in r.headers["content-type"]


def test_a_company_a_valuation_applies_to_gets_no_speculative_workbook(client):
    r = client.get("/valuation/GOOD/speculative/excel")
    assert r.status_code == 422
    assert r.json()["code"] == "speculative_not_applicable"


def test_the_phase_after_profitability_keeps_paying_for_its_investment():
    """The forecast's two halves have to agree.

    Years one to N glide investment and depreciation toward each other; from
    year N the standard DCF takes over, and it was handed capex and
    depreciation set EQUAL - so net investment fell to nothing exactly where
    most of the value sits. The glide then bought nothing: a company paid
    for its assets during the visible years and was given them free forever
    after.
    """
    derived = derive(grower(capex_share=0.23))
    result = run_speculative(derived.inputs, derived.assumptions)

    mature_capex = derived.assumptions.mature_capex_pct
    mature_da = derived.assumptions.mature_da_pct
    assert mature_da is not None and mature_da < mature_capex

    # The DCF phase's own projection must show investment costing something.
    year = result.after_profitability.years[-1]
    assert year.capex > year.da > 0, (year.capex, year.da)
    assert year.capex / year.revenue == pytest.approx(mature_capex, abs=1e-9)
    assert year.da / year.revenue == pytest.approx(mature_da, abs=1e-9)


def test_a_known_company_uses_its_own_industrys_cover_not_the_markets():
    """The lookup is wired to the SIC map, and it is worth proving: falling
    back to the whole market for everything would be invisible in a figure
    and wrong in every provenance line."""
    import industry_betas as IB
    import reinvestment as Rv
    from industry_capex_data import INDUSTRY_CAPEX

    semiconductor = Rv.depreciation_cover(INDUSTRY_CAPEX["Semiconductor"])[0]
    market = Rv.depreciation_cover(INDUSTRY_CAPEX[Rv.FALLBACK_INDUSTRY])[0]
    assert semiconductor != pytest.approx(market), "fixture sanity"

    found = IB.industry_capex_for("NVDA")
    assert found is not None and found.name == "Semiconductor"
    assert Rv.depreciation_cover(found)[0] == pytest.approx(semiconductor)


def test_the_derivation_is_wired_to_the_ticker_s_own_industry():
    """Not just the lookup - the wiring.

    A synthetic company carrying a real ticker is enough, because the
    industry is found from the ticker's SIC code and nothing else. Without
    this, replacing the lookup with None leaves every figure unchanged on
    the fixtures, which have no SIC codes at all - so the wiring could be
    cut and no test would notice.
    """
    import reinvestment as Rv
    from industry_capex_data import INDUSTRY_CAPEX

    semiconductor = Rv.depreciation_cover(INDUSTRY_CAPEX["Semiconductor"])[0]
    market = Rv.depreciation_cover(INDUSTRY_CAPEX[Rv.FALLBACK_INDUSTRY])[0]
    assert semiconductor != pytest.approx(market), "fixture sanity"

    chipmaker = derive(grower("NVDA", capex_share=0.10))
    assert chipmaker.diagnostics["mature_cover"] == pytest.approx(semiconductor)
    assert chipmaker.diagnostics["mature_cover_industry"] == "Semiconductor"
    assert "Semiconductor" in chipmaker.provenance["mature_da_pct"].detail

    # And a company whose ticker is in no map gets the market's, labelled
    # as the market's rather than quietly passed off as its own.
    unknown = derive(grower("ZZZZ", capex_share=0.10))
    assert unknown.diagnostics["mature_cover"] == pytest.approx(market)
    assert unknown.diagnostics["mature_cover_industry"] == Rv.FALLBACK_INDUSTRY
