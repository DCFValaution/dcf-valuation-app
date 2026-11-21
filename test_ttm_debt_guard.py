"""A debt-free company keeps its trailing base, and a stale base says so.

The guard this replaces refused the whole trailing year when neither
quarterly debt line was reported - which is precisely what a debt-free
company looks like, because the data source publishes no debt figure when
there is no debt to publish. Palantir was valued on $4.5bn of revenue while
$7.3bn sat in the same response: a 63% understatement, and the only warning
on screen was about the growth cap.

Revenue and cash are what a base cannot be built without. Debt is filled
from the annual balance sheet or counted as nil, and the base notes say
which of the three happened.
"""
import pytest

import analysis as An
import assumptions as A
import market_data as M
from market_data import CompanyFinancials


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def ttm(**values):
    """A trailing base with whatever fields a case needs."""
    defaults = {"revenue": 10_000e6, "cash_and_short_term": 2_000e6,
                "shares_diluted": 1_000e6, "capex": -200e6}
    defaults.update(values)
    return M.TTMBase(values=defaults,
                     as_of={k: "2026-09-30" for k in defaults},
                     period_end="2026-09-30", currency="USD",
                     notes=[], stale={})


def fin_with(trailing, *, annual_debt=None, name="Test Co"):
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 8_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "date": f"{2025 - i}-12-31",
            "revenue": revenue, "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "interestExpense": 10e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        row = {
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "commonStockEquity": revenue * 0.5,
        }
        if annual_debt is not None:
            row["shortTermDebt"] = 0.0
            row["longTermDebt"] = annual_debt
        balance.append(row)
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": name, "beta": 1.0, "marketCap": 100_000e6,
                 "price": 100.0, "sector": "Technology",
                 "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow, ttm=trailing)


# --- The guard itself -----------------------------------------------------

class TestTheGuard:
    def test_the_base_survives_with_no_quarterly_debt(self):
        base, meta = M.base_year_from(fin_with(ttm(), annual_debt=None))
        assert meta["base_period"] == "ttm", (
            "the trailing base was discarded over an absent debt line")
        assert base.revenue == pytest.approx(10_000.0)

    def test_revenue_is_still_required(self):
        """It is the one figure the base is built from."""
        trailing = ttm()
        del trailing.values["revenue"]
        with pytest.raises(M.DataUnavailableError):
            M.base_year_from(fin_with(trailing))

    def test_the_annual_debt_fills_in(self):
        base, meta = M.base_year_from(
            fin_with(ttm(), annual_debt=4_000e6))
        assert base.total_debt == pytest.approx(4_000.0)
        assert any("year-end figure" in n for n in meta["base_notes"])

    def test_a_reported_nil_annual_debt_is_a_fact_about_the_company(self):
        base, meta = M.base_year_from(fin_with(ttm(), annual_debt=0.0))
        assert base.total_debt == 0.0
        assert any("reports no borrowings" in n for n in meta["base_notes"])

    def test_an_unreported_annual_debt_says_the_figure_is_missing(self):
        """Not the same claim. `debt_was_reported` exists to draw exactly
        this line: both lines absent is the balance sheet not arriving, not
        the company being debt-free - and counting it as nil flatters the
        company, so it is said plainly."""
        base, meta = M.base_year_from(fin_with(ttm(), annual_debt=None))
        assert base.total_debt == 0.0
        note = next(n for n in meta["base_notes"] if "borrowings" in n)
        assert "could be read" in note
        assert "values the shares too high" in note
        assert "reports no borrowings" not in note

    def test_a_rounding_of_debt_is_treated_as_nil(self):
        """A few hundred thousand of finance leases on a ten-billion
        balance sheet is not a capital structure."""
        base, _meta = M.base_year_from(fin_with(ttm(), annual_debt=100.0))
        assert base.total_debt == 0.0

    def test_a_quarterly_debt_figure_still_wins(self):
        base, meta = M.base_year_from(
            fin_with(ttm(current_debt=500e6, long_term_debt=1_500e6),
                     annual_debt=9_000e6))
        assert base.total_debt == pytest.approx(2_000.0)
        assert not any("year-end figure" in n for n in meta["base_notes"])

    def test_one_half_reported_does_not_trigger_the_annual_fallback(self):
        """Amazon reports long-term debt and no current debt. That is one
        half of a real figure, not an absent one."""
        base, meta = M.base_year_from(
            fin_with(ttm(long_term_debt=1_500e6), annual_debt=9_000e6))
        assert base.total_debt == pytest.approx(1_500.0)
        assert not any("year-end figure" in n for n in meta["base_notes"])


# --- A stale base is never silent -----------------------------------------

class TestTheAnnualBaseWarning:
    def _warn(self, meta):
        class Derived:
            wacc_inputs: dict = {}
            capm_inputs: dict = {}
            provenance: dict = {}
            diagnostics: dict = {}

        return {w.kind: w for w in An.input_source_warnings(
            Derived(), dcf=True,
            annual_base_reason=An._annual_base_reason(meta))}

    def test_a_refused_trailing_base_is_said(self):
        got = self._warn({"base_period": "annual",
                          "ttm_declined": "no trailing revenue figure was "
                                          "reported"})
        assert "annual_base" in got
        assert got["annual_base"].severity == An.SEVERITY_CAUTION
        assert "no trailing revenue" in got["annual_base"].detail["why"]

    def test_a_trailing_base_says_nothing(self):
        assert "annual_base" not in self._warn({"base_period": "ttm"})

    def test_a_trailing_base_says_nothing_even_with_a_stale_reason(self):
        """The period is what decides it, not the presence of a reason.

        A reason can linger from an earlier attempt, and warning that the
        figures are a year old while showing a trailing base would be
        false. Mutation testing found this: removing the period check
        survived, because every other case here has no reason to linger.
        """
        got = self._warn({"base_period": "ttm",
                          "ttm_declined": "no trailing revenue figure was "
                                          "reported"})
        assert "annual_base" not in got

    def test_a_company_with_no_trailing_data_to_want_says_nothing(self):
        """A 20-F filer publishes no quarterly figures, so its annual base
        is not stale - it is everything there is. Warning about it would
        cry wolf on every foreign issuer."""
        assert "annual_base" not in self._warn({"base_period": "annual",
                                                "ttm_declined": None})

    def test_the_headline_says_how_old_the_figures_may_be(self):
        got = self._warn({"base_period": "annual",
                          "ttm_declined": "the data source returned no "
                                          "trailing figures at all"})
        assert "year" in got["annual_base"].headline

    @pytest.mark.parametrize("reason", [
        "no trailing revenue figure was reported",
        "the latest quarterly cash figure is missing or too old to use",
        "the data source returned no trailing figures at all",
        "no quarterly balance sheet date could be established",
    ])
    def test_every_refusal_reason_reaches_the_reader(self, reason):
        got = self._warn({"base_period": "annual", "ttm_declined": reason})
        assert reason in got["annual_base"].detail["why"]


class TestTheRefusalReasons:
    """fetch_ttm_base records why it declined, so the warning can say."""

    def _rows(self, **overrides):
        rows = {
            M._TTM_SERIES["revenue"]: {
                "reportedValue": {"raw": 10_000e6}, "asOfDate": "2026-09-30",
                "currencyCode": "USD"},
            M._TTM_SERIES["cash_and_short_term"]: {
                "reportedValue": {"raw": 2_000e6}, "asOfDate": "2026-09-30",
                "currencyCode": "USD"},
            M._TTM_SERIES["current_debt"]: {
                "reportedValue": {"raw": 500e6}, "asOfDate": "2026-09-30",
                "currencyCode": "USD"},
        }
        rows.update(overrides)
        return rows

    def _fetch(self, monkeypatch, rows, *, seed=None):
        M.clear_caches()
        M._ttm_declined.clear()
        if seed is not None:
            # Seeded AFTER the clear. The first version of this seeded
            # before, so the clear wiped it and the test could not fail -
            # found by mutation testing, which removed the line that clears
            # a stale reason and survived.
            M._ttm_declined["TEST"] = seed
        monkeypatch.setattr(M, "_ttm_rows", lambda ticker: rows)
        return M.fetch_ttm_base("TEST")

    def test_no_revenue_is_recorded(self, monkeypatch):
        rows = self._rows()
        del rows[M._TTM_SERIES["revenue"]]
        assert self._fetch(monkeypatch, rows) is None
        assert "revenue" in M.ttm_declined_reason("TEST")

    def test_no_cash_is_recorded(self, monkeypatch):
        rows = self._rows()
        del rows[M._TTM_SERIES["cash_and_short_term"]]
        assert self._fetch(monkeypatch, rows) is None
        assert "cash" in M.ttm_declined_reason("TEST")

    def test_a_successful_base_clears_any_earlier_reason(self, monkeypatch):
        """Otherwise a company that failed once carries the complaint
        forever and every later valuation warns about nothing."""
        got = self._fetch(monkeypatch, self._rows(),
                          seed="something earlier")
        assert got is not None
        assert M.ttm_declined_reason("TEST") is None

    def test_an_unreached_ticker_has_no_reason(self):
        M._ttm_declined.clear()
        assert M.ttm_declined_reason("NOPE") is None
