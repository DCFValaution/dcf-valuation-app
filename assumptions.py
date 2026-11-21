"""
Derives DCF assumptions from a company's own historical financials.

The DCF engine (dcf.py) is deliberately dumb: it applies whatever assumptions
it is handed. Handing every company Apple's 32% operating margin produces
confident nonsense for companies that look nothing like Apple. This module
reads each company's actual recent history and derives a starting point from
it.

WHAT IS DERIVED vs DEFAULTED
----------------------------
Derived from the company's own filings:
    revenue_growth      revenue CAGR over a fixed three-year window
    operating_margin    median operatingIncome / revenue
    tax_rate            median incomeTaxExpense / incomeBeforeTax
    da_pct              median D&A / revenue
    capex_pct           median |capitalExpenditure| / revenue
    nwc_pct             aggregate change in NWC / change in revenue

    wacc                CAPM cost of equity and actual cost of debt, weighted
                        by the company's capital structure (see _derive_wacc)

Global defaults (not company-specific):
    terminal_growth     long-run nominal growth; no company grows faster than
                        the economy forever
    projection_years    modelling convention

MEDIAN, NOT MEAN
----------------
Ratios use the median of recent years rather than the average, because single
years are routinely distorted by one-offs. Apple's FY2024 effective tax rate
was 24.1% because of a one-time EU State Aid charge, against ~15% in adjacent
years; the mean would carry that distortion into every projected year, the
median discards it.

CAGR FOR GROWTH
---------------
Growth uses endpoint-to-endpoint CAGR rather than the latest year, which
would let a single strong or weak year set the trajectory for the entire
projection.

Over a FIXED three-year window, FY-3 to the latest fiscal year, and not over
however much history happened to arrive. That distinction turned out to
matter more than the choice of CAGR itself: the window used to be "as many
years as the source gave", so the same company got a different growth
assumption depending on how much history its data source returned. Moving
the statements to SEC EDGAR, which files five years where the aggregator
returns four, changed Exxon's growth from -6.7% to +4.68% and Coinbase's
from +31% to -2.2% - because the extra year was 2021, Exxon's post-COVID
trough and Coinbase's crypto-boom peak. Neither number was wrong; the
window was.

A company with less history than that uses what it has and the provenance
says how much, because three years of a two-year-old listing do not exist
and inventing them would be worse than measuring what is there.

CLAMPING
--------
Derived values are clamped to defensible ranges. Short histories and one-off
years can produce ratios that are arithmetically correct but useless as
forward assumptions (a company that tripled revenue off a tiny base does not
sustain 200% growth for five years). Every clamp is recorded in the
provenance, so a clamped value is never silently passed off as observed.
"""

from dataclasses import dataclass, field
from datetime import date
from statistics import median

from dcf import FADE_EXPONENTIAL, FADE_LINEAR, Assumptions
from industry_betas import (MAX_DEBT_TO_EQUITY, as_of_text, industry_for,
                            relevered_beta)
from market_data import (CompanyFinancials, fetch_risk_free_rate, num,
                         reported, total_debt_of)

# --- Global defaults ------------------------------------------------------
DEFAULT_WACC = 0.085             # only used when WACC derivation fails
# NOT long-run nominal GDP growth, whatever this comment used to say - the
# Fed's own longer-run projections put that at about 4.0% (see
# LONG_RUN_NOMINAL_GROWTH below). This is a conservative stand-in growth
# rate used in five unrelated places: the DCF's revenue-growth fallback
# when history is too thin to measure, the DCF's terminal ceiling when no
# risk-free rate can be had, the dividend model's growth fallback and its
# terminal growth, and the speculative model's growth floor and terminal
# growth. Those are different questions with different right answers, and
# they share a number only by history. Changing it moves all five.
DEFAULT_TERMINAL_GROWTH = 0.025
DEFAULT_PROJECTION_YEARS = 5

# How far below the discount rate the derived terminal growth is held.
#
# The Gordon perpetuity divides by (WACC - g), so the two converging sends
# the terminal value towards infinity. A margin is therefore not a matter of
# taste: without one, a low-WACC company whose risk-free rate sits close to
# its cost of capital would produce an enormous figure from a rounding
# distance.
#
# 1.5 points rather than something smaller because the sensitivity grid
# moves g by up to a full point above the model's own figure. A margin of
# one point or less would put the grid's top row at or past WACC, and a
# table whose own cells are undefined is a poor way to show how sensitive a
# figure is. At 1.5 the grid's highest growth still clears WACC by half a
# point.
# Long-run US nominal growth: real GDP plus inflation, both taken from the
# FOMC's own longer-run medians rather than estimated here.
#
#   Change in real GDP, longer run   2.0%   (central tendency 2.0-2.2%)
#   PCE inflation, longer run        2.0%   (central tendency 2.0%)
#
# Summary of Economic Projections, 16 September 2026, table 1:
# federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm
#
# It matters because a company cannot outgrow its economy forever - at a
# growth rate above this one it eventually becomes the economy - and with
# the ten-year Treasury at 5.07%, the old ceiling of "the risk-free rate"
# was above it for every company. The same projections put the longer-run
# federal funds rate at 3.2%, so today's ten-year is above where the Fed
# expects even the policy rate to settle.
#
# Configured, not fetched, for the reason the equity risk premium is: a
# valuation that changes because someone else's page changed is not
# reproducible.
LONG_RUN_NOMINAL_GROWTH = 0.040
LONG_RUN_GROWTH_AS_OF = date(2026, 9, 16)

# The SEP is published quarterly, so a figure older than this means one was
# missed rather than merely not yet due.
SEP_STALE_AFTER_DAYS = 100

# How far terminal growth is held below the discount rate.
#
# Raised from 1.5%, which held the line but not much more: the Gordon
# denominator is (WACC - g), so at a 1.6-point spread - Exxon's, Johnson &
# Johnson's - a half-point error in either input moves the terminal value
# by about a fifth, and the terminal value was already nine tenths of the
# answer.
#
# Three points is a modelling judgement rather than a published standard,
# and is described as one wherever it appears. It is wide enough that an
# ordinary error in WACC does not dominate the result, and narrow enough
# that it only binds for companies whose cost of capital is genuinely close
# to the economy's growth rate.
TERMINAL_GROWTH_SAFETY_MARGIN = 0.030

# How far outside its stated range a supplied value may fall before it is
# refused. One basis point: wide enough to absorb a client that rounds the
# range it was given to two decimal places before echoing a value back,
# narrow enough that nothing economically meaningful passes through it.
_RANGE_TOLERANCE = 1e-4


def terminal_growth_ceiling(risk_free_rate: float, wacc: float) -> float:
    """The highest long-run growth this company can be given, and why.

    Three limits, whichever binds first:

      the risk-free rate       the rate environment, as before
      long-run nominal growth  no company outgrows its economy forever
      WACC less the margin     the perpetuity has to mean something

    The second is new and binds most often today, because a 5.07% ten-year
    is above the 4.0% the Fed projects for long-run nominal growth. The
    third binds for companies whose cost of capital is close to that growth
    rate - a low-beta staple, a utility - where the first two would leave a
    denominator too thin to carry nine tenths of the valuation.
    """
    return min(risk_free_rate, LONG_RUN_NOMINAL_GROWTH,
               wacc - TERMINAL_GROWTH_SAFETY_MARGIN)


def derived_terminal_growth(risk_free_rate: float, wacc: float) -> float:
    """Kept as the old name for the ceiling."""
    return terminal_growth_ceiling(risk_free_rate, wacc)


def _today_for_sep() -> date:
    return _today()


def sep_age_days(today: date | None = None) -> int:
    """Days since the projections the growth ceiling is taken from."""
    return ((today or _today()) - LONG_RUN_GROWTH_AS_OF).days


def sep_is_stale(today: date | None = None) -> bool:
    return sep_age_days(today) > SEP_STALE_AFTER_DAYS


def long_run_growth_source(today: date | None = None) -> str:
    """Where the figure came from, and whether it is due a refresh."""
    text = (f"long-run US nominal growth (Fed SEP, "
            f"{LONG_RUN_GROWTH_AS_OF:%b %Y}: 2.0% real GDP + 2.0% PCE "
            f"inflation)")
    if sep_is_stale(today):
        text += (f" - those projections are {sep_age_days(today)} days old "
                 "and a newer set is due")
    return text

# The growth fade is opt-in and off by default, so a valuation nobody has
# touched is the flat projection it has always been.
DEFAULT_FADE_ENABLED = False
DEFAULT_FADE_START_YEAR = 3
DEFAULT_FADE_PATTERN = "linear"
FALLBACK_TAX_RATE = 0.21         # US federal statutory, when no taxed year exists

# When the reported provisions stop describing a rate and start describing
# one-off events. Both must hold together: a low rate on a steady series is
# a real tax position - Simon Property pays 0.85%, Frontline 1.5%, Realty
# Income 7.1%, every one of them within three points year to year - while a
# low rate on a series that swings by a full turn of revenue is an average
# of unrelated accidents. Qualcomm is the case: -44.8%, +56.2%, +2.2%,
# median 2.2%. The gap between the widest steady series (2.4 points) and
# the narrowest lumpy one (89.5 points) is wide enough that the exact
# thresholds are not load-bearing.
TAX_IMPLAUSIBLY_LOW = 0.05
TAX_VOLATILE_SPREAD = 0.25

# The same test the other way up. A median above half of pre-tax income is
# usually a real rate - BP pays 81.9% on the UK Energy Profits Levy and
# Norway's 78% petroleum rate - but it is equally the shape a year of
# impairments leaves behind, and nothing distinguishes the two from the
# ratio alone. The published rate does: it strips unusual items, so where
# the two disagree by a wide margin the reported figure is carrying
# something that is not tax policy. Ordinary disagreement between the two
# runs under five points across the companies measured (Exxon 4.7, 3M 3.6,
# Rio Tinto 1.9); BP's is 57.
TAX_IMPLAUSIBLY_HIGH = 0.50
TAX_NORMALIZED_DISAGREEMENT = 0.15

# --- WACC inputs ----------------------------------------------------------
# The risk-free rate is fetched live from the Treasury curve; this static
# value is only the fallback when both that and the aggregator's quote are
# unavailable.
#
# It carries a date, like the equity risk premium and the long-run growth
# figure, because the previous version did not and drifted ninety basis
# points below the curve without anything noticing. That was found by
# accident: a BABA valuation came out at $223.00 instead of $189.31 - 17.8%
# higher - because a rate lookup failed and this constant answered in its
# place, and nothing in the output distinguished the two runs.
#
# Set to the last published ten-year par yield rather than to a long-run
# average on purpose. A fallback that is a round number invites being left
# alone; one that is a dated observation reads as something that expires.
FALLBACK_RISK_FREE_RATE = 0.0515
FALLBACK_RISK_FREE_AS_OF = date(2026, 11, 13)

# Treasury publishes the curve every business day, so a stored rate is
# always "not the latest". That is not what this threshold is for. It marks
# the point at which the stored rate is old enough to be materially wrong:
# the ten-year moves of the order of ten to twenty basis points a month, so
# a month puts it within ordinary noise of the curve and a quarter does not.
RISK_FREE_STALE_AFTER_DAYS = 30

RISK_FREE_TENOR = "year10"  # matched to a ~perpetual horizon, not a short bill

# Equity risk premium: the excess return demanded over the risk-free rate.
# Held as a global default because credible ERP estimates come from academic
# and practitioner surveys (Damodaran, Fernandez et al.), not from company
# filings. Mainstream estimates for developed markets cluster around 4.5-6%.
# Damodaran's implied equity risk premium: what the S&P 500's own price
# and expected cash flows imply investors are demanding, rather than what
# they demanded on average since 1928. Published monthly at
# pages.stern.nyu.edu/~adamodar, and the headline measure of the five he
# gives - trailing twelve month, with adjusted payout.
#
# Configured rather than fetched. A valuation that silently changes because
# someone else's web page changed is not reproducible, and a scrape that
# fails at the wrong moment would leave the model with no premium at all.
# The cost is that this goes stale, which is why the date travels with it
# and the app says so.
#
# It replaces a hard-coded 5.5%, which was a reasonable long-run average and
# 1.09 points above what the market is actually pricing.
DAMODARAN_IMPLIED_ERP = 0.0441
DAMODARAN_ERP_AS_OF = date(2026, 11, 1)

# He republishes at the start of each month, so a figure more than two
# months old means an update was missed rather than merely not yet due.
ERP_STALE_AFTER_DAYS = 60

DEFAULT_EQUITY_RISK_PREMIUM = DAMODARAN_IMPLIED_ERP


def _today() -> date:
    """Indirection so a test can age the premium without waiting."""
    return date.today()


def erp_age_days(today: date | None = None) -> int:
    """Days since the configured premium was published."""
    return ((today or _today()) - DAMODARAN_ERP_AS_OF).days


def erp_is_stale(today: date | None = None) -> bool:
    return erp_age_days(today) > ERP_STALE_AFTER_DAYS


def erp_detail(today: date | None = None) -> str:
    """The provenance line, which says how old the figure is when it matters."""
    text = (f"Damodaran implied ERP, "
            f"{DAMODARAN_ERP_AS_OF:%B %Y}")
    if erp_is_stale(today):
        text += (f" - published {erp_age_days(today)} days ago and due an "
                 "update; check pages.stern.nyu.edu/~adamodar")
    return text


def risk_free_age_days(today: date | None = None) -> int:
    """Days since the stored fallback rate was the published curve."""
    return ((today or _today()) - FALLBACK_RISK_FREE_AS_OF).days


def risk_free_is_stale(today: date | None = None) -> bool:
    return risk_free_age_days(today) > RISK_FREE_STALE_AFTER_DAYS


def risk_free_as_of_text() -> str:
    """The fallback's date, written the way a sentence wants it.

    Built by hand because the obvious format code is not portable: "%-d"
    strips the leading zero on Linux, where this is deployed, and is a
    ValueError on Windows, where it is developed.
    """
    return (f"{FALLBACK_RISK_FREE_AS_OF.day} "
            f"{FALLBACK_RISK_FREE_AS_OF:%B %Y}")


def fallback_risk_free_detail(today: date | None = None) -> str:
    """The provenance line for a valuation built on the stored rate."""
    text = ("live Treasury curve and market quote both unavailable; used the "
            f"stored {FALLBACK_RISK_FREE_RATE:.2%} rate from "
            f"{risk_free_as_of_text()}")
    if risk_free_is_stale(today):
        text += (f" - {risk_free_age_days(today)} days old and no longer a "
                 "reliable stand-in for the curve")
    return text

DEFAULT_BETA = 1.0          # market beta; now only Blume's shrink target

# A note on what is NOT done here, because it was done and then undone.
#
# 1.0.32 weighted a company's own beta by the t-statistic of its own
# regression - the slope over its standard error - on the grounds that a
# slope the data cannot tell apart from zero is not evidence. That reasoning
# is wrong, and the arithmetic says why.
#
# The t-statistic tests the beta against ZERO, which is not the question. The
# question is whether the company's own figure beats the industry's, and a
# measurement can answer that while failing the other. Johnson & Johnson
# measured 0.183 with a standard error of 0.15: a t-statistic of 1.25, so it
# cannot rule out zero - but it rules out the 0.96 industry beta by more than
# five standard errors. Discarding it for the industry figure threw away the
# more precise of the two estimates.
#
# Worse, the test is correlated with the thing it tests. t = beta / se, and
# standard errors across large companies cluster in one narrow band, so t is
# very nearly proportional to beta: a LOW beta is mechanically harder to
# measure "significantly" than a high one. Of the ten largest US
# pharmaceutical companies, the only two clearing |t| = 2.5 were the two
# with the highest raw betas. A significance-weighted rule therefore pushes
# every defensive company upward, which is not a correction but a bias.
#
# So the industry beta is a fallback and not a weight: it stands in when
# there is no usable measurement at all, and never competes with one. The
# precision figures are still computed and reported - see
# `market_data.BetaRegression` - because they belong in the record and
# because the replacement worth building uses them properly. PARKED.md has
# that: cap-weighted industry betas from Damodaran's own membership lists,
# combined with the company's own by relative precision rather than by a
# significance test against the wrong null.

# Blume's adjustment: betas revert toward the market over time, so a beta
# measured on five years of history overstates how far from 1.0 the company
# will be over the decades a terminal value spans. Two thirds measured, one
# third market, which is the form Bloomberg and most practitioners use.
#
# It cuts both ways and that is the point: it pulls NVIDIA's 2.34 down and
# Coca-Cola's 0.24 up, because neither extreme persists. A company at the
# market beta is left exactly where it is - 0.67 x 1.0 + 0.33 = 1.0 - so
# the fallback needs no special case.
BLUME_WEIGHT = 0.67
BLUME_MARKET_WEIGHT = 0.33


def blume_adjusted(raw: float) -> float:
    """Pull a measured beta toward the market, as betas themselves do."""
    return BLUME_WEIGHT * raw + BLUME_MARKET_WEIGHT * DEFAULT_BETA
DEFAULT_DEBT_SPREAD = 0.02  # over risk-free, when interest expense is unusable

# Sanity bands. Outside these, an input is treated as bad data rather than as
# a surprising-but-real fact, and a documented fallback is substituted.
SANE_BETA = (0.10, 4.00)
SANE_COST_OF_DEBT = (0.005, 0.25)
SANE_WACC = (0.04, 0.20)

# --- Clamps: (floor, ceiling) --------------------------------------------
CLAMP_REVENUE_GROWTH = (0.00, 0.15)
CLAMP_OPERATING_MARGIN = (-1.00, 0.60)
# Raised from 0.35, which was below what real companies pay. Chevron's
# three-year median is 35.5%, Shell's 39.8%, and BP's is 81.9% - two years
# at about 82% on the UK Energy Profits Levy and Norway's 78% petroleum
# rate. A ceiling that cuts off genuine figures is not a safety rail, it is
# a quiet error, and clamping a tax rate DOWN inflates the valuation. The
# wall is at 100%, where the figure stops being a rate a company can pay
# out of its pre-tax income.
CLAMP_TAX_RATE = (0.00, 1.00)
CLAMP_DA_PCT = (0.00, 0.30)
# Raised from 0.40, which was below what real companies spend. Intel's
# three-year median is 45.1% and was being reported as 40.0%; Oracle's
# trailing intensity is 105% of revenue. A ceiling that cuts off genuine
# figures is not a safety rail, it is a quiet error - and an override already
# bypasses it, so it was constraining the model and not the user.
CLAMP_CAPEX_PCT = (0.00, 1.00)
# Working capital as a share of revenue.
#
# Recalibrated when the derivation changed from a two-endpoint delta to a
# median level, because the two are not the same quantity and the old bounds
# were not a rail for this one - they were the answer. Measured across 44
# large listed companies, the median level runs from -32.7% (Salesforce, paid
# in advance) to +100.3% (Deere, whose receivables are its business), with a
# median of -5.6%. Sixteen of the forty-four sat below the old -10% floor, so
# more than a third of companies would have had their coefficient set by the
# clamp rather than by their filings - five of them pinned to exactly -0.100.
#
# These bounds clear every one of those except Deere, which is a captive
# finance balance sheet a growth-perpetuity DCF describes poorly whatever
# this number says.
CLAMP_NWC_PCT = (-0.40, 0.60)

RATIO_WINDOW = 3  # years of history used for median ratios

# Years spanned by the revenue growth CAGR: FY-3 to the latest fiscal year,
# which is four observations and three periods of growth.
#
# Fixed rather than "the whole window", so the assumption does not move when
# a data source returns a different amount of history. Three to match
# RATIO_WINDOW: the margin, tax rate, capex and D&A ratios are all medians
# over three years, and a growth rate measured over a different span from
# the margins it is multiplied by is a harder thing to reason about than one
# measured over the same span.
GROWTH_WINDOW_YEARS = 3


@dataclass
class Provenance:
    """How one assumption got its value."""
    name: str
    value: float
    # "derived" | "derived (clamped)" | "derived (industry)" | "default"
    # | "published" | "override"
    source: str
    detail: str

    # For a clamped value, the facts behind it in structured form: the figure
    # the company's own history gave, the limit it was held to, and - where a
    # value can be limited for more than one reason - which. `detail` stays the
    # precise technical record (it is what the workbook carries); these let a
    # plain-language warning be written from the numbers rather than by
    # parsing that string.
    raw: float | None = None
    limit: float | None = None
    why: str | None = None

    # The range this assumption may be moved through, where that depends on
    # the company rather than on the kind of quantity.
    #
    # Most sliders have bounds that are a property of the thing being
    # measured - an operating margin is a percentage of revenue whatever the
    # company - and those live with the control. Terminal growth does not:
    # its ceiling is this company's own risk-free rate held below this
    # company's own WACC, so it has to travel with the value.
    #
    # None means "the control's own bounds apply", which is every other
    # assumption.
    allowed_min: float | None = None
    allowed_max: float | None = None

    @property
    def is_derived(self) -> bool:
        return self.source.startswith("derived")


@dataclass
class DerivedAssumptions:
    assumptions: Assumptions
    provenance: dict[str, Provenance] = field(default_factory=dict)
    # CAPM/WACC inputs are recorded separately: they feed the `wacc` assumption
    # rather than being engine assumptions in their own right, but they are
    # overridable and their provenance matters just as much.
    wacc_inputs: dict[str, Provenance] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)

    def derived_names(self) -> list[str]:
        return [k for k, p in self.provenance.items() if p.is_derived]

    def defaulted_names(self) -> list[str]:
        return [k for k, p in self.provenance.items() if p.source == "default"]

    def overridden_names(self) -> list[str]:
        return [k for k, p in self.provenance.items() if p.source == "override"]


def _clamp(value: float, bounds: tuple[float, float]) -> tuple[float, bool]:
    low, high = bounds
    clamped = max(low, min(high, value))
    return clamped, clamped != value


def _net_working_capital(balance_row: dict) -> float | None:
    """
    Non-cash, non-debt working capital.

    Cash and short-term investments are excluded because they are counted in
    the equity bridge, and short-term debt is excluded because it is financing,
    not operations. Including either would double-count.

    The two structural terms have to be there. Banks and insurers do not
    classify their balance sheets into current and non-current at all, and
    reading those absences as zero produces a working capital of minus the
    cash pile - a number with no meaning that the model would then project
    forward. So this returns None rather than inventing one.

    The three deductions are different: a company with no short-term
    investments and no short-term debt reports neither, and nil is the
    correct reading of that silence.
    """
    current_assets = reported(balance_row, "totalCurrentAssets")
    current_liabilities = reported(balance_row, "totalCurrentLiabilities")
    if current_assets is None or current_liabilities is None:
        return None

    cash = num(balance_row, "cashAndCashEquivalents")
    short_investments = num(balance_row, "shortTermInvestments")
    short_debt = num(balance_row, "shortTermDebt")

    operating_assets = current_assets - cash - short_investments
    operating_liabilities = current_liabilities - short_debt
    return operating_assets - operating_liabilities


def _cost_of_debt_history(fin: CompanyFinancials) -> list[tuple[str, float]]:
    """
    Implied cost of debt for each year with usable data.

    Interest expense is divided by AVERAGE debt across the year where the prior
    year's balance sheet is available, since interest accrues on the balance
    through the period rather than on the closing figure.

    Years where FMP reports zero interest expense against non-zero debt are
    skipped rather than treated as a 0% cost of debt. That combination is a
    reporting gap, not a fact: Apple's FY2024 and FY2025 both come back as
    zero interest expense against ~$100bn of debt, while FY2023 correctly
    shows $3.9bn. Taking the zeros at face value would drag the cost of debt
    to nil and understate WACC.
    """
    rates: list[tuple[str, float]] = []
    for i, income_row in enumerate(fin.income):
        if i >= len(fin.balance):
            break
        interest = abs(num(income_row, "interestExpense"))
        if interest <= 0:
            continue

        debt_now = total_debt_of(fin.balance[i])
        debt_prior = total_debt_of(fin.balance[i + 1]) if i + 1 < len(fin.balance) else 0.0
        debt = (debt_now + debt_prior) / 2 if debt_prior > 0 else debt_now
        if debt <= 0:
            continue

        rates.append((str(income_row.get("fiscalYear", "?")), interest / debt))
    return rates


def capital_values(fin: CompanyFinancials) -> tuple[float, float]:
    """(market value of equity, book value of debt) for *fin*.

    Equity at MARKET value and debt at book, the asymmetry the WACC weights
    use: a market capitalisation is observable and is what equity investors
    have at stake, whereas the market value of corporate debt rarely is and
    book value is close to par for an investment-grade issuer.

    Equity at BOOK value was tried as a stand-in when no price is available
    and rejected on measurement - see PARKED.md. It was further from the
    truth than the generic discount rate it was meant to improve on.

    Extracted so the WACC weights and the leverage an industry beta is
    relevered on cannot be computed two different ways.
    """
    equity_value = num(fin.profile, "marketCap")
    if equity_value <= 0:
        price = num(fin.profile, "price")
        shares = num(fin.income[0], "weightedAverageShsOutDil") \
            if fin.income else 0.0
        equity_value = price * shares
    debt_value = total_debt_of(fin.balance[0]) if fin.balance else 0.0
    return equity_value, debt_value


def _market_debt_to_equity(fin: CompanyFinancials) -> float:
    equity_value, debt_value = capital_values(fin)
    return debt_value / equity_value if equity_value > 0 else 0.0


def _industry_beta(fin: CompanyFinancials) -> tuple[float, str]:
    """The industry beta for *fin*, relevered, and how it was arrived at.

    Used where the company's own regression cannot be trusted. An industry
    average is an average of betas and so is already pulled toward the
    market; putting Blume's adjustment on top of it would shrink a figure
    that has been shrunk, so it is not applied here.
    """
    industry, sic, mapped = industry_for(fin.ticker)
    leverage = _market_debt_to_equity(fin)
    beta = relevered_beta(industry, leverage)

    if mapped:
        where = (f"the {industry.name} industry beta "
                 f"({industry.unlevered_beta:.2f} unlevered, across "
                 f"{industry.firms:,} companies) relevered on "
                 f"{fin.company_name}'s own capital structure")
    else:
        # No SIC code on file, or a code with no industry. Saying which is
        # better than implying an industry was identified.
        why = ("its SIC code is not one this model maps to an industry"
               if sic else "the SEC has no SIC code on file for it")
        where = (f"the whole-market beta ({industry.unlevered_beta:.2f} "
                 f"unlevered, across {industry.firms:,} companies) "
                 f"relevered on {fin.company_name}'s own capital "
                 f"structure, because {why}")

    detail = f"{where}; Damodaran industry data, {as_of_text()}"
    if leverage > MAX_DEBT_TO_EQUITY:
        detail += (f" - leverage of {leverage:.1f}x capped at "
                   f"{MAX_DEBT_TO_EQUITY:.0f}x for the relevering")
    return beta, detail


def _capm_inputs(fin: CompanyFinancials, record_input) -> tuple[float, float, float]:
    """
    Risk-free rate, equity risk premium and beta, each recorded with provenance.

    Shared by the DCF's WACC build-up and the dividend discount model's cost
    of equity, so the two methods can never disagree about what the market
    demands of the same company.
    """
    # --- Risk-free rate: live Treasury curve, static fallback --------------
    live = fetch_risk_free_rate(RISK_FREE_TENOR)
    if live is not None:
        rf = record_input("risk_free_rate", live[0], "derived", live[1])
    else:
        rf = record_input("risk_free_rate", FALLBACK_RISK_FREE_RATE,
                          "default", fallback_risk_free_detail())

    # "published" rather than "default": it is a figure someone computed
    # and dated, not a number this model picked for want of one.
    erp = record_input("equity_risk_premium", DAMODARAN_IMPLIED_ERP,
                       "published", erp_detail())

    # --- Beta --------------------------------------------------------------
    #
    # One gate, and it is the one that was always here: is this a number a
    # company can have? SANE_BETA's band is what "usable" means, and a beta
    # inside it is the company's own measurement, Blume-adjusted as before.
    #
    # What changed is only the fallback. A beta outside the band, or no beta
    # at all, used to become a flat 1.0 - a figure that said nothing about
    # the company. It now becomes the company's own industry, relevered on
    # the company's own borrowing: an estimate with reasons behind it. It is
    # a replacement for a missing measurement and never an argument against
    # one; see the note above DEFAULT_BETA for why it is not weighted
    # against the regression's precision.
    raw_beta = num(fin.profile, "beta")
    usable = SANE_BETA[0] <= raw_beta <= SANE_BETA[1]
    # A company with no beta at all needs no separate test: an absent one
    # reads as 0.0, which the band already rejects. The distinction still
    # matters for the MESSAGE, though - "we could not compute one" and "we
    # computed one and it was not a beta" send a reader to different places
    # - so it is drawn below rather than here. Mutation testing found this:
    # a test for the redundant condition could not fail.
    reported = fin.profile.get("beta")
    has_beta = (isinstance(reported, (int, float))
                and reported == reported          # not NaN
                and reported != 0)

    if usable:
        # Either quoted by Yahoo or computed from price history when the
        # quote endpoint was unavailable; the profile records which, so the
        # report can say rather than imply.
        source = fin.profile.get("betaSource") or "quoted by the market data provider"
        # Both numbers, because the adjustment is a judgement and a reader
        # who disagrees with it should be able to see what it did.
        beta = record_input(
            "beta", blume_adjusted(raw_beta), "derived",
            f"Blume-adjusted from raw {raw_beta:.3g}; {source}")
        return rf, erp, beta

    value, where = _industry_beta(fin)
    # "measured", not "reported". The figure rejected here is almost always
    # one this model computed itself - a regression of the company's own
    # five years of monthly returns against the S&P 500 - and calling it
    # "reported" blamed the data provider for our own arithmetic.
    if not has_beta:
        why = (fin.profile.get("betaSource")
               or "could not be computed from price history")
    else:
        why = f"measured {raw_beta:g}, outside plausible range"

    # "derived (industry)" rather than plain "derived", on the same pattern
    # as "derived (clamped)": this figure comes from the company's industry
    # rather than from its own share price, and a reader is entitled to know
    # which. The suffix keeps `is_derived` true, so nothing downstream
    # mistakes it for a figure picked for want of one.
    beta = record_input("beta", value, "derived (industry)",
                        f"{why}, so {where}")

    return rf, erp, beta


def _derive_wacc(fin: CompanyFinancials, tax_rate: float,
                 record_input) -> tuple[float | None, str, dict]:
    """
    WACC = We*Re + Wd*Rd*(1-t), with Re from CAPM.

        Re = risk-free rate + beta * equity risk premium
        Rd = interest expense / average debt   (the company's actual rate)
        We, Wd = market value of equity and book debt, as shares of the total

    Equity is weighted at MARKET value and debt at BOOK value. That asymmetry
    is deliberate and conventional: a company's market cap is observable and is
    what equity investors have at stake, whereas the market value of corporate
    debt is rarely observable and book value is a close proxy for
    investment-grade issuers near par.

    Returns (wacc, detail, components). A wacc of None means derivation failed
    and the caller should fall back to DEFAULT_WACC.
    """
    components: dict = {}

    rf, erp, beta = _capm_inputs(fin, record_input)

    cost_of_equity = rf + beta * erp

    # --- Cost of debt ------------------------------------------------------
    history = _cost_of_debt_history(fin)
    components["cost_of_debt_by_year"] = history

    if history:
        candidate = median([rate for _, rate in history])
        years_used = ", ".join(f"FY{y}" for y, _ in history)
        if SANE_COST_OF_DEBT[0] <= candidate <= SANE_COST_OF_DEBT[1]:
            cost_of_debt = record_input("cost_of_debt", candidate, "derived",
                                        f"median interest expense / average debt ({years_used})")
        else:
            cost_of_debt = record_input(
                "cost_of_debt", rf + DEFAULT_DEBT_SPREAD, "default",
                f"implied rate {candidate:.1%} outside plausible range; used risk-free + "
                f"{DEFAULT_DEBT_SPREAD:.0%} spread")
    else:
        cost_of_debt = record_input(
            "cost_of_debt", rf + DEFAULT_DEBT_SPREAD, "default",
            f"no year with usable interest expense; used risk-free + {DEFAULT_DEBT_SPREAD:.0%} spread")

    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)

    # --- Capital structure weights -----------------------------------------
    equity_value, debt_value = capital_values(fin)
    capital = equity_value + debt_value

    components.update({
        "risk_free_rate": rf,
        "equity_risk_premium": erp,
        "beta": beta,
        "cost_of_equity": cost_of_equity,
        "cost_of_debt": cost_of_debt,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "tax_rate": tax_rate,
        "equity_value": equity_value,
        "debt_value": debt_value,
    })

    if capital <= 0:
        return None, "market value of equity and debt could not be established", components

    weight_equity = equity_value / capital
    weight_debt = debt_value / capital
    components["weight_equity"] = weight_equity
    components["weight_debt"] = weight_debt

    wacc = weight_equity * cost_of_equity + weight_debt * after_tax_cost_of_debt
    components["wacc"] = wacc

    if not (SANE_WACC[0] <= wacc <= SANE_WACC[1]):
        return None, f"computed WACC of {wacc:.1%} is outside the plausible range", components

    detail = (f"CAPM: {rf:.2%} + {beta:.3g}x{erp:.2%} = {cost_of_equity:.2%} equity; "
              f"debt {cost_of_debt:.2%} ({after_tax_cost_of_debt:.2%} after tax); "
              f"weights {weight_equity:.1%}E / {weight_debt:.1%}D")
    return wacc, detail, components


def _ratio_series(rows: list[dict], denominator_rows: list[dict],
                  numerator: str, absolute: bool = False
                  ) -> tuple[list[float], int]:
    """Each year's `numerator` over revenue, newest first, with a count of
    the years dropped because the numerator was not reported.

    A year with no figure is not a year of zero. It is dropped, and the
    count comes back so the caller can say how many of the years it claims
    to have measured it actually measured.
    """
    out, skipped = [], 0
    for row, den_row in zip(rows, denominator_rows):
        den = num(den_row, "revenue")
        if den <= 0:
            continue
        value = reported(row, numerator)
        if value is None:
            skipped += 1
            continue
        out.append(abs(value) / den if absolute else value / den)
    return out, skipped


def _ttm_capex_ratio(fin: CompanyFinancials) -> float | None:
    """Trailing capex as a share of trailing revenue, or None.

    A ratio of two figures reported in the same currency, so it needs no
    conversion: the scalar cancels. Reading it off the raw trailing values is
    therefore both simpler and safer than converting first.
    """
    ttm = getattr(fin, "ttm", None)
    if ttm is None:
        return None
    capex = ttm.values.get("capex")
    revenue = ttm.values.get("revenue")
    if capex is None or not revenue or revenue <= 0:
        return None
    return abs(float(capex)) / float(revenue)


def derive_assumptions(fin: CompanyFinancials,
                       overrides: dict | None = None) -> DerivedAssumptions:
    """
    Build a starting assumption set for *fin* from its own history.

    `overrides` maps any Assumptions field name to a user-supplied value, which
    wins over both derived and default values and is recorded as such. These
    are starting points meant to be adjusted, not answers.
    """
    overrides = dict(overrides or {})
    income = fin.income
    balance = fin.balance
    cashflow = fin.cashflow

    provenance: dict[str, Provenance] = {}
    wacc_inputs: dict[str, Provenance] = {}
    diagnostics: dict = {}

    def _record_into(store: dict, name: str, value: float, source: str, detail: str,
                     **clamp) -> float:
        if name in overrides:
            supplied = float(overrides[name])
            # The allowed range describes the control, not the value, so it
            # survives the override: a slider the user has moved still needs
            # to know how far it may travel.
            low = clamp.get("allowed_min")
            high = clamp.get("allowed_max")
            # ...and where there is a range, it is a limit, not a label. This
            # used to be reported and then ignored: the API would answer a
            # request for a terminal growth above the company's own ceiling
            # with a number, while the same response carried the range that
            # number broke. Only terminal growth carries a range today, and
            # for it the ceiling is the whole point of the control - above
            # the risk-free rate, or within 1.5 points of WACC, the Gordon
            # perpetuity stops meaning anything and starts dividing by a gap
            # that is about to go negative. A caller that asks for that has
            # asked for a number no one should act on, so it is refused
            # rather than served.
            if low is not None and supplied < low - _RANGE_TOLERANCE:
                raise ValueError(
                    f"{name} must be at least {low:.4%}; got {supplied:.4%}")
            if high is not None and supplied > high + _RANGE_TOLERANCE:
                raise ValueError(
                    f"{name} must be at most {high:.4%} for this company; "
                    f"got {supplied:.4%}")
            store[name] = Provenance(
                name, supplied, "override",
                f"supplied by caller (would have been {value:.4g} from {source})",
                allowed_min=low, allowed_max=high)
            return supplied
        store[name] = Provenance(name, value, source, detail, **clamp)
        return value

    def record(name: str, value: float, source: str, detail: str, **clamp) -> float:
        return _record_into(provenance, name, value, source, detail, **clamp)

    def record_input(name: str, value: float, source: str, detail: str) -> float:
        return _record_into(wacc_inputs, name, value, source, detail)

    def derive(name: str, value: float | None, bounds: tuple[float, float],
               detail: str, fallback: float, fallback_detail: str) -> float:
        """Record a derived value, clamping it and noting if the clamp bit."""
        if value is None:
            return record(name, fallback, "default", fallback_detail)
        clamped, was_clamped = _clamp(value, bounds)
        if was_clamped:
            return record(name, clamped, "derived (clamped)",
                          f"{detail}; raw {value:.1%} clamped to [{bounds[0]:.0%}, {bounds[1]:.0%}]",
                          raw=value, limit=clamped, why="range")
        return record(name, clamped, "derived", detail)

    # --- Revenue history ---------------------------------------------------
    revenues = [num(r, "revenue") for r in income]
    years = [r.get("fiscalYear", "?") for r in income]
    diagnostics["revenue_by_year"] = list(zip(years, revenues))

    # --- Revenue growth: endpoint CAGR over a FIXED window -----------------
    #
    # The window is GROWTH_WINDOW_YEARS and not len(revenues), so the figure
    # does not change when a source returns an extra year of history. See
    # the note on GROWTH_WINDOW_YEARS.
    growth_value, growth_detail = None, ""
    series = revenues[:GROWTH_WINDOW_YEARS + 1]
    if len(series) >= 2:
        newest, oldest = series[0], series[-1]
        periods = len(series) - 1
        if oldest > 0 and newest > 0:
            cagr = (newest / oldest) ** (1 / periods) - 1
            growth_value = cagr
            growth_detail = (f"{periods}-year revenue CAGR, "
                             f"FY{years[periods]}-FY{years[0]} "
                             f"({oldest / 1e9:,.1f}bn -> {newest / 1e9:,.1f}bn)")
            if periods < GROWTH_WINDOW_YEARS:
                # Said rather than implied. A two-year CAGR is a weaker
                # measurement than a three-year one and the reader is
                # entitled to know which they have.
                growth_detail += (
                    f"; only {len(series)} years of revenue history, so the "
                    f"usual {GROWTH_WINDOW_YEARS}-year window could not be "
                    "used")
    revenue_growth = derive(
        "revenue_growth", growth_value, CLAMP_REVENUE_GROWTH, growth_detail,
        DEFAULT_TERMINAL_GROWTH, "insufficient revenue history; fell back to terminal growth",
    )

    # --- Ratio medians over the recent window ------------------------------
    window = income[:RATIO_WINDOW]
    cf_window = cashflow[:RATIO_WINDOW]

    def median_ratio(rows: list[dict], numerator: str, denominator_rows: list[dict],
                     denominator: str = "revenue", absolute: bool = False
                     ) -> tuple[float | None, int, int]:
        """(median, years measured, years skipped for want of a figure).

        A year whose numerator was never reported is dropped rather than
        scored as zero - the distinction that cost Amazon 70% of its value
        through the tax rate, and that would cost more than twice that
        through capital spending.
        """
        ratios, skipped = [], 0
        for row, den_row in zip(rows, denominator_rows):
            den = num(den_row, denominator)
            if den <= 0:
                continue
            value = reported(row, numerator)
            if value is None:
                skipped += 1
                continue
            ratios.append(abs(value) / den if absolute else value / den)
        return (median(ratios) if ratios else None), len(ratios), skipped

    def measured(detail: str, years: int, skipped: int) -> str:
        """The detail line, with the years it could not measure named."""
        if skipped:
            return f"{detail}; {skipped} more reported no figure"
        return detail

    # Operating margin
    margin_value, margin_years, margin_skipped = median_ratio(
        window, "operatingIncome", window)
    operating_margin = derive(
        "operating_margin", margin_value, CLAMP_OPERATING_MARGIN,
        measured(f"median operatingIncome / revenue over {margin_years} "
                 f"years", margin_years, margin_skipped),
        0.0,
        # A zero margin is a company that earns nothing, which is a claim,
        # and the old wording made it in the model's voice. Say instead
        # which of the two absences caused it.
        ("operating income not reported in any year; assumed nil"
         if margin_skipped else "no usable revenue history; assumed nil"),
    )
    diagnostics["operating_margin_by_year"] = [
        (r.get("fiscalYear"),
         (reported(r, "operatingIncome") / num(r, "revenue"))
         if num(r, "revenue") > 0 and reported(r, "operatingIncome") is not None
         else None)
        for r in income
    ]

    # --- Effective tax rate ------------------------------------------
    #
    # Three sources, tried in order of how directly they answer the
    # question. The fall-through is about the quality of the data, never
    # about the answer being inconvenient: a company that genuinely pays
    # very little keeps its rate.
    #
    #   1. The reported provision over pre-tax income, across profitable
    #      years that actually reported a provision.
    #   2. Yahoo's own normalized effective rate, where the reported
    #      figures are missing, negative, or measuring one-off items.
    #   3. The US statutory rate, said out loud as a default.
    #
    # The first of those used to be the only one, and it read a year with
    # no tax line as a year of no tax: `num` returns zero for an absent
    # field, and Yahoo serves a datacentre IP a thinner income statement
    # than it serves a laptop. Amazon came out of the deployed backend at
    # $61.63 a share against $36.09 from the same code run locally. Zero
    # tax is the single most value-inflating thing this model can assume,
    # and it was being inferred from silence.
    tax_ratios: list[float] = []
    profitable_years = 0      # years that could have shown a rate
    unreported_years = 0      # ...of which, none did
    for row in window:
        pretax = num(row, "incomeBeforeTax")
        if pretax <= 0:
            continue          # a loss produces a benefit, not a rate
        profitable_years += 1
        expense = reported(row, "incomeTaxExpense")
        if expense is None:
            unreported_years += 1
            continue
        tax_ratios.append(expense / pretax)

    reported_rate = median(tax_ratios) if tax_ratios else None
    spread = max(tax_ratios) - min(tax_ratios) if len(tax_ratios) > 1 else 0.0

    normalized = [r for r in (reported(row, "effectiveTaxRate")
                              for row in window) if r is not None]
    normalized_rate = median(normalized) if normalized else None

    # Why the reported figures cannot be used, in words, or "" if they can.
    # The tests are symmetric: a median too low to be a rate, and a median
    # too high to be one, each confirmed by a second signal before the
    # reported figures are set aside. Neither fires on a number that is
    # merely unusual - only on one the data cannot support.
    if reported_rate is None:
        rejected = (f"no tax figure reported in {profitable_years} "
                    "profitable year(s)" if profitable_years
                    else "no profitable year in the window")
    elif reported_rate <= 0:
        rejected = (f"the reported provisions median to {reported_rate:.1%}, "
                    "which is a one-off benefit rather than a rate this "
                    "company pays")
    elif reported_rate < TAX_IMPLAUSIBLY_LOW and spread > TAX_VOLATILE_SPREAD:
        rejected = (f"the reported provisions swing {spread:.0%} across the "
                    f"window and median to {reported_rate:.1%}, which "
                    "measures one-off items rather than a rate")
    elif (reported_rate > TAX_IMPLAUSIBLY_HIGH
          and normalized_rate is not None
          and abs(reported_rate - normalized_rate)
          > TAX_NORMALIZED_DISAGREEMENT):
        rejected = (f"the reported provisions median to {reported_rate:.1%}, "
                    "inflated by one-off levies and impairments, against a "
                    f"published rate of {normalized_rate:.1%}")
    else:
        rejected = ""

    if not rejected:
        tax_value = reported_rate
        tax_detail = (f"median effective rate over {len(tax_ratios)} "
                      f"profitable year(s)")
        if unreported_years:
            # Said out loud, because a rate drawn from one year of three is
            # a weaker number than the same rate drawn from three.
            tax_detail += f"; {unreported_years} more reported no tax figure"
    elif normalized_rate is not None:
        tax_value = normalized_rate
        tax_detail = (f"normalized effective rate as published, median over "
                      f"{len(normalized)} year(s), because {rejected}")
    else:
        tax_value = None
        tax_detail = ""

    tax_rate = derive(
        "tax_rate", tax_value, CLAMP_TAX_RATE, tax_detail,
        FALLBACK_TAX_RATE,
        f"tax data unavailable ({rejected}, and no normalized rate was "
        "published); used US statutory rate",
    )
    diagnostics["tax_rate_by_year"] = [
        (row.get("fiscalYear", "?"),
         (reported(row, "incomeTaxExpense") / num(row, "incomeBeforeTax")
          if num(row, "incomeBeforeTax") > 0
          and reported(row, "incomeTaxExpense") is not None else None),
         reported(row, "effectiveTaxRate"))
        for row in window
    ]

    # D&A and capex come from the cash flow statement, which reports them as
    # actual cash movements rather than accounting allocations.
    da_value, da_years, da_skipped = median_ratio(
        cf_window, "depreciationAndAmortization", window)
    da_pct = derive(
        "da_pct", da_value, CLAMP_DA_PCT,
        measured(f"median D&A / revenue over {da_years} years",
                 da_years, da_skipped),
        0.0, "no cash flow history",
    )

    # --- Capex: where it is now, and where it settles ----------------------
    #
    # Two numbers rather than one. A three-year median applied flat to every
    # projected year understates a company mid-buildout by construction - the
    # median of a rising series is its middle year, a level already left
    # behind - and that understatement is worth 71% of Alphabet's value and
    # 114% of Meta's. Holding the current rate forever is the opposite error.
    #
    # So the projection starts at what the company is spending now and glides
    # to a normalized level. See dcf.capex_path().
    capex_ratios, capex_skipped = _ratio_series(
        cf_window, window, "capitalExpenditure", absolute=True)

    # The anchor: the mean, not the median, of the recent past. For a series
    # that trends, the mean sits closer to where the company is heading - it
    # registers how far the level moved, where the median only reports which
    # year was in the middle. On a flat series the two are the same number to
    # within a rounding error, which is why this is a near no-op for the
    # companies that are not ramping.
    capex_terminal_value = (sum(capex_ratios) / len(capex_ratios)
                            if capex_ratios else None)

    # If no year reported capital spending, the fallback cannot be nil.
    # Nil capex is the single most value-inflating assumption available to
    # this model - worth +111% on Alphabet, three times what a zero tax
    # rate was worth - and a company that reported nothing is not a company
    # that spent nothing. Depreciation is the standing proxy for the
    # spending needed to keep the existing asset base intact, so a business
    # whose capital spending did not arrive is modelled as replacing what
    # it consumes and no more. It is a convention, it is named as one, and
    # it is bounded by the company's own figures rather than by zero.
    capex_missing_detail, capex_assumed = "", 0.0
    if capex_terminal_value is None:
        if da_value is not None:
            capex_assumed = da_value
            capex_missing_detail = (
                f"no capital spending reported in {capex_skipped} year(s); "
                f"assumed equal to depreciation at {da_value:.1%} of revenue")
        else:
            capex_missing_detail = (
                "neither capital spending nor depreciation reported; "
                "assumed nil, which flatters the valuation")

    capex_terminal_pct = derive(
        "capex_terminal_pct", capex_terminal_value, CLAMP_CAPEX_PCT,
        measured(f"mean capex / revenue over {len(capex_ratios)} years",
                 len(capex_ratios), capex_skipped),
        capex_assumed, capex_missing_detail or "no cash flow history",
    )

    # The start: trailing-twelve-month intensity, the same figure the base
    # year displays, so year one of the projection visibly IS the base year.
    #
    # It is a ratio of two figures converted by the same scalar, so no
    # currency conversion is needed or wanted here - the rate cancels.
    ttm_capex_ratio = _ttm_capex_ratio(fin)
    if ttm_capex_ratio is not None:
        capex_value = ttm_capex_ratio
        # The whole glide in one line, because the number on screen is only
        # the first year of it and a reader seeing 33.9% is entitled to know
        # the forecast does not hold it there.
        capex_detail = (
            f"capex / revenue, trailing twelve months; fades from "
            f"{ttm_capex_ratio:.1%} (current) to {capex_terminal_pct:.1%} "
            f"(3-yr mean) over the forecast")
    else:
        # Alibaba reports no trailing capital expenditure. Falling back to the
        # median keeps the old behaviour for the start as well as the anchor,
        # which collapses the fade to a flat line - the honest outcome when
        # there is nothing current to fade from.
        capex_value, capex_years, skipped = median_ratio(
            cf_window, "capitalExpenditure", window, absolute=True)
        capex_detail = measured(
            f"median capex / revenue over {capex_years} years "
            "(no trailing figure reported)", capex_years, skipped)
    capex_pct = derive(
        "capex_pct", capex_value, CLAMP_CAPEX_PCT, capex_detail,
        capex_assumed, capex_missing_detail or "no cash flow history",
    )
    diagnostics["capex_by_year"] = [
        (r.get("fiscalYear", "?"), ratio)
        for r, ratio in zip(window, capex_ratios)
    ]

    # --- Change in NWC as a share of revenue growth ------------------------
    # The working capital a business ties up, as the share of revenue it has
    # actually held - not the change between two years.
    #
    # This was a two-endpoint delta: (NWC_newest - NWC_oldest) divided by the
    # change in revenue. It is the same shape of mistake the revenue CAGR
    # used to make, and it failed the same way. Two endpoints cannot tell a
    # structural ratio from a transitional drift, and when a company's
    # working capital is drifting the delta reports the drift as if it were
    # the structure.
    #
    # Alibaba is the case that exposed it. Its working capital is negative -
    # a marketplace collects from buyers before it pays merchants, so growth
    # is funded by the float - but it has been eroding, from -23.0% of
    # revenue to -15.1% over four years. The delta read that erosion as
    # +0.292: every extra dollar of revenue consumes 29 cents. Carried into
    # a perpetuity that assumes the ratio swings 44 points, crosses zero in
    # year eight and settles at +29%, which nothing in the filings proposes.
    # It made the company appear to destroy value by growing.
    #
    # A median of year-on-year changes does not fix it and was measured
    # rather than assumed: for Alibaba it gives +0.459, worse than the
    # endpoint, because every delta over this window carries the same drift.
    # The disagreement is not noise against robustness. It is two different
    # questions - "how has working capital moved lately" against "what share
    # of revenue does this business tie up" - and only the second is a
    # statement about the next thirty years.
    #
    # So the level, across every year rather than the latest, and the median
    # rather than the mean so that one unusual balance-sheet date cannot
    # carry it. Where the ratio really is stable the two methods agree, and
    # most companies barely move.
    nwc_value, nwc_detail = None, ""
    ratios, nwc_skipped = [], 0
    for row, revenue in zip(balance, revenues):
        if revenue <= 0:
            continue
        working_capital = _net_working_capital(row)
        if working_capital is None:
            # A balance sheet that does not split current from non-current,
            # which is every bank and most insurers. Reading that as nil
            # working capital would be a claim about the business drawn
            # from the absence of a classification.
            nwc_skipped += 1
            continue
        ratios.append(working_capital / revenue)
    if ratios:
        nwc_value = median(ratios)
        nwc_detail = measured(
            f"median working capital as a share of revenue across "
            f"FY{years[-1]}-FY{years[0]} ({len(ratios)} years)",
            len(ratios), nwc_skipped)
        diagnostics["nwc_ratios"] = ratios
    nwc_pct = derive(
        "nwc_pct", nwc_value, CLAMP_NWC_PCT, nwc_detail,
        0.0,
        ("no balance sheet reported current assets and liabilities; "
         "working capital assumed to stay flat"
         if nwc_skipped else
         "no year had revenue to measure working capital against"),
    )

    # --- WACC: derived from CAPM + the company's own capital structure -----
    # Derived after tax_rate, which the after-tax cost of debt depends on.
    wacc_value, wacc_detail, wacc_components = _derive_wacc(fin, tax_rate, record_input)
    diagnostics["wacc_components"] = wacc_components

    if wacc_value is None:
        wacc = record("wacc", DEFAULT_WACC, "default",
                      f"derivation failed ({wacc_detail}); fell back to global default")
    else:
        wacc = record("wacc", wacc_value, "derived", wacc_detail)

    # --- Terminal growth: the rate is the ceiling, not a suggestion --------
    #
    # The slider runs from nothing to the current risk-free rate and rests at
    # the top. That is a deliberate shape. Long-run growth above the
    # risk-free rate is the assumption that quietly produces enormous
    # figures, so rather than leaving it reachable and warning about it, it
    # is simply not offered: the user can argue the company down from the
    # rate, never up past it.
    #
    # The ceiling is held below WACC as well, because a perpetuity divides by
    # the gap between them. For a company whose cost of capital is close to
    # the risk-free rate the binding limit is WACC, not the rate.
    #
    # The range is company-specific, so it travels with the value rather than
    # living in the control.
    rf_for_growth = wacc_components.get("risk_free_rate")
    wacc_ceiling = max(wacc - TERMINAL_GROWTH_SAFETY_MARGIN, 0.0)

    if rf_for_growth is None:
        ceiling = min(LONG_RUN_NOMINAL_GROWTH, wacc_ceiling)
        terminal_growth = record(
            "terminal_growth", ceiling, "default",
            f"no risk-free rate available; the ceiling is "
            f"{long_run_growth_source()}, held at least "
            f"{TERMINAL_GROWTH_SAFETY_MARGIN * 100:.0f} percentage points "
            "below WACC; adjustable "
            "downward to 0%",
            allowed_min=0.0, allowed_max=ceiling)
        diagnostics["terminal_growth_capped"] = ceiling < LONG_RUN_NOMINAL_GROWTH
        diagnostics["terminal_growth_uncapped"] = LONG_RUN_NOMINAL_GROWTH
    else:
        uncapped = float(rf_for_growth)
        ceiling = max(terminal_growth_ceiling(uncapped, wacc), 0.0)
        capped = ceiling < uncapped

        # Which of the three limits produced the number, said plainly. A
        # reader who wants to argue with the figure needs to know which
        # argument to have.
        if ceiling >= uncapped - 1e-12:
            why = ("defaults to the current risk-free rate (US 10-year "
                   "Treasury), which is the lowest of the three limits here")
        elif ceiling >= LONG_RUN_NOMINAL_GROWTH - 1e-12:
            why = (f"held at {long_run_growth_source()} rather than at the "
                   f"{uncapped:.2%} risk-free rate, because no company "
                   "outgrows its own economy in perpetuity")
        else:
            why = (f"held {TERMINAL_GROWTH_SAFETY_MARGIN * 100:.0f} "
                   "percentage points below this "
                   f"company's {wacc:.2%} discount rate, which binds before "
                   f"either the {uncapped:.2%} risk-free rate or long-run "
                   "growth; that minimum spread is a modelling judgement "
                   "rather than a published standard, chosen so an ordinary "
                   "error in the discount rate cannot dominate a terminal "
                   "value that is most of the answer")
        detail = f"{why}; adjustable downward to 0%"

        terminal_growth = record("terminal_growth", ceiling, "derived", detail,
                                 allowed_min=0.0, allowed_max=ceiling)
        diagnostics["terminal_growth_capped"] = capped
        diagnostics["terminal_growth_uncapped"] = uncapped
        diagnostics["terminal_growth_binding"] = (
            "risk_free_rate" if ceiling >= uncapped - 1e-12
            else "long_run_growth"
            if ceiling >= LONG_RUN_NOMINAL_GROWTH - 1e-12 else "wacc_margin")

    diagnostics["terminal_growth_ceiling"] = ceiling

    # --- Global defaults ---------------------------------------------------
    projection_years = record("projection_years", DEFAULT_PROJECTION_YEARS, "default",
                              "modelling convention")

    # The fade: off unless asked for. Recorded like any other assumption so
    # that when it is on, the screen can say so and say where it came from.
    fade_enabled = record("fade_enabled", DEFAULT_FADE_ENABLED, "default",
                          "off; growth is held flat across the forecast")
    fade_start_year = record("fade_start_year", DEFAULT_FADE_START_YEAR, "default",
                             "last year at the full starting rate before the glide")
    # The shape of the glide is a choice between two named curves, not a
    # quantity, so it is kept out of the numeric provenance channel - which
    # exists to say where a *number* came from - and recorded as a
    # diagnostic instead. An unfamiliar name falls back to the straight line
    # rather than failing a valuation over a spelling.
    supplied_pattern = str(overrides.get("fade_pattern", DEFAULT_FADE_PATTERN)).lower()
    fade_pattern = (supplied_pattern if supplied_pattern in (FADE_LINEAR, FADE_EXPONENTIAL)
                    else DEFAULT_FADE_PATTERN)
    diagnostics["fade_pattern"] = fade_pattern
    diagnostics["fade_pattern_source"] = (
        "override" if "fade_pattern" in overrides else "default")

    assumptions = Assumptions(
        revenue_growth=revenue_growth,
        operating_margin=operating_margin,
        tax_rate=tax_rate,
        da_pct=da_pct,
        capex_pct=capex_pct,
        capex_terminal_pct=capex_terminal_pct,
        nwc_pct=nwc_pct,
        wacc=wacc,
        terminal_growth=terminal_growth,
        projection_years=int(projection_years),
        fade_enabled=bool(fade_enabled),
        fade_start_year=int(fade_start_year),
        fade_pattern=str(fade_pattern),
    )

    unknown = set(overrides) - set(provenance) - set(wacc_inputs) - {"fade_pattern"}
    if unknown:
        known = sorted(set(provenance) | set(wacc_inputs))
        raise ValueError(
            f"Unknown assumption override(s): {', '.join(sorted(unknown))}. "
            f"Valid names: {', '.join(known)}"
        )

    return DerivedAssumptions(assumptions=assumptions,
                              provenance=provenance,
                              wacc_inputs=wacc_inputs,
                              diagnostics=diagnostics)
