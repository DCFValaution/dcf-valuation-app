"""What the market currently pays for a company, beside what the model says.

Reference figures, not model inputs. Nothing here feeds a valuation, and
nothing in a valuation feeds it: these are the multiples a reader would
find on any finance site, carried so the intrinsic estimate can be read
against them without leaving the screen.

They are deliberately kept visually and verbally apart from the figure
above them. A price/earnings ratio is what the market is willing to pay
today; the intrinsic value is what this model thinks the business is
worth. Putting them in one list without a line between them would invite
the reader to treat a market multiple as a second opinion from the model,
which it is not.

Every monetary figure is in the trading currency, which is the currency
the statements have already been converted to and the one the valuation is
expressed in. That is the whole correctness question for a foreign
listing: Alibaba's earnings per share arriving in yuan beside a dollar
price is a price/earnings ratio of 2.5 where the truth is 16.6.
"""

from __future__ import annotations

from dataclasses import dataclass

from market_data import CompanyFinancials, num


@dataclass(frozen=True)
class MarketSnapshot:
    """Current market figures. Any field may be absent."""

    currency: str
    market_cap: float | None
    pe_ratio: float | None
    pb_ratio: float | None
    dividend_yield: float | None
    revenue_ttm: float | None
    net_margin: float | None
    eps: float | None
    current_ratio: float | None

    @property
    def has_anything(self) -> bool:
        return any(v is not None for v in (
            self.market_cap, self.pe_ratio, self.pb_ratio,
            self.dividend_yield, self.revenue_ttm, self.net_margin, self.eps,
            self.current_ratio))


def _positive(value: float | None) -> float | None:
    """A figure that only means something above zero."""
    return value if value is not None and value > 0 else None


# The classifications whose balance sheets are split into current and
# non-current the ordinary way. A bank does not present one: deposits are
# payable on demand and loans run for years, and dividing one Yahoo-inferred
# total by the other produces a number that looks like liquidity and measures
# nothing. Insurers and other balance-sheet lenders are in the same position.
#
# "unclassified" is excluded deliberately. It means the company is in the
# Financial Services sector and the data could not say which kind, so the
# honest reading is that the ratio may not apply - and the dash says exactly
# that. A fee-based financial (Visa, an asset manager) does present a
# classified balance sheet, so it keeps the ratio.
_CLASSIFIED_BALANCE_SHEET = frozenset({"not_financial", "fee_based"})


def _current_ratio(fin: CompanyFinancials,
                   balance: dict) -> float | None:
    """Current assets over current liabilities, where that division means
    something.

    Two gates, and both are needed. The first is what the company is: a
    bank or an insurer is withheld even when the data source hands over a
    pair of figures, because the ratio is not a liquidity measure on an
    unclassified balance sheet. The second is whether the figures exist at
    all - they are genuinely absent for lenders, and absent for anyone else
    whose filing was not parsed into the two lines.

    No currency concern: both sides are converted by the same scalar on the
    way in, so the rate cancels.
    """
    # Imported here, not at module scope: analysis imports this module, so
    # a top-level import the other way is a cycle.
    from analysis import classify_financial

    if classify_financial(fin).kind not in _CLASSIFIED_BALANCE_SHEET:
        return None
    assets = _positive(num(balance, "totalCurrentAssets"))
    liabilities = _positive(num(balance, "totalCurrentLiabilities"))
    return assets / liabilities if (assets and liabilities) else None


def market_snapshot(fin: CompanyFinancials,
                    price: float | None) -> MarketSnapshot | None:
    """Build the snapshot, leaving out anything that is not knowable.

    Absent is absent. Every field that cannot be computed honestly comes
    back None and is rendered as a dash, because a zero in a ratio column
    reads as a measurement and a negative price/earnings ratio reads as a
    bargain.
    """
    if not fin.income:
        return None

    income, balance = fin.income[0], (fin.balance[0] if fin.balance else {})
    cashflow = fin.cashflow[0] if fin.cashflow else {}

    revenue = _positive(num(income, "revenue"))
    net_income = income.get("netIncomeCommon")
    net_income = float(net_income) if net_income is not None else None
    eps = income.get("dilutedEPS")
    eps = float(eps) if eps is not None else None

    # Market capitalisation from the quote where there is one, and from
    # price times the diluted share count where there is not. The crumb-free
    # profile a cloud deployment usually gets omits market cap entirely, and
    # the share count it does carry comes off the income statement, so the
    # product is available on the path where the quoted figure is not.
    market_cap = fin.profile.get("marketCap")
    shares = fin.profile.get("sharesOutstanding") or income.get(
        "weightedAverageShsOutDil")
    if market_cap is None and price and shares:
        market_cap = float(price) * float(shares)
    market_cap = _positive(float(market_cap) if market_cap else None)

    # A price/earnings ratio on a loss is not a low multiple, it is not a
    # multiple: the arithmetic returns a negative number that reads like a
    # bargain, and it is withheld.
    #
    # Earnings per share is not withheld with it. A loss of $1.50 a share
    # is a true figure and one of the more useful ones on the card, and
    # hiding it would put a dash beside a net margin of -2.0% printed in
    # the next column - two fields disagreeing about whether the company
    # made money. The ratio is meaningless; the number is not.
    profitable_eps = _positive(eps)
    pe_ratio = (price / profitable_eps
                if profitable_eps and price and price > 0 else None)

    equity = _positive(num(balance, "commonStockEquity"))
    pb_ratio = market_cap / equity if (market_cap and equity) else None

    # Dividends are reported as a negative number on the cash flow
    # statement. A company that pays none is not yielding zero in any
    # interesting sense - it has no dividend - so it gets a dash.
    paid = cashflow.get("dividendsPaid")
    paid = abs(float(paid)) if paid is not None else None
    dividend_yield = (paid / market_cap
                      if (paid and paid > 0 and market_cap) else None)

    net_margin = (net_income / revenue
                  if (net_income is not None and revenue) else None)

    current_ratio = _current_ratio(fin, balance)

    snapshot = MarketSnapshot(
        currency=fin.trading_currency or fin.profile.get("currency") or "USD",
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        pb_ratio=pb_ratio,
        dividend_yield=dividend_yield,
        revenue_ttm=revenue,
        net_margin=net_margin,
        eps=eps,
        current_ratio=current_ratio,
    )
    return snapshot if snapshot.has_anything else None
