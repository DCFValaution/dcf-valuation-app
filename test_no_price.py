"""A valuation with no share price.

The price is not an input to any figure the model produces. It decides what
the answer is compared with, and nothing else. So when the price source will
not give us one, the valuation still happens and the comparison is dropped -
which used to be a refusal of a company whose statements had all arrived.

Everything here was written after mutation testing found four escapes: the
missing-price warning, its tier, the null upside, and the crumb-free
profile's survival were all untested. Each has a test now because each was
a live behaviour nothing was holding in place.
"""
import pytest

import analysis as An
import assumptions as A
import market_data as M
import price_source as P
from dcf import BaseYearData, run_dcf
from market_data import CompanyFinancials


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def base(price):
    return BaseYearData(
        revenue=10_000.0, total_debt=1_000.0, cash=500.0, shares=1_000.0,
        current_price=price, minority_interest=0.0)


def assumptions():
    return A.Assumptions(
        revenue_growth=0.05, operating_margin=0.25, tax_rate=0.21,
        da_pct=0.05, capex_pct=0.04, nwc_pct=0.02, wacc=0.09,
        terminal_growth=0.025, projection_years=5)


# --- The engine -----------------------------------------------------------

class TestTheEngine:
    def test_a_price_gives_a_comparison(self):
        result = run_dcf(base(100.0), assumptions())
        assert result.current_price == 100.0
        assert result.upside_downside is not None

    def test_no_price_gives_no_comparison_rather_than_a_zero(self):
        """A zero would read as a measurement - "this is worth exactly what
        it costs" - which is a statement, and the opposite of silence."""
        result = run_dcf(base(None), assumptions())
        assert result.current_price is None
        assert result.upside_downside is None, (
            "a missing price was reported as 0% upside")

    def test_the_valuation_itself_is_identical_either_way(self):
        """The whole claim behind showing it at all."""
        priced = run_dcf(base(100.0), assumptions())
        unpriced = run_dcf(base(None), assumptions())
        assert unpriced.intrinsic_value_per_share == pytest.approx(
            priced.intrinsic_value_per_share)
        assert unpriced.enterprise_value == pytest.approx(
            priced.enterprise_value)

    def test_a_zero_price_is_treated_as_no_price(self):
        """Nothing trades at zero, so a zero is an absence wearing a
        number - and dividing by it would raise."""
        assert run_dcf(base(0.0), assumptions()).upside_downside is None


# --- What the reader is told ----------------------------------------------

class TestTheWarning:
    def _kinds(self, price_unavailable):
        class Derived:
            wacc_inputs = {
                "beta": A.Provenance("beta", 1.0, "derived", "measured"),
                "risk_free_rate": A.Provenance("risk_free_rate", 0.045,
                                               "derived", "live"),
                "cost_of_debt": A.Provenance("cost_of_debt", 0.04,
                                             "derived", "measured"),
                "equity_risk_premium": A.Provenance(
                    "equity_risk_premium", 0.0441, "published", "Damodaran"),
            }
            capm_inputs: dict = {}
            provenance = {"wacc": A.Provenance("wacc", 0.09, "derived", "ok")}
            diagnostics: dict = {}

        return {w.kind: w for w in An.input_source_warnings(
            Derived(), dcf=True, price_unavailable=price_unavailable)}

    def test_a_missing_price_is_said(self):
        got = self._kinds(True)
        assert "price_unavailable" in got, (
            "the price was missing and nothing was said about it")

    def test_a_present_price_says_nothing(self):
        assert "price_unavailable" not in self._kinds(False)

    def test_it_is_caution_not_info(self):
        """Info is for something that qualifies a figure. This removes the
        comparison the reader came for, which is louder than that - and
        quieter than serious, because no figure is wrong."""
        got = self._kinds(True)["price_unavailable"]
        assert got.severity == An.SEVERITY_CAUTION
        assert got.severity != An.SEVERITY_INFO
        assert got.severity != An.SEVERITY_SERIOUS

    def test_it_says_the_valuation_is_unaffected(self):
        """Otherwise a reader reasonably assumes the figure is damaged."""
        got = self._kinds(True)["price_unavailable"]
        assert "unaffected" in got.headline or "unaffected" in str(got)

    def test_it_reaches_a_real_valuation(self, monkeypatch):
        """End to end, not just the warning builder."""
        fin = _fin(price=None)
        monkeypatch.setattr(An, "fetch_financials", lambda t, **k: fin)
        report = An.value_company("TEST")
        assert report.suitable
        assert report.result.upside_downside is None
        kinds = {w.kind for w in report.suitability.warnings}
        assert "price_unavailable" in kinds


# --- The crumb-free profile -----------------------------------------------

def _chart_payload():
    return {"chart": {"result": [{"meta": {
        "symbol": "TEST", "longName": "Test Co",
        "regularMarketPrice": 100.0, "currency": "USD",
        "exchangeName": "NYQ", "regularMarketTime": 1763600000}}]}}


class TestTheCrumbFreeProfile:
    """Reached when Yahoo will not authenticate its quote endpoint, which on
    Render is almost every request. It used to return None without a price,
    refusing the company outright."""

    def _session(self, monkeypatch, *, chart_ok=True):
        class Response:
            def __init__(self, payload, status=200):
                self._payload = payload
                self.status_code = status

            def json(self):
                return self._payload

        class Session:
            @staticmethod
            def get(url, **kwargs):
                if "search" in url:
                    return Response({"quotes": [
                        {"symbol": "TEST", "shortname": "Test Co",
                         "quoteType": "EQUITY", "sector": "Technology",
                         "industry": "Software"}]})
                if not chart_ok:
                    return Response({}, status=503)
                return Response(_chart_payload())

        monkeypatch.setattr(M, "_browser_session", lambda: Session())
        monkeypatch.setattr(M, "fetch_reporting_currency", lambda t: "USD")

    def test_with_a_price_it_carries_one(self, monkeypatch):
        self._session(monkeypatch)
        profile = M.crumb_free_profile("TEST")
        assert profile is not None
        assert profile["price"] == 100.0
        assert profile["priceAsOf"]
        assert "no price" not in profile["profileSource"]

    def test_without_a_price_it_still_describes_the_company(self, monkeypatch):
        self._session(monkeypatch, chart_ok=False)
        profile = M.crumb_free_profile("TEST")
        assert profile is not None, (
            "the company was refused over a missing price")
        assert profile["price"] is None
        assert profile["sector"] == "Technology", (
            "the sector decides the method, and it came from search")

    def test_it_says_there_is_no_price_and_no_conversion(self, monkeypatch):
        self._session(monkeypatch, chart_ok=False)
        profile = M.crumb_free_profile("TEST")
        assert "no price available" in profile["profileSource"]
        assert "no currency conversion" in profile["profileSource"]
        assert profile["priceAsOfText"] == "no price available"

    def test_no_unverified_exchange_rate_is_applied(self, monkeypatch):
        """The trading currency comes off the same request as the price, so
        losing one loses the other. Using the reporting currency for both
        applies no conversion, which is the conservative choice: an
        unverified rate is worse than none."""
        self._session(monkeypatch, chart_ok=False)
        profile = M.crumb_free_profile("TEST")
        assert profile["currency"] == profile["financialCurrency"] == "USD"

    def test_nothing_at_all_is_still_a_refusal(self, monkeypatch):
        """No price AND no name is not a company with a missing price. It
        is a company that is not there, and inventing a profile for it
        would be worse than saying so."""
        class Session:
            @staticmethod
            def get(url, **kwargs):
                raise RuntimeError("blocked")

        monkeypatch.setattr(M, "_browser_session", lambda: Session())
        monkeypatch.setattr(M, "fetch_reporting_currency", lambda t: None)
        assert M.crumb_free_profile("TEST") is None


# --- The base year --------------------------------------------------------

def _fin(price):
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 10_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "revenue": revenue, "date": "2025-12-31",
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
        profile={"companyName": "Test Co", "beta": 1.0,
                 "marketCap": 1_000_000e6 if price else 0,
                 "price": price, "sector": "Technology", "exchange": "NASDAQ",
                 "priceAsOfText": ("close as of 20 November 2026" if price
                                   else "no price available")},
        income=income, balance=balance, cashflow=cashflow)


class TestTheBaseYear:
    def test_a_missing_price_does_not_refuse_the_company(self):
        got, meta = M.base_year_from(_fin(price=None))
        assert got.current_price is None
        assert got.revenue > 0
        assert got.shares > 0, "the share count never needed the price"

    def test_the_provenance_does_not_claim_a_price(self):
        _, meta = M.base_year_from(_fin(price=None))
        assert meta["price_source"] == "no share price available"

    def test_the_provenance_dates_a_price_it_does_have(self):
        _, meta = M.base_year_from(_fin(price=100.0))
        assert "20 November 2026" in meta["price_source"]
        assert "live" not in meta["price_source"], (
            'this field used to read "quote (live)" unconditionally'
        )
