"""
Telling a fee-earning financial from a lender.

The sector holds two businesses a valuation model must not confuse. A payments
network, an exchange, an asset manager and a brokerage earn fees: cash comes in
for a service, and a DCF describes that. A bank, a card lender or a broker with
a bank inside it earns a spread on money it borrows and lends: its debt is raw
material rather than financing, and unlevered free cash flow is meaningless for
it.

The old rule asked one question - interest expense against revenue - and any
company whose provider published no interest line fell through to a blanket
refusal. Robinhood and T. Rowe Price are the cases that mattered: profitable
fee businesses with no interest line at all.

The rule these tests pin is ordered, and every step looks for POSITIVE evidence
of lending or underwriting:

    1. banks and insurers, by industry              -> the dividend model
    2. no statements                                -> refused
    3. earns premiums / pays policyholder benefits  -> refused
    4. loan book >= 1.0x revenue                    -> refused
    5. interest expense > 10% of revenue            -> refused
    6. interest income > 25% of revenue             -> refused
    7. asset manager, balance sheet > 10x revenue   -> refused
    8. otherwise                                    -> the DCF

The thresholds come from probing 36 real financial companies; the numbers in
the gap tests below are that probe's, rounded. Nothing between 0.26x and 2.4x
revenue of loans was found in the whole sample.

Run with:  python -m pytest test_financial_routing.py -v
"""

import pytest

import analysis
from analysis import classify_financial, lending_not_itemised_warning
from test_api import fake_fetch_financials, make_fin
from test_ddm_api import financial, make_bank


def with_balance(fin, **rows):
    for row in fin.balance:
        row.update(rows)
    return fin


def with_income(fin, **rows):
    for row in fin.income:
        row.update(rows)
    return fin


def revenue_of(fin):
    return fin.income[0]["revenue"]


def lender(industry="Credit Services", *, loans_x_revenue=3.0, **kwargs):
    """A company whose loan book is `loans_x_revenue` years of revenue."""
    fin = financial(industry, **kwargs)
    return with_balance(fin, loans=revenue_of(fin) * loans_x_revenue)


def fee_business(industry="Credit Services", **kwargs):
    """A financial company with no loan book reported at all."""
    return financial(industry, **kwargs)


# ---------------------------------------------------------------------------
# The four companies this change was made for
#
# Shaped like the real ones as probed: the figures in the comments are theirs.
# ---------------------------------------------------------------------------

def robinhood_shaped():
    """HOOD: profitable, no interest line at all, no loan book reported."""
    fin = fee_business("Capital Markets")
    with_income(fin, interestExpense=None)
    return with_balance(fin, totalAssets=revenue_of(fin) * 8.5)


def trowe_shaped():
    """TROW: an asset manager with no interest line and a small balance sheet."""
    fin = fee_business("Asset Management")
    with_income(fin, interestExpense=None)
    return with_balance(fin, totalAssets=revenue_of(fin) * 2.0)


def ameriprise_shaped():
    """AMP: files as an asset manager, earns $2.3bn of insurance premiums."""
    fin = fee_business("Asset Management")
    with_income(fin, premiumsEarned=revenue_of(fin) * 0.13)
    return with_balance(fin, loans=revenue_of(fin) * 0.35,
                        totalAssets=revenue_of(fin) * 10.3)


def apollo_shaped():
    """APO: an asset manager whose balance sheet is 14x its revenue."""
    fin = fee_business("Asset Management")
    return with_balance(fin, totalAssets=revenue_of(fin) * 14.4)


def test_robinhood_shaped_reaches_the_dcf():
    classification = classify_financial(robinhood_shaped())

    assert classification.kind == "fee_based"
    # Admitted because nothing says it lends, not because something says it
    # does not - which is what the warning exists to say out loud.
    assert classification.admitted_on_absence is True


def test_trowe_shaped_reaches_the_dcf():
    assert classify_financial(trowe_shaped()).kind == "fee_based"


def test_ameriprise_shaped_is_refused_for_underwriting():
    classification = classify_financial(ameriprise_shaped())

    assert classification.kind == "balance_sheet"
    assert classification.evidence == "underwrites insurance"


def test_apollo_shaped_is_refused_for_its_balance_sheet():
    classification = classify_financial(apollo_shaped())

    assert classification.kind == "balance_sheet"
    assert "balance sheet" in classification.evidence


# ---------------------------------------------------------------------------
# The companies that must not move
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("industry,interest_share,assets_x", [
    ("Credit Services", 0.018, 2.5),                    # V
    ("Credit Services", 0.023, 1.7),                    # MA
    ("Financial Data & Stock Exchanges", 0.077, 10.8),  # ICE
    ("Financial Data & Stock Exchanges", 0.027, 30.4),  # CME, collateral-heavy
    ("Asset Management", 0.025, 7.0),                   # BLK
    ("Insurance Brokers", 0.047, 3.0),                  # AON: sells, never underwrites
])
def test_fee_businesses_keep_the_dcf(industry, interest_share, assets_x):
    fin = fee_business(industry, interest_share=interest_share)
    with_balance(fin, totalAssets=revenue_of(fin) * assets_x)

    assert classify_financial(fin).kind == "fee_based"


@pytest.mark.parametrize("loans_x,interest_share", [
    (3.1, 0.114),    # AXP
    (8.1, 0.296),    # COF
    (6.2, 0.276),    # SYF
    (2.4, 0.157),    # SCHW: a brokerage with a bank inside it
    (4.2, 0.743),    # MS
    (4.1, 1.146),    # GS
])
def test_lenders_stay_refused(loans_x, interest_share):
    fin = lender(loans_x_revenue=loans_x, interest_share=interest_share)

    assert classify_financial(fin).kind == "balance_sheet"


@pytest.mark.parametrize("industry", ["Banks - Diversified", "Banks - Regional",
                                      "Insurance - Property & Casualty"])
def test_banks_and_insurers_still_take_the_dividend_model(industry):
    fin = financial(industry)

    assert classify_financial(fin).kind == "bank_or_insurer"
    assert analysis.uses_dividend_discount_model(fin) is True


def test_an_ordinary_company_is_untouched_by_any_of_this():
    fin = fake_fetch_financials("GOOD")

    assert classify_financial(fin).kind == "not_financial"


# ---------------------------------------------------------------------------
# The loan-book test, and the gap it sits in
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loans_x", [0.0, 0.19, 0.22, 0.26])   # BLK, COIN, LPLA, PYPL
def test_an_incidental_loan_book_is_not_a_lending_business(loans_x):
    fin = lender(loans_x_revenue=loans_x, interest_share=0.013)

    assert classify_financial(fin).kind == "fee_based"


@pytest.mark.parametrize("loans_x", [2.4, 2.8, 3.1, 15.3])     # SCHW, KKR, AXP, ALLY
def test_a_loan_book_bigger_than_revenue_is_a_lending_business(loans_x):
    fin = lender(loans_x_revenue=loans_x, interest_share=0.013)

    assert classify_financial(fin).kind == "balance_sheet"


def test_the_threshold_sits_inside_the_gap_the_probe_found():
    # Nothing in 36 probed companies fell between these two, so the line can
    # sit anywhere between them without splitting a real case.
    assert 0.26 < analysis.MAX_FEE_BUSINESS_LOANS_TO_REVENUE < 2.4


# ---------------------------------------------------------------------------
# Ordering: each test fires on its own evidence, in its own right
# ---------------------------------------------------------------------------

def test_underwriting_is_checked_before_everything_else():
    # An insurer that also lends is refused as an underwriter: the first
    # matching test wins, and premiums are the strongest statement.
    fin = lender("Asset Management", loans_x_revenue=5.0)
    with_income(fin, premiumsEarned=revenue_of(fin) * 0.10)

    assert classify_financial(fin).evidence == "underwrites insurance"


def test_a_loan_book_alone_is_enough_without_any_interest_line():
    # The gap the old rule left: a lender whose provider publishes no interest
    # expense used to be "unclassified"; now its loan book gives it away.
    fin = lender(loans_x_revenue=4.0)
    with_income(fin, interestExpense=None)

    classification = classify_financial(fin)

    assert classification.kind == "balance_sheet"
    assert "loan book" in classification.evidence


def test_interest_income_alone_is_enough():
    # Schwab's shape with the expense line missing: what it EARNS in interest
    # still says it lends.
    fin = fee_business()
    with_income(fin, interestExpense=None,
                interestIncome=revenue_of(fin) * 0.65)

    classification = classify_financial(fin)

    assert classification.kind == "balance_sheet"
    assert "interest income" in classification.evidence


def test_a_fee_business_keeps_its_small_interest_income():
    fin = fee_business()
    with_income(fin, interestIncome=revenue_of(fin) * 0.029)  # MKTX's share

    assert classify_financial(fin).kind == "fee_based"


def test_the_asset_manager_balance_sheet_test_is_scoped_to_asset_managers():
    # CME's balance sheet is 30x its revenue and it is a fee business: member
    # collateral, not an investment book. The test must not reach it.
    exchange = fee_business("Financial Data & Stock Exchanges")
    with_balance(exchange, totalAssets=revenue_of(exchange) * 30.4)

    assert classify_financial(exchange).kind == "fee_based"


def test_a_financial_with_no_balance_sheet_is_refused_rather_than_guessed():
    fin = fee_business()
    with_income(fin, interestExpense=None)
    with_balance(fin, totalAssets=None)

    assert classify_financial(fin).kind == "unclassified"


# ---------------------------------------------------------------------------
# The warning that travels with an absence-of-evidence admission
# ---------------------------------------------------------------------------

def test_the_warning_says_what_the_data_could_not_see():
    warning = lending_not_itemised_warning(robinhood_shaped())

    assert "lending activity" in warning
    assert "extra caution" in warning
    # It says which way the uncertainty runs, rather than hedging generally.
    assert "do not itemise" in warning


def test_the_warning_travels_with_the_valuation(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials",
                        lambda ticker, years=5: robinhood_shaped())

    report = analysis.value_company("HOODLIKE")

    assert report.suitable
    # Carried as a doubt about the method, not as an ordinary caveat - and
    # only once, so the app can pin it beside the figure without repeating it.
    assert any("lending activity" in w for w in report.suitability.method_fit)
    assert not any("lending activity" in w for w in report.suitability.warnings)


def test_a_fee_business_that_reports_its_interest_carries_no_such_warning(monkeypatch):
    # Visa's shape: a small interest expense IS reported, so the conclusion
    # rests on a published line and needs no caveat.
    fin = fee_business(interest_share=0.018)
    monkeypatch.setattr(analysis, "fetch_financials", lambda ticker, years=5: fin)

    report = analysis.value_company("VLIKE")

    assert report.suitable
    assert not any("lending activity" in w for w in report.suitability.warnings)


def test_a_refused_lender_gets_no_warning_only_a_refusal(monkeypatch):
    fin = lender(loans_x_revenue=6.0)
    monkeypatch.setattr(analysis, "fetch_financials", lambda ticker, years=5: fin)

    report = analysis.value_company("LENDERLIKE")

    assert not report.suitable
    assert not any("lending activity" in w for w in report.suitability.warnings)
    assert any("loan book" in r for r in report.suitability.reasons)


def test_a_bank_is_not_given_the_fee_warning():
    fin = make_bank("BANKY")

    assert classify_financial(fin).admitted_on_absence is False


# ---------------------------------------------------------------------------
# The method-fit doubt reaches the response in its own field
#
# The app pins these beside the figure. They must arrive separately from the
# ordinary caveats, and only once, or the app would either bury them among
# notes on growth and tax or show them twice.
# ---------------------------------------------------------------------------

def _api_body(monkeypatch, fin, ticker):
    from fastapi.testclient import TestClient

    import api
    from api import app

    monkeypatch.setattr(analysis, "fetch_financials", lambda t, years=5: fin)
    api.reset_rate_limits()
    return TestClient(app).get(f"/valuation/{ticker}").json()


def test_the_lending_doubt_arrives_as_a_method_fit_warning(monkeypatch):
    body = _api_body(monkeypatch, robinhood_shaped(), "HOODLIKE")

    assert body["method"] == "dcf"
    assert len(body["method_fit_warnings"]) == 1
    assert "lending activity" in body["method_fit_warnings"][0]
    # And not a second time among the ordinary caveats.
    assert not any("lending activity" in w for w in body["warnings"])


def test_an_ordinary_company_has_no_method_fit_warnings(monkeypatch):
    body = _api_body(monkeypatch, fake_fetch_financials("GOOD"), "GOOD")

    assert body["method"] == "dcf"
    assert body["method_fit_warnings"] == []


def test_a_fee_business_that_reports_interest_has_no_method_fit_warning(monkeypatch):
    body = _api_body(monkeypatch, fee_business(interest_share=0.018), "VLIKE")

    assert body["method_fit_warnings"] == []


def test_the_warning_text_is_unchanged_by_moving_it():
    # Moving the doubt changed where it is shown, not what it says.
    warning = lending_not_itemised_warning(robinhood_shaped())

    assert warning.endswith("so treat this valuation with extra caution.")
