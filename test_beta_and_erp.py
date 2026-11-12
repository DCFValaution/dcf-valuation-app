"""Beta's reversion to the market, and a premium with a date on it.

Two inputs that had been guesses dressed as measurements.

The beta was measured over five years and then used to discount cash flows
thirty years out, as though a company that happened to be volatile recently
would stay exactly that volatile forever. Betas do not behave that way; they
revert toward the market, which is what Blume's adjustment encodes. It cuts
both ways and that is the point: NVIDIA's 2.34 comes down to 1.90 and
Coca-Cola's 0.24 goes up to 0.49, because neither extreme persists.

The premium was 5.5%, a reasonable long-run historical average with no date
attached. It is now Damodaran's implied premium - solved from what the index
costs against the cash flows it is expected to produce - which is a figure
someone computed on a stated day, and so can go out of date. It carries that
date, and says so when it is old.

The third fix here has no effect on any number today, which is the honest
way to describe it. Yahoo returns sixty monthly bars and then repeats the
last one, stamped with the moment of the request: a 0.000000% return month
that never happened. It was being discarded only because the index's copy
and each company's are stamped a few seconds apart, so the timestamp
intersection dropped it by accident. Resting correctness on that is resting
it on the clock.

Run with:  python -m pytest test_beta_and_erp.py -v
"""

from datetime import date, datetime, timedelta, timezone

import pytest

import assumptions as A
import market_data as M
from assumptions import (DAMODARAN_ERP_AS_OF, DAMODARAN_IMPLIED_ERP,
                         ERP_STALE_AFTER_DAYS, blume_adjusted,
                         derive_assumptions)
from market_data import CompanyFinancials


def make_fin(beta=1.0) -> CompanyFinancials:
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 10_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "interestExpense": 40e6, "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": beta, "marketCap": 1_000_000e6,
                 "price": 100.0, "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


@pytest.fixture(autouse=True)
def _fixed_risk_free(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


# --- Blume -----------------------------------------------------------

class TestTheBlumeAdjustment:
    def test_it_is_two_thirds_measured_and_one_third_market(self):
        assert blume_adjusted(2.0) == pytest.approx(0.67 * 2.0 + 0.33)

    def test_it_pulls_a_high_beta_down(self):
        # NVIDIA's measured beta.
        assert blume_adjusted(2.3386) == pytest.approx(1.8969, abs=1e-4)

    def test_it_pushes_a_low_beta_up(self):
        """Not a one-way haircut. Coca-Cola's 0.24 is as unlikely to persist
        as NVIDIA's 2.34, and the adjustment says so in both directions."""
        assert blume_adjusted(0.2352) == pytest.approx(0.4876, abs=1e-4)
        assert blume_adjusted(0.2352) > 0.2352

    def test_the_market_beta_is_left_exactly_alone(self):
        """0.67 + 0.33 = 1, so the fallback needs no special case."""
        assert blume_adjusted(1.0) == 1.0

    def test_the_capm_uses_the_adjusted_beta(self):
        d = derive_assumptions(make_fin(beta=2.0))
        assert d.wacc_inputs["beta"].value == pytest.approx(blume_adjusted(2.0))
        coe = d.diagnostics["wacc_components"]["cost_of_equity"]
        assert coe == pytest.approx(
            0.045 + blume_adjusted(2.0) * DAMODARAN_IMPLIED_ERP)

    def test_the_provenance_shows_both_numbers(self):
        """A reader who disagrees with the adjustment has to be able to see
        what it did, and to what."""
        d = derive_assumptions(make_fin(beta=2.31))
        detail = d.wacc_inputs["beta"].detail
        assert "Blume-adjusted" in detail
        assert "2.31" in detail, "the raw beta is named"
        assert d.wacc_inputs["beta"].value == pytest.approx(1.8777, abs=1e-4)

    def test_a_beta_the_caller_supplied_is_their_beta(self):
        """An override is a statement, not a measurement. Adjusting it would
        quietly value a company on a number the user did not type."""
        d = derive_assumptions(make_fin(beta=2.0), overrides={"beta": 1.5})
        assert d.wacc_inputs["beta"].value == 1.5

    def test_an_unusable_beta_still_falls_back_to_the_market(self):
        d = derive_assumptions(make_fin(beta=99.0))
        assert d.wacc_inputs["beta"].value == pytest.approx(A.DEFAULT_BETA)
        assert d.wacc_inputs["beta"].source == "default"


# --- The premium ------------------------------------------------------

class TestTheEquityRiskPremium:
    def test_it_is_damodarans_published_figure(self):
        assert DAMODARAN_IMPLIED_ERP == pytest.approx(0.0441)
        assert DAMODARAN_ERP_AS_OF == date(2026, 11, 1)

    def test_it_is_recorded_as_published_rather_than_defaulted(self):
        """It is a figure someone computed and dated, not a number this
        model picked for want of one."""
        p = derive_assumptions(make_fin()).wacc_inputs["equity_risk_premium"]
        assert p.value == pytest.approx(DAMODARAN_IMPLIED_ERP)
        assert p.source == "published"
        assert "Damodaran" in p.detail
        assert "November 2026" in p.detail

    def test_a_fresh_premium_says_nothing_about_its_age(self):
        fresh = DAMODARAN_ERP_AS_OF + timedelta(days=10)
        assert not A.erp_is_stale(fresh)
        assert "due an update" not in A.erp_detail(fresh)

    def test_an_old_premium_says_so_and_where_to_look(self):
        old = DAMODARAN_ERP_AS_OF + timedelta(days=ERP_STALE_AFTER_DAYS + 1)
        assert A.erp_is_stale(old)
        detail = A.erp_detail(old)
        assert "due an update" in detail
        assert "adamodar" in detail, "it names where the new figure lives"

    def test_the_boundary_is_not_stale_until_it_is_passed(self):
        on_the_day = DAMODARAN_ERP_AS_OF + timedelta(days=ERP_STALE_AFTER_DAYS)
        assert not A.erp_is_stale(on_the_day)
        assert A.erp_is_stale(on_the_day + timedelta(days=1))

    def test_the_configured_figure_is_not_fetched_at_valuation_time(self):
        """Deliberately a constant. A valuation that changes because someone
        else's web page changed is not reproducible, and a scrape that fails
        would leave the model with no premium at all."""
        import inspect
        source = inspect.getsource(A._capm_inputs)
        for reaching_out in ("requests", "urlopen", "_browser_session", "http"):
            assert reaching_out not in source


# --- The duplicated trailing bar --------------------------------------

def _chart(bars):
    stamps = [t for t, _ in bars]
    closes = [v for _, v in bars]
    return {"chart": {"result": [{
        "timestamp": stamps,
        "indicators": {"adjclose": [{"adjclose": closes}]},
    }]}}


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def install(monkeypatch, bars):
    payload = _chart(bars)

    class Session:
        def get(self, *a, **k):
            return _FakeResponse(payload)

    monkeypatch.setattr(M, "_browser_session", lambda: Session())


def stamp(year, month, day=1, hour=4):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


class TestTheDuplicatedBar:
    def test_a_repeat_of_the_current_month_is_dropped(self):
        bars = [(stamp(2026, 9), 100.0), (stamp(2026, 10), 110.0),
                (stamp(2026, 11), 120.0),
                (stamp(2026, 11, 10, 22), 120.0)]   # the repeat
        monkeypatch = pytest.MonkeyPatch()
        try:
            install(monkeypatch, bars)
            closes = M._monthly_closes("TEST")
        finally:
            monkeypatch.undo()
        assert len(closes) == 3
        assert closes[-1][0] == stamp(2026, 11)

    def test_the_phantom_zero_return_month_never_reaches_the_series(self):
        bars = [(stamp(2026, 9), 100.0), (stamp(2026, 10), 110.0),
                (stamp(2026, 11), 120.0), (stamp(2026, 11, 10, 22), 120.0)]
        monkeypatch = pytest.MonkeyPatch()
        try:
            install(monkeypatch, bars)
            returns = M._returns(M._monthly_closes("TEST"))
        finally:
            monkeypatch.undo()
        assert 0.0 not in [round(r, 12) for r in returns.values()]
        assert len(returns) == 2

    def test_a_genuine_new_month_is_kept(self):
        """The rule is "same calendar month as the bar before it", not
        "drop the last bar" - the series must still grow each month."""
        bars = [(stamp(2026, 9), 100.0), (stamp(2026, 10), 110.0),
                (stamp(2026, 11), 120.0), (stamp(2026, 12), 130.0)]
        monkeypatch = pytest.MonkeyPatch()
        try:
            install(monkeypatch, bars)
            closes = M._monthly_closes("TEST")
        finally:
            monkeypatch.undo()
        assert len(closes) == 4

    def test_a_repeat_is_dropped_even_when_the_price_moved(self):
        """Yahoo stamps the repeat at the moment of the request, so late in
        a month it carries a different close from the month's own bar. It is
        still the same month twice."""
        bars = [(stamp(2026, 10), 110.0), (stamp(2026, 11), 120.0),
                (stamp(2026, 11, 28, 21), 126.0)]
        monkeypatch = pytest.MonkeyPatch()
        try:
            install(monkeypatch, bars)
            closes = M._monthly_closes("TEST")
        finally:
            monkeypatch.undo()
        assert len(closes) == 2
        assert closes[-1][1] == 120.0


# --- The invariant that the lower discount rate squeezes --------------

def test_terminal_growth_stays_below_wacc_by_its_full_margin():
    """A lower premium lowers WACC, which narrows the gap this ceiling
    lives in. The low-beta companies are where it bites: Exxon's WACC fell
    to 6.67%, so its terminal growth has a 5.17% ceiling.
    """
    for beta in (0.15, 0.25, 0.5, 1.0, 1.5, 2.5, 4.0):
        a = derive_assumptions(make_fin(beta=beta)).assumptions
        assert a.terminal_growth <= a.wacc - 0.015 + 1e-9, f"beta={beta}"


# --- The flag a client can act on -------------------------------------

class TestTheApiSaysWhenThePremiumIsOld:
    """The detail string carries the warning in prose, but a client should
    not have to parse prose to find out."""

    @pytest.fixture
    def client(self, monkeypatch):
        import analysis
        from fastapi.testclient import TestClient
        import api
        from test_api import fake_fetch_financials
        monkeypatch.setattr(analysis, "fetch_financials", fake_fetch_financials)
        monkeypatch.setattr(A, "fetch_risk_free_rate",
                            lambda tenor="year10": (0.045, "pinned for test"))
        api.reset_rate_limits()
        return TestClient(api.app)

    def _erp(self, body):
        return next(a for a in body["global_levers"] + body["assumptions"]
                    if a["name"] == "equity_risk_premium")

    def test_it_travels_with_its_publication_month(self, client):
        from test_api import PROFITABLE
        erp = self._erp(client.get(f"/valuation/{PROFITABLE}").json())
        assert erp["as_of"] == "November 2026"
        assert erp["is_stale"] is False

    def test_an_old_premium_is_flagged_as_data_not_only_as_prose(
            self, client, monkeypatch):
        import api
        from test_api import PROFITABLE
        monkeypatch.setattr(api, "erp_is_stale", lambda *a, **k: True)
        erp = self._erp(client.get(f"/valuation/{PROFITABLE}").json())
        assert erp["is_stale"] is True
        assert erp["as_of"] == "November 2026"

    def test_a_premium_the_caller_set_is_never_called_stale(
            self, client, monkeypatch):
        """Their number, their date. Telling someone the figure they just
        typed is out of date would be nonsense."""
        import api
        from test_api import PROFITABLE
        monkeypatch.setattr(api, "erp_is_stale", lambda *a, **k: True)
        body = client.post("/valuation",
                           json={"ticker": PROFITABLE,
                                 "overrides": {"equity_risk_premium": 0.06}}).json()
        erp = self._erp(body)
        assert erp["source"] == "override"
        assert erp["is_stale"] is False
