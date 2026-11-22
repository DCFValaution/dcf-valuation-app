"""How investment and depreciation move across a forecast.

THE PROBLEM THIS SOLVES
-----------------------
A forecast has to say what a company spends on its assets and what those
assets lose in value, and the two are not independent: assets bought in year
three depreciate in years four onward. Treating them as independent produces
one of two errors, and the model has made both.

  * Depreciation pinned to today's rate. A company building out at 24% of
    revenue is assumed to keep depreciating at the rate of the much smaller
    asset base it has now, forever. Investment then looks permanently
    expensive, and a heavy spender can never be worth anything.

  * Depreciation set equal to investment. Then net investment is zero, the
    assets maintain themselves, and growth is free. This is the one that
    shipped: it made the capital-investment control literally inert, because
    whatever rate was set, the same rate was subtracted and added back.

Neither is a steady state. A real one has investment settling at a level and
depreciation rising to a fixed fraction of it, the shortfall being the net
investment that buys the growth.

WHAT IS ASSUMED, AND FROM WHERE
-------------------------------
The fraction is not invented. Damodaran publishes, per industry, gross
capital expenditure and depreciation as shares of revenue; their ratio is
how much of its investment a mature company in that industry covers out of
depreciation. For Auto & Truck he publishes 1.49 dollars spent per
dollar depreciated, so depreciation covers about two thirds of investment -
leaving net investment of 2.1% of revenue, which is what the industry
actually runs at.

So: investment glides to wherever it is set or derived, depreciation glides
to that level times the industry's cover, and the gap between them is the
net investment the company keeps paying for its growth.

THIS IS DELIBERATELY A SEPARATE MODULE
--------------------------------------
The same question arises in three places - the speculative path, the
user-built hypothetical, and the main DCF's own forecast - and they had three
different silent answers. One mechanism, one set of assumptions, one place to
argue with them.
"""
from __future__ import annotations

from dataclasses import dataclass

from industry_capex_data import INDUSTRY_CAPEX, TOTAL_MARKET, IndustryCapex

# How much of its gross investment a mature company covers out of
# depreciation, clamped.
#
# The ceiling is the important one. An industry whose depreciation exceeds
# its capital expenditure is one shrinking its asset base - true of a few
# rows in any year, and a real description of a declining business, but not a
# steady state a going concern can be valued on. Allowing it would mean
# depreciation adding more cash than investment consumes, forever.
#
# The floor stops an industry in the middle of a build-out from setting a
# permanent assumption that almost nothing is ever covered.
DEPRECIATION_COVER_CLAMP = (0.50, 0.95)

# Where the cover is unusable - an industry with no capital expenditure to
# speak of, or none on file - the whole market's own ratio stands in, and
# the provenance says so.
FALLBACK_INDUSTRY = TOTAL_MARKET


@dataclass(frozen=True)
class MatureInvestment:
    """What investment and depreciation settle at, and why.

    `capex_pct` and `da_pct` are shares of revenue. `detail` is the sentence
    a reader sees; it names the industry and the figures, because an
    assumption nobody can trace is an assumption nobody can argue with.
    """

    capex_pct: float
    da_pct: float
    cover: float
    industry: str
    detail: str

    @property
    def net_capex_pct(self) -> float:
        """What the company keeps paying, once mature."""
        return self.capex_pct - self.da_pct


# What a mature company in an industry with no usable figures invests, net
# of depreciation, as a share of revenue. The whole market's own rate.
def _fallback() -> IndustryCapex:
    return INDUSTRY_CAPEX[FALLBACK_INDUSTRY]


# The most any company is assumed to spend on investment indefinitely, as a
# share of revenue.
#
# A ceiling is needed because a build-out rate is not a steady state:
# Lucid spends 72% of revenue on capital investment and Rivian 24%, and
# carrying either forward forever says the company never finishes building
# the thing it is building.
#
# 20% rather than a rounder number because it is above the highest industry
# AVERAGE in Damodaran's table - Semiconductor, at 20.9% - so it moderates
# only companies that are outliers against their own industry, and leaves
# every ordinary spender on its own figure.
MATURE_CAPEX_CEILING = 0.20


def depreciation_cover(industry: IndustryCapex | None) -> tuple[float, str]:
    """(cover, industry name) - the share of investment depreciation covers.

    Damodaran publishes capital expenditure per dollar of depreciation, so
    the cover is simply its reciprocal; nothing is divided by a figure that
    might be near zero.

    Falls back to the whole market where an industry has no usable ratio,
    which is an honest "this company's industry is unknown" rather than a
    guess at one.
    """
    if industry is None or industry.capex_to_depreciation <= 0:
        industry = _fallback()
    raw = 1.0 / industry.capex_to_depreciation
    low, high = DEPRECIATION_COVER_CLAMP
    return (min(max(raw, low), high), industry.name)


def derived_mature_capex(own_capex_pct: float, own_da_pct: float) -> float:
    """What a company settles at, from its own figures.

    Anchored on the company and not on its industry's level, for a reason
    worth stating: Damodaran's two published columns are on DIFFERENT bases.
    `Cap Ex/Deprecn` is property, plant and equipment; `Net Cap Ex/Sales`
    adds net R&D and acquisitions. Combining them to derive a gross rate
    gives Pharmaceuticals a capital intensity of 22% of revenue and
    Healthcare Products one of 35%, which is an artefact of the mixture
    rather than a fact about either industry.

    So the industry supplies only the structural relationship - how much of
    its investment depreciation covers, which is a pure PP&E ratio - and the
    level comes from what the company itself spends, moderated by the
    ceiling above.

    Floored at the company's own depreciation, because a business that
    spends less than its assets lose is shrinking, and a forecast that
    assumes it does so forever has stopped describing a going concern.
    """
    return min(max(own_capex_pct, own_da_pct, 0.0), MATURE_CAPEX_CEILING)


def mature_investment(capex_pct: float,
                      industry: IndustryCapex | None,
                      *, chosen_by: str) -> MatureInvestment:
    """Where investment and depreciation end up.

    `capex_pct` is the gross rate investment settles at - supplied by the
    reader, or derived. `chosen_by` says which, in words, so the sentence
    this returns reads correctly either way.
    """
    cover, name = depreciation_cover(industry)
    da = capex_pct * cover
    return MatureInvestment(
        capex_pct=capex_pct,
        da_pct=da,
        cover=cover,
        industry=name,
        detail=(
            f"investment settles at {capex_pct:.1%} of revenue ({chosen_by}), "
            f"with depreciation rising to {da:.1%} - {cover:.0%} of it, "
            f"which is what {name} companies cover out of depreciation "
            f"(Damodaran). The {capex_pct - da:.1%} gap is the net "
            f"investment the growth is paid for with."),
    )


def hypothetical_capex_bounds() -> tuple[float, float]:
    """What a reader may assume about investment, for callers that need the
    pair without importing the hypothetical's own module."""
    from hypothetical import CAPEX_PCT_BOUNDS

    return CAPEX_PCT_BOUNDS


def glide(start: float, end: float, year: int, years: int) -> float:
    """A straight line from *start* to *end*, reaching *end* at *years*.

    Year 0 is the starting point and is never asked for; year `years` is the
    mature level exactly. A forecast of no length returns the end, because
    there is nowhere for the glide to happen and the mature level is the
    honest answer rather than the starting one.
    """
    if years <= 0:
        return end
    progress = min(max(year / years, 0.0), 1.0)
    return start + (end - start) * progress


@dataclass(frozen=True)
class InvestmentPath:
    """Investment and depreciation, year by year, as shares of revenue."""

    capex: list[float]
    da: list[float]
    mature: MatureInvestment

    def net(self, year: int) -> float:
        """Net investment in a year, as a share of revenue."""
        return self.capex[year] - self.da[year]


def investment_path(*, capex_start: float, da_start: float,
                    mature: MatureInvestment, years: int) -> InvestmentPath:
    """The whole forecast's investment and depreciation.

    Both glide in a straight line, but to different ends: investment to the
    mature rate, depreciation to its covered share of that rate. They
    converge without meeting, which is the point - the distance between them
    at the end is the net investment that never goes away.
    """
    capex = [glide(capex_start, mature.capex_pct, t, years)
             for t in range(1, max(years, 0) + 1)]
    da = [glide(da_start, mature.da_pct, t, years)
          for t in range(1, max(years, 0) + 1)]
    return InvestmentPath(capex=capex, da=da, mature=mature)
