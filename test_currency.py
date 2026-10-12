"""
Currency conversion: the statements are restated into the currency the share
trades in, before anything reads them.

Three things are being protected.

That a company which reports and trades in one currency is untouched. Not
"close enough" - untouched, because the conversion never runs for it. That
is the whole no-regression story and it is asserted structurally rather than
numerically: a numeric comparison against live data cannot prove it, since
the same code run twice already differs in the eighth decimal as the market
capitalisation moves.

That a converted company is converted correctly, and in the right direction.
A rate applied the wrong way round is wrong by its own square and still
looks like a number - TSM was valued at $3,958 against a $435 price before
any of this existed.

And that a company whose currency cannot be established is refused. That
case used to proceed, which was tolerable only while nothing converted.

Run with:  python -m pytest test_currency.py -v
"""

import pytest

import market_data as M
from test_market_data import (AAPL_BALANCE, AAPL_CASHFLOW, AAPL_INCOME,
                              AAPL_INFO, FakeTicker, frame, install)


@pytest.fixture(autouse=True)
def _clear_caches():
    M.clear_caches()
    yield
    M.clear_caches()


@pytest.fixture(autouse=True)
def _no_live_calls(monkeypatch):
    """Nothing here may reach the network.

    The reporting-currency lookup and the FX fetch both make their own
    requests, and a test that silently fell through to the real endpoints
    would pass or fail with the weather.
    """
    monkeypatch.setattr(M, "fetch_reporting_currency",
                        lambda ticker: (_ for _ in ()).throw(
                            AssertionError("unexpected live currency lookup")))
    monkeypatch.setattr(M, "_fx_close",
                        lambda symbol: (_ for _ in ()).throw(
                            AssertionError("unexpected live FX call")))


def foreign_info(reporting="TWD", trading="USD", **overrides):
    info = dict(AAPL_INFO)
    info["financialCurrency"] = reporting
    info["currency"] = trading
    info.update(overrides)
    return info


def pin_fx(monkeypatch, rate, as_of="2026-10-12"):
    """Serve one rate, and its exact inverse, without touching the network."""
    def close(symbol):
        if symbol.endswith("=X") and len(symbol) == 8:
            frm, to = symbol[0:3], symbol[3:6]
            if (frm, to) == ("TWD", "USD"):
                return (rate, as_of)
            if (frm, to) == ("USD", "TWD"):
                return (1.0 / rate, as_of)
        return None
    monkeypatch.setattr(M, "_fx_close", close)


# ---------------------------------------------------------------------------
# A company that reports and trades in one currency is untouched
# ---------------------------------------------------------------------------

def test_a_usd_company_is_never_converted(monkeypatch):
    """The structural no-regression proof.

    convert_statements() is the only thing this feature does to a company's
    figures. If it is never called, nothing downstream can have moved.
    """
    called = []
    real = M.convert_statements
    monkeypatch.setattr(M, "convert_statements",
                        lambda *a, **k: called.append(a) or real(*a, **k))

    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("AAPL")

    assert called == [], "a USD company must not be put through conversion"
    assert fin.fx is None
    assert (fin.reporting_currency, fin.trading_currency) == ("USD", "USD")


def test_a_usd_company_keeps_every_figure_exactly(monkeypatch):
    """Not approximately: the same float objects' values, compared with ==."""
    install(monkeypatch, FakeTicker(
        info=AAPL_INFO, income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))
    fin = M.fetch_financials("AAPL")

    assert fin.income[0]["revenue"] == 416_161e6
    assert fin.balance[0]["longTermDebt"] == 78_328e6
    assert fin.balance[0]["cashAndCashEquivalents"] == 35_934e6
    assert fin.cashflow[0]["capitalExpenditure"] == -12_715e6
    # Share counts are counts, not money, in either currency.
    assert fin.income[0]["weightedAverageShsOutDil"] == 15_004.7e6


def test_the_identity_rate_is_exactly_one():
    """x * 1.0 == x, which is what makes the no-op exact rather than close."""
    rate = M.fetch_fx_rate("USD", "USD")
    assert rate["rate"] == 1.0
    assert rate["pair"] is None


# ---------------------------------------------------------------------------
# A foreign company is converted, in the right direction
# ---------------------------------------------------------------------------

def test_statements_are_restated_into_the_trading_currency(monkeypatch):
    pin_fx(monkeypatch, 0.03134403)
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("TSM")

    assert fin.fx["pair"] == "TWDUSD=X"
    assert fin.income[0]["revenue"] == pytest.approx(416_161e6 * 0.03134403)
    assert fin.balance[0]["longTermDebt"] == pytest.approx(78_328e6 * 0.03134403)
    assert fin.cashflow[0]["capitalExpenditure"] == pytest.approx(
        -12_715e6 * 0.03134403)


def test_the_rate_is_applied_the_right_way_round(monkeypatch):
    """TWD is worth far less than a dollar, so the figures must get smaller.

    Inverting the rate is the classic silent fault: it is wrong by the square
    of the rate and still produces a plausible-looking number.
    """
    pin_fx(monkeypatch, 0.03134403)
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("TSM")
    assert fin.income[0]["revenue"] < 416_161e6 / 10


def test_a_currency_worth_more_than_a_dollar_grows_the_figures(monkeypatch):
    """The direction check that the TWD case cannot make: GBP is worth more."""
    def close(symbol):
        return {"GBPUSD=X": (1.3245033, "2026-10-12"),
                "USDGBP=X": (1 / 1.3245033, "2026-10-12")}.get(symbol)
    monkeypatch.setattr(M, "_fx_close", close)
    install(monkeypatch, FakeTicker(
        info=foreign_info(reporting="GBP"), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("BTI")
    assert fin.income[0]["revenue"] == pytest.approx(416_161e6 * 1.3245033)
    assert fin.income[0]["revenue"] > 416_161e6


def test_share_counts_and_the_price_are_left_alone(monkeypatch):
    """They are already in the trading currency, or are not money at all."""
    pin_fx(monkeypatch, 0.03134403)
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("TSM")
    assert fin.income[0]["weightedAverageShsOutDil"] == 15_004.7e6
    assert fin.profile["price"] == AAPL_INFO["currentPrice"]
    assert fin.profile["marketCap"] == AAPL_INFO["marketCap"]


def test_the_rate_and_its_date_are_recorded_not_just_applied(monkeypatch):
    """The figure now moves with this rate, so it is part of the workings."""
    pin_fx(monkeypatch, 0.03134403)
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    _, meta = M.base_year_from(M.fetch_financials("TSM"))
    assert meta["reporting_currency"] == "TWD"
    assert meta["currency"] == "USD"
    assert meta["fx"]["pair"] == "TWDUSD=X"
    assert meta["fx"]["as_of"] == "2026-10-12"
    assert "TWD" in meta["currency_source"] and "USD" in meta["currency_source"]


# ---------------------------------------------------------------------------
# The crumb-free reporting-currency lookup
#
# These exercise the function's own body rather than stubbing it out. The
# other tests here replace it wholesale, which is right for them and was
# wrong as the only coverage: a typo inside it - a timeout constant that did
# not exist - was swallowed by its own exception handler and surfaced as
# "the data provider did not say which currency TSM reports in". The suite
# was entirely green while production refused every foreign listing.
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def serve(monkeypatch, response, record=None):
    """Answer the timeseries request without a network."""
    class Session:
        def get(self, url, params=None, timeout=None):
            if record is not None:
                record.append({"url": url, "params": params, "timeout": timeout})
            return response
    monkeypatch.setattr(M, "_browser_session", lambda: Session())


# A real response, trimmed: this is the shape Yahoo actually returns.
TSM_TIMESERIES = (
    '{"timeseries":{"result":[{"meta":{"symbol":["TSM"],'
    '"type":["annualTotalRevenue"]},"timestamp":[1767139200],'
    '"annualTotalRevenue":[{"dataId":20100,"asOfDate":"2025-12-31",'
    '"periodType":"12M","currencyCode":"TWD",'
    '"reportedValue":{"raw":3809054300000.0,"fmt":"3.81T"}}]}]}}'
)


def test_the_reporting_currency_is_read_from_the_statements(monkeypatch):
    monkeypatch.undo()
    serve(monkeypatch, FakeResponse(TSM_TIMESERIES))
    assert M.fetch_reporting_currency("TSM") == "TWD"


def test_the_lookup_actually_issues_a_request(monkeypatch):
    """Guards the call itself, which is where the typo was."""
    monkeypatch.undo()
    calls = []
    serve(monkeypatch, FakeResponse(TSM_TIMESERIES), record=calls)

    assert M.fetch_reporting_currency("TSM") == "TWD"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"].endswith("/TSM")
    assert call["params"]["type"] == "annualTotalRevenue"
    assert isinstance(call["timeout"], (int, float)) and call["timeout"] > 0


@pytest.mark.parametrize("body, why", [
    ('{"timeseries":{"result":[{"meta":{}}]}}', "no rows, so no currency"),
    ('{"timeseries":{"result":[{"a":[{"currencyCode":"TWD"}],'
     '"b":[{"currencyCode":"USD"}]}]}}', "restated: two currencies, so a guess"),
])
def test_an_unusable_response_yields_nothing(monkeypatch, body, why):
    monkeypatch.undo()
    serve(monkeypatch, FakeResponse(body))
    assert M.fetch_reporting_currency("TSM") is None, why


def test_a_non_200_yields_nothing(monkeypatch):
    monkeypatch.undo()
    serve(monkeypatch, FakeResponse(TSM_TIMESERIES, status_code=429))
    assert M.fetch_reporting_currency("TSM") is None


def test_a_transport_failure_is_logged_not_silently_swallowed(monkeypatch, caplog):
    monkeypatch.undo()

    class Exploding:
        def get(self, *a, **k):
            raise RuntimeError("connection reset")
    monkeypatch.setattr(M, "_browser_session", lambda: Exploding())

    with caplog.at_level("WARNING"):
        assert M.fetch_reporting_currency("TSM") is None
    assert "connection reset" in caplog.text


# ---------------------------------------------------------------------------
# What cannot be established is refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["currency", "financialCurrency"])
def test_an_unstated_currency_is_refused(monkeypatch, missing):
    info = {k: v for k, v in AAPL_INFO.items() if k != missing}
    monkeypatch.setattr(M, "fetch_reporting_currency", lambda ticker: None)
    install(monkeypatch, FakeTicker(
        info=info, income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    with pytest.raises(M.UnsupportedListingError, match="did not say which currency"):
        M.fetch_financials("X")


def test_a_missing_rate_refuses_rather_than_valuing_unconverted(monkeypatch):
    """The failure this whole feature exists to avoid."""
    monkeypatch.setattr(M, "_fx_close", lambda symbol: None)
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    with pytest.raises(M.UnsupportedListingError, match="exchange rate could not"):
        M.fetch_financials("TSM")


def test_a_rate_that_disagrees_with_its_inverse_is_refused(monkeypatch):
    """The only independent check available on a number that scales everything."""
    def close(symbol):
        return {"TWDUSD=X": (0.03134403, "2026-10-12"),
                # Implies 0.0303, about 3% away - far outside quoting noise.
                "USDTWD=X": (33.0, "2026-10-12")}.get(symbol)
    monkeypatch.setattr(M, "_fx_close", close)
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    with pytest.raises(M.UnsupportedListingError, match="exchange rate could not"):
        M.fetch_financials("TSM")


def test_a_quiet_inverse_does_not_block_a_good_rate(monkeypatch):
    """The cross-check is a veto on disagreement, not a second requirement."""
    monkeypatch.setattr(M, "_fx_close", lambda symbol: (
        (0.03134403, "2026-10-12") if symbol == "TWDUSD=X" else None))
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    fin = M.fetch_financials("TSM")
    assert fin.fx["cross_check"] is None
    assert fin.income[0]["revenue"] == pytest.approx(416_161e6 * 0.03134403)


def test_a_non_positive_rate_is_refused(monkeypatch):
    monkeypatch.setattr(M, "_fx_close", lambda symbol: (0.0, "2026-10-12"))
    install(monkeypatch, FakeTicker(
        info=foreign_info(), income=frame(AAPL_INCOME),
        balance=frame(AAPL_BALANCE), cashflow=frame(AAPL_CASHFLOW)))

    with pytest.raises(M.UnsupportedListingError, match="exchange rate could not"):
        M.fetch_financials("TSM")
