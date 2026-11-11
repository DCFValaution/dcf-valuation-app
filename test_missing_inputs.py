"""A statement line that did not arrive is not a line that read zero.

The tax bug was one instance of a class. `num` returns 0.0 for an absent
field, which is right for a quantity being added up and wrong for one being
read as evidence, and the derivation read six fields that way. Measured on
Alphabet, each silent zero was worth:

    capex              +111.4%     the largest exposure in the model
    tax rate            +32.3%     the one that actually bit
    debt                 +7.5%
    D&A                 -26.8%
    cash                -23.9%
    operating margin   -164.2%

The three that inflate are the dangerous ones, and capex is three times the
tax rate. The deflating ones are no more honest, they just fail safe.

Probing fifty-nine companies found the class live in two places nobody had
looked: Realty Income and Prologis file no "Capital Expenditure" line at
all - property companies report "Purchase Of Investment Properties" - so
both were being valued as though they never spent a penny on the portfolio,
at $341.42 and $179.03 a share. Eight financials were deriving working
capital of -40% of revenue, the clamp floor, from balance sheets that
simply do not classify current from non-current.

Run with:  python -m pytest test_missing_inputs.py -v
"""

import pytest

import assumptions as A
from assumptions import _net_working_capital, derive_assumptions
from market_data import (CompanyFinancials, base_year_from, debt_was_reported,
                         num, reported, total_debt_of)

OMITTED = object()


def make_fin(*, capex=-400e6, da=500e6, operating_income=2_500e6,
             current_assets=4_000e6, current_liabilities=2_500e6,
             short_debt=200e6, long_debt=1_000e6, cash=1_000e6,
             investments=0.0, years=3) -> CompanyFinancials:
    """A plain profitable company. Any field set to OMITTED is left off the
    statement entirely, as a thinner upstream response would leave it."""
    income, balance, cashflow = [], [], []
    for i in range(years):
        revenue = 10_000e6 * (1.05 ** (years - 1 - i))
        irow = {
            "fiscalYear": str(2025 - i), "revenue": revenue,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "effectiveTaxRate": 0.21,
            "interestExpense": 40e6,
            "weightedAverageShsOutDil": 1_000e6,
        }
        if operating_income is not OMITTED:
            irow["operatingIncome"] = operating_income
        brow = {"fiscalYear": str(2025 - i)}
        for key, value in (("totalCurrentAssets", current_assets),
                           ("totalCurrentLiabilities", current_liabilities),
                           ("shortTermDebt", short_debt),
                           ("longTermDebt", long_debt),
                           ("cashAndCashEquivalents", cash),
                           ("totalInvestments", investments)):
            if value is not OMITTED:
                brow[key] = value
        crow = {"fiscalYear": str(2025 - i)}
        if capex is not OMITTED:
            crow["capitalExpenditure"] = capex
        if da is not OMITTED:
            crow["depreciationAndAmortization"] = da
        income.append(irow)
        balance.append(brow)
        cashflow.append(crow)
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": 1.0, "marketCap": 1_000_000e6,
                 "price": 100.0, "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


@pytest.fixture(autouse=True)
def _fixed_risk_free(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def prov(fin, name):
    d = derive_assumptions(fin)
    return getattr(d.assumptions, name), d.provenance[name]


# --- The reader itself ------------------------------------------------

class TestTheSharedReader:
    def test_it_separates_absent_from_zero(self):
        assert reported({"x": 0.0}, "x") == 0.0
        assert reported({}, "x") is None
        assert num({}, "x") == 0.0, "num still reads silence as nil"

    def test_it_guards_against_nan_and_junk(self):
        assert reported({"x": float("nan")}, "x") is None
        assert reported({"x": "elephant"}, "x") is None
        assert reported({"x": None}, "x") is None

    def test_a_default_can_be_supplied(self):
        assert reported({}, "x", 0.0) == 0.0


# --- Capex: the largest exposure --------------------------------------

class TestCapex:
    def test_a_year_without_a_capex_line_is_skipped_not_scored_as_zero(self):
        fin = make_fin()
        del fin.cashflow[0]["capitalExpenditure"]
        rate, p = prov(fin, "capex_terminal_pct")
        assert rate == pytest.approx(0.04, abs=2e-3), "the reported years decide"
        assert "1 more reported no figure" in p.detail

    def test_with_no_capex_at_all_it_assumes_depreciation_not_nil(self):
        """Nil capex is worth +111% on Alphabet. A company that reported
        nothing is not a company that spent nothing, so it is modelled as
        replacing what it consumes."""
        rate, p = prov(make_fin(capex=OMITTED), "capex_terminal_pct")
        assert rate > 0.0
        assert rate == pytest.approx(0.0476, abs=1e-3), "the D&A ratio"
        assert "no capital spending reported" in p.detail
        assert "assumed equal to depreciation" in p.detail

    def test_the_start_of_the_glide_says_the_same_thing(self):
        rate, p = prov(make_fin(capex=OMITTED), "capex_pct")
        assert rate == pytest.approx(0.0476, abs=1e-3)
        assert "assumed equal to depreciation" in p.detail

    def test_with_neither_capex_nor_depreciation_it_says_it_flatters(self):
        """The one case where nil is unavoidable. It is still not silent."""
        rate, p = prov(make_fin(capex=OMITTED, da=OMITTED),
                       "capex_terminal_pct")
        assert rate == 0.0
        assert "flatters the valuation" in p.detail

    def test_it_never_reports_a_derived_zero_from_an_absent_line(self):
        for fin in (make_fin(capex=OMITTED),
                    make_fin(capex=OMITTED, da=OMITTED)):
            _, p = prov(fin, "capex_pct")
            assert p.source != "derived", p.detail


def test_property_companies_file_capex_under_their_own_name():
    """Realty Income and Prologis report no "Capital Expenditure" row.

    Reading that absence as nil valued Realty Income at $341.42 a share on
    the assumption that it never spends anything on its portfolio. The
    label chain now reaches the line they do file.
    """
    import pandas as pd
    from market_data import _cashflow_row

    frame = pd.DataFrame(
        {"Purchase Of Investment Properties": [-2_500e6],
         "Depreciation And Amortization": [1_900e6]},
        index=[pd.Timestamp("2025-12-31")]).T
    row = _cashflow_row(frame, 0)
    assert row["capitalExpenditure"] == pytest.approx(-2_500e6)


# --- Operating margin, D&A -------------------------------------------

class TestTheOtherRatios:
    def test_a_year_without_operating_income_is_skipped(self):
        fin = make_fin()
        del fin.income[0]["operatingIncome"]
        rate, p = prov(fin, "operating_margin")
        assert rate > 0.0, "a missing year must not drag the margin to nil"
        assert "1 more reported no figure" in p.detail

    def test_with_no_operating_income_the_reason_is_named(self):
        rate, p = prov(make_fin(operating_income=OMITTED), "operating_margin")
        assert rate == 0.0
        assert p.source == "default"
        assert "operating income not reported in any year" in p.detail

    def test_a_year_without_depreciation_is_skipped(self):
        fin = make_fin()
        del fin.cashflow[0]["depreciationAndAmortization"]
        rate, p = prov(fin, "da_pct")
        assert rate > 0.0
        assert "1 more reported no figure" in p.detail

    def test_the_detail_never_counts_years_it_did_not_measure(self):
        fin = make_fin()
        for row in fin.cashflow[:2]:
            del row["depreciationAndAmortization"]
        _, p = prov(fin, "da_pct")
        assert "over 1 years" in p.detail


# --- Working capital --------------------------------------------------

class TestWorkingCapital:
    def test_a_balance_sheet_without_the_split_yields_nothing(self):
        """Banks and insurers do not classify current from non-current.
        Reading those absences as zero produced working capital of minus
        the cash pile, which clamped to -40% of revenue."""
        assert _net_working_capital({"cashAndCashEquivalents": 500e6}) is None
        assert _net_working_capital({"totalCurrentAssets": 4_000e6}) is None

    def test_the_optional_deductions_still_read_absence_as_nil(self):
        """A company with no short-term debt and no short-term investments
        reports neither, and nil is the correct reading of that silence."""
        value = _net_working_capital({"totalCurrentAssets": 4_000e6,
                                      "totalCurrentLiabilities": 2_500e6})
        assert value == pytest.approx(1_500e6)

    def test_such_a_company_falls_back_and_says_why(self):
        fin = make_fin(current_assets=OMITTED, current_liabilities=OMITTED)
        rate, p = prov(fin, "nwc_pct")
        assert rate == 0.0
        assert p.source == "default"
        assert "did not report current assets" in p.detail or \
               "no balance sheet reported current assets" in p.detail

    def test_it_does_not_land_on_the_clamp_floor(self):
        """The old reading produced -40% of revenue - the floor - which is
        a claim that the business is funded by its suppliers to the tune of
        half a year of sales."""
        rate, _ = prov(make_fin(current_assets=OMITTED,
                                current_liabilities=OMITTED), "nwc_pct")
        assert rate != pytest.approx(-0.40)


# --- Debt and cash, in the equity bridge ------------------------------

class TestTheBridge:
    def test_one_absent_debt_line_is_still_nil(self):
        """Plenty of companies carry no short-term debt."""
        assert total_debt_of({"longTermDebt": 1_000e6}) == pytest.approx(1_000e6)
        assert debt_was_reported({"longTermDebt": 1_000e6})

    def test_both_absent_is_a_balance_sheet_that_did_not_arrive(self):
        assert not debt_was_reported({"cashAndCashEquivalents": 10.0})

    def test_the_base_year_says_when_it_inferred_debt(self):
        fin = make_fin(short_debt=OMITTED, long_debt=OMITTED)
        _, meta = base_year_from(fin)
        notes = " ".join(meta.get("base_notes") or [])
        assert "no debt reported" in notes
        assert "inferred, not read" in notes

    def test_the_base_year_says_when_it_inferred_cash(self):
        fin = make_fin(cash=OMITTED, investments=OMITTED)
        _, meta = base_year_from(fin)
        notes = " ".join(meta.get("base_notes") or [])
        assert "no cash reported" in notes

    def test_a_complete_balance_sheet_says_nothing(self):
        _, meta = base_year_from(make_fin())
        assert not (meta.get("base_notes") or [])


# --- Nothing moves when the data is there -----------------------------

def test_a_complete_company_is_untouched():
    """The whole change has to be invisible where the statements are whole.

    Fifty-one of fifty-nine real companies come out bit-for-bit identical;
    this pins the property in a form that runs offline.
    """
    d = derive_assumptions(make_fin())
    a = d.assumptions
    assert a.capex_pct == pytest.approx(0.04, abs=2e-3)
    assert a.da_pct == pytest.approx(0.0476, abs=1e-3)
    assert a.operating_margin == pytest.approx(0.2381, abs=1e-3)
    assert a.nwc_pct == pytest.approx(0.05, abs=0.02)
    for name in ("capex_pct", "da_pct", "operating_margin", "nwc_pct"):
        assert d.provenance[name].source == "derived", name
        assert "reported no figure" not in d.provenance[name].detail
