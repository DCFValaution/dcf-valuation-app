"""
The user-built hypothetical: the last opt-in, and the weakest claim in the app.

WHAT THIS IS FOR
----------------
A company like Intel loses money AND is shrinking. The speculative engine
refuses it, correctly: that engine extends a company's own trajectory into
profit, and a shrinking company has no growth to extend. Deriving one anyway
would mean inventing the turnaround and then presenting it as the model's
finding.

So this path does not derive it. The user supplies the three drivers - revenue
growth, the target operating margin, and the years to reach it - and the
engine does the arithmetic on THEIR numbers. That is the whole difference, and
every honesty obligation here follows from it:

  * the figure is never called a valuation, an intrinsic value, or even a
    speculative estimate. It is a hypothetical the user built;
  * the company's real figures travel beside every input, so an assumed
    turnaround is always shown against the trajectory it contradicts;
  * nothing is pre-filled with a flattering number. The placeholders are
    deliberately neutral - no growth, no profit - and a hypothetical cannot be
    computed until the user has chosen a profit to project, which forces the
    assumption to be theirs rather than ours.

WHAT IT REFUSES ANYWAY
----------------------
A company with no revenue is still refused, and so is one with revenue too
small to anchor anything, or too short a history to contrast against. There is
nothing to grow from and nothing to contradict, so the output would be
arithmetic on two numbers the user typed - fiction rather than a hypothetical
about a real company.
"""

from dataclasses import dataclass

# The placeholders the app starts from. Neutral by construction: no growth,
# no profit, and a mid-range horizon. The margin deliberately cannot produce a
# figure - the user must choose a profit before anything is computed.
NEUTRAL_REVENUE_GROWTH = 0.0
NEUTRAL_TARGET_MARGIN = 0.0
NEUTRAL_YEARS_TO_TARGET = 5

# What a person may assume. Wide, because it is their hypothetical, but not
# unbounded: past these the arithmetic stops describing a company at all.
REVENUE_GROWTH_BOUNDS = (-0.50, 1.00)
TARGET_MARGIN_BOUNDS = (0.00, 0.60)
YEARS_TO_TARGET_BOUNDS = (1, 15)

HYPOTHETICAL_HEADLINE = "A HYPOTHETICAL YOU BUILT - NOT A VALUATION"

# Two routes reach this screen, and they are not the same question.
#
#   PATH_TO_PROFIT is the original: a company that loses money, whose own
#   figures point away from the profit being assumed. Everything starts
#   neutral - no growth, no profit - because any pre-filled figure would be
#   the app suggesting a turnaround on the reader's behalf.
#
#   WHAT_WOULD_HAVE_TO_BE_TRUE is the second, and Tesla is the case. It makes
#   a profit; it was refused because its investment outspends that profit.
#   There is no turnaround to assume and nothing pointing the other way, so
#   the loss-maker's framing is simply false about it - and starting from zero
#   growth and zero margin would be a worse lie than starting from the
#   company's own figures, which is where the question actually begins: what
#   would have to be true about growth, margins and investment for this to be
#   worth something?
#
# So on that route the screen opens on the company's own current figures,
# labelled as its own, and the reader changes what they want to argue with.
FRAMING_PATH_TO_PROFIT = "path_to_profit"
FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE = "what_would_have_to_be_true"

USER_INPUT_NAMES = ("revenue_growth", "target_operating_margin",
                    "years_to_target", "capex_pct")

# What a person may assume about capital investment, as a share of revenue.
# Zero is allowed: a company that stops investing is a real thing to assume,
# and the arithmetic handles it. The ceiling is well above any sustained
# rate - Rivian's build-out ran at 23% - because the point of the control is
# to let someone argue that investment stays high, not only that it falls.
CAPEX_PCT_BOUNDS = (0.00, 0.50)


@dataclass
class UserAssumptions:
    """The four drivers, as supplied by the person asking.

    `capex_pct` is the share of revenue the company is assumed to settle at
    once the target margin is reached; investment glides there from what it
    spends today, on the same path and over the same years as the margin.

    None means "whatever the company spends today" - the company's own
    figure, not a number this code chose. That distinction is the whole
    reason the field is optional rather than defaulted to a constant: a
    mature-business default of 5% used to be substituted silently here, and
    for a company refused FOR spending past its profits that one substitution
    decided the answer.
    """
    revenue_growth: float
    target_operating_margin: float
    years_to_target: int
    capex_pct: float | None = None


class HypotheticalRefused(Exception):
    """Even a user-built hypothetical does not apply to this company."""

    def __init__(self, message: str, reasons: list[str] | None = None,
                 code: str = "hypothetical_not_applicable"):
        super().__init__(message)
        self.reasons = list(reasons or [])
        self.code = code


def validate(user: UserAssumptions) -> None:
    """
    Check the three inputs before anything is computed.

    Bounds only: these are properties of the controls, so they can be
    checked before anything about the company is known or fetched.

    Whether a profit has been assumed is NOT checked here. A target margin of
    zero is not an error the user made - on the loss-maker route it is the
    neutral placeholder they start from - and the message explaining it is
    written per route, so it lives in `require_a_profit`, which the caller
    runs once it knows which route this is.
    """
    low, high = REVENUE_GROWTH_BOUNDS
    if not low <= user.revenue_growth <= high:
        raise HypotheticalRefused(
            f"Assumed revenue growth must be between {low:.0%} and {high:.0%} a year. "
            "Outside that the arithmetic stops describing a company.",
            code="hypothetical_out_of_bounds")

    low, high = YEARS_TO_TARGET_BOUNDS
    if not low <= user.years_to_target <= high or int(user.years_to_target) != user.years_to_target:
        raise HypotheticalRefused(
            f"Years to the target margin must be a whole number from {low} to {high}.",
            code="hypothetical_out_of_bounds")

    _, high = TARGET_MARGIN_BOUNDS
    if user.target_operating_margin > high:
        raise HypotheticalRefused(
            f"A target operating margin above {high:.0%} is higher than all but a "
            "handful of companies have ever sustained.",
            code="hypothetical_out_of_bounds")

    low, high = CAPEX_PCT_BOUNDS
    if user.capex_pct is not None and not low <= user.capex_pct <= high:
        raise HypotheticalRefused(
            f"Assumed capital investment must be between {low:.0%} and {high:.0%} of "
            "revenue. Outside that the arithmetic stops describing a company.",
            code="hypothetical_out_of_bounds")



def require_a_profit(user: UserAssumptions,
                     framing: str = FRAMING_PATH_TO_PROFIT) -> None:
    """No profit assumed, so there is nothing to put a value on.

    Separate from the bounds checks because it is the one message written per
    route, and the route is not known until the company has been looked up -
    while the bounds are a property of the controls and can be checked before
    anything is fetched.
    """
    if user.target_operating_margin <= 0:
        raise HypotheticalRefused(
            ("A company earning nothing on its sales produces no cash to put a value on, so "
             "there is no figure at this margin. Move it above 0% to see what your "
             "assumption implies."
             if framing == FRAMING_WHAT_WOULD_HAVE_TO_BE_TRUE else
             "This hypothetical has no profit in it yet. A path to profitability needs a "
             "target operating margin above 0%, and choosing it is the point of this "
             "screen: nothing here will pick one for you."),
            code="hypothetical_incomplete")
