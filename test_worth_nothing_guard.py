"""A company whose whole value comes out at zero or less.

THE BUG THIS EXISTS FOR
-----------------------
The terminal-dominance rule asks whether the answer is coming from the
part of the model anyone can check, and it asks it as a share:
`pv_terminal / enterprise_value > 150%`.

That share has a discontinuity in it, and the rule only watches one side.
Enterprise value is the forecast plus the terminal value. For a company
whose forecast is worth LESS THAN NOTHING, growing the terminal value
first drives the share up toward infinity, then past it - and it comes
back as a NEGATIVE number. Oracle, once its long-run return on capital was
measured on the same basis as the figure shown beside the valuation, read

    forecast  -$207.2bn
    terminal  +$204.2bn
    EV          -$3.0bn
    share       -6951%

and `-6951% > 150%` is False. It was valued, with a negative enterprise
value, carrying no warning at all. Nothing in the old rule could see it:
every threshold it has is an upper bound on a quantity that had gone
negative.

So the denominator is now checked on its own terms. The question "is this
company worth anything?" does not depend on a ratio and must not be asked
through one.

WHAT THIS RULE IS NOT
---------------------
It is not "the forecast contributes nothing". That rule was measured and
rejected, because it also catches Amazon - whose forecast only turns
negative because the derived capital spending outruns its margin, and
whose enterprise value is solidly positive. Amazon keeps its serious
warning and keeps being valued. The tests below hold that line.

Run with:  python -m pytest test_worth_nothing_guard.py -v
"""

import pytest

import analysis as A
from analysis import HYPOTHETICAL_REFUSAL_CODES, TV_REFUSAL_SHARE
from market_data import CompanyFinancials


def _fin(revenue=100_000e6):
    """An ordinary profitable company; the figures are overridden below."""
    income, balance, cashflow = [], [], []
    for i in range(4):
        rev = revenue / 1.08 ** i
        income.append({
            "fiscalYear": str(2025 - i), "date": f"{2025 - i}-12-31",
            "revenue": rev, "operatingIncome": rev * 0.25,
            "incomeBeforeTax": rev * 0.24,
            "incomeTaxExpense": rev * 0.24 * 0.21,
            "interestExpense": 40e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalAssets": rev * 1.20,
            "totalCurrentAssets": rev * 0.40,
            "totalCurrentLiabilities": rev * 0.25,
            "cashAndCashEquivalents": rev * 0.10,
            "shortTermInvestments": 0.0,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
            "commonStockEquity": rev * 0.80,
        })
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


def _assess(monkeypatch, *, pv_forecast, pv_terminal):
    """Assess a company whose forecast and terminal value are exactly these.

    The last forecast year is left alone, so the earlier `negative_fcf`
    rule does not claim the company first and the rule under test is the
    one that answers.
    """
    import assumptions as Asm
    from market_data import base_year_from

    monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned"))
    fin = _fin()
    base, _ = base_year_from(fin)
    derived = Asm.derive_assumptions(fin)

    real_run = A.run_dcf

    def fake_run(b, a, **kwargs):
        result = real_run(b, a, **kwargs)
        ev = pv_forecast + pv_terminal
        object.__setattr__(result, "pv_ufcf_sum", pv_forecast)
        object.__setattr__(result, "pv_terminal_value", pv_terminal)
        object.__setattr__(result, "enterprise_value", ev)
        object.__setattr__(result, "tv_pct_of_ev",
                           pv_terminal / ev if ev else 0.0)
        return result

    monkeypatch.setattr(A, "run_dcf", fake_run)
    return A.assess_suitability(fin, derived, base)


class TestTheArithmeticHazard:
    def test_a_negative_enterprise_value_makes_the_share_negative(self):
        # The whole reason a separate rule is needed. Stated as arithmetic
        # so it cannot be argued with.
        pv_forecast, pv_terminal = -207_200.0, 204_200.0
        ev = pv_forecast + pv_terminal
        share = pv_terminal / ev

        assert ev < 0
        assert share < 0
        assert not (share > TV_REFUSAL_SHARE), \
            "this is the gap: a ratio that has gone negative passes an " \
            "upper-bound test untouched"

    def test_growing_the_terminal_value_can_LOWER_the_share(self):
        """Counter-intuitive, and the reason the threshold could not just
        be moved. Where the forecast is negative the share is a decreasing
        function of the terminal value on the far side of the crossing."""
        pv_forecast = -200_000.0
        shares = []
        for pv_terminal in (210_000.0, 250_000.0, 400_000.0):
            ev = pv_forecast + pv_terminal
            shares.append(pv_terminal / ev)
        assert shares == sorted(shares, reverse=True), \
            "a bigger terminal value brought the share DOWN"


class TestTheRefusal:
    def test_a_company_worth_nothing_is_refused(self, monkeypatch):
        s = _assess(monkeypatch, pv_forecast=-207_200.0, pv_terminal=204_200.0)
        assert not s.suitable
        assert "negative_enterprise_value" in s.codes

    def test_the_reason_does_not_quote_the_meaningless_share(self, monkeypatch):
        """-6951% is not an explanation, it is a symptom of the bug."""
        s = _assess(monkeypatch, pv_forecast=-207_200.0, pv_terminal=204_200.0)
        reason = " ".join(s.reasons)
        assert "worth nothing or less" in reason
        assert "%" not in reason, \
            "the percentage text belongs to the other rule, not this one"

    def test_exactly_zero_is_refused_too(self, monkeypatch):
        # "Worth nothing" is the claim; nothing is not a positive number.
        s = _assess(monkeypatch, pv_forecast=-100_000.0, pv_terminal=100_000.0)
        assert not s.suitable
        assert "negative_enterprise_value" in s.codes

    def test_a_negative_forecast_with_a_POSITIVE_value_is_still_valued(
            self, monkeypatch):
        """Amazon's case, and the line this rule must not cross.

        Its forecast is worth less than nothing because the derived capital
        spending outruns its margin - a complaint about an input, not about
        the business - and its enterprise value is solidly positive.
        """
        s = _assess(monkeypatch, pv_forecast=-167_500.0, pv_terminal=629_500.0)
        assert "negative_enterprise_value" not in s.codes
        assert s.suitable, \
            "refusing this is the rule that was measured and rejected"

    def test_an_ordinary_company_is_untouched(self, monkeypatch):
        s = _assess(monkeypatch, pv_forecast=1_252_900.0,
                    pv_terminal=1_572_500.0)
        assert s.suitable
        assert "negative_enterprise_value" not in s.codes


class TestTheReaderIsNotLeftAtADeadEnd:
    def test_the_refusal_reaches_the_hypothetical(self):
        """Same door Tesla uses. A company refused over its growth and its
        investment is refused over two of the three figures that screen
        hands the reader, so stopping here would be the dead end the whole
        route exists to remove."""
        assert "negative_enterprise_value" in HYPOTHETICAL_REFUSAL_CODES

    def test_it_is_framed_as_a_profitable_company(self):
        """It makes money. It must not be handed the loss-maker's words."""
        framing = A.hypothetical_framing_for(["negative_enterprise_value"])
        assert framing == A.FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE
