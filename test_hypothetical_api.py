"""
The user-built hypothetical: the last opt-in, and the weakest claim in the app.

What these tests exist to hold:

  * the honest refusal stays the default. A shrinking loss-maker is refused a
    standard valuation, then refused a speculative estimate, exactly as before
    this screen existed;
  * nothing about it is automatic. It is reachable only from its own endpoint,
    with all three drivers supplied, and the standard and speculative paths
    cannot reach it even with overrides;
  * nothing is derived, defaulted or flattering. The placeholders offered are
    neutral, and the neutral ones deliberately produce no figure;
  * the contradiction is always shown: the company's real figures travel with
    every assumption;
  * a company with no revenue is refused even here.

The synthetic companies are shared with the speculative tests. SHRINKER is
shaped like Intel and Lumen as probed: real revenue, losing money, and revenue
falling year on year - the case the speculative engine refuses.

Run with:  python -m pytest test_hypothetical_api.py -v
"""

import pytest
from fastapi.testclient import TestClient

import analysis
import api
from api import app
from hypothetical import (NEUTRAL_REVENUE_GROWTH, NEUTRAL_TARGET_MARGIN,
                          NEUTRAL_YEARS_TO_TARGET, HypotheticalRefused,
                          UserAssumptions)
from test_speculative_api import (BANK, DEEPLOSS, GROWER, NOREV, SHRINKER, TINY,
                                  _stubbed, client)  # noqa: F401

# A plainly optimistic hypothetical for a company going the other way.
ASSUMED = {"revenue_growth": 0.08, "target_operating_margin": 0.15, "years_to_target": 5}


def ask(client, ticker=SHRINKER, **overrides):
    params = {**ASSUMED, **overrides}
    return client.get(f"/valuation/{ticker}/hypothetical", params=params)


# ---------------------------------------------------------------------------
# The honest refusal is still the default
# ---------------------------------------------------------------------------

def test_a_shrinking_loss_maker_is_still_refused_a_standard_valuation(client):
    r = client.get(f"/valuation/{SHRINKER}")

    assert r.status_code == 422
    assert r.json()["code"] == "not_suitable"
    assert r.json()["intrinsic_value_per_share"] is None


def test_it_is_still_refused_a_speculative_estimate_for_the_same_reason(client):
    r = client.get(f"/valuation/{SHRINKER}/speculative")

    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "not_suitable"
    # The refusal's own words are unchanged: a shrinking company offers no
    # growth to extend.
    assert any("shrinking company" in reason for reason in body["reasons"])


def test_the_refusal_offers_the_opt_in_without_taking_a_step_towards_it(client):
    body = client.get(f"/valuation/{SHRINKER}/speculative").json()

    assert body["hypothetical_available"] is True
    # Offered, never pre-computed: the placeholders are neutral and no figure
    # appears anywhere in the refusal.
    assert body["hypothetical_placeholders"] == {
        "revenue_growth": NEUTRAL_REVENUE_GROWTH,
        "target_operating_margin": NEUTRAL_TARGET_MARGIN,
        "years_to_target": float(NEUTRAL_YEARS_TO_TARGET),
    }
    assert "hypothetical_value_per_share" not in body


def test_nothing_automatic_reaches_the_hypothetical(client, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the hypothetical ran without being asked for")

    monkeypatch.setattr(analysis, "value_company_hypothetically", never)
    monkeypatch.setattr(api, "value_company_hypothetically", never)

    assert client.get(f"/valuation/{SHRINKER}").status_code == 422
    assert client.get(f"/valuation/{SHRINKER}/speculative").status_code == 422
    assert client.get(f"/valuation/{GROWER}/speculative").status_code == 200


# ---------------------------------------------------------------------------
# The user supplies the drivers - all of them
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", list(ASSUMED))
def test_every_driver_must_be_supplied(client, missing):
    params = {k: v for k, v in ASSUMED.items() if k != missing}

    r = client.get(f"/valuation/{SHRINKER}/hypothetical", params=params)

    # Nothing is defaulted on the caller's behalf, so an incomplete request is
    # rejected rather than quietly filled in.
    assert r.status_code == 422


def test_the_neutral_placeholders_produce_no_figure(client):
    r = ask(client,
            revenue_growth=NEUTRAL_REVENUE_GROWTH,
            target_operating_margin=NEUTRAL_TARGET_MARGIN,
            years_to_target=NEUTRAL_YEARS_TO_TARGET)

    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "hypothetical_incomplete"
    assert "above 0%" in body["message"]
    # And it says whose job the choice is.
    assert "nothing here will pick one for you" in body["message"].lower()


def test_the_placeholders_are_not_flattering(client):
    # No growth and no profit: a hypothetical that starts from them says
    # nothing good about the company.
    assert NEUTRAL_REVENUE_GROWTH == 0.0
    assert NEUTRAL_TARGET_MARGIN == 0.0


def test_the_figure_is_computed_from_the_supplied_assumptions(client):
    body = ask(client).json()

    assert body["your_assumptions"] == {
        "revenue_growth": 0.08,
        "target_operating_margin": 0.15,
        "years_to_target": 5,
    }
    # A different hypothetical gives a different figure - it is arithmetic on
    # the caller's numbers, not a stored answer about the company.
    other = ask(client, target_operating_margin=0.25).json()
    assert other["hypothetical_value_per_share"] != body["hypothetical_value_per_share"]


def test_the_assumptions_are_recorded_as_the_callers_own(client):
    body = ask(client).json()
    by_name = {a["name"]: a for a in body["assumptions"]}

    for name in ("speculative_revenue_growth", "target_operating_margin",
                 "years_to_profitability"):
        assert by_name[name]["source"] == "override", name


# ---------------------------------------------------------------------------
# It is never called a valuation
# ---------------------------------------------------------------------------

def test_the_response_never_names_itself_a_valuation(client):
    body = ask(client).json()

    assert body["method"] == "hypothetical"
    assert body["is_valuation"] is False
    assert body["derived_from_company_data"] is False
    assert "hypothetical_value_per_share" in body
    for forbidden in ("intrinsic_value_per_share", "speculative_value_per_share",
                      "relative_value_per_share"):
        assert forbidden not in body, forbidden


def test_no_upside_or_downside_is_computed_against_the_price(client):
    body = ask(client).json()

    assert body["current_price"] > 0          # context only
    for forbidden in ("upside_downside", "estimate_vs_price", "hypothetical_vs_price"):
        assert forbidden not in body, forbidden


def test_the_disclaimer_is_the_strongest_in_the_app(client):
    body = ask(client).json()
    disclaimer = body["disclaimer"]

    assert body["disclaimer_headline"] == "A HYPOTHETICAL YOU BUILT - NOT A VALUATION"
    assert "THIS IS NOT A VALUATION" in disclaimer
    # It names the caller as the author of the assumptions, and the company's
    # own trajectory as contradicting them.
    assert "you typed in" in disclaimer
    assert "+8.0%" in disclaimer and "15%" in disclaimer
    assert "revenue is actually falling" in disclaimer
    assert "not as information about the company" in disclaimer


def test_both_earlier_refusals_travel_with_it(client):
    body = ask(client).json()

    assert body["why_standard_valuation_refused"]
    assert any("shrinking company" in r for r in body["why_speculative_estimate_refused"])


# ---------------------------------------------------------------------------
# The contradiction is always visible
# ---------------------------------------------------------------------------

def test_every_assumption_carries_the_companys_real_figure(client):
    body = ask(client).json()
    reality = {c["name"]: c for c in body["reality"]}

    assert set(reality) == {"revenue_growth", "target_operating_margin", "years_to_target"}
    growth = reality["revenue_growth"]
    assert growth["assumed"] == 0.08
    assert growth["actual"] < 0                      # the company is shrinking
    assert growth["contradicts"] is True
    assert "actually fallen" in growth["statement"]

    margin = reality["target_operating_margin"]
    assert margin["actual"] < 0
    assert margin["contradicts"] is True
    assert "You have assumed" in margin["statement"]


def test_an_assumption_that_does_not_flatter_is_not_marked_as_contradicting(client):
    # Assuming the decline continues contradicts nothing.
    body = ask(client, revenue_growth=-0.20).json()
    growth = next(c for c in body["reality"] if c["name"] == "revenue_growth")

    assert growth["contradicts"] is False


def test_the_pace_of_improvement_is_spelled_out(client):
    body = ask(client, years_to_target=2).json()
    years = next(c for c in body["reality"] if c["name"] == "years_to_target")

    assert "percentage points every year" in years["statement"]
    assert "Nothing in" in years["statement"]


# ---------------------------------------------------------------------------
# What it still refuses
# ---------------------------------------------------------------------------

def test_a_company_with_no_revenue_is_refused_even_here(client):
    r = ask(client, ticker=NOREV)

    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "hypothetical_not_applicable"
    assert any("no revenue" in reason for reason in body["reasons"])
    # And the reason is the honest one: there is nothing to grow from.
    assert any("still nothing" in reason for reason in body["reasons"])


def test_the_no_revenue_refusal_is_offered_no_opt_in(client):
    body = client.get(f"/valuation/{NOREV}/speculative").json()

    assert body["hypothetical_available"] is False
    assert body["hypothetical_placeholders"] is None


def test_revenue_too_small_to_anchor_is_refused(client):
    r = ask(client, ticker=TINY)

    assert r.status_code == 422
    assert "too small to anchor" in " ".join(r.json()["reasons"])


def test_a_company_the_speculative_path_serves_is_sent_back_to_it(client):
    r = ask(client, ticker=GROWER)

    assert r.status_code == 422
    assert "already produced" in r.json()["message"]


def test_a_bank_is_refused(client):
    r = ask(client, ticker=BANK)

    assert r.status_code == 422
    assert r.json()["code"] == "hypothetical_not_applicable"


def test_an_absurd_assumption_is_refused(client):
    assert ask(client, target_operating_margin=0.95).status_code == 422
    assert ask(client, revenue_growth=5.0).status_code == 422
    assert ask(client, years_to_target=40).status_code == 422


def test_deep_losses_are_still_reachable_because_the_pace_is_the_users_to_assume(client):
    # DEEPLOSS is refused a speculative estimate for how far it has to travel.
    # That is a judgement about pace, which is exactly what this screen hands
    # to the user - so it is offered, and the contradiction is shown instead.
    r = ask(client, ticker=DEEPLOSS)

    assert r.status_code == 200
    years = next(c for c in r.json()["reality"] if c["name"] == "years_to_target")
    assert years["contradicts"] is True


# ---------------------------------------------------------------------------
# The engine layer, without the API
# ---------------------------------------------------------------------------

def test_validate_rejects_a_fractional_year():
    with pytest.raises(HypotheticalRefused):
        analysis.validate(UserAssumptions(0.05, 0.10, 2.5))


def test_the_report_never_calls_itself_a_valuation():
    report = analysis.value_company_hypothetically(
        SHRINKER, UserAssumptions(0.08, 0.15, 5))

    assert report.method == "hypothetical"
    assert report.reality and all(c.statement for c in report.reality)
