"""
Warnings a person reads must be written for a person.

The model keeps a precise technical record of every assumption - variable
names, raw ratios, clamp bounds in brackets - and that record is right for the
workbook and the API. What reaches the screen must not be that record. These
tests pin the plain-language versions: what they must say (so the honesty is
kept) and what they must not contain (so the engine-speak cannot creep back).
"""

import re

import pytest

import plain_language as P
from assumptions import Provenance

COMPANY = "Apple Inc."

# Anything that reads as engine output rather than a sentence for a person.
ENGINE_SPEAK = re.compile(
    r"_pct|nwc|NWC|\[|\]|clamped|raw |\bFY\d|mm\b|WACC > g|Gordon|upstream|HTTP \d")


def clamped(name, raw, limit, why="range", detail="technical detail; raw x clamped to [a, b]"):
    return Provenance(name, limit, "derived (clamped)", detail, raw=raw, limit=limit, why=why)


def assert_plain(text):
    assert not ENGINE_SPEAK.search(text), f"engine-speak in: {text!r}"


# ---------------------------------------------------------------------------
# Working capital - the two warnings that prompted this
# ---------------------------------------------------------------------------

def test_dcf_working_capital_that_freed_cash_is_explained_plainly():
    # AAPL's real case: -28.7% held to -10%.
    text = P.clamp_warning(clamped("nwc_pct", -0.287, -0.10), COMPANY)

    assert_plain(text)
    assert "28.7" not in text, "no raw ratio"
    assert text.startswith("Apple Inc.'s recent figures")
    assert "freeing up an unusually large amount of cash" in text
    assert "keep the estimate realistic" in text


def test_dcf_working_capital_that_tied_up_cash_says_the_opposite():
    text = P.clamp_warning(clamped("nwc_pct", 0.55, 0.30), COMPANY)

    assert_plain(text)
    assert "tying up an unusually large amount of cash" in text
    assert "freeing" not in text


def test_speculative_working_capital_is_explained_plainly():
    # RIVN's real case: 28.7% of revenue growth held to 10% once mature.
    text = P.clamp_warning(clamped("mature_nwc_pct", 0.287, 0.10), "Rivian Automotive, Inc.")

    assert_plain(text)
    assert "28.7" not in text
    assert "ties up an unusually large amount of cash" in text
    assert "once the business matures" in text


def test_speculative_working_capital_below_the_limit_is_explained_too():
    text = P.clamp_warning(clamped("mature_nwc_pct", -0.05, 0.0), "Rivian Automotive, Inc.")

    assert_plain(text)
    assert "frees up cash" in text


# ---------------------------------------------------------------------------
# Every other clamp the models can apply
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prov, must_say", [
    (clamped("revenue_growth", 0.48, 0.15), ["grew 48% a year", "uses 15% a year"]),
    (clamped("revenue_growth", -0.06, 0.0), ["shrank", "holds steady", "too high"]),
    (clamped("operating_margin", 0.72, 0.60), ["72%", "60%"]),
    (clamped("tax_rate", 0.51, 0.35), ["51%", "35%"]),
    (clamped("tax_rate", -0.10, 0.0), ["tax credit"]),
    (clamped("da_pct", 0.41, 0.30), ["depreciation", "30% of revenue"]),
    (clamped("capex_pct", 0.52, 0.40), ["52%", "40%"]),
    (clamped("dividend_growth", 0.25, 0.12), ["grew 25% a year", "12% a year"]),
    (clamped("dividend_growth", -0.04, 0.0), ["shrinking", "too high"]),
    (clamped("dividend_growth", 0.14, 0.07, why="sustainable"),
     ["faster than its profits can support", "7% a year", "earnings it keeps"]),
    (clamped("speculative_revenue_growth", 0.48, 0.30), ["48%", "30% a year"]),
    (clamped("years_to_profitability", 14, 10), ["14 years", "limited to 10 years"]),
    (clamped("years_to_profitability", 1, 2), ["at least 2 years"]),
    (clamped("wacc", 0.082, 0.10, why="floor"), ["8.2%", "at least 10%", "never made a profit"]),
])
def test_every_clamp_reads_as_a_sentence_with_its_facts(prov, must_say):
    text = P.clamp_warning(prov, COMPANY)

    assert_plain(text)
    for phrase in must_say:
        assert phrase in text, f"{phrase!r} missing from {text!r}"


def test_the_sustainable_growth_cap_is_told_apart_from_the_range_cap():
    range_cap = P.clamp_warning(clamped("dividend_growth", 0.25, 0.12), COMPANY)
    earnings_cap = P.clamp_warning(clamped("dividend_growth", 0.14, 0.07, "sustainable"), COMPANY)

    assert "profits can support" in earnings_cap
    assert "profits can support" not in range_cap


def test_a_clamp_without_structured_facts_still_never_leaks_the_record():
    prov = Provenance("nwc_pct", -0.10, "derived (clamped)",
                      "aggregate change in NWC / change in revenue; raw -28.7% clamped to [-10%, 30%]")

    text = P.clamp_warning(prov, COMPANY)

    assert_plain(text)
    assert "working capital" in text


def test_an_unfamiliar_assumption_falls_back_to_a_sentence():
    text = P.clamp_warning(clamped("some_new_field", 5.0, 1.0), COMPANY)
    assert_plain(text)
    assert "realistic range" in text


# ---------------------------------------------------------------------------
# The other warnings
# ---------------------------------------------------------------------------

def test_growth_below_terminal_growth_says_what_it_means():
    text = P.growth_below_terminal_warning(COMPANY, 0.018, 0.025, 5)

    assert_plain(text)
    assert "1.8% a year for the next 5 years" in text
    assert "2.5% a year forever after" in text
    assert "worth checking" in text
    assert "Derived" not in text and "terminal growth" not in text.lower()


def test_a_defaulted_dividend_growth_says_why():
    prov = Provenance("dividend_growth", 0.025, "default",
                      "fewer than 3 complete years of dividend history to measure growth; used terminal growth")

    text = P.dividend_growth_default_warning(prov, "Bank Co.")

    assert_plain(text)
    assert "dividend_growth" not in text
    assert "not have enough years of dividend history" in text
    assert "2.5% a year" in text


# ---------------------------------------------------------------------------
# End to end: the clamp facts are recorded where the clamps happen
# ---------------------------------------------------------------------------

def _company_with_extreme_working_capital():
    """A synthetic company whose supplier credit balloons as it grows, so the
    working-capital ratio lands far below the model's -10% limit - AAPL's shape."""
    from test_api import make_fin

    fin = make_fin("WCAP", "Working Capital Co.")
    newest = fin.balance[0]
    newest["totalCurrentLiabilities"] = newest["totalCurrentAssets"] * 3
    return fin


def test_the_dcf_records_the_facts_a_clamp_warning_needs():
    from assumptions import CLAMP_NWC_PCT, derive_assumptions

    derived = derive_assumptions(_company_with_extreme_working_capital())

    nwc = derived.provenance["nwc_pct"]
    assert nwc.source == "derived (clamped)", "fixture sanity: the clamp must bite"
    assert nwc.raw < CLAMP_NWC_PCT[0]
    assert nwc.limit == CLAMP_NWC_PCT[0] == nwc.value
    assert nwc.why == "range"
    # The technical record the workbook carries is unchanged in form.
    assert "clamped to [" in nwc.detail

    for prov in derived.provenance.values():
        if prov.source != "derived (clamped)":
            assert prov.raw is None, "only a clamp carries clamp facts"


def test_the_screen_gets_the_plain_warning_not_the_record():
    from analysis import assess_suitability
    from assumptions import derive_assumptions
    from market_data import base_year_from

    fin = _company_with_extreme_working_capital()
    derived = derive_assumptions(fin)
    base, _ = base_year_from(fin)

    warnings = assess_suitability(fin, derived, base).warnings

    assert any("freeing up an unusually large amount of cash" in w for w in warnings), warnings
    for w in warnings:
        assert_plain(w)
