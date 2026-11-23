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


def opening(placeholders: dict) -> dict:
    """The screen's starting values, as query parameters."""
    return {"revenue_growth": placeholders["revenue_growth"],
            "target_operating_margin": placeholders["target_operating_margin"],
            "years_to_target": int(placeholders["years_to_target"]),
            "capex_pct": placeholders["capex_pct"]}


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
    # Offered, never pre-computed: growth and margin start neutral and no
    # figure appears anywhere in the refusal.
    start = body["hypothetical_placeholders"]
    assert start["revenue_growth"] == NEUTRAL_REVENUE_GROWTH
    assert start["target_operating_margin"] == NEUTRAL_TARGET_MARGIN
    assert start["years_to_target"] == float(NEUTRAL_YEARS_TO_TARGET)
    # Investment is the exception, and deliberately so: there is no neutral
    # rate of capital spending, and a constant here would be the engine's
    # old 5% substitution wearing a different hat. It starts at what the
    # company actually spends.
    assert start["capex_pct"] > 0
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
        # Not supplied, so the company's own rate was used and is reported
        # back: a caller reading this never has to guess what ran.
        "capex_pct": body["your_assumptions"]["capex_pct"],
    }
    assert body["your_assumptions"]["capex_pct"] > 0
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


def test_the_disclaimer_is_one_sentence_in_normal_case(client):
    """It was a paragraph opening in capitals, and it was read by nobody.

    It sat under three bullets that said the same thing in fewer words and
    in the second person, so the top half of the screen was a wall of red
    shouting - which is how a warning stops being a warning. The headline
    and the bullets carry the force; this says the one thing they do not,
    which is what the figure actually rests on.
    """
    body = ask(client).json()
    disclaimer = body["disclaimer"]

    assert body["disclaimer_headline"] == "A HYPOTHETICAL YOU BUILT - NOT A VALUATION"
    # One sentence. Counted by sentence boundaries rather than full stops,
    # because the figures in it have decimal points.
    assert disclaimer.rstrip().count(". ") == 0, disclaimer
    assert len(disclaimer) < 300, len(disclaimer)
    assert disclaimer.upper() != disclaimer
    assert "THIS IS NOT A VALUATION" not in disclaimer

    # It still names whose assumptions these are, all four of them, and
    # refuses both of the things a reader might mistake it for.
    assert "your assumptions" in disclaimer
    assert "+8.0%" in disclaimer and "15.0%" in disclaimer
    assert "not a valuation" in disclaimer
    assert "not a price target" in disclaimer


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
    # Its refusal is now the terminal-dominance rule rather than the
    # last-forecast-year one: over ten years, with depreciation allowed to
    # catch up, a heavy spender's final year does turn positive. What has
    # not changed is that it is refused, and refused for something other
    # than losing money.
    assert any("Terminal value would be" in reason
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
    start = spec["hypothetical_placeholders"]
    assert start["revenue_growth"] == NEUTRAL_REVENUE_GROWTH
    assert start["target_operating_margin"] == NEUTRAL_TARGET_MARGIN
    assert start["years_to_target"] == float(NEUTRAL_YEARS_TO_TARGET)


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


def test_assumptions_that_support_no_value_say_so_rather_than_print_one(client):
    """A negative number here would be the refusal restated with a decimal
    point on it, and a reader would read it as a price target.

    Reached by assuming the company earns almost nothing and spends heavily,
    which is a thing a reader can assume and a thing the arithmetic cannot
    value. The screen names the two levers rather than stopping.
    """
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    r = ask(client, ticker=OUTSPENDER,
            **{**opening(start), "target_operating_margin": 0.01,
               "capex_pct": 0.45})

    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "hypothetical_incomplete"
    assert "no value to show" in body["message"]
    assert "Raise the margin" in body["message"]
    assert "lower the investment" in body["message"]


def test_its_own_figures_now_do_support_one(client):
    """They did not, while depreciation was pinned to today's rate.

    A company building out at 20% of revenue was assumed to keep
    depreciating the much smaller asset base it has now, forever - so
    investment looked permanently expensive and a heavy spender could never
    be worth anything. Depreciation rising toward investment is what makes
    the opening figure a figure.
    """
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    r = ask(client, ticker=OUTSPENDER, **opening(start))

    assert r.status_code == 200, r.json()
    assert r.json()["hypothetical_value_per_share"] > 0


def test_moving_either_lever_produces_one(client):
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]

    richer = ask(client, ticker=OUTSPENDER,
                 **{**opening(start), "target_operating_margin": 0.30})
    assert richer.status_code == 200, richer.json()
    assert richer.json()["hypothetical_value_per_share"] > 0

    leaner = ask(client, ticker=OUTSPENDER,
                 **{**opening(start), "capex_pct": 0.02})
    assert leaner.status_code == 200, leaner.json()
    assert leaner.json()["hypothetical_value_per_share"] > 0


def test_the_no_profit_message_is_written_for_the_route(client):
    profitable = ask(client, ticker=OUTSPENDER,
                     target_operating_margin=0).json()
    assert profitable["code"] == "hypothetical_incomplete"
    assert "earning nothing on its sales" in profitable["message"]
    assert "path to profitability" not in profitable["message"]

    loss = ask(client, target_operating_margin=0).json()
    assert loss["code"] == "hypothetical_incomplete"
    assert "path to profitability" in loss["message"]


def test_investment_is_the_readers_to_set_and_actually_moves_the_figure(client):
    """It was held fixed, and worse than fixed: the engine substituted a 5%
    mature rate for whatever the company spends. For a company refused FOR
    spending past its profits, that decided the answer.

    The first attempt at a slider changed nothing, because the engine also
    set depreciation equal to capex once mature, so the two cancelled in the
    cash flow. Both halves are needed for the control to mean anything, and
    this holds the pair.
    """
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]

    heavy = ask(client, ticker=OUTSPENDER,
                **{**opening(start), "target_operating_margin": 0.30,
                   "capex_pct": 0.20}).json()
    light = ask(client, ticker=OUTSPENDER,
                **{**opening(start), "target_operating_margin": 0.30,
                   "capex_pct": 0.02}).json()

    assert heavy["your_assumptions"]["capex_pct"] == 0.20
    assert light["your_assumptions"]["capex_pct"] == 0.02
    assert light["hypothetical_value_per_share"] >         heavy["hypothetical_value_per_share"],         "spending more on investment has to cost something"


def test_assuming_no_change_is_not_called_a_contradiction(client):
    """The reader has assumed exactly what the company earns. Telling them
    "nothing in its figures says that happens" is a statement about its own
    figures, and it is false."""
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    # The margin is left exactly where the company has it; investment is
    # lowered, which is what makes a figure appear at all.
    body = ask(client, ticker=OUTSPENDER,
               **{**opening(start), "capex_pct": 0.02}).json()

    years = next(c for c in body["reality"] if c["name"] == "years_to_target")
    assert years["contradicts"] is False
    assert "does nothing yet" in years["statement"]
    assert "Nothing in" not in years["statement"]


def test_the_disclaimer_names_all_four_drivers_on_this_route(client):
    """Short, but not vague. One sentence that says what the figure rests
    on - including the investment rate, which is the input this company was
    refused over."""
    text = ask(client, ticker=OUTSPENDER, capex_pct=0.02).json()["disclaimer"]

    assert "your assumptions" in text
    assert "2.0% of revenue invested" in text
    assert "not a valuation" in text and "not a price target" in text


def test_the_offer_is_honoured_and_not_refused_a_step_later(client):
    """The door opens on something. The figure may be absent where the
    company's own numbers support none, but the screen must serve the route
    rather than refuse it as not applicable."""
    r = ask(client, ticker=OUTSPENDER)

    assert r.status_code == 200, r.json()
    assert r.json()["hypothetical_value_per_share"] is not None


def test_the_disclaimer_does_not_call_a_profitable_company_a_loss_maker(client):
    """The loss route's clause is a statement about the company's own
    figures, and it is false of this one."""
    text = ask(client, ticker=OUTSPENDER).json()["disclaimer"]
    assert "actually earned" not in text

    # The loss route still carries it, naming what the company really did.
    loss = ask(client).json()["disclaimer"]
    assert "which actually earned -30.0% last year" in loss


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
    assert report.suitability.codes == ["terminal_value_dominates"],         "fixture sanity"
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
               **{**opening(start), "target_operating_margin": 0.01,
                  "capex_pct": 0.0}).json()

    years = next(c for c in body["reality"] if c["name"] == "years_to_target")
    assert "falling by about" in years["statement"]
    assert "improving" not in years["statement"]
    # And the pace is stated without a minus sign, since the word carries it.
    assert "-" not in years["statement"].split("by about")[1][:10]


def test_an_assumed_improvement_still_says_improving(client):
    body = ask(client, ticker=OUTSPENDER,
               target_operating_margin=0.30, years_to_target=5,
               capex_pct=0.02).json()
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

def test_no_mature_default_is_substituted_for_the_companys_own_rate(client):
    """The substitution this release removes.

    The engine replaced a heavy spender's capital investment with 5% of
    revenue, called it "typical of a mature business", and left the figure
    resting on it. Nothing is substituted now: where the caller says
    nothing, the company's own rate is used and reported back.
    """
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    derived = {a["name"]: a["value"]
               for a in client.get(f"/valuation/{OUTSPENDER}").json()["assumptions"]}

    assert start["capex_pct"] == pytest.approx(derived["capex_pct"])
    assert start["capex_pct"] > 0.05, (
        "fixture sanity: this company spends well above the old default")

    used = ask(client, ticker=OUTSPENDER,
               **{**opening(start), "target_operating_margin": 0.30}
               ).json()["your_assumptions"]["capex_pct"]
    assert used == pytest.approx(start["capex_pct"])
    assert used != pytest.approx(0.05)


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


def test_omitting_investment_uses_the_companys_own_rate_not_a_constant(client):
    """The call that exercises the fallback.

    Every other test here sends an investment rate, so none of them touches
    the branch that decides what happens when a caller sends none - and that
    branch is exactly where a convenient constant would hide.
    """
    start = client.get(f"/valuation/{OUTSPENDER}").json()[
        "hypothetical_placeholders"]
    body = ask(client, ticker=OUTSPENDER, target_operating_margin=0.30).json()

    used = body["your_assumptions"]["capex_pct"]
    assert used == pytest.approx(start["capex_pct"])
    assert used != pytest.approx(0.05), (
        "the 5% mature default is what this replaced")
