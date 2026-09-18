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

USER_INPUT_NAMES = ("revenue_growth", "target_operating_margin", "years_to_target")


@dataclass
class UserAssumptions:
    """The three drivers, as supplied by the person asking."""
    revenue_growth: float
    target_operating_margin: float
    years_to_target: int


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

    A target margin of zero is not an error the user made - it is the neutral
    placeholder they start from - so it is reported as an unfinished
    hypothetical rather than a bad request.
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

    if user.target_operating_margin <= 0:
        raise HypotheticalRefused(
            "This hypothetical has no profit in it yet. A path to profitability needs a "
            "target operating margin above 0%, and choosing it is the point of this "
            "screen: nothing here will pick one for you.",
            code="hypothetical_incomplete")
