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
    def __init__(self, value, source, detail="forced for this test"):
        self.value, self.source, self.detail = value, source, detail


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


# ---------------------------------------------------------------------------
# The rest of the same class: every other stand-in the discount rate rests on
# ---------------------------------------------------------------------------
#
# Each of these recorded `source: "default"` and showed it on an assumption
# row only, so a valuation discounted at a generic rate looked exactly like
# one built from the company's own capital structure. Graded by how much of
# the answer the stand-in carries.

def _dcf_warnings(monkeypatch, **patches):
    """Drive the real DCF path with one input forced onto its default."""
    import test_warning_tiers as T
    from market_data import base_year_from

    _a_rate(monkeypatch)
    for target, value in patches.items():
        monkeypatch.setattr(Asm, target, value)
    fin = T._fin()
    base, _ = base_year_from(fin)
    derived = Asm.derive_assumptions(fin)
    return A.assess_suitability(fin, derived, base).warnings


def _kinds(warnings):
    return {getattr(w, "kind", "") for w in warnings}


def _of_kind(warnings, kind):
    return next(w for w in warnings if getattr(w, "kind", "") == kind)


def test_a_generic_discount_rate_is_serious(monkeypatch):
    """The one serious tier here. The figure stops being about the company.

    LCID is the only real ticker that reaches this path, and it is refused
    for being loss-making before a figure is produced - so this is forced,
    and the forcing is the reason the test exists rather than a shortcut.
    """
    ws = _dcf_warnings(monkeypatch, _derive_wacc=lambda fin, tax, rec: (
        None, "computed WACC of 3.6% is outside the plausible range", {}))
    got = _of_kind(ws, "wacc_default")
    assert severity_of(got) == A.SEVERITY_SERIOUS
    assert "generic 8.5% discount rate" in got.headline
    assert "says little about this company" in got.headline
    assert got.detail["wacc"] == Asm.DEFAULT_WACC


def test_a_generic_discount_rate_leads_the_list(monkeypatch):
    """A complaint about the whole output outranks one about an input."""
    ws = _dcf_warnings(monkeypatch, _derive_wacc=lambda fin, tax, rec: (
        None, "failed", {}))
    assert A.most_severe(ws).kind == "wacc_default"
    assert "wacc_default" in A._METHOD_KINDS


def test_the_api_sorts_a_generic_rate_first():
    """analysis and api had two copies of this set; now they share one.

    Asserted on the ordering the app receives, not on the identity of the
    set. The first version of this test compared the two objects, which
    passed happily with api.py sorting against a hard-coded set of one.
    """
    import api

    assert api.METHOD_KINDS is A._METHOD_KINDS

    class FakeSuit:
        method_fit: list = []
        # Equally serious, and the one that must win is built SECOND. The
        # sort is stable, so an ordering that does not consult the set
        # leaves these in the order written and the assertion fails.
        warnings = [
            A.Warning("some other serious thing",
                      severity=A.SEVERITY_SERIOUS, kind="not_a_method_kind",
                      headline="some other serious thing"),
            A.Warning("generic rate", severity=A.SEVERITY_SERIOUS,
                      kind="wacc_default", headline="generic rate"),
            A.Warning("a caution", severity=SEVERITY_CAUTION,
                      kind="beta_default", headline="a caution"),
        ]

    class FakeReport:
        suitability = FakeSuit()
        meta: dict = {}

    kinds = [i.kind for i in api._warning_items(FakeReport())]
    assert kinds[0] == "wacc_default", (
        f"a generic discount rate must lead the list, got {kinds}")
    assert kinds[-1] == "beta_default", "a caution outranked a serious one"


def test_a_defaulted_beta_is_a_caution(monkeypatch):
    """Real on SHEL and LMT, where Yahoo reports a beta of 0.07."""
    ws = _dcf_warnings(monkeypatch, _capm_inputs=lambda fin, rec: (
        0.0515,
        rec("equity_risk_premium", 0.0441, "published", "pinned"),
        rec("beta", Asm.DEFAULT_BETA, "default",
            "beta measured 0.0720447, outside plausible range; used market "
            "beta of 1.0")))
    got = _of_kind(ws, "beta_default")
    assert severity_of(got) == SEVERITY_CAUTION
    assert "market average of 1.0" in got.headline
    assert got.detail["beta"] == 1.0


def test_a_defaulted_cost_of_debt_stays_in_the_quiet_tier(monkeypatch):
    """Real on NKE and eight others. A weighted minority of the rate."""
    ws = _dcf_warnings(monkeypatch, _cost_of_debt_history=lambda fin: [])
    got = _of_kind(ws, "cost_of_debt_default")
    assert severity_of(got) == SEVERITY_INFO
    assert "could not be worked out from its accounts" in str(got)


def test_nothing_is_raised_when_every_input_was_derived(monkeypatch):
    """The ordinary case. These must not fire on a healthy company."""
    ws = _dcf_warnings(monkeypatch)
    assert not _kinds(ws) & {"wacc_default", "beta_default",
                             "cost_of_debt_default", "risk_free_fallback",
                             "erp_stale", "long_run_growth_stale"}


def test_a_stale_risk_premium_reaches_the_reader(monkeypatch):
    """It already had a staleness flag. The flag reached nobody."""
    monkeypatch.setattr(Asm, "_today", lambda: date(2027, 4, 1))
    ws = _dcf_warnings(monkeypatch)
    got = _of_kind(ws, "erp_stale")
    assert severity_of(got) == SEVERITY_CAUTION
    assert f"{Asm.DAMODARAN_ERP_AS_OF:%B %Y}" in got.headline
    assert got.detail["age_days"] > Asm.ERP_STALE_AFTER_DAYS


def test_a_stale_long_run_growth_figure_reaches_the_reader(monkeypatch):
    monkeypatch.setattr(Asm, "_today", lambda: date(2027, 4, 1))
    ws = _dcf_warnings(monkeypatch)
    if "long_run_growth_stale" not in _kinds(ws):
        pytest.skip("the fixture company's ceiling is not bound by long-run "
                    "growth; covered by the binding tests below")
    got = _of_kind(ws, "long_run_growth_stale")
    assert severity_of(got) == SEVERITY_CAUTION
    assert f"{Asm.LONG_RUN_GROWTH_AS_OF:%B %Y}" in got.headline


def test_a_stale_growth_figure_that_did_not_bind_stays_quiet(monkeypatch):
    """KO's ceiling is set by its own discount rate, not by this figure.

    Warning about a stale number that lost to the other two limits would
    be noise, and noise in this tier teaches a reader to ignore it.
    """
    monkeypatch.setattr(Asm, "_today", lambda: date(2027, 4, 1))

    class Derived:
        wacc_inputs: dict = {}
        capm_inputs: dict = {}
        provenance: dict = {}
        diagnostics = {"terminal_growth_binding": "wacc_margin"}

    kinds = {w.kind for w in A.input_source_warnings(Derived(), dcf=True)}
    assert "erp_stale" in kinds
    assert "long_run_growth_stale" not in kinds


def test_a_stale_growth_figure_that_bound_does_warn(monkeypatch):
    monkeypatch.setattr(Asm, "_today", lambda: date(2027, 4, 1))

    class Derived:
        wacc_inputs: dict = {}
        capm_inputs: dict = {}
        provenance: dict = {}
        diagnostics = {"terminal_growth_binding": "long_run_growth"}

    kinds = {w.kind for w in A.input_source_warnings(Derived(), dcf=True)}
    assert "long_run_growth_stale" in kinds


def test_the_dividend_model_gets_the_ones_that_apply_to_it(monkeypatch):
    """It has no WACC and no cost of debt; it has the same CAPM build-up."""
    class Derived:
        wacc_inputs: dict = {}
        capm_inputs = {
            "beta": _Prov(1.0, "default"),
            "cost_of_equity": _Prov(0.0882, "default"),
        }
        provenance: dict = {}
        diagnostics: dict = {}

    kinds = {w.kind for w in A.input_source_warnings(Derived(), dcf=False)}
    assert "cost_of_equity_default" in kinds
    assert "beta_default" in kinds
    # Neither of these exists in a dividend model.
    assert "wacc_default" not in kinds
    assert "cost_of_debt_default" not in kinds


def test_a_generic_cost_of_equity_is_serious_too():
    class Derived:
        wacc_inputs: dict = {}
        capm_inputs = {"cost_of_equity": _Prov(0.0882, "default")}
        provenance: dict = {}
        diagnostics: dict = {}

    got = _of_kind(A.input_source_warnings(Derived(), dcf=False),
                   "cost_of_equity_default")
    assert severity_of(got) == A.SEVERITY_SERIOUS
    assert "says little about this company" in got.headline


def test_the_beta_message_does_not_blame_the_provider_for_our_own_figure():
    """The rejected beta is one this model regressed, not one it was given.

    LMT and SHEL both reach the default on a beta of about 0.07, and both
    of those figures came out of our own five-year regression. The message
    used to call them "reported", which reads as the data provider's fault
    and sends anyone debugging it to the wrong place.
    """
    import assumptions as Asm
    from market_data import CompanyFinancials

    recorded = {}

    def record(name, value, source, detail, **kw):
        recorded[name] = (value, source, detail)
        return value

    fin = CompanyFinancials(
        ticker="LMT",
        profile={"beta": 0.0698,
                 "betaSource": "computed from 59 monthly returns vs ^GSPC"},
        income=[{}], balance=[{}], cashflow=[{}])
    Asm._capm_inputs(fin, record)

    _, source, detail = recorded["beta"]
    assert source == "default"
    assert "measured 0.0698" in detail
    assert "reported" not in detail
