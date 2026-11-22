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

import dataclasses

import pytest
from fastapi.testclient import TestClient

import analysis
import api
from api import XLSX_MEDIA_TYPE, app
from hypothetical import (NEUTRAL_REVENUE_GROWTH, NEUTRAL_TARGET_MARGIN,
                          NEUTRAL_YEARS_TO_TARGET, HypotheticalRefused,
                          UserAssumptions)
from test_speculative_api import (BANK, DEEPLOSS, GROWER, LENDER, LENDERLOSS,
                                  NOREV, OUTSPENDER, PROFITABLE, SHRINKER,
                                  TINY, _stubbed, client)  # noqa: F401

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


# ---------------------------------------------------------------------------
# The workbook endpoint
# ---------------------------------------------------------------------------

def _xlsx(client, ticker=SHRINKER, **overrides):
    params = {**ASSUMED, **overrides}
    return client.get(f"/valuation/{ticker}/hypothetical/excel", params=params)


def test_the_hypothetical_workbook_is_served_as_a_spreadsheet(client):
    r = _xlsx(client)
    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX_MEDIA_TYPE
    assert r.headers["content-disposition"] == \
        f'attachment; filename="{SHRINKER}_Hypothetical.xlsx"'
    assert r.content[:2] == b"PK"


def test_the_filename_never_calls_it_a_valuation(client):
    assert "valuation" not in _xlsx(client).headers["content-disposition"].lower()


def test_assumptions_outside_the_permitted_range_produce_no_file(client):
    r = _xlsx(client, revenue_growth=5)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert "spreadsheetml" not in r.headers["content-type"]


def test_a_company_the_hypothetical_is_refused_for_produces_no_file(client):
    """Where the hypothetical itself is declined, there is nothing to export."""
    refused = ask(client, ticker=NOREV)
    if refused.status_code == 200:
        pytest.skip("this fixture is not refused the hypothetical")
    r = _xlsx(client, ticker=NOREV)
    assert r.status_code == refused.status_code
    assert "spreadsheetml" not in r.headers["content-type"]


# ---------------------------------------------------------------------------
# A refusal with nothing after it
#
# Tesla found the gap these cover. It is profitable, so the speculative
# estimate - which exists to project a way out of losses - is not offered for
# it; and it is refused a standard valuation because its investment outspends
# its profits. The hypothetical was reachable only from a speculative refusal,
# so the screen said why the model would not value the company and stopped.
#
# What has to hold is the pairing: the refusal offers the step, and the step
# is actually served. One without the other is worse than neither - an opt-in
# that refuses a tap, or a screen nothing points at.
# ---------------------------------------------------------------------------

def test_the_outspender_is_profitable_and_still_refused(client):
    r = client.get(f"/valuation/{OUTSPENDER}")

    assert r.status_code == 422
    body = r.json()
    assert any("spending more cash than it brings in" in reason
               for reason in body["reasons"])
    # Not for losing money: the words that would say so are absent, and the
    # loss-maker's tier is not offered.
    assert not any("loss-making" in reason for reason in body["reasons"])
    assert body["speculative_estimate_available"] is False


def test_that_refusal_offers_the_hypothetical_instead(client):
    body = client.get(f"/valuation/{OUTSPENDER}").json()

    assert body["hypothetical_available"] is True
    # Offered, never taken: no figure of any kind in the refusal.
    assert body["intrinsic_value_per_share"] is None
    assert "hypothetical_value_per_share" not in body


def test_this_refusal_asks_a_different_question_and_says_so(client):
    body = client.get(f"/valuation/{OUTSPENDER}").json()
    assert body["hypothetical_framing"] == "what_would_have_to_be_true"

    # And a loss-maker's refusal, reached through the speculative tier, keeps
    # the original one.
    spec = client.get(f"/valuation/{SHRINKER}/speculative").json()
    assert spec["hypothetical_available"] is True
    assert spec["hypothetical_placeholders"] == {
        "revenue_growth": NEUTRAL_REVENUE_GROWTH,
        "target_operating_margin": NEUTRAL_TARGET_MARGIN,
        "years_to_target": float(NEUTRAL_YEARS_TO_TARGET),
    }


def test_a_profitable_refusal_opens_on_the_companys_own_figures(client):
    """Not neutral, and deliberately so.

    Zero growth and zero profit are the honest starting point for a company
    assuming a turnaround: anything else would be the app suggesting one. For
    a company that already makes a profit they are not modest, they are wrong
    about the company - and the question is what would have to CHANGE, which
    starts from what it does now.
    """
    refusal = client.get(f"/valuation/{OUTSPENDER}").json()
    start = refusal["hypothetical_placeholders"]
    derived = {a["name"]: a["value"] for a in refusal["assumptions"]}

    assert start["revenue_growth"] == pytest.approx(derived["revenue_growth"])
    assert start["target_operating_margin"] == pytest.approx(
        derived["operating_margin"])
    assert start["target_operating_margin"] > 0, (
        "a nil margin would land the reader on the no-profit screen at once")


def test_opening_on_them_produces_a_figure_rather_than_a_prompt(client):
    """The loss-maker's screen opens with no figure on purpose - the reader
    has not chosen a profit yet. This one opens with the answer to "what does
    today imply", which is the question it exists to ask."""
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    r = ask(client, ticker=OUTSPENDER,
            revenue_growth=start["revenue_growth"],
            target_operating_margin=start["target_operating_margin"],
            years_to_target=int(start["years_to_target"]))

    assert r.status_code == 200, r.json()
    assert r.json()["hypothetical_value_per_share"] is not None


def test_the_no_profit_message_is_written_for_the_route(client):
    profitable = ask(client, ticker=OUTSPENDER,
                     target_operating_margin=0).json()
    assert profitable["code"] == "hypothetical_incomplete"
    assert "earning nothing on its sales" in profitable["message"]
    assert "path to profitability" not in profitable["message"]

    loss = ask(client, target_operating_margin=0).json()
    assert loss["code"] == "hypothetical_incomplete"
    assert "path to profitability" in loss["message"]


def test_the_screen_states_what_the_reader_is_not_setting(client):
    body = ask(client, ticker=OUTSPENDER).json()

    note = body["investment_note"]
    assert "Investment is not yours to set" in note
    assert "%" in note, "the rate itself has to be in it"
    assert body["framing"] == "what_would_have_to_be_true"


def test_assuming_no_change_is_not_called_a_contradiction(client):
    """The reader has assumed exactly what the company earns. Telling them
    "nothing in its figures says that happens" is a statement about its own
    figures, and it is false."""
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    body = ask(client, ticker=OUTSPENDER,
               revenue_growth=start["revenue_growth"],
               target_operating_margin=start["target_operating_margin"],
               years_to_target=int(start["years_to_target"])).json()

    years = next(c for c in body["reality"] if c["name"] == "years_to_target")
    assert years["contradicts"] is False
    assert "does nothing yet" in years["statement"]
    assert "Nothing in" not in years["statement"]


def test_the_disclaimer_asks_the_right_question(client):
    text = ask(client, ticker=OUTSPENDER).json()["disclaimer"]

    assert ("what would have to be true about its growth, its margins and "
            "its investment") in text
    assert "opens on the company" in text
    # The loss-maker's claim is false here and must not travel.
    assert "Nothing here is derived from the company's data" not in text
    assert "Nothing here is derived from the company's data" in ask(
        client).json()["disclaimer"]


def test_the_offer_is_honoured_and_not_refused_a_step_later(client):
    r = ask(client, ticker=OUTSPENDER)

    assert r.status_code == 200, r.json()
    assert r.json()["hypothetical_value_per_share"] is not None


def test_the_disclaimer_does_not_call_a_profitable_company_a_loss_maker(client):
    text = ask(client, ticker=OUTSPENDER).json()["disclaimer"]

    assert "spending more cash than it brings in" in text
    assert "this company is not losing money" in text
    # The sentence written for the loss route must not travel to this one.
    assert "loses money and was refused" not in text
    # And the loss route still says what it always said.
    assert "loses money and was refused" in ask(client).json()["disclaimer"]


def test_the_speculative_refusal_block_is_empty_because_there_was_none(client):
    body = ask(client, ticker=OUTSPENDER).json()

    assert body["why_speculative_estimate_refused"] == []
    assert body["why_standard_valuation_refused"] != []
    # The loss route still carries both, so the empty list means "never
    # offered" rather than "stopped being reported".
    assert ask(client).json()["why_speculative_estimate_refused"] != []


def test_a_structural_refusal_is_offered_no_step_at_all(client):
    # A loss-making lender is refused because interest is how it earns revenue,
    # so unlevered free cash flow means nothing for it. No figure the reader
    # types on that screen repairs the shape of the model, and the screen
    # builds exactly that bridge - so the door is not offered...
    body = client.get(f"/valuation/{LENDERLOSS}").json()
    assert body["speculative_estimate_available"] is False
    assert body["hypothetical_available"] is False
    assert body["hypothetical_placeholders"] is None

    # ...and would refuse if forced.
    r = ask(client, ticker=LENDERLOSS)
    assert r.status_code == 422
    assert r.json()["code"] == "hypothetical_not_applicable"


def test_a_company_the_model_can_value_is_offered_nothing(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()

    assert body["suitable"] is True
    assert "hypothetical_available" not in body

    # And refused for that reason, in those words. The message matters as much
    # as the status: a reader who reached this screen for a company the model
    # values should be sent back to the valuation, not told the model does not
    # fit the company.
    r = ask(client, ticker=PROFITABLE)
    assert r.status_code == 422
    assert "can be valued from its own figures" in r.json()["message"]


def test_a_profitable_lender_is_refused_even_though_it_loses_nothing(client):
    # The case that separates the two reasons a step is withheld. A loss-making
    # lender is caught by the speculative engine's own gate on the way past;
    # this one never reaches it, because there is no loss to route through. So
    # the whitelist is the only thing standing between the reader and a figure
    # built on an enterprise-to-equity bridge that means nothing for a lender.
    refusal = client.get(f"/valuation/{LENDER}").json()
    assert refusal["suitable"] is False
    assert refusal["speculative_estimate_available"] is False
    assert refusal["hypothetical_available"] is False

    r = ask(client, ticker=LENDER)
    assert r.status_code == 422
    assert r.json()["code"] == "hypothetical_not_applicable"
    assert "your assumptions here do not reach" in r.json()["message"]


# ---------------------------------------------------------------------------
# The offer gate itself
#
# The endpoint and the opt-in are judged by the same whitelist, but the opt-in
# has two floors of its own - enough revenue to anchor the arithmetic, and
# enough history to show what an assumption departs from. They cannot be
# reached through a synthetic company, because a company small enough to trip
# them is also too small for the rest of the model to mean anything. So they
# are exercised directly, on a real report with one figure changed.
# ---------------------------------------------------------------------------

def _outspender_report():
    report = analysis.value_company(OUTSPENDER)
    assert report.suitability.codes == ["negative_fcf"], "fixture sanity"
    return report, analysis.fetch_financials(OUTSPENDER)


def test_the_offer_needs_enough_revenue_to_anchor_the_arithmetic():
    report, fin = _outspender_report()
    assert analysis.hypothetical_available_after_dcf(report, fin) is True

    thin = dataclasses.replace(
        report, base=dataclasses.replace(
            report.base, revenue=analysis.MIN_SPECULATIVE_REVENUE_MM))
    assert analysis.hypothetical_available_after_dcf(thin, fin) is False


def test_the_offer_needs_enough_history_to_depart_from():
    report, fin = _outspender_report()

    short = dataclasses.replace(
        fin, income=fin.income[:analysis.MIN_HISTORY_YEARS - 1])
    assert short.years_available < analysis.MIN_HISTORY_YEARS
    assert analysis.hypothetical_available_after_dcf(report, short) is False


def test_a_refusal_whose_reason_carried_no_code_is_not_offered():
    # The codes and the prose reasons are appended together today, so this
    # cannot arise - which is exactly why it is worth pinning. An empty set is
    # a subset of the whitelist, so a reason that arrived without a code would
    # pass a plain subset test and be offered a step for a refusal nobody
    # classified.
    report, fin = _outspender_report()
    uncoded = dataclasses.replace(
        report, suitability=dataclasses.replace(
            report.suitability, codes=[]))
    assert uncoded.suitability.reasons, "fixture sanity: the refusal remains"
    assert analysis.hypothetical_available_after_dcf(uncoded, fin) is False


def test_a_margin_below_the_companys_own_is_called_falling(client):
    """Not every assumption on this route is optimistic.

    A reader can argue that a profitable company's margin erodes, and the
    sentence has to describe the direction it actually goes. It said
    "improving" for both until a mutation run moved the sign.
    """
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    body = ask(client, ticker=OUTSPENDER,
               revenue_growth=start["revenue_growth"],
               target_operating_margin=0.01,
               years_to_target=5).json()

    years = next(c for c in body["reality"] if c["name"] == "years_to_target")
    assert "falling by about" in years["statement"]
    assert "improving" not in years["statement"]
    # And the pace is stated without a minus sign, since the word carries it.
    assert "-" not in years["statement"].split("by about")[1][:10]


def test_an_assumed_improvement_still_says_improving(client):
    body = ask(client, ticker=OUTSPENDER,
               target_operating_margin=0.30, years_to_target=5).json()
    years = next(c for c in body["reality"] if c["name"] == "years_to_target")
    assert "improving by about" in years["statement"]


# ---------------------------------------------------------------------------
# What is held fixed
#
# Growth and the margin are the reader's. Investment is not, and the model
# does not simply keep the company's own rate - for a heavy spender it
# substitutes a mature-business default, which for a company refused FOR
# spending past its profits is the assumption that decides the answer. So the
# note has to say which of the two it did.
# ---------------------------------------------------------------------------

def test_a_substituted_rate_is_named_as_a_default_beside_todays(client):
    note = ask(client, ticker=OUTSPENDER).json()["investment_note"]

    assert "mature-business default, not this company's rate" in note
    assert "10.0% today" in note, "what it actually spends has to be in it"
    assert "5.0% of revenue" in note, "and what the model assumes instead"


def test_a_company_already_at_a_mature_rate_is_not_told_it_is_a_default(client):
    note = ask(client).json()["investment_note"]

    assert "own current rate" in note
    assert "default" not in note


def test_only_the_profitable_route_reaches_these_placeholders():
    """The invariant that lets `hypothetical_placeholders_for` have no
    neutral branch.

    A refusal for losing money has the speculative tier in front of it, so it
    never offers this step from the DCF screen - which means every report
    that reaches that function is on the other route. If the whitelist of
    refusal codes ever widens, this fails, and whoever widened it has to
    decide what the screen should open on rather than finding out from a
    loss-maker shown someone else's starting figures.
    """
    for code in analysis.HYPOTHETICAL_REFUSAL_CODES:
        offered_from_the_dcf_screen = code not in analysis.LOSS_REFUSAL_CODES
        if offered_from_the_dcf_screen:
            assert analysis.hypothetical_framing_for([code]) == \
                "what_would_have_to_be_true", code
