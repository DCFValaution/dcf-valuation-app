"""
Terminal growth, derived from the rate environment rather than fixed.

The long-run growth a company is assumed to settle into used to be a
constant - 2.5%, roughly long-run nominal GDP - chosen once and never
revisited. It now tracks the 10-year Treasury yield the cost of equity is
already built on, so the figure moves as rates do.

What has to hold, whatever rates do:

  g < WACC, always, with room to spare. The Gordon perpetuity divides by
  (WACC - g), so the two meeting is not a bad answer but an undefined one.
  The cap is what guarantees it, and it has to bind before the subtraction
  gets anywhere near zero.

  The sensitivity grid stays usable. It moves g a full point above the
  model's own figure, so a margin of a point or less would put its top row
  at or past WACC - a table of undefined cells being a poor way to show how
  sensitive a figure is.

  And the slider can still show it. A derived value outside the control's
  own range could not be displayed on the thing meant to move it.

Run with:  python -m pytest test_terminal_growth.py -v
"""

import pytest

import assumptions as A
from assumptions import TERMINAL_GROWTH_SAFETY_MARGIN, derived_terminal_growth
from dcf import SENSITIVITY_GROWTH_STEP, SENSITIVITY_OFFSETS


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------

def test_it_tracks_the_risk_free_rate_when_there_is_room():
    assert derived_terminal_growth(0.047, 0.11) == 0.047


def test_it_caps_below_wacc_when_there_is_not():
    """A utility: the risk-free rate is most of its cost of capital."""
    assert derived_terminal_growth(0.047, 0.055) == pytest.approx(
        0.055 - TERMINAL_GROWTH_SAFETY_MARGIN)


@pytest.mark.parametrize("rf", [0.0, 0.01, 0.025, 0.047, 0.06, 0.09])
@pytest.mark.parametrize("wacc", [0.04, 0.055, 0.085, 0.11, 0.20])
def test_growth_is_always_below_wacc(rf, wacc):
    """The invariant the perpetuity cannot survive without."""
    g = derived_terminal_growth(rf, wacc)
    assert g < wacc
    assert wacc - g >= TERMINAL_GROWTH_SAFETY_MARGIN - 1e-12


@pytest.mark.parametrize("wacc", [0.04, 0.055, 0.085, 0.11, 0.20])
def test_the_whole_sensitivity_row_stays_below_wacc(wacc):
    """The grid's highest growth must still clear the model's own WACC.

    This is the reason the margin is 1.5 points rather than something
    tighter: the grid reaches a full point above g.
    """
    g = derived_terminal_growth(0.047, wacc)
    highest = g + max(SENSITIVITY_OFFSETS) * SENSITIVITY_GROWTH_STEP
    assert highest < wacc


def test_the_cap_is_what_binds_not_the_rate(monkeypatch):
    """At a high enough rate every company is capped, and none breaks."""
    for wacc in (0.04, 0.06, 0.09, 0.15):
        g = derived_terminal_growth(0.50, wacc)
        assert g == pytest.approx(wacc - TERMINAL_GROWTH_SAFETY_MARGIN)
        assert g < wacc


def test_a_negative_rate_is_passed_through_rather_than_floored():
    """The formula does not invent a floor; the bounds clamp does that, and
    only where a value has to be shown on a slider."""
    assert derived_terminal_growth(-0.01, 0.08) == -0.01


# ---------------------------------------------------------------------------
# What the derivation records
# ---------------------------------------------------------------------------

def derive(monkeypatch, rf, fin):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (rf, "pinned for test"))
    return A.derive_assumptions(fin)


@pytest.fixture
def profitable():
    from test_api import PROFITABLE, fake_fetch_financials
    return fake_fetch_financials(PROFITABLE)


def test_it_is_marked_derived_not_default(monkeypatch, profitable):
    derived = derive(monkeypatch, 0.047, profitable)
    prov = derived.provenance["terminal_growth"]
    assert prov.source.startswith("derived")
    assert "risk-free rate" in prov.detail


def test_the_detail_says_when_the_cap_bound(monkeypatch, profitable):
    """A reader should be able to tell which of the two produced the figure."""
    derived = derive(monkeypatch, 0.50, profitable)
    prov = derived.provenance["terminal_growth"]
    assert "which is WACC less" in prov.detail
    assert derived.diagnostics["terminal_growth_capped"] is True
    assert derived.assumptions.terminal_growth < derived.assumptions.wacc


def test_the_detail_says_when_it_simply_tracks_the_rate(monkeypatch, profitable):
    derived = derive(monkeypatch, 0.01, profitable)
    prov = derived.provenance["terminal_growth"]
    assert "defaults to the current risk-free rate" in prov.detail
    assert derived.diagnostics["terminal_growth_capped"] is False
    assert derived.assumptions.terminal_growth == pytest.approx(0.01)


def test_the_slider_range_travels_with_the_value(monkeypatch, profitable):
    """The ceiling is this company's, so the control cannot hold it."""
    derived = derive(monkeypatch, 0.047, profitable)
    prov = derived.provenance["terminal_growth"]
    assert prov.allowed_min == 0.0
    assert prov.allowed_max == pytest.approx(derived.assumptions.terminal_growth)


def test_the_default_sits_at_the_top_of_its_own_range(monkeypatch, profitable):
    """It rests at the ceiling; the user argues it down, never up."""
    for rf in (0.01, 0.025, 0.047, 0.08):
        derived = derive(monkeypatch, rf, profitable)
        prov = derived.provenance["terminal_growth"]
        assert derived.assumptions.terminal_growth == prov.allowed_max, rf
        assert prov.allowed_min == 0.0


def test_the_ceiling_is_the_rate_where_there_is_room(monkeypatch, profitable):
    derived = derive(monkeypatch, 0.02, profitable)
    assert derived.provenance["terminal_growth"].allowed_max == pytest.approx(0.02)
    assert derived.diagnostics["terminal_growth_capped"] is False


def test_the_ceiling_is_wacc_less_the_margin_where_there_is_not(
        monkeypatch, profitable):
    derived = derive(monkeypatch, 0.50, profitable)
    wacc = derived.assumptions.wacc
    assert derived.provenance["terminal_growth"].allowed_max == pytest.approx(
        wacc - TERMINAL_GROWTH_SAFETY_MARGIN)
    assert derived.diagnostics["terminal_growth_capped"] is True


def test_the_range_survives_an_override(monkeypatch, profitable):
    """A slider the user has moved still needs to know how far it may go."""
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.047, "pinned for test"))
    derived = A.derive_assumptions(profitable, overrides={"terminal_growth": 0.01})
    prov = derived.provenance["terminal_growth"]
    assert prov.source == "override"
    assert prov.value == pytest.approx(0.01)
    assert prov.allowed_min == 0.0 and prov.allowed_max is not None
    assert prov.allowed_max > 0.01


def test_sliding_down_recovers_the_conservative_figure(monkeypatch, profitable):
    """The old behaviour is still reachable: it is where the slider used to
    rest, not somewhere the user has been locked out of."""
    import analysis
    from assumptions import DEFAULT_TERMINAL_GROWTH
    from test_api import PROFITABLE
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.047, "pinned for test"))

    at_ceiling = analysis.value_company(PROFITABLE)
    dialled_down = analysis.value_company(
        PROFITABLE, overrides={"terminal_growth": DEFAULT_TERMINAL_GROWTH})

    assert (dialled_down.result.intrinsic_value_per_share
            < at_ceiling.result.intrinsic_value_per_share)
    assert dialled_down.derived.assumptions.terminal_growth == pytest.approx(
        DEFAULT_TERMINAL_GROWTH)


def test_the_invariant_holds_through_the_whole_derivation(
        monkeypatch, profitable):
    for rf in (0.0, 0.01, 0.025, 0.047, 0.08, 0.5):
        derived = derive(monkeypatch, rf, profitable)
        a = derived.assumptions
        assert a.terminal_growth < a.wacc, f"rf={rf}"


# ---------------------------------------------------------------------------
# And it reaches the figure
# ---------------------------------------------------------------------------

def test_the_sensitivity_centre_cell_still_matches_the_headline(
        monkeypatch, profitable):
    import analysis
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.047, "pinned for test"))
    from test_api import PROFITABLE
    report = analysis.value_company(PROFITABLE)
    result = report.result
    centre = len(result.sensitivity) // 2
    assert result.sensitivity[centre][centre] == pytest.approx(
        result.intrinsic_value_per_share, rel=1e-12)
    assert result.sensitivity_gs[centre] == pytest.approx(
        report.derived.assumptions.terminal_growth)
