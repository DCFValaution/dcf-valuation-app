"""
Offline tests for the WACC derivation guards.

These use synthetic CompanyFinancials so the bad-data paths can be exercised
deterministically - the live API will not reliably serve a company with a
beta of 90 on demand.

Run with:  python -m pytest test_wacc_fallbacks.py -v
"""

import pytest

import assumptions as A
from assumptions import DEFAULT_BETA, DEFAULT_WACC, derive_assumptions
from market_data import CompanyFinancials


def make_fin(*, beta=1.0, market_cap=1_000_000e6, interest=40e6,
             short_debt=0.0, long_debt=1_000e6, years=4) -> CompanyFinancials:
    """A minimal but internally consistent profitable company."""
    income, balance, cashflow = [], [], []
    for i in range(years):
        revenue = 10_000e6 * (1.05 ** (years - 1 - i))
        income.append({
            "fiscalYear": str(2025 - i),
            "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.20,
            "interestExpense": interest,
            "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0,
            "totalInvestments": revenue * 0.10,
            "shortTermDebt": short_debt,
            "longTermDebt": long_debt,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": beta, "marketCap": market_cap,
                 "price": 100.0, "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


@pytest.fixture(autouse=True)
def _fixed_risk_free(monkeypatch):
    """Pin the risk-free rate so these tests do not depend on the Treasury curve."""
    monkeypatch.setattr(A, "fetch_risk_free_rate", lambda tenor="year10": (0.045, "pinned for test"))


# --- Happy path ------------------------------------------------------------

def test_wacc_is_derived_for_clean_data():
    d = derive_assumptions(make_fin())
    assert d.provenance["wacc"].source == "derived"
    assert 0.04 <= d.assumptions.wacc <= 0.20


def test_wacc_components_recorded():
    d = derive_assumptions(make_fin())
    for name in ("risk_free_rate", "equity_risk_premium", "beta", "cost_of_debt"):
        assert name in d.wacc_inputs
    assert d.diagnostics["wacc_components"]["cost_of_equity"] > 0


# --- Beta guards -----------------------------------------------------------

def test_absurd_beta_falls_back_to_market_beta():
    d = derive_assumptions(make_fin(beta=90.0))
    assert d.wacc_inputs["beta"].value == DEFAULT_BETA
    assert d.wacc_inputs["beta"].source == "default"
    assert "outside plausible range" in d.wacc_inputs["beta"].detail


def test_missing_beta_falls_back_to_market_beta():
    d = derive_assumptions(make_fin(beta=0.0))
    assert d.wacc_inputs["beta"].value == DEFAULT_BETA
    assert "not reported" in d.wacc_inputs["beta"].detail


# --- Cost-of-debt guards ---------------------------------------------------

def test_zero_interest_expense_is_skipped_not_treated_as_zero_rate():
    """The Apple case: FMP reports 0 interest against real debt."""
    d = derive_assumptions(make_fin(interest=0.0))
    prov = d.wacc_inputs["cost_of_debt"]
    assert prov.source == "default"
    assert prov.value > 0, "a 0% cost of debt would understate WACC"
    assert "no year with usable interest expense" in prov.detail


def test_absurd_cost_of_debt_falls_back_to_spread():
    d = derive_assumptions(make_fin(interest=900e6, long_debt=1_000e6))  # 90%
    prov = d.wacc_inputs["cost_of_debt"]
    assert prov.source == "default"
    assert "outside plausible range" in prov.detail


def test_debt_free_company_still_derives_wacc():
    d = derive_assumptions(make_fin(long_debt=0.0, short_debt=0.0, interest=0.0))
    assert d.provenance["wacc"].source == "derived"
    components = d.diagnostics["wacc_components"]
    assert components["weight_debt"] == 0.0
    assert d.assumptions.wacc == pytest.approx(components["cost_of_equity"])


# --- WACC sanity band ------------------------------------------------------

def test_absurd_wacc_falls_back_to_global_default():
    # A very high ERP pushes cost of equity past the sane ceiling.
    d = derive_assumptions(make_fin(), overrides={"equity_risk_premium": 0.90})
    assert d.assumptions.wacc == DEFAULT_WACC
    assert d.provenance["wacc"].source == "default"
    assert "outside the plausible range" in d.provenance["wacc"].detail


def test_no_market_cap_falls_back_to_price_times_shares():
    d = derive_assumptions(make_fin(market_cap=0.0))
    assert d.diagnostics["wacc_components"]["equity_value"] == pytest.approx(100.0 * 1_000e6)


# --- Overrides -------------------------------------------------------------

@pytest.mark.parametrize("name,value", [
    ("risk_free_rate", 0.03),
    ("equity_risk_premium", 0.04),
    ("beta", 1.5),
    ("cost_of_debt", 0.06),
])
def test_wacc_inputs_are_overridable(name, value):
    d = derive_assumptions(make_fin(), overrides={name: value})
    assert d.wacc_inputs[name].value == value
    assert d.wacc_inputs[name].source == "override"


def test_wacc_itself_is_overridable():
    d = derive_assumptions(make_fin(), overrides={"wacc": 0.11})
    assert d.assumptions.wacc == 0.11
    assert d.provenance["wacc"].source == "override"


# --- Sensitivity grid centring ---------------------------------------------

def test_sensitivity_grid_centres_on_derived_wacc_and_growth():
    from dcf import BaseYearData, run_dcf

    fin = make_fin()
    d = derive_assumptions(fin, overrides={"wacc": 0.1053, "terminal_growth": 0.025})
    base = BaseYearData(revenue=10_000, total_debt=1_000, cash=1_000,
                        shares=1_000, current_price=100.0)
    r = run_dcf(base, d.assumptions)

    assert r.sensitivity_waccs == [0.0953, 0.1003, 0.1053, 0.1103, 0.1153]
    assert r.sensitivity_gs == [0.015, 0.020, 0.025, 0.030, 0.035]
    # The centre cell must equal the headline valuation.
    assert r.sensitivity[2][2] == pytest.approx(r.intrinsic_value_per_share)


def test_sensitivity_marks_undefined_cells_when_growth_meets_wacc():
    """A low WACC makes the top-left of the grid degenerate (g >= WACC)."""
    from dcf import BaseYearData, run_dcf

    fin = make_fin()
    d = derive_assumptions(fin, overrides={"wacc": 0.03, "terminal_growth": 0.025})
    base = BaseYearData(revenue=10_000, total_debt=1_000, cash=1_000,
                        shares=1_000, current_price=100.0)
    r = run_dcf(base, d.assumptions)

    # WACC grid [2.0, 2.5, 3.0, 3.5, 4.0]; g grid [1.5, 2.0, 2.5, 3.0, 3.5]
    assert r.sensitivity[0][1] is None, "g 2.0% >= WACC 2.0% must be undefined"
    assert r.sensitivity[4][0] is not None, "g 1.5% < WACC 4.0% is well defined"


def test_unknown_override_is_rejected_and_lists_valid_names():
    with pytest.raises(ValueError) as excinfo:
        derive_assumptions(make_fin(), overrides={"bogus": 1})
    assert "bogus" in str(excinfo.value)
    assert "risk_free_rate" in str(excinfo.value)
