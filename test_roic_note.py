"""Telling the reader when return on capital could not be measured.

When a company's own return on capital cannot be worked out from its
filings, the long-run return falls back to the cost of capital. That is
the right fallback - it says growth creates no value rather than inventing
a number - but it is a large haircut to the terminal value, and it was
applied in silence. The valuation looked like any other while one of its
biggest assumptions had not been derived from the company at all.

The same absence hides the return-on-capital box beside the valuation, so
there is a gap on the page exactly where the explanation belongs.

Run with:  python -m pytest test_roic_note.py -v
"""

import pytest

import analysis as A
import assumptions as Asm
from analysis import SEVERITY_CAUTION, severity_of
from market_data import CompanyFinancials, base_year_from


def _fin(*, total_assets=True):
    """A profitable company, with or without a total-assets line.

    Invested capital is built from the assets side, so a filing with no
    total assets has no measurable return on capital - which is the case
    under test, and a real one: the figure is not always published.
    """
    income, balance, cashflow = [], [], []
    for i in range(4):
        rev = 100_000e6 / 1.08 ** i
        income.append({
            "fiscalYear": str(2025 - i), "date": f"{2025 - i}-12-31",
            "revenue": rev, "operatingIncome": rev * 0.25,
            "incomeBeforeTax": rev * 0.24,
            "incomeTaxExpense": rev * 0.24 * 0.21,
            "interestExpense": 40e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        row = {
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": rev * 0.40,
            "totalCurrentLiabilities": rev * 0.25,
            "cashAndCashEquivalents": rev * 0.10,
            "shortTermInvestments": 0.0,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
            "commonStockEquity": rev * 0.80,
        }
        if total_assets:
            row["totalAssets"] = rev * 1.20
        balance.append(row)
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": rev * 0.05,
            "capitalExpenditure": -rev * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": 1.0,
                 "marketCap": 1_000_000e6, "price": 100.0,
                 "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow)


def _warnings(monkeypatch, *, total_assets):
    monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned"))
    fin = _fin(total_assets=total_assets)
    base, _ = base_year_from(fin)
    derived = Asm.derive_assumptions(fin)
    return derived, list(A.assess_suitability(fin, derived, base).warnings)


def _note(warnings):
    for w in warnings:
        if getattr(w, "kind", "") == "roic_unmeasurable":
            return w
    return None


class TestWhenItCannotBeMeasured:
    def test_the_fallback_really_is_the_cost_of_capital(self, monkeypatch):
        """Fixture sanity, and the fact the note describes."""
        derived, _ = _warnings(monkeypatch, total_assets=False)
        assert derived.diagnostics["current_roic"] is None
        assert derived.assumptions.terminal_roic == pytest.approx(
            derived.assumptions.wacc)
        assert derived.provenance["terminal_roic"].source == "default"

    def test_the_reader_is_told(self, monkeypatch):
        _, warnings = _warnings(monkeypatch, total_assets=False)
        note = _note(warnings)
        assert note is not None, \
            "a silent haircut to the terminal value is the whole bug"

    def test_it_says_what_follows_from_it(self, monkeypatch):
        """Not just that a figure is missing - what that costs the answer."""
        _, warnings = _warnings(monkeypatch, total_assets=False)
        text = str(_note(warnings))
        assert "cost of capital" in text
        assert "adds nothing" in text

    def test_it_is_a_caution_and_not_an_alarm(self, monkeypatch):
        """The figure still means what it says. It is conservative on
        purpose, which is worth telling the reader and not worth shouting
        at them: `serious` is reserved for a figure that does not mean what
        it appears to."""
        _, warnings = _warnings(monkeypatch, total_assets=False)
        assert severity_of(_note(warnings)) == SEVERITY_CAUTION

    def test_the_valuation_still_happens(self, monkeypatch):
        """A note, not a refusal. The conservative answer is still an
        answer, and withholding it would serve nobody."""
        monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                            lambda tenor="year10": (0.045, "pinned"))
        fin = _fin(total_assets=False)
        base, _ = base_year_from(fin)
        s = A.assess_suitability(fin, Asm.derive_assumptions(fin), base)
        assert s.suitable


class TestWhenItCanBeMeasured:
    def test_there_is_no_note(self, monkeypatch):
        derived, warnings = _warnings(monkeypatch, total_assets=True)
        assert derived.diagnostics["current_roic"] is not None, \
            "fixture sanity: this company's return IS measurable"
        assert _note(warnings) is None, \
            "a note on every valuation is a note nobody reads"

    def test_and_the_long_run_return_is_derived_not_defaulted(
            self, monkeypatch):
        derived, _ = _warnings(monkeypatch, total_assets=True)
        assert derived.provenance["terminal_roic"].source != "default"
        assert derived.assumptions.terminal_roic > derived.assumptions.wacc
