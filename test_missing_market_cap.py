"""What stands in for a market capitalisation when there is no price.

Three tiers, in order, because they are not equally good and the reader
should be told which one they got.

  1. The last price this app obtained, however old, dated and flagged. A
     real figure that was real on its date beats an empty field.
  2. Book common equity, when it is positive and a believable share of the
     capital structure. Worse than a market value, much better than
     abandoning the derivation.
  3. The generic WACC, with the serious warning it already had.

The valuation's own inputs are untouched throughout. What the market cap
decides is the WACC weights and the comparison with the share price.
"""
import pytest

import analysis as An
import assumptions as A
import market_data as M
import price_source as P
from market_data import CompanyFinancials


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(*, market_cap=0.0, price=None, book_equity=50_000e6,
             debt=50_000e6, last_known=False):
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 100_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "date": f"{2025 - i}-12-31",
            "revenue": revenue, "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "interestExpense": 2_000e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": debt,
            "commonStockEquity": book_equity,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    profile = {"companyName": "Test Co", "beta": 1.0, "marketCap": market_cap,
               "price": price, "sector": "Technology", "exchange": "NASDAQ",
               "priceIsLastKnown": last_known,
               "priceAsOfText": ("close as of 18 November 2026" if price
                                 else "no price available")}
    return CompanyFinancials(ticker="TEST", profile=profile, income=income,
                             balance=balance, cashflow=cashflow)


# --- Tier 1: the last price we ever got -----------------------------------

class TestTheLastKnownPrice:
    def test_the_cache_keeps_an_expired_close_to_fall_back_to(self):
        from datetime import date, datetime, timezone

        cache = P._ClosePriceCache()
        morning = datetime(2026, 11, 20, 14, tzinfo=timezone.utc)
        cache.put("AAPL", P.LatestClose("AAPL", 100.0, date(2026, 11, 20)),
                  now=morning)
        later = datetime(2026, 11, 25, 22, tzinfo=timezone.utc)
        assert cache.get("AAPL", now=later) is None, "it is not current"
        assert cache.last_known("AAPL") is not None, "and it is still there"

    def test_nothing_was_ever_cached_means_no_last_known(self):
        assert P._ClosePriceCache().last_known("AAPL") is None

    def test_the_fallback_is_used_and_flagged(self, monkeypatch):
        from datetime import date, datetime, timezone

        class Dead:
            name = "dead"

            def latest_close(self, symbol):
                raise P.PriceUnavailable("blocked")

            def monthly_closes(self, symbol):
                return []

            def dividend_events(self, symbol):
                return []

            def daily_close(self, symbol):
                return None

        P.clear_caches()
        morning = datetime(2026, 11, 20, 14, tzinfo=timezone.utc)
        P._close_cache.put("AAPL",
                           P.LatestClose("AAPL", 100.0, date(2026, 11, 20)),
                           now=morning)
        monkeypatch.setitem(P._SOURCES, "dead", Dead)
        monkeypatch.setenv(P.PRICE_SOURCE_VAR, "dead")
        P.reset_source()

        later = datetime(2026, 11, 25, 22, tzinfo=timezone.utc)
        got = P.latest_close("AAPL", now=later)
        assert got is not None and got.price == 100.0
        assert got.is_last_known is True
        assert got.as_of == date(2026, 11, 20), (
            "the date is the price's own, not today's")

    def test_a_current_price_is_never_flagged(self, monkeypatch):
        from datetime import date

        class Live:
            name = "live"

            def latest_close(self, symbol):
                return P.LatestClose("AAPL", 101.0, date(2026, 11, 25))

            def monthly_closes(self, symbol):
                return []

            def dividend_events(self, symbol):
                return []

            def daily_close(self, symbol):
                return None

        monkeypatch.setitem(P._SOURCES, "live", Live)
        monkeypatch.setenv(P.PRICE_SOURCE_VAR, "live")
        P.reset_source()
        assert P.latest_close("AAPL").is_last_known is False

    def test_a_last_known_price_still_produces_a_comparison(self):
        """It is a real price. Losing the comparison as well would be
        throwing away the figure for being a day old."""
        fin = make_fin(market_cap=400_000e6, price=400.0, last_known=True)
        base, _meta = M.base_year_from(fin)
        assert base.current_price == 400.0

    def test_the_provenance_says_it_is_not_current(self):
        fin = make_fin(market_cap=400_000e6, price=400.0, last_known=True)
        _base, meta = M.base_year_from(fin)
        assert "last known" in meta["price_source"]
        assert "18 November 2026" in meta["price_source"]

    def test_the_warning_is_caution_and_says_the_valuation_is_unaffected(self):
        class Derived:
            wacc_inputs: dict = {}
            capm_inputs: dict = {}
            provenance: dict = {}
            diagnostics: dict = {}

        got = {w.kind: w for w in An.input_source_warnings(
            Derived(), dcf=True, price_is_last_known=True)}
        assert "price_last_known" in got
        assert got["price_last_known"].severity == An.SEVERITY_CAUTION
        assert "unaffected" in got["price_last_known"].headline

    def test_a_current_price_raises_no_such_warning(self):
        class Derived:
            wacc_inputs: dict = {}
            capm_inputs: dict = {}
            provenance: dict = {}
            diagnostics: dict = {}

        got = {w.kind for w in An.input_source_warnings(
            Derived(), dcf=True, price_is_last_known=False)}
        assert "price_last_known" not in got


# --- Tier 3: the generic rate, loudly -------------------------------------

class TestTheGenericRate:
    def test_it_is_reached_when_no_price_was_ever_obtained(self):
        derived = A.derive_assumptions(make_fin(market_cap=0.0, price=None))
        prov = derived.provenance["wacc"]
        assert prov.source == "default"
        assert prov.value == A.DEFAULT_WACC

    def test_it_is_still_serious(self):
        derived = A.derive_assumptions(make_fin(market_cap=0.0, price=None))
        got = {w.kind: w for w in An.input_source_warnings(derived, dcf=True)}
        assert got["wacc_default"].severity == An.SEVERITY_SERIOUS

    def test_book_equity_is_not_used_as_a_stand_in(self):
        """Tried, measured, and rejected - see PARKED.md.

        On Apple the true market-weighted WACC was 9.38%, book-weighted
        6.75% and generic 8.50%, so the book weights were three times
        further from the truth than the generic rate they were meant to
        improve on. Years of buybacks leave book equity far below market
        value, and a threshold on book-against-capital cannot see that
        because the gap is to the MARKET value, which is the missing figure.
        """
        fin = make_fin(market_cap=0.0, price=None, book_equity=50_000e6)
        equity, _debt = A.capital_values(fin)
        assert equity == 0.0, "book equity was used as an equity weight"
        assert A.derive_assumptions(fin).provenance["wacc"].source == "default"

    def test_a_market_value_still_gives_a_derived_rate(self):
        """The guard rail: dropping the book tier must not have broken the
        ordinary path."""
        derived = A.derive_assumptions(make_fin(market_cap=400_000e6))
        assert derived.provenance["wacc"].source == "derived"


def test_the_last_known_flag_reaches_the_profile(monkeypatch):
    """Built by _profile_from, not by hand.

    Every other test here constructs the profile itself, so none of them
    exercised the one line that puts the flag there - and mutation testing
    found that by pinning it to False and surviving.
    """
    from datetime import date

    import price_source as PS

    stale = PS.LatestClose("TEST", 100.0, date(2026, 11, 18),
                           is_last_known=True)
    monkeypatch.setattr(PS, "latest_close", lambda symbol, **kw: stale)
    profile = M._profile_from({"symbol": "TEST"}, "TEST")
    assert profile["priceIsLastKnown"] is True
    assert profile["price"] == 100.0
    assert profile["priceAsOf"] == "2026-11-18"


def test_a_current_price_reaches_the_profile_unflagged(monkeypatch):
    from datetime import date

    import price_source as PS

    fresh = PS.LatestClose("TEST", 101.0, date(2026, 11, 21))
    monkeypatch.setattr(PS, "latest_close", lambda symbol, **kw: fresh)
    profile = M._profile_from({"symbol": "TEST"}, "TEST")
    assert profile["priceIsLastKnown"] is False


def test_the_crumb_free_profile_carries_the_flag_too(monkeypatch):
    """The path Render actually takes."""
    from datetime import date

    import price_source as PS

    stale = PS.LatestClose("TEST", 100.0, date(2026, 11, 18),
                           currency="USD", is_last_known=True)
    monkeypatch.setattr(PS, "latest_close", lambda symbol, **kw: stale)
    monkeypatch.setattr(M, "_search_exact", lambda t, s: {"symbol": "TEST"})
    monkeypatch.setattr(M, "_browser_session", lambda: None)
    monkeypatch.setattr(M, "fetch_reporting_currency", lambda t: "USD")
    profile = M.crumb_free_profile("TEST")
    assert profile is not None
    assert profile["priceIsLastKnown"] is True
