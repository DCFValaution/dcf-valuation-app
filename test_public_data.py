"""The two inputs that now come from the institutions that publish them.

Every test here fakes the HTTP layer. The point is not that Treasury and the
ECB are up - the live suite covers that - but that the right number comes out
of the bytes they send, that a cross-rate is labelled as computed rather than
published, and that each source falls through to the aggregator rather than
failing a valuation.
"""

from datetime import date

import pytest

import market_data as M
import public_data as P


# ---------------------------------------------------------------------------
# Fixtures: the bytes each source actually sends
# ---------------------------------------------------------------------------

TREASURY_CSV = (
    'Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr",'
    '"3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
    "11/13/2026,4.10,4.18,4.26,4.32,4.34,4.42,4.65,4.88,4.92,4.98,5.05,5.15,5.52,5.47\n"
    "11/12/2026,4.12,4.18,4.26,4.32,4.35,4.42,4.65,4.86,4.90,4.95,5.02,5.10,5.48,5.43\n"
    "11/10/2026,4.05,4.16,4.21,4.30,4.28,4.38,4.56,4.73,4.74,4.81,4.89,4.98,5.39,5.35\n"
)

ECB_XML = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<gesmes:Envelope><Cube><Cube time='2026-11-13'>"
    "<Cube currency='USD' rate='1.1052'/>"
    "<Cube currency='JPY' rate='176.83'/>"
    "<Cube currency='DKK' rate='7.4756'/>"
    "<Cube currency='GBP' rate='0.83559'/>"
    "<Cube currency='CNY' rate='7.3738'/>"
    "</Cube></Cube></gesmes:Envelope>"
)


@pytest.fixture
def served(monkeypatch):
    """Serve a canned body per URL fragment; record what was asked for."""
    asked: list[str] = []
    bodies: dict[str, str | None] = {}

    def fake_get(url, *, timeout=20):
        asked.append(url)
        for fragment, body in bodies.items():
            if fragment in url:
                return body
        return None

    monkeypatch.setattr(P, "_get", fake_get)
    return bodies, asked


# ---------------------------------------------------------------------------
# Treasury
# ---------------------------------------------------------------------------

def test_treasury_reads_the_newest_row_for_the_right_column(served):
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = TREASURY_CSV
    rate, source = P.treasury_par_yield("year10")
    assert rate == pytest.approx(0.0515)
    assert "10-year" in source
    assert "2026-11-13" in source
    assert "Par Yield Curve" in source


@pytest.mark.parametrize("tenor,expected", [
    ("month3", 0.0432),
    ("year5", 0.0498),
    ("year10", 0.0515),
    ("year30", 0.0547),
])
def test_treasury_each_tenor_maps_to_its_own_column(served, tenor, expected):
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = TREASURY_CSV
    rate, _ = P.treasury_par_yield(tenor)
    assert rate == pytest.approx(expected)


def test_treasury_picks_the_latest_date_not_the_first_row(served):
    """The newest row is sorted for, not assumed to be first."""
    bodies, _ = served
    header, newest, middle, oldest = TREASURY_CSV.strip().split("\n")
    bodies["daily-treasury-rates.csv"] = "\n".join(
        [header, oldest, newest, middle]) + "\n"
    rate, source = P.treasury_par_yield("year10")
    assert rate == pytest.approx(0.0515)
    assert "2026-11-13" in source


def test_treasury_skips_a_blank_cell_and_uses_the_next_day(served):
    """The 30-year point was simply not published for four years."""
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = (
        'Date,"10 Yr","30 Yr"\n'
        "11/13/2026,5.15,\n"
        "11/12/2026,5.10,5.43\n"
    )
    assert P.treasury_par_yield("year10")[0] == pytest.approx(0.0515)
    rate, source = P.treasury_par_yield("year30")
    assert rate == pytest.approx(0.0543)
    assert "2026-11-12" in source


def test_treasury_falls_back_to_last_year_in_early_january(served):
    """On 2 January the new year's file exists and is empty."""
    bodies, asked = served
    bodies["field_tdr_date_value=2026"] = 'Date,"10 Yr"\n'
    bodies["field_tdr_date_value=2025"] = 'Date,"10 Yr"\n12/31/2025,4.57\n'
    rate, source = P.treasury_par_yield("year10", today=date(2026, 1, 2))
    assert rate == pytest.approx(0.0457)
    assert "2025-12-31" in source
    assert len(asked) == 2


def test_treasury_rejects_an_absurd_yield(served):
    """A parse error must not become a 90% discount rate."""
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = 'Date,"10 Yr"\n11/13/2026,9000\n'
    assert P.treasury_par_yield("year10") is None


def test_treasury_rejects_a_zero_yield(served):
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = 'Date,"10 Yr"\n11/13/2026,0.0\n'
    assert P.treasury_par_yield("year10") is None


def test_treasury_unknown_tenor_asks_for_nothing(served):
    _, asked = served
    assert P.treasury_par_yield("year7") is None
    assert asked == []


def test_treasury_missing_column_is_not_a_crash(served):
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = 'Date,"1 Mo"\n11/13/2026,4.10\n'
    assert P.treasury_par_yield("year10") is None


def test_treasury_unparseable_date_is_skipped(served):
    bodies, _ = served
    bodies["daily-treasury-rates.csv"] = (
        'Date,"10 Yr"\nnot-a-date,5.99\n11/12/2026,5.10\n')
    rate, source = P.treasury_par_yield("year10")
    assert rate == pytest.approx(0.0510)
    assert "2026-11-12" in source


def test_treasury_no_response_is_none(served):
    served  # nothing registered, so every URL returns None
    assert P.treasury_par_yield("year10") is None


# ---------------------------------------------------------------------------
# ECB
# ---------------------------------------------------------------------------

def test_ecb_table_includes_the_euro_at_one(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    rates, as_of = P.ecb_reference_rates()
    assert rates["EUR"] == 1.0
    assert rates["USD"] == pytest.approx(1.1052)
    assert as_of == "2026-11-13"


def test_ecb_euro_pair_is_the_published_quote(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    out = P.ecb_fx_rate("EUR", "USD")
    assert out["rate"] == pytest.approx(1.1052)
    assert out["pair"] == "EUR/USD"
    assert out["as_of"] == "2026-11-13"
    assert out["source"] == "ecb"
    assert out["basis"].startswith("published")
    assert "cross-rate" not in out["basis"]


def test_ecb_inverse_euro_pair_inverts_the_quote(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    out = P.ecb_fx_rate("USD", "EUR")
    assert out["rate"] == pytest.approx(1 / 1.1052)


def test_ecb_cross_rate_divides_the_two_quotes(served):
    """DKK->USD is USD-per-euro over DKK-per-euro, and nothing else."""
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    out = P.ecb_fx_rate("DKK", "USD")
    assert out["rate"] == pytest.approx(1.1052 / 7.4756)
    # The arithmetic the other way round would give 6.76 instead of 0.148,
    # which is why this asserts the value and not just that it is positive.
    assert out["rate"] == pytest.approx(0.1478409, abs=1e-6)


def test_ecb_cross_rate_says_it_was_computed(served):
    """The ECB's licence requires a modification to be stated explicitly."""
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    out = P.ecb_fx_rate("DKK", "USD")
    assert "cross-rate" in out["basis"]
    assert "EUR/DKK" in out["basis"] and "EUR/USD" in out["basis"]
    assert "European Central Bank" in out["basis"]


def test_ecb_cross_rate_round_trips(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    there = P.ecb_fx_rate("JPY", "GBP")["rate"]
    back = P.ecb_fx_rate("GBP", "JPY")["rate"]
    assert there * back == pytest.approx(1.0)


def test_ecb_never_claims_a_cross_check(served):
    """One table cannot disagree with itself; a drift of zero would lie."""
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    assert P.ecb_fx_rate("DKK", "USD")["cross_check"] is None


def test_ecb_unquoted_currency_is_none(served):
    """The New Taiwan dollar is not one of the twenty-nine."""
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    assert P.ecb_fx_rate("TWD", "USD") is None
    assert P.ecb_fx_rate("USD", "TWD") is None


def test_ecb_empty_feed_is_rejected_not_read_as_euro_only(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = (
        "<gesmes:Envelope><Cube><Cube time='2026-11-13'>"
        "</Cube></Cube></gesmes:Envelope>")
    assert P.ecb_reference_rates() is None


def test_ecb_feed_without_a_date_is_rejected(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = (
        "<gesmes:Envelope><Cube>"
        "<Cube currency='USD' rate='1.1052'/>"
        "</Cube></gesmes:Envelope>")
    assert P.ecb_reference_rates() is None


def test_ecb_handles_double_quoted_attributes(served):
    """The 90-day file uses double quotes where the daily file uses single."""
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = (
        '<gesmes:Envelope><Cube><Cube time="2026-11-13">'
        '<Cube currency="USD" rate="1.1052"/>'
        '</Cube></Cube></gesmes:Envelope>')
    rates, as_of = P.ecb_reference_rates()
    assert rates["USD"] == pytest.approx(1.1052)
    assert as_of == "2026-11-13"


def test_ecb_no_response_is_none(served):
    served
    assert P.ecb_fx_rate("EUR", "USD") is None


def test_ecb_blank_currency_is_none(served):
    bodies, _ = served
    bodies["eurofxref-daily.xml"] = ECB_XML
    assert P.ecb_fx_rate("", "USD") is None
    assert P.ecb_fx_rate("EUR", None) is None


# ---------------------------------------------------------------------------
# Who we say we are
# ---------------------------------------------------------------------------

def test_user_agent_names_the_project(monkeypatch):
    monkeypatch.delenv(P.PUBLIC_DATA_CONTACT_VAR, raising=False)
    agent = P.public_user_agent()
    assert "dcf-valuation-app" in agent
    assert P.public_data_contact() == ""


def test_user_agent_carries_a_configured_contact(monkeypatch):
    monkeypatch.setenv(P.PUBLIC_DATA_CONTACT_VAR, "ops@example.com")
    assert "ops@example.com" in P.public_user_agent()


def test_user_agent_ignores_a_blank_contact(monkeypatch):
    monkeypatch.setenv(P.PUBLIC_DATA_CONTACT_VAR, "   ")
    assert "(" in P.public_user_agent()  # the project URL's own parenthesis
    assert P.public_user_agent().endswith(")")
    assert P.public_data_contact() == ""


def test_the_declared_agent_is_sent(monkeypatch):
    """A header built but never attached would be worse than none."""
    seen = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(P, "_plain_session", FakeSession)
    assert P._get("https://example.test/x") == "ok"
    assert seen["headers"]["User-Agent"] == P.public_user_agent()


def test_a_non_200_is_not_treated_as_a_body(monkeypatch):
    class FakeResponse:
        status_code = 429
        text = "slow down"

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(P, "_plain_session", FakeSession)
    assert P._get("https://example.test/x") is None


# ---------------------------------------------------------------------------
# The fall-through, from market_data's side
# ---------------------------------------------------------------------------

def test_risk_free_rate_prefers_treasury(monkeypatch):
    M.clear_caches()
    monkeypatch.setattr(
        M, "treasury_par_yield",
        lambda tenor, **kw: (0.0515, "US Treasury 10-year par yield as of X"))
    monkeypatch.setattr(M, "_quoted_treasury_yield",
                        lambda tenor: (0.9, "should not be used"))
    rate, source = M.fetch_risk_free_rate("year10")
    assert rate == pytest.approx(0.0515)
    assert "par yield" in source


def test_risk_free_rate_falls_back_to_the_quote(monkeypatch):
    M.clear_caches()
    monkeypatch.setattr(M, "treasury_par_yield", lambda tenor, **kw: None)
    monkeypatch.setattr(
        M, "_quoted_treasury_yield",
        lambda tenor: (0.0484, "US Treasury 10-year (^TNX) as of X"))
    rate, source = M.fetch_risk_free_rate("year10")
    assert rate == pytest.approx(0.0484)
    assert "^TNX" in source


def test_risk_free_rate_is_none_when_neither_answers(monkeypatch):
    M.clear_caches()
    monkeypatch.setattr(M, "treasury_par_yield", lambda tenor, **kw: None)
    monkeypatch.setattr(M, "_quoted_treasury_yield", lambda tenor: None)
    assert M.fetch_risk_free_rate("year10") is None


def test_risk_free_rate_unknown_tenor_asks_neither(monkeypatch):
    M.clear_caches()
    calls = []
    monkeypatch.setattr(M, "treasury_par_yield",
                        lambda tenor, **kw: calls.append(tenor))
    monkeypatch.setattr(M, "_quoted_treasury_yield",
                        lambda tenor: calls.append(tenor))
    assert M.fetch_risk_free_rate("year7") is None
    assert calls == []


def test_fx_prefers_the_ecb(monkeypatch):
    M.clear_caches()
    monkeypatch.setattr(M, "ecb_fx_rate", lambda frm, to: {
        "rate": 0.1478, "pair": "DKK/USD", "as_of": "2026-11-13",
        "cross_check": None, "source": "ecb", "basis": "cross-rate ..."})
    monkeypatch.setattr(M, "_quoted_fx_rate",
                        lambda frm, to: pytest.fail("the quote was used"))
    out = M.fetch_fx_rate("DKK", "USD")
    assert out["source"] == "ecb"
    assert out["rate"] == pytest.approx(0.1478)


def test_fx_falls_back_for_a_currency_the_ecb_does_not_quote(monkeypatch):
    M.clear_caches()
    monkeypatch.setattr(M, "ecb_fx_rate", lambda frm, to: None)
    monkeypatch.setattr(M, "_quoted_fx_rate", lambda frm, to: {
        "rate": 0.0304, "pair": f"{frm}{to}=X", "as_of": "2026-11-16",
        "cross_check": 0.0, "source": "quote", "basis": "market quote"})
    out = M.fetch_fx_rate("TWD", "USD")
    assert out["source"] == "quote"
    assert out["pair"] == "TWDUSD=X"


def test_fx_same_currency_is_still_exactly_one(monkeypatch):
    M.clear_caches()
    monkeypatch.setattr(M, "ecb_fx_rate",
                        lambda frm, to: pytest.fail("no lookup needed"))
    assert M.fetch_fx_rate("USD", "USD")["rate"] == 1.0


def test_fx_caches_the_ecb_answer(monkeypatch):
    M.clear_caches()
    calls = []

    def once(frm, to):
        calls.append((frm, to))
        return {"rate": 1.1052, "pair": "EUR/USD", "as_of": "2026-11-13",
                "cross_check": None, "source": "ecb", "basis": "published"}

    monkeypatch.setattr(M, "ecb_fx_rate", once)
    M.fetch_fx_rate("EUR", "USD")
    M.fetch_fx_rate("EUR", "USD")
    assert calls == [("EUR", "USD")]
