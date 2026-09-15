"""
Derives dividend discount model inputs from a company's own dividend record.

The DDM counterpart to assumptions.py, with the same discipline: every input
is derived from the company's own data where possible, defaulted to a
documented global value where not, clamped when the raw figure is not a
defensible forward assumption, and labelled with which of those happened.

WHAT IS DERIVED vs DEFAULTED
----------------------------
Derived from the company's own data:
    current dividend   last regular payment x payments per year - the
                       indicated annual rate, as Yahoo itself reports it
    dividend_growth    dividend-per-share CAGR over complete calendar years,
                       capped at the growth the company's earnings can fund
    cost_of_equity     CAPM: risk-free + beta x equity risk premium, built
                       from exactly the inputs the DCF uses

Global defaults:
    terminal_growth    long-run nominal growth, shared with the DCF
    high_growth_years  modelling convention, matching the DCF horizon

REGULAR vs SPECIAL DIVIDENDS
----------------------------
A one-off special dividend is a return of excess capital, not a run rate.
Costco's $15.00 special in December 2023 sat among $1.02 regular payments;
annualising it would have implied a $60 dividend. A payment well above the
median of the payments around it is excluded from the run rate and from the
growth history, and listed so the exclusion is visible.

RECURRING VARIABLE DIVIDENDS
----------------------------
Not every above-regular payment is a one-off. Progressive pays $0.10 a
quarter plus a variable dividend each January - $0.75, $4.50 and $13.50 in
the last three - which is dividend policy, not an accident. Excluding it
valued the company on $0.40 a year. So when above-regular payments recur at
roughly annual intervals, three or more times running and recently, they are
counted: at their average over up to five years, with a year in which none
was paid counting as zero, because the amounts swing too far for any single
year to stand in for the rest. Costco's specials came three years apart and
stay excluded.

Yahoo reports a variable dividend declared alongside the regular one as a
single event ($13.60 = $0.10 regular + $13.50 variable). Only the variable
portion is added to the regular rate, or the regular dividend would be
counted twice. Growth is measured on the regular dividend alone: a series
that went from $0.75 to $13.50 in two years has no meaningful growth rate.

WHY THE INDICATED RATE, NOT A TRAILING SUM
------------------------------------------
Summing the last 365 days depends on where ex-dates happen to fall: a
quarterly payer can show five payments in a window, or three. The last
regular payment multiplied by its frequency has no such jitter, and it
reproduces Yahoo's own forward rate exactly (JPM $1.50 x 4 = $6.00,
BAC $0.32 x 4 = $1.28). The trailing sum is still reported, for reference.

SUSTAINABLE GROWTH CAP
----------------------
A company can grow its dividend faster than its earnings only while it keeps
raising its payout ratio, which cannot go on forever. The sustainable rate,
ROE x (1 - payout ratio), is the growth retained earnings can fund, and
historical dividend growth above it is capped to it. Bank of America grew its
dividend about 8.5% a year on a ~10.6% ROE and a ~33% payout, which funds
about 7%.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Sequence

from assumptions import (DEFAULT_BETA, DEFAULT_PROJECTION_YEARS,
                         DEFAULT_TERMINAL_GROWTH, Provenance, _capm_inputs,
                         _clamp)
from ddm import DDMAssumptions
from market_data import CompanyFinancials, num

# --- Regular vs special ------------------------------------------------------
SPECIAL_DIVIDEND_MULTIPLE = 1.75   # above this x the neighbouring median
SPECIAL_NEIGHBOURS = 4             # payments either side used as reference

# --- Suspension ----------------------------------------------------------------
# Quarterly: a payment is overdue after 91 x 1.5 + 45 = ~182 days.
SUSPENSION_GAP_MULTIPLE = 1.5
SUSPENSION_GRACE_DAYS = 45

# --- Recurring variable dividends ---------------------------------------------
RECURRING_VARIABLE_MIN_PAYMENTS = 3    # at annual intervals, counting back from the latest
ANNUAL_GAP_DAYS = (270, 460)           # tolerant of a December or January ex-date
RECURRING_MAX_DAYS_SINCE_LAST = 455    # a pattern that has lapsed is history, not policy
VARIABLE_AVERAGE_YEARS = 5
COMBINED_PAYMENT_DAYS = 30             # a regular payment this close means they were separate

# --- Growth ---------------------------------------------------------------------
GROWTH_WINDOW_YEARS = 5
MIN_GROWTH_POINTS = 3              # complete years needed to measure a trend
CLAMP_DIVIDEND_GROWTH = (0.00, 0.12)
DIVIDEND_CUT_TOLERANCE = 0.01      # a fall of more than 1% counts as a cut

# --- Cost of equity -------------------------------------------------------------
SANE_COST_OF_EQUITY = (0.04, 0.20)

RATIO_WINDOW = 3
MAX_HIGH_GROWTH_YEARS = 30

DDM_ASSUMPTION_NAMES = ("dividend_growth", "high_growth_years",
                        "terminal_growth", "cost_of_equity")
COST_OF_EQUITY_INPUT_NAMES = ("risk_free_rate", "equity_risk_premium", "beta")

# Names that belong to the DCF alone. Recognised so that a caller sending one
# to a DDM valuation is told which method it applies to, rather than that it
# does not exist.
DCF_ONLY_NAMES = ("revenue_growth", "operating_margin", "tax_rate", "da_pct",
                  "capex_pct", "nwc_pct", "wacc", "projection_years",
                  "cost_of_debt")

_FREQUENCIES = {12: "monthly", 4: "quarterly", 2: "semi-annual", 1: "annual"}


# ---------------------------------------------------------------------------
# The dividend record
# ---------------------------------------------------------------------------

@dataclass
class DividendRecord:
    payments: list[tuple[date, float]]           # everything, oldest first
    regular: list[tuple[date, float]]
    # Above-regular payments that do NOT recur on an annual pattern: one-off
    # returns of capital, excluded from the run rate.
    specials: list[tuple[date, float]]
    payments_per_year: int | None
    last_regular_date: date | None
    last_regular_amount: float | None
    days_since_last: int | None
    suspended: bool
    current_dividend: float | None               # regular rate + variable average
    trailing_twelve_months: float                # regular + recurring variable, paid
    annual_history: list[tuple[int, float]]      # regular dividend, complete years
    regular_dividend: float | None = None        # indicated regular rate
    # Above-regular payments that DO recur at roughly annual intervals, as
    # (date, amount paid, variable portion). Counted at a multi-year average.
    variable_dividends: list[tuple[date, float, float]] = field(default_factory=list)
    variable_annual_dividend: float | None = None
    variable_average_years: int | None = None
    variable_recurring_years: int = 0            # annual payments running, latest back


def _to_date(timestamp: int) -> date:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()


def _frequency_from_gaps(gaps: Sequence[int]) -> int:
    typical = median(gaps)
    if typical <= 45:
        return 12
    if typical <= 120:
        return 4
    if typical <= 240:
        return 2
    return 1


def _annual_chain_length(specials: Sequence[tuple[date, float]], today: date) -> int:
    """
    How many of the most recent above-regular payments recur a year apart.

    Counted back from the latest, stopping at the first gap that is not
    roughly annual. Zero if the latest is too old to be a live pattern.
    """
    if not specials or (today - specials[-1][0]).days > RECURRING_MAX_DAYS_SINCE_LAST:
        return 0
    chain = 1
    for (earlier, _), (later, _) in reversed(list(zip(specials, specials[1:]))):
        if not ANNUAL_GAP_DAYS[0] <= (later - earlier).days <= ANNUAL_GAP_DAYS[1]:
            break
        chain += 1
    return chain


def _variable_portion(paid: date, gross: float,
                      regular: Sequence[tuple[date, float]]) -> float:
    """
    The part of an above-regular payment that is not the regular dividend.

    When Yahoo reports a regular and a variable dividend as one event, no
    separate regular payment appears near that date - Progressive's quarterly
    $0.10 is simply missing from January, folded into the $13.60. Only then is
    the prevailing regular payment taken out. A variable dividend paid on its
    own date, with a regular payment close by, is counted whole.
    """
    if any(abs((d - paid).days) <= COMBINED_PAYMENT_DAYS for d, _ in regular):
        return gross
    before = [amount for d, amount in regular if d <= paid]
    prevailing = before[-1] if before else regular[0][1]
    return max(0.0, gross - prevailing)


def analyse_dividends(events: Sequence[tuple[int, float]],
                      today: date | None = None) -> DividendRecord:
    """Classify, annualise and summarise raw (unix_time, amount) events."""
    today = today or date.today()
    payments = [(_to_date(ts), float(amount))
                for ts, amount in sorted(events) if amount > 0]

    regular: list[tuple[date, float]] = []
    specials: list[tuple[date, float]] = []
    for i, (paid, amount) in enumerate(payments):
        neighbours = ([a for _, a in payments[max(0, i - SPECIAL_NEIGHBOURS):i]]
                      + [a for _, a in payments[i + 1:i + 1 + SPECIAL_NEIGHBOURS]])
        if len(neighbours) >= 3 and amount > SPECIAL_DIVIDEND_MULTIPLE * median(neighbours):
            specials.append((paid, amount))
        else:
            regular.append((paid, amount))

    recent = regular[-13:]
    gaps = [(later - earlier).days
            for (earlier, _), (later, _) in zip(recent, recent[1:])
            if (later - earlier).days > 0]
    frequency = _frequency_from_gaps(gaps) if gaps else None

    last_date = last_amount = days_since = regular_rate = None
    suspended = False
    if regular:
        last_date, last_amount = regular[-1]
        days_since = (today - last_date).days
        if frequency:
            overdue_after = (365 / frequency) * SUSPENSION_GAP_MULTIPLE + SUSPENSION_GRACE_DAYS
            suspended = days_since > overdue_after
            regular_rate = last_amount * frequency

    # --- Recurring variable dividends: counted, not excluded ---------------
    chain = _annual_chain_length(specials, today)
    variable: list[tuple[date, float, float]] = []
    variable_annual = variable_years = None
    if chain >= RECURRING_VARIABLE_MIN_PAYMENTS and regular:
        # Averaged over the years the company has been paying them, up to
        # five, so a pattern that began three years ago is not diluted by
        # years before it existed - but a year inside the window with no
        # variable dividend does count as zero.
        years_paying = math.ceil((today - specials[0][0]).days / 365.25)
        variable_years = max(1, min(VARIABLE_AVERAGE_YEARS, years_paying))
        window_start = today - timedelta(days=round(variable_years * 365.25))
        variable = [(paid, gross, _variable_portion(paid, gross, regular))
                    for paid, gross in specials if paid > window_start]
        variable_annual = sum(net for _, _, net in variable) / variable_years
        # They recur, so none of them is a one-off - including older payments
        # from the same pattern that fall before the averaging window.
        specials = []

    current = None
    if regular_rate is not None:
        current = regular_rate + (variable_annual or 0.0)

    cutoff = today - timedelta(days=365)
    trailing = (sum(amount for paid, amount in regular if paid > cutoff)
                + sum(gross for paid, gross, _ in variable if paid > cutoff))

    # Complete calendar years only. Each year is annualised as its mean
    # regular payment times the frequency rather than summed, so an ex-date
    # slipping across New Year cannot make one year look like three payments
    # and the next like five. A year with too few payments - usually the
    # partial first year of the data window - is left out rather than
    # mistaken for a cut.
    annual: list[tuple[int, float]] = []
    if frequency:
        by_year: dict[int, list[float]] = {}
        for paid, amount in regular:
            if paid.year < today.year:
                by_year.setdefault(paid.year, []).append(amount)
        for year in sorted(by_year):
            amounts = by_year[year]
            if len(amounts) >= max(1, frequency - 1):
                annual.append((year, sum(amounts) / len(amounts) * frequency))

    return DividendRecord(
        payments=payments, regular=regular, specials=specials,
        payments_per_year=frequency, last_regular_date=last_date,
        last_regular_amount=last_amount, days_since_last=days_since,
        suspended=suspended, current_dividend=current,
        trailing_twelve_months=trailing, annual_history=annual,
        regular_dividend=regular_rate, variable_dividends=variable,
        variable_annual_dividend=variable_annual,
        variable_average_years=variable_years,
        variable_recurring_years=chain if variable else 0,
    )


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

@dataclass
class DerivedDDMAssumptions:
    assumptions: DDMAssumptions
    provenance: dict[str, Provenance] = field(default_factory=dict)
    # CAPM inputs feed cost_of_equity rather than being model assumptions in
    # their own right, but they are overridable and their provenance matters.
    capm_inputs: dict[str, Provenance] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def _statement_ratios(fin: CompanyFinancials) -> dict:
    """ROE, payout ratio and sustainable growth from the company's filings."""
    out: dict = {}
    roes: list[tuple[str, float]] = []
    payouts: list[tuple[str, float]] = []

    for i, row in enumerate(fin.income[:RATIO_WINDOW]):
        net_income = row.get("netIncomeCommon")
        if net_income is None:
            continue
        net_income = float(net_income)

        if i < len(fin.balance):
            equity_now = num(fin.balance[i], "commonStockEquity")
            equity_prior = (num(fin.balance[i + 1], "commonStockEquity")
                            if i + 1 < len(fin.balance) else 0.0)
            equity = ((equity_now + equity_prior) / 2
                      if equity_now > 0 and equity_prior > 0 else equity_now)
            if equity > 0:
                roes.append((str(row.get("fiscalYear")), net_income / equity))

        if i < len(fin.cashflow) and net_income > 0:
            paid = fin.cashflow[i].get("dividendsPaid")
            if paid is not None:
                payouts.append((str(row.get("fiscalYear")), abs(float(paid)) / net_income))

    latest = fin.income[0] if fin.income else {}
    latest_cashflow = fin.cashflow[0] if fin.cashflow else {}
    net_income = latest.get("netIncomeCommon")
    paid = latest_cashflow.get("dividendsPaid")
    buybacks = latest_cashflow.get("stockRepurchased")

    out["latest_fiscal_year"] = latest.get("fiscalYear")
    out["latest_net_income_common"] = float(net_income) if net_income is not None else None
    out["latest_dividends_paid"] = abs(float(paid)) if paid is not None else None
    out["latest_buybacks"] = abs(float(buybacks)) if buybacks is not None else None
    out["latest_payout_ratio"] = (
        abs(float(paid)) / float(net_income)
        if paid is not None and net_income is not None and float(net_income) > 0
        else None)

    out["roe_by_year"] = roes
    out["payout_by_year"] = payouts
    out["return_on_equity"] = median([v for _, v in roes]) if roes else None
    # Includes preferred dividends where Yahoo reports them together, which
    # overstates the common payout slightly and so errs toward a lower
    # sustainable growth rate - the conservative direction.
    out["payout_ratio"] = median([v for _, v in payouts]) if payouts else None
    if out["return_on_equity"] is not None and out["payout_ratio"] is not None:
        retention = max(0.0, 1.0 - min(out["payout_ratio"], 1.0))
        out["sustainable_growth"] = out["return_on_equity"] * retention
    else:
        out["sustainable_growth"] = None
    return out


def _reject_unknown_overrides(fin: CompanyFinancials, overrides: dict) -> None:
    valid = set(DDM_ASSUMPTION_NAMES) | set(COST_OF_EQUITY_INPUT_NAMES)
    unknown = set(overrides) - valid
    if unknown:
        dcf_only = sorted(unknown & set(DCF_ONLY_NAMES))
        other = sorted(unknown - set(DCF_ONLY_NAMES))
        parts = []
        if dcf_only:
            verb = "applies" if len(dcf_only) == 1 else "apply"
            parts.append(f"{', '.join(dcf_only)} {verb} to the DCF, but "
                         f"{fin.company_name} is valued with a "
                         "dividend discount model")
        if other:
            parts.append(f"Unknown assumption override(s): {', '.join(other)}")
        raise ValueError("; ".join(parts)
                         + f". Valid names for this method: {', '.join(sorted(valid))}")

    if "high_growth_years" in overrides:
        years = float(overrides["high_growth_years"])
        if years != int(years) or not 0 <= years <= MAX_HIGH_GROWTH_YEARS:
            raise ValueError(f"high_growth_years must be a whole number from 0 "
                             f"to {MAX_HIGH_GROWTH_YEARS}.")


def derive_ddm_assumptions(fin: CompanyFinancials, dividends: DividendRecord,
                           overrides: dict | None = None) -> DerivedDDMAssumptions:
    """
    Build a starting DDM assumption set for *fin* from its own record.

    `overrides` maps any DDM assumption or CAPM input to a user-supplied value,
    which wins over both derived and default values and is recorded as such.
    """
    overrides = dict(overrides or {})
    _reject_unknown_overrides(fin, overrides)

    provenance: dict[str, Provenance] = {}
    capm_inputs: dict[str, Provenance] = {}
    diagnostics: dict = {}

    def _record_into(store: dict, name: str, value: float, source: str, detail: str,
                     **clamp) -> float:
        if name in overrides:
            supplied = float(overrides[name])
            store[name] = Provenance(name, supplied, "override",
                                     f"supplied by caller (would have been {value:.4g} from {source})")
            return supplied
        store[name] = Provenance(name, value, source, detail, **clamp)
        return value

    def record(name: str, value: float, source: str, detail: str, **clamp) -> float:
        return _record_into(provenance, name, value, source, detail, **clamp)

    def record_input(name: str, value: float, source: str, detail: str) -> float:
        return _record_into(capm_inputs, name, value, source, detail)

    # --- The current dividend (reported, not an overridable assumption) ---
    if dividends.current_dividend is not None:
        schedule = _FREQUENCIES.get(dividends.payments_per_year, "per-year")
        paid = dividends.last_regular_date
        times = dividends.payments_per_year
        diagnostics["current_dividend_detail"] = (
            f"Based on the latest regular dividend of "
            f"${dividends.last_regular_amount:,.2f}, paid on {paid.day} "
            f"{paid.strftime('%B %Y')}, which the company pays "
            f"{'once' if times == 1 else f'{times} times'} a year ({schedule})")
        if dividends.variable_dividends:
            diagnostics["current_dividend_detail"] += (
                f" - ${dividends.regular_dividend:,.2f} a year - plus a variable "
                f"dividend averaging ${dividends.variable_annual_dividend:,.2f} a year "
                f"over the last {dividends.variable_average_years} years, which it has "
                "paid every year")
        diagnostics["current_dividend_detail"] += "."

    # --- Cost of equity: CAPM, with the DCF's own inputs -------------------
    rf, erp, beta = _capm_inputs(fin, record_input)
    capm_cost = rf + beta * erp
    diagnostics["capm"] = {"risk_free_rate": rf, "equity_risk_premium": erp,
                           "beta": beta, "cost_of_equity": capm_cost}
    if SANE_COST_OF_EQUITY[0] <= capm_cost <= SANE_COST_OF_EQUITY[1]:
        cost_of_equity = record("cost_of_equity", capm_cost, "derived",
                                f"CAPM: {rf:.2%} + {beta:.3g}x{erp:.2%}")
    else:
        cost_of_equity = record(
            "cost_of_equity", rf + DEFAULT_BETA * erp, "default",
            f"CAPM gave {capm_cost:.1%}, outside the plausible range; used "
            "risk-free + market beta x equity risk premium")

    # --- Dividend growth: complete-year CAGR, capped at sustainable --------
    ratios = _statement_ratios(fin)
    diagnostics.update(ratios)

    history = dividends.annual_history[-(GROWTH_WINDOW_YEARS + 1):]
    diagnostics["dividend_history_used"] = history

    raw_growth, detail = None, ""
    if len(history) >= MIN_GROWTH_POINTS:
        (first_year, first), (last_year, last) = history[0], history[-1]
        span = last_year - first_year
        if first > 0 and last > 0 and span > 0:
            raw_growth = (last / first) ** (1 / span) - 1
            detail = (f"{span}-year dividend-per-share CAGR, {first_year}-{last_year} "
                      f"(${first:,.2f} -> ${last:,.2f})")
            if dividends.variable_dividends:
                detail += ("; regular dividend only - the recurring variable "
                           "dividend is held at its multi-year average")

    if raw_growth is None:
        dividend_growth = record(
            "dividend_growth", DEFAULT_TERMINAL_GROWTH, "default",
            f"fewer than {MIN_GROWTH_POINTS} complete years of dividend history "
            "to measure growth; used terminal growth")
    else:
        value, was_clamped = _clamp(raw_growth, CLAMP_DIVIDEND_GROWTH)
        notes = []
        why = "range" if was_clamped else None
        if was_clamped:
            notes.append(f"raw {raw_growth:.1%} clamped to "
                         f"[{CLAMP_DIVIDEND_GROWTH[0]:.0%}, {CLAMP_DIVIDEND_GROWTH[1]:.0%}]")

        sustainable = ratios["sustainable_growth"]
        if sustainable is not None:
            ceiling = max(sustainable, CLAMP_DIVIDEND_GROWTH[0])
            if value > ceiling:
                notes.append(
                    f"capped at sustainable growth of {ceiling:.1%} "
                    f"(ROE {ratios['return_on_equity']:.1%} x retention "
                    f"{1 - min(ratios['payout_ratio'], 1.0):.1%}), the rate "
                    "retained earnings can fund")
                value = ceiling
                why = "sustainable"

        if notes:
            dividend_growth = record("dividend_growth", value, "derived (clamped)",
                                     f"{detail}; " + "; ".join(notes),
                                     raw=raw_growth, limit=value, why=why)
        else:
            if sustainable is not None:
                detail += f"; within sustainable growth of {sustainable:.1%}"
            dividend_growth = record("dividend_growth", value, "derived", detail)

    # --- Global defaults ---------------------------------------------------
    terminal_growth = record("terminal_growth", DEFAULT_TERMINAL_GROWTH, "default",
                             "global default (~long-run nominal GDP growth), "
                             "shared with the DCF")
    high_growth_years = record("high_growth_years", DEFAULT_PROJECTION_YEARS, "default",
                               "modelling convention, matching the DCF's forecast horizon")

    return DerivedDDMAssumptions(
        assumptions=DDMAssumptions(
            cost_of_equity=cost_of_equity,
            dividend_growth=dividend_growth,
            high_growth_years=int(high_growth_years),
            terminal_growth=terminal_growth,
        ),
        provenance=provenance,
        capm_inputs=capm_inputs,
        diagnostics=diagnostics,
    )
