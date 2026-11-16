"""The stored risk-free rate, and the caution that now travels with it.

Why this file exists. The stored fallback drifted ninety basis points below
the curve and nothing noticed, because a valuation built on it was
indistinguishable from one built on the live curve: same shape of output,
same confidence, a figure up to eighteen per cent higher. It was found by
accident, in a comparison harness, from a number that could not be right.

So: the constant carries a date, the date can go stale, and a valuation
that rests on it says so at the same volume as the other things that change
the answer.
"""

from datetime import date

import pytest

import analysis as A
import assumptions as Asm
from analysis import SEVERITY_CAUTION, SEVERITY_INFO, severity_of


# ---------------------------------------------------------------------------
# The constant and its date
# ---------------------------------------------------------------------------

def test_the_stored_rate_has_a_date():
    """The whole point. A constant with no date cannot be seen to be old."""
    assert isinstance(Asm.FALLBACK_RISK_FREE_AS_OF, date)


def test_the_stored_rate_is_a_plausible_treasury_yield():
    assert 0.0 < Asm.FALLBACK_RISK_FREE_RATE < 0.25


def test_the_stored_rate_is_not_the_one_that_drifted():
    """4.25% was 90bp below the curve and worth up to +18% on the figure."""
    assert Asm.FALLBACK_RISK_FREE_RATE != 0.0425


def test_the_date_reads_without_a_leading_zero():
    """A sentence says "3 January", not "03 January"."""
    Asm.FALLBACK_RISK_FREE_AS_OF = date(2026, 1, 3)
    try:
        assert Asm.risk_free_as_of_text() == "3 January 2026"
    finally:
        Asm.FALLBACK_RISK_FREE_AS_OF = date(2026, 11, 13)


def test_age_is_measured_from_the_stored_date():
    assert Asm.risk_free_age_days(
        Asm.FALLBACK_RISK_FREE_AS_OF) == 0
    assert Asm.risk_free_age_days(
        date(2026, 11, 13) + __import__("datetime").timedelta(days=45)) == 45


def test_a_fresh_rate_is_not_stale():
    assert not Asm.risk_free_is_stale(Asm.FALLBACK_RISK_FREE_AS_OF)


def test_a_rate_a_day_inside_the_window_is_not_stale():
    from datetime import timedelta
    when = Asm.FALLBACK_RISK_FREE_AS_OF + timedelta(
        days=Asm.RISK_FREE_STALE_AFTER_DAYS)
    assert not Asm.risk_free_is_stale(when)


def test_a_rate_a_day_past_the_window_is_stale():
    from datetime import timedelta
    when = Asm.FALLBACK_RISK_FREE_AS_OF + timedelta(
        days=Asm.RISK_FREE_STALE_AFTER_DAYS + 1)
    assert Asm.risk_free_is_stale(when)


def test_the_window_is_short_enough_to_matter():
    """A year-long window would make the flag decorative."""
    assert Asm.RISK_FREE_STALE_AFTER_DAYS <= 92


# ---------------------------------------------------------------------------
# The provenance line
# ---------------------------------------------------------------------------

def test_the_detail_names_the_rate_and_its_date():
    text = Asm.fallback_risk_free_detail(Asm.FALLBACK_RISK_FREE_AS_OF)
    assert f"{Asm.FALLBACK_RISK_FREE_RATE:.2%}" in text
    assert Asm.risk_free_as_of_text() in text
    assert "unavailable" in text


def test_the_detail_says_so_once_the_rate_is_old():
    from datetime import timedelta
    when = Asm.FALLBACK_RISK_FREE_AS_OF + timedelta(days=200)
    text = Asm.fallback_risk_free_detail(when)
    assert "200 days old" in text
    assert "no longer a reliable stand-in" in text


def test_the_detail_is_quiet_while_the_rate_is_fresh():
    text = Asm.fallback_risk_free_detail(Asm.FALLBACK_RISK_FREE_AS_OF)
    assert "days old" not in text


# ---------------------------------------------------------------------------
# The warning
# ---------------------------------------------------------------------------

class _Prov:
    def __init__(self, value, source):
        self.value, self.source = value, source


def test_no_warning_when_the_rate_came_from_the_curve():
    got = A.stored_rate_warning({"risk_free_rate": _Prov(0.0515, "derived")})
    assert got is None


def test_no_warning_when_the_reader_set_the_rate_themselves():
    got = A.stored_rate_warning({"risk_free_rate": _Prov(0.04, "override")})
    assert got is None


def test_no_warning_when_there_is_no_rate_at_all():
    assert A.stored_rate_warning({}) is None
    assert A.stored_rate_warning(None) is None


def test_the_stored_rate_is_a_caution_not_an_aside():
    """Louder than the quiet list of defaults, because it moves the answer."""
    got = A.stored_rate_warning({"risk_free_rate": _Prov(0.0515, "default")})
    assert got is not None
    assert severity_of(got) == SEVERITY_CAUTION
    assert severity_of(got) != SEVERITY_INFO
    assert got.kind == "risk_free_fallback"


def test_the_headline_says_what_happened_and_when_the_rate_is_from():
    got = A.stored_rate_warning({"risk_free_rate": _Prov(0.0515, "default")})
    assert "live interest rate was unavailable" in got.headline
    assert "5.15%" in got.headline
    assert Asm.risk_free_as_of_text() in got.headline
    assert "may be out of date" in got.headline


def test_the_headline_hardens_once_the_stored_rate_is_itself_old(monkeypatch):
    monkeypatch.setattr(Asm, "_today", lambda: date(2027, 6, 1))
    got = A.stored_rate_warning({"risk_free_rate": _Prov(0.0515, "default")})
    assert "no longer be close" in got.headline or \
           "no longer close" in got.headline
    assert "may be out of date" not in got.headline


def test_the_detail_carries_the_figures_for_the_app():
    got = A.stored_rate_warning({"risk_free_rate": _Prov(0.0515, "default")})
    assert got.detail["rate"] == 0.0515
    assert got.detail["as_of"] == Asm.risk_free_as_of_text()
    assert got.detail["is_stale"] is False


# ---------------------------------------------------------------------------
# It reaches the reader, from both methods
# ---------------------------------------------------------------------------

def _no_rate(monkeypatch):
    monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                        lambda tenor="year10": None)


def _a_rate(monkeypatch, rate=0.0515):
    monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                        lambda tenor="year10": (rate, "pinned"))


def test_the_dcf_raises_it(monkeypatch):
    import test_warning_tiers as T

    _no_rate(monkeypatch)
    fin = T._fin()
    from market_data import base_year_from
    base, _ = base_year_from(fin)
    suit = A.assess_suitability(fin, Asm.derive_assumptions(fin), base)
    kinds = [getattr(w, "kind", "") for w in suit.warnings]
    assert "risk_free_fallback" in kinds


def test_the_dcf_stays_quiet_when_the_rate_is_live(monkeypatch):
    import test_warning_tiers as T

    _a_rate(monkeypatch)
    fin = T._fin()
    from market_data import base_year_from
    base, _ = base_year_from(fin)
    suit = A.assess_suitability(fin, Asm.derive_assumptions(fin), base)
    kinds = [getattr(w, "kind", "") for w in suit.warnings]
    assert "risk_free_fallback" not in kinds


def _ddm_suitability(monkeypatch, *, live):
    """Drive the real dividend-model path, not the shared helper."""
    import ddm_assumptions as D
    import test_ddm_assumptions as T
    from ddm import DDMInputs

    if live:
        _a_rate(monkeypatch)
    else:
        _no_rate(monkeypatch)

    fin = T.bank()
    record = D.analyse_dividends(T.quarterly(T.STEADY), today=T.TODAY)
    derived = D.derive_ddm_assumptions(fin, record)
    inputs = DDMInputs(current_dividend=1.20, current_price=50.0)
    return A.assess_ddm_suitability(fin, record, derived, inputs)


def test_the_dividend_model_raises_it_too(monkeypatch):
    """The DDM builds its cost of equity from the same rate.

    Driven through assess_ddm_suitability rather than through the shared
    helper. An earlier version of this test called the helper directly,
    which passed happily with the DDM's own call site deleted.
    """
    suit = _ddm_suitability(monkeypatch, live=False)
    found = [w for w in suit.warnings
             if getattr(w, "kind", "") == "risk_free_fallback"]
    assert found, "the dividend model did not raise the stored-rate caution"
    assert severity_of(found[0]) == SEVERITY_CAUTION


def test_the_dividend_model_stays_quiet_when_the_rate_is_live(monkeypatch):
    suit = _ddm_suitability(monkeypatch, live=True)
    kinds = [getattr(w, "kind", "") for w in suit.warnings]
    assert "risk_free_fallback" not in kinds


def test_the_api_surfaces_it_at_caution(monkeypatch):
    from api import _warning_items

    class FakeSuit:
        method_fit: list = []
        warnings = [A.stored_rate_warning(
            {"risk_free_rate": _Prov(0.0515, "default")})]

    class FakeReport:
        suitability = FakeSuit()
        meta: dict = {}

    items = _warning_items(FakeReport())
    found = [i for i in items if i.kind == "risk_free_fallback"]
    assert len(found) == 1
    assert found[0].severity == SEVERITY_CAUTION
    assert "5.15%" in found[0].headline


def test_the_assumption_carries_its_date_only_when_stored():
    from api import _as_of, _is_stale

    assert _as_of("risk_free_rate", _Prov(0.0515, "default")) == \
        Asm.risk_free_as_of_text()
    assert _as_of("risk_free_rate", _Prov(0.0515, "derived")) is None
    assert _is_stale("risk_free_rate", _Prov(0.0515, "derived")) is False
    assert _is_stale("risk_free_rate", _Prov(0.04, "override")) is False


def test_the_equity_risk_premium_still_carries_its_own_date():
    """The helper serves two inputs; neither may shadow the other."""
    from api import _as_of, _is_stale

    assert _as_of("equity_risk_premium", _Prov(0.0441, "published")) == \
        f"{Asm.DAMODARAN_ERP_AS_OF:%B %Y}"
    assert _is_stale("equity_risk_premium",
                     _Prov(0.0441, "override")) is False
