"""The EDGAR shadow fetcher, against canned XBRL rather than the SEC.

The things that can go wrong here are not transport failures. They are:
reading a quarter as a year, reading a 10-K's comparative column as a
separate year, taking a superseded figure over the restatement that replaced
it, mislabelling a September fiscal year as the calendar year it ends in,
losing half a company's history at the ASC 606 tag change, and inverting the
sign convention on capital expenditure. Each of those has its own test.
"""

import json

import pytest

import edgar_data as E
import public_data as P


# ---------------------------------------------------------------------------
# Building canned facts
# ---------------------------------------------------------------------------

def point(val, start, end, *, form="10-K", filed="2026-02-01", fy=None,
          fp="FY", accn="0000-25-000001"):
    out = {"val": val, "end": end, "form": form, "filed": filed, "fp": fp,
           "accn": accn}
    if start is not None:
        out["start"] = start
    if fy is not None:
        out["fy"] = fy
    return out


def facts(**tags):
    """{'facts': {'us-gaap': {tag: {'units': {unit: [points]}}}}}"""
    built = {}
    for tag, spec in tags.items():
        unit, points = spec
        built[tag] = {"units": {unit: points}}
    return {"entityName": "Test Co", "cik": 1, "facts": {"us-gaap": built}}


def year(y, revenue, **extra):
    """A whole fiscal year ending 31 December."""
    return point(revenue, f"{y}-01-01", f"{y}-12-31", fy=y, **extra)


# ---------------------------------------------------------------------------
# Period selection
# ---------------------------------------------------------------------------

def test_a_quarter_is_not_a_year():
    """A 10-K carries quarterly figures too; a 92-day span is not a year.

    The quarter deliberately ends on a different day from the fiscal year.
    A Q4 point ending 31 December collides with the annual point on the same
    key and gets dropped by the de-duplication whether the span check works
    or not - so a test built that way passes against a broken span check.
    """
    f = facts(Revenues=("USD", [
        year(2025, 100.0),
        point(30.0, "2025-07-01", "2025-09-30", fy=2025, fp="Q3"),
        point(55.0, "2025-01-01", "2025-06-30", fy=2025, fp="Q2"),
    ]))
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert sorted(series) == ["2025-12-31"]
    assert [p["val"] for p in series.values()] == [100.0]


def test_a_two_year_comparative_is_not_a_year():
    f = facts(Revenues=("USD", [
        year(2025, 100.0),
        point(190.0, "2024-01-01", "2025-12-31", fy=2025),
    ]))
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert [p["val"] for p in series.values()] == [100.0]


def test_a_fifty_two_week_retail_year_is_a_year():
    """364 days is a fiscal year even though it is not 365."""
    f = facts(Revenues=("USD", [
        point(100.0, "2025-02-02", "2026-01-31", fy=2025),
    ]))
    assert len(E._series(f, E.INCOME_TAGS["revenue"])) == 1


def test_only_annual_forms_count():
    f = facts(Revenues=("USD", [
        year(2025, 100.0, form="10-Q"),
        year(2024, 90.0, form="8-K"),
        year(2023, 80.0, form="10-K"),
    ]))
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert [p["val"] for p in series.values()] == [80.0]


def test_an_amended_annual_report_still_counts():
    f = facts(Revenues=("USD", [year(2025, 100.0, form="20-F/A")]))
    assert len(E._series(f, E.INCOME_TAGS["revenue"])) == 1


def test_a_twenty_f_counts_so_foreign_filers_are_not_empty():
    f = facts(Revenues=("USD", [year(2025, 100.0, form="20-F")]))
    assert len(E._series(f, E.INCOME_TAGS["revenue"])) == 1


# ---------------------------------------------------------------------------
# Restatements
# ---------------------------------------------------------------------------

def test_the_restatement_wins_over_the_figure_it_replaced():
    """Same period, two filings. The later one is the company's own view."""
    f = facts(Revenues=("USD", [
        year(2024, 900.0, filed="2025-02-01", accn="a"),
        year(2024, 950.0, filed="2026-02-01", accn="b"),
    ]))
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert series["2024-12-31"]["val"] == 950.0


def test_the_comparative_column_does_not_become_a_second_year():
    """A 10-K repeats two prior years. Three filings, three years, not nine."""
    points = []
    for filed, years in (("2024-02-01", (2021, 2022, 2023)),
                         ("2025-02-01", (2022, 2023, 2024)),
                         ("2026-02-01", (2023, 2024, 2025))):
        for y in years:
            points.append(year(y, float(y), filed=filed))
    f = facts(Revenues=("USD", points))
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert len(series) == 5
    assert sorted(series) == ["2021-12-31", "2022-12-31", "2023-12-31",
                              "2024-12-31", "2025-12-31"]


# ---------------------------------------------------------------------------
# Tag chains
# ---------------------------------------------------------------------------

def test_the_first_tag_in_the_chain_wins_for_a_period():
    f = facts(
        RevenueFromContractWithCustomerExcludingAssessedTax=(
            "USD", [year(2025, 100.0)]),
        Revenues=("USD", [year(2025, 777.0)]),
    )
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert series["2025-12-31"]["val"] == 100.0


def test_a_mid_history_tag_change_keeps_both_halves():
    """ASC 606 moved almost every filer onto a new revenue tag."""
    f = facts(
        RevenueFromContractWithCustomerExcludingAssessedTax=(
            "USD", [year(2025, 100.0), year(2024, 90.0)]),
        SalesRevenueNet=("USD", [year(2023, 80.0), year(2022, 70.0)]),
    )
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert len(series) == 4
    assert series["2025-12-31"]["val"] == 100.0
    assert series["2022-12-31"]["val"] == 70.0


def test_a_tag_in_another_taxonomy_is_found():
    """An IFRS filer's facts live under ifrs-full, not us-gaap."""
    f = {"facts": {"ifrs-full": {
        "Revenue": {"units": {"EUR": [year(2025, 100.0, form="20-F")]}}}}}
    series = E._series(f, E.INCOME_TAGS["revenue"])
    assert series["2025-12-31"]["val"] == 100.0


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

def test_the_currency_comes_off_the_figures_themselves():
    f = {"facts": {"ifrs-full": {
        "Revenue": {"units": {"DKK": [year(2025, 100.0, form="20-F")]}}}}}
    assert E.reporting_currency_of(f) == "DKK"


def test_a_restated_currency_resolves_to_the_one_most_of_the_history_is_in():
    """A filer that changed presentation currency has facts in both.

    Picking the rarer one would restate four years of statements by an
    exchange rate that applies to one of them.
    """
    f = {"facts": {"ifrs-full": {"Revenue": {"units": {
        "USD": [year(y, 100.0, form="20-F") for y in (2022, 2023, 2024, 2025)],
        "EUR": [year(2021, 90.0, form="20-F")],
    }}}}}
    assert E.reporting_currency_of(f) == "USD"


def test_a_share_unit_is_not_mistaken_for_a_currency():
    f = facts(Revenues=("shares", [year(2025, 100.0)]))
    assert E.reporting_currency_of(f) is None


def test_no_facts_means_no_currency():
    assert E.reporting_currency_of({"facts": {}}) is None


# ---------------------------------------------------------------------------
# The whole object
# ---------------------------------------------------------------------------

@pytest.fixture
def company(monkeypatch):
    """A whole small filer, wired so no request is made."""
    def build(extra_tags=None, ticker="TEST"):
        tags = {
            "Revenues": ("USD", [year(y, 1000.0 * (1.1 ** (y - 2021)))
                                 for y in range(2021, 2026)]),
            "OperatingIncomeLoss": ("USD", [year(y, 200.0)
                                            for y in range(2021, 2026)]),
            "IncomeTaxExpenseBenefit": ("USD", [year(y, 40.0)
                                                for y in range(2021, 2026)]),
            "Assets": ("USD", [point(5000.0, None, f"{y}-12-31", fy=y)
                               for y in range(2021, 2026)]),
            "StockholdersEquity": ("USD", [point(3000.0, None, f"{y}-12-31",
                                                 fy=y)
                                           for y in range(2021, 2026)]),
            "PaymentsToAcquirePropertyPlantAndEquipment": (
                "USD", [year(y, 150.0) for y in range(2021, 2026)]),
            "WeightedAverageNumberOfDilutedSharesOutstanding": (
                "shares", [year(y, 1_000_000.0) for y in range(2021, 2026)]),
        }
        tags.update(extra_tags or {})
        f = facts(**tags)
        monkeypatch.setattr(E, "cik_for",
                            lambda t: ("0000000001", "Test Co Inc"))
        monkeypatch.setattr(E, "company_facts", lambda cik: f)
        return E.edgar_financials(ticker)
    return build


def test_five_years_newest_first(company):
    fin = company()
    assert [r["fiscalYear"] for r in fin.income] == [2025, 2024, 2023, 2022,
                                                     2021]
    assert len(fin.balance) == 5
    assert len(fin.cashflow) == 5


def test_the_three_statements_are_period_aligned(company):
    fin = company()
    for i in range(5):
        assert fin.income[i]["date"] == fin.balance[i]["date"]
        assert fin.income[i]["date"] == fin.cashflow[i]["date"]


def test_capex_is_negated_to_match_the_aggregator(company):
    """Filers tag cash paid as positive; every consumer expects an outflow."""
    fin = company()
    assert fin.cashflow[0]["capitalExpenditure"] == -150.0


def test_an_already_negative_outflow_is_left_alone(company):
    fin = company({"PaymentsToAcquirePropertyPlantAndEquipment":
                   ("USD", [year(2025, -150.0)])})
    assert fin.cashflow[0]["capitalExpenditure"] == -150.0


def test_a_september_year_end_keeps_the_filer_s_own_label(company):
    """Apple's 2025 ends in September 2025; Microsoft's ends in June."""
    fin = company({"Revenues": ("USD", [
        point(1000.0, "2024-10-01", "2025-09-27", fy=2025)])})
    assert fin.income[0]["fiscalYear"] == 2025
    assert fin.income[0]["date"] == "2025-09-27"


def test_a_year_ending_in_january_is_labelled_by_the_filer_not_the_date(
        company):
    """A retailer's FY2025 ends 31 January 2026. "2026" would be wrong."""
    fin = company({"Revenues": ("USD", [
        point(1000.0, "2025-02-02", "2026-01-31", fy=2025)])})
    assert fin.income[0]["fiscalYear"] == 2025


def test_a_balance_sheet_dated_a_day_out_is_still_matched(company):
    """A 52/53-week filer can date the two statements days apart."""
    fin = company({
        "Revenues": ("USD", [point(1000.0, "2025-01-01", "2025-12-31",
                                   fy=2025)]),
        "Assets": ("USD", [point(5000.0, None, "2025-12-28", fy=2025)]),
    })
    assert fin.balance[0]["totalAssets"] == 5000.0


def test_a_balance_sheet_from_a_different_year_is_not_matched(company):
    fin = company({
        "Revenues": ("USD", [point(1000.0, "2025-01-01", "2025-12-31",
                                   fy=2025)]),
        "Assets": ("USD", [point(5000.0, None, "2024-12-31", fy=2024)]),
    })
    assert fin.balance[0]["totalAssets"] is None


def test_operating_income_is_rebuilt_where_no_filer_reports_one(company):
    """Insurers and some conglomerates present no operating income line."""
    fin = company({
        "OperatingIncomeLoss": ("USD", []),
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            ("USD", [year(2025, 180.0)]),
        "InterestExpense": ("USD", [year(2025, 20.0)]),
    })
    assert fin.income[0]["operatingIncome"] == pytest.approx(200.0)


def test_interest_expense_is_made_positive(company):
    fin = company({"InterestExpense": ("USD", [year(2025, -20.0)])})
    assert fin.income[0]["interestExpense"] == 20.0


def test_the_normalised_tax_rate_is_absent_and_says_so(company):
    """No filer reports one, so the source chain must fall through."""
    fin = company()
    assert fin.income[0]["effectiveTaxRate"] is None


def test_investments_are_summed_the_way_the_aggregator_presents_them(company):
    fin = company({
        "ShortTermInvestments": ("USD", [point(100.0, None, "2025-12-31")]),
        "LongTermInvestments": ("USD", [point(400.0, None, "2025-12-31")]),
    })
    assert fin.balance[0]["shortTermInvestments"] == 100.0
    assert fin.balance[0]["totalInvestments"] == 500.0


def test_a_missing_investment_line_sums_to_zero_not_to_none(company):
    fin = company()
    assert fin.balance[0]["totalInvestments"] == 0.0


def test_no_ttm_is_built(company):
    """A 20-F filer has no quarters, so neither source gets one here."""
    assert company().ttm is None


def test_a_lent_profile_fills_what_edgar_cannot_supply(company, monkeypatch):
    lent = {"sector": "Technology", "industry": "Software", "price": 100.0,
            "beta": 1.2, "currency": "USD"}
    f = facts(Revenues=("USD", [year(2025, 1000.0)]))
    monkeypatch.setattr(E, "cik_for", lambda t: ("0000000001", "Test Co Inc"))
    monkeypatch.setattr(E, "company_facts", lambda cik: f)
    fin = E.edgar_financials("TEST", profile=lent)
    assert fin.sector == "Technology"
    assert fin.profile["source"] == "edgar+lent-profile"
    assert fin.profile["cik"] == "0000000001"


def test_without_a_lent_profile_the_company_has_no_sector(company):
    """Honest: EDGAR cannot route a company to a method."""
    fin = company()
    assert fin.profile["source"] == "edgar"
    assert fin.sector == "Unknown"


def test_an_unknown_ticker_says_which_cases_land_there(monkeypatch):
    monkeypatch.setattr(E, "cik_for", lambda t: None)
    with pytest.raises(LookupError, match="not in the SEC's ticker list"):
        E.edgar_financials("NOPE")


def test_no_facts_is_a_lookup_error(monkeypatch):
    monkeypatch.setattr(E, "cik_for", lambda t: ("0000000001", "Test Co"))
    monkeypatch.setattr(E, "company_facts", lambda cik: None)
    with pytest.raises(LookupError, match="no facts"):
        E.edgar_financials("TEST")


# ---------------------------------------------------------------------------
# Ticker to CIK
# ---------------------------------------------------------------------------

@pytest.fixture
def cik_list(monkeypatch):
    body = json.dumps({
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 1067983, "ticker": "BRK-B",
              "title": "BERKSHIRE HATHAWAY INC"},
    })
    monkeypatch.setattr(E, "_fetch", lambda url, **kw: body)
    monkeypatch.setattr(E, "_cik_index", None)
    yield
    monkeypatch.setattr(E, "_cik_index", None)


def test_cik_is_zero_padded_to_ten_digits(cik_list):
    assert E.cik_for("AAPL") == ("0000320193", "Apple Inc.")


def test_cik_lookup_is_case_insensitive(cik_list):
    assert E.cik_for("aapl")[0] == "0000320193"


def test_a_class_suffix_is_tried_several_ways(cik_list):
    """The aggregator writes BRK-B; the SEC has written it three ways."""
    assert E.cik_for("BRK-B")[0] == "0001067983"


def test_an_unlisted_ticker_is_none_not_a_crash(cik_list):
    assert E.cik_for("ZZZZ") is None


# ---------------------------------------------------------------------------
# The SEC's one condition
# ---------------------------------------------------------------------------

def test_no_contact_configured_refuses_before_making_a_request(monkeypatch):
    monkeypatch.delenv(P.PUBLIC_DATA_CONTACT_VAR, raising=False)
    with pytest.raises(E.EdgarNotConfigured, match="PUBLIC_DATA_CONTACT"):
        E.sec_user_agent()


def test_an_address_is_sent_as_the_agent(monkeypatch):
    monkeypatch.setenv(P.PUBLIC_DATA_CONTACT_VAR, "Intrinsic ops@example.com")
    assert E.sec_user_agent() == "Intrinsic ops@example.com"


def test_a_contact_without_an_at_is_appended_rather_than_trusted(monkeypatch):
    monkeypatch.setenv(P.PUBLIC_DATA_CONTACT_VAR, "Intrinsic")
    agent = E.sec_user_agent()
    assert agent.endswith("Intrinsic")
    assert "dcf-valuation-app" in agent


def test_the_rate_limit_is_the_sec_s_published_one():
    assert E.SEC_RATE_LIMIT_PER_SECOND <= 10


def test_the_rate_limit_actually_sleeps():
    """A constant that nothing enforces is a comment, not a rate limit.

    The harness reads dozens of companies in a loop; without this it would
    send them as fast as the socket allows, which is how a client earns the
    "undeclared automated tool" page whatever its User-Agent says.
    """
    import time

    E._last_request = 0.0
    calls = 8
    start = time.monotonic()
    for _ in range(calls):
        E._throttle()
    elapsed = time.monotonic() - start
    # The first call does not wait, so (calls - 1) gaps are owed. Nine tenths
    # of the budget, because a sleep can return marginally early.
    expected = (calls - 1) / E.SEC_RATE_LIMIT_PER_SECOND
    assert elapsed >= expected * 0.9, (
        f"{calls} calls took {elapsed:.3f}s; at "
        f"{E.SEC_RATE_LIMIT_PER_SECOND}/s they owe {expected:.3f}s")


# ---------------------------------------------------------------------------
# Bulk archive
# ---------------------------------------------------------------------------

def test_the_bulk_archive_is_read_in_preference_to_the_api(monkeypatch,
                                                           tmp_path):
    import zipfile

    path = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("CIK0000000001.json", json.dumps({"entityName": "Bulk"}))
    monkeypatch.setenv(E.BULK_FACTS_VAR, str(path))
    monkeypatch.setattr(E, "_fetch",
                        lambda url, **kw: pytest.fail("the API was called"))
    E.reset_bulk()
    try:
        assert E.company_facts("0000000001")["entityName"] == "Bulk"
    finally:
        E.reset_bulk()


def test_a_company_missing_from_the_archive_falls_through_to_the_api(
        monkeypatch, tmp_path):
    import zipfile

    path = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("CIK0000000001.json", json.dumps({"entityName": "Bulk"}))
    monkeypatch.setenv(E.BULK_FACTS_VAR, str(path))
    monkeypatch.setattr(E, "_fetch",
                        lambda url, **kw: json.dumps({"entityName": "Api"}))
    E.reset_bulk()
    try:
        assert E.company_facts("0000000002")["entityName"] == "Api"
    finally:
        E.reset_bulk()


def test_a_missing_archive_path_falls_through_rather_than_failing(
        monkeypatch, tmp_path):
    monkeypatch.setenv(E.BULK_FACTS_VAR, str(tmp_path / "absent.zip"))
    monkeypatch.setattr(E, "_fetch",
                        lambda url, **kw: json.dumps({"entityName": "Api"}))
    E.reset_bulk()
    try:
        assert E.company_facts("0000000001")["entityName"] == "Api"
    finally:
        E.reset_bulk()


def test_no_archive_configured_uses_the_api(monkeypatch):
    monkeypatch.delenv(E.BULK_FACTS_VAR, raising=False)
    monkeypatch.setattr(E, "_fetch",
                        lambda url, **kw: json.dumps({"entityName": "Api"}))
    E.reset_bulk()
    assert E.company_facts("0000000001")["entityName"] == "Api"


# ---------------------------------------------------------------------------
# Nothing in production calls this
# ---------------------------------------------------------------------------

def test_the_shadow_fetcher_is_not_wired_into_anything():
    """The whole premise of this stage. Asserted rather than remembered."""
    import pathlib

    root = pathlib.Path(__file__).parent
    production = ["analysis.py", "api.py", "assumptions.py", "market_data.py",
                  "dcf.py", "ddm.py", "ddm_assumptions.py", "hypothetical.py",
                  "relative.py", "roic.py", "speculative.py",
                  "speculative_assumptions.py", "market_snapshot.py",
                  "excel_export.py", "plain_language.py"]
    for name in production:
        text = (root / name).read_text(encoding="utf-8")
        assert "edgar_data" not in text, (
            f"{name} imports the shadow fetcher. It is not ready to be "
            "production's source; switching it on is a later stage.")
