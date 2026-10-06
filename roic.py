"""
Return on invested capital, as a diagnostic beside the valuation.

This answers a different question from the DCF. The model asks what the
future cash flows are worth; this asks whether the business has historically
earned more on the capital tied up in it than that capital costs. A company
can be cheap and value-destroying, or expensive and excellent, and the two
figures disagreeing is information rather than an error.

It is read-only. Nothing here feeds an assumption, a projection, a terminal
value or a share price, and nothing here appears in the workbook: the
workbook is a model to argue with, and this is a historical fact with no
cell behind it.

Two choices are worth recording, because both standard definitions are
defensible and this one was picked on evidence rather than taste.

Invested capital is built from the *assets* side - what is tied up in the
business - rather than the financing side (debt + equity - cash). The
financing basis measures a company's financing history as much as its
operations: Apple's decades of buybacks have shrunk book equity to the point
that the financing basis reads about 95% against 67% here, and for a lender
it goes negative outright. The assets side is steadier and does not reward a
company for having bought its own shares back.

The figure is the median of the annual returns over the same window the rest
of the app uses for its ratios, not the latest year. One bad year otherwise
decides it: Ford's latest year reads -4.5% against a median of +2.6%.
"""

from dataclasses import dataclass, field
from statistics import median

from assumptions import RATIO_WINDOW
from market_data import CompanyFinancials

# How far ROIC must sit from WACC before the difference is called either way.
#
# Not a hedge about the arithmetic: the verdict survived every definition
# tried and a +/-1pp move in the equity risk premium. It is about what the
# gap can bear. A regulated utility is allowed by its regulator to earn
# roughly its cost of capital, and Southern and Duke land about 1.3pp below
# theirs; calling that value destruction would be a strong claim to make from
# book figures and a 1.3pp gap.
NEUTRAL_BAND = 0.02

ABOVE = "above"
ABOUT = "about"
BELOW = "below"


@dataclass
class CapitalYear:
    """One year's return, kept so the figure can be shown its workings."""
    fiscal_year: str
    nopat: float
    invested_capital: float
    roic: float


@dataclass
class CapitalReturns:
    roic: float
    wacc: float
    verdict: str                      # ABOVE | ABOUT | BELOW
    detail: str                       # how the number was arrived at
    years: list[CapitalYear] = field(default_factory=list)

    @property
    def spread(self) -> float:
        """Percentage points by which the business out-earns its capital."""
        return self.roic - self.wacc

    @property
    def sentence(self) -> str:
        """The plain-language read, without the company's name in it.

        The caller has the name and knows where the sentence is going, so it
        is left out here rather than guessing at the grammar around it.
        """
        if self.verdict == ABOVE:
            return "earns more on the capital in the business than that capital costs"
        if self.verdict == BELOW:
            return "earns less on the capital in the business than that capital costs"
        return "earns about what the capital in the business costs"


def _num(row: dict | None, key: str) -> float:
    value = row.get(key) if row else None
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def invested_capital(balance_row: dict) -> float:
    """What is tied up in the business, from the assets side.

    Total assets, less the current liabilities that are not borrowings -
    payables and accruals are financing the supplier extends for free, so
    they are not capital anyone invested - and less cash, which is not at
    work in the operations.

    Short-term debt is deliberately *not* netted off: it is borrowed money
    with a cost, so it stays in.
    """
    non_debt_current = max(
        _num(balance_row, "totalCurrentLiabilities") - _num(balance_row, "shortTermDebt"),
        0.0,
    )
    cash = (_num(balance_row, "cashAndCashEquivalents")
            + _num(balance_row, "shortTermInvestments"))
    return _num(balance_row, "totalAssets") - non_debt_current - cash


def nopat(income_row: dict, tax_rate: float) -> float:
    """Operating profit after tax, at the tax rate the app already derived.

    Each year's own effective rate is noisier and undefined in a loss year,
    and a second tax derivation living here could drift from the one the
    valuation uses.
    """
    return _num(income_row, "operatingIncome") * (1 - tax_rate)


def verdict_for(roic: float, wacc: float) -> str:
    spread = roic - wacc
    if spread > NEUTRAL_BAND:
        return ABOVE
    if spread < -NEUTRAL_BAND:
        return BELOW
    return ABOUT


def compute(fin: CompanyFinancials, tax_rate: float, wacc: float) -> CapitalReturns | None:
    """The diagnostic, or None where it would not mean anything.

    Returns None rather than a misleading number: a company with no usable
    balance sheet, or whose invested capital comes out at or below zero, has
    no return on capital to report. Loss-makers and lenders never reach here
    - the valuation refuses them first - but the guards hold independently so
    that this stays true if a caller changes.
    """
    years: list[CapitalYear] = []
    window = min(RATIO_WINDOW, len(fin.income), len(fin.balance))
    for i in range(window):
        income_row, balance_row = fin.income[i], fin.balance[i]
        capital = invested_capital(balance_row)
        if capital <= 0:
            continue
        profit = nopat(income_row, tax_rate)
        years.append(CapitalYear(
            fiscal_year=str(income_row.get("fiscalYear", "?")),
            nopat=profit,
            invested_capital=capital,
            roic=profit / capital,
        ))

    if not years:
        return None

    value = median(year.roic for year in years)
    return CapitalReturns(
        roic=value,
        wacc=wacc,
        verdict=verdict_for(value, wacc),
        detail=(
            f"median of {len(years)} year(s): operating profit after tax at "
            f"{tax_rate:.1%}, over total assets less non-debt current "
            "liabilities and cash"
        ),
        years=years,
    )
