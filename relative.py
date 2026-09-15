"""
Relative valuation: how the market prices similar companies right now.

A second opinion beside an intrinsic value, never a replacement for one. The
DCF and DDM ask what a company's cash flows are worth on stated assumptions;
this asks what the market currently pays for comparable companies, and applies
that to this company's own figures. The two can agree or disagree, and neither
is made right by agreeing: if a whole sector is overpriced, its median multiple
is too, and so is everything derived from it.

Pure arithmetic over MarketFigures. Peer selection and the honesty framing live
in analysis.py.

MULTIPLES
---------
    P/E          market value / trailing net income to common
    EV/EBITDA    (market value + debt - cash) / trailing EBITDA
    Price/Sales  market value / trailing revenue
    Price/Book   market value / book value of common equity

A DCF-valued company is compared on P/E, EV/EBITDA and price/sales. A bank or
insurer, valued with the DDM, on P/E and price/book: its debt is raw material,
so enterprise value and EBITDA mean nothing, and its "revenue" is not sales.

A multiple is skipped rather than computed where it has no meaning - P/E for a
company without profits, EV/EBITDA without positive EBITDA - and so is any
multiple above a ceiling, where it describes a denominator close to zero rather
than how the market prices a business: probing found CrowdStrike at 4,752x
earnings and Tesla at 377x. Medians, not means, and only from at least three
peers: a median of two companies is just their average.

And at least two multiples must survive all of that before any value is
reported - see MIN_APPLICABLE_MULTIPLES.
"""

from dataclasses import dataclass
from datetime import date
from statistics import median

MIN_PEERS = 3
MIN_PEER_VALUES = 3
# A relative valuation needs more than one multiple to stand on. Salesforce's
# peer group left only price-to-sales - with an unprofitable Snowflake at 21x
# pulling the median - and a figure resting on that alone moves with a single
# peer, with nothing to cross-check it. Below this, the multiples are reported
# as information and no value is drawn from them.
MIN_APPLICABLE_MULTIPLES = 2
MAX_AUTO_PEERS = 6          # modest, to limit requests against a throttled source
MAX_PEERS = 8               # including any a user adds
SIZE_BAND = (0.1, 10.0)     # peer market value as a multiple of the company's
STALE_AFTER_DAYS = 456      # about fifteen months

MULTIPLE_CEILINGS = {"pe": 100.0, "ev_ebitda": 60.0, "ps": 30.0, "pb": 20.0}
LABELS = {"pe": "P/E", "ev_ebitda": "EV/EBITDA", "ps": "Price/Sales", "pb": "Price/Book"}
STANDARD_MULTIPLES = ("pe", "ev_ebitda", "ps")
FINANCIAL_MULTIPLES = ("pe", "pb")
WIDE_RANGE_RATIO = 2.0


def multiple_of(figures, name: str) -> tuple[float | None, str | None]:
    """(value, reason it is not meaningful) for one company and one multiple."""
    cap = figures.market_cap
    if cap is None or cap <= 0:
        return None, "no current share price or share count, so its market value is unknown"

    if name == "pe":
        if figures.net_income is None:
            return None, "no recent earnings figures are available"
        if figures.net_income <= 0:
            return None, "not profitable over the last twelve months"
        value = cap / figures.net_income
    elif name == "ev_ebitda":
        if figures.ebitda is None:
            return None, "no recent EBITDA figures are available"
        if figures.ebitda <= 0:
            return None, "EBITDA was not positive over the last twelve months"
        value = figures.enterprise_value / figures.ebitda
        if value <= 0:
            return None, ("it holds more cash than its debt and market value combined, so this "
                          "ratio does not mean anything")
    elif name == "ps":
        if not figures.revenue or figures.revenue <= 0:
            return None, "no recent revenue figures are available"
        value = cap / figures.revenue
    elif name == "pb":
        if figures.common_equity is None:
            return None, "no book value is reported"
        if figures.common_equity <= 0:
            return None, "its book value is not positive"
        value = cap / figures.common_equity
    else:
        raise ValueError(f"Unknown multiple: {name}")

    ceiling = MULTIPLE_CEILINGS[name]
    if value > ceiling:
        return None, (f"at {value:,.0f}x it is far outside the normal range - its "
                      f"{_DENOMINATORS[name]} are too small for this ratio to say much about "
                      "how the market values the business")
    return value, None


# What each multiple divides by, in words, for explaining an extreme value.
_DENOMINATORS = {"pe": "earnings", "ev_ebitda": "operating earnings (EBITDA)",
                 "ps": "sales", "pb": "net assets"}


def _peers(n: int) -> str:
    return "1 peer has" if n == 1 else f"{n} peers have"


def implied_value_per_share(figures, name: str, multiple: float) -> float | None:
    """The per-share value *figures* would carry at *multiple*."""
    shares = figures.shares
    if not shares or shares <= 0:
        return None
    if name == "pe":
        return multiple * figures.net_income / shares
    if name == "ps":
        return multiple * figures.revenue / shares
    if name == "pb":
        return multiple * figures.common_equity / shares
    if name == "ev_ebitda":
        enterprise = multiple * figures.ebitda
        return (enterprise - (figures.total_debt or 0.0) + (figures.cash or 0.0)) / shares
    raise ValueError(f"Unknown multiple: {name}")


def peer_exclusion(company, peer, today: date, enforce_size: bool = True) -> str | None:
    """Why *peer* cannot be compared with *company*, or None if it can."""
    if peer.ticker == company.ticker:
        return "is the company itself"
    if peer.name and company.name and peer.name.lower() == company.name.lower():
        return "another share class of the same company"
    if (peer.trading_currency and peer.reporting_currency
            and peer.trading_currency != peer.reporting_currency):
        return (f"it reports its results in {peer.reporting_currency} but its shares trade in "
                f"{peer.trading_currency}, so its figures cannot be compared fairly")
    if peer.price is None or not peer.shares:
        return "no current price or share count"
    if not peer.as_of:
        return "no recent figures are available"
    try:
        age = (today - date.fromisoformat(peer.as_of)).days
    except ValueError:
        return "its recent figures have no usable date"
    if age > STALE_AFTER_DAYS:
        return f"its latest figures are from {peer.as_of}, too old to compare with today's price"
    if enforce_size and company.market_cap and peer.market_cap:
        ratio = peer.market_cap / company.market_cap
        if ratio > SIZE_BAND[1]:
            return (f"it is more than {SIZE_BAND[1]:g} times the size of {company.ticker} - too "
                    "different in size to compare fairly")
        if ratio < SIZE_BAND[0]:
            return (f"it is less than 1/{round(1 / SIZE_BAND[0])} the size of "
                    f"{company.ticker} - too different in size to compare fairly")
    return None


@dataclass
class PeerMultiple:
    ticker: str
    value: float | None
    excluded_reason: str | None


@dataclass
class MultipleResult:
    name: str
    label: str
    company_value: float | None
    company_reason: str | None        # why it is not meaningful for the company
    peers: list[PeerMultiple]
    peer_median: float | None
    peer_count: int                   # peers with a meaningful value
    premium_to_median: float | None   # company / median - 1
    implied_value_per_share: float | None
    applicable: bool
    reason: str | None                # why it was not applied


@dataclass
class RelativeResult:
    multiples: list[MultipleResult]
    central_value_per_share: float | None   # median of the applied multiples' values
    low_value_per_share: float | None
    high_value_per_share: float | None
    current_price: float | None
    central_vs_price: float | None


def run_relative(company, peers: list, multiples: tuple[str, ...]) -> RelativeResult:
    results: list[MultipleResult] = []

    for name in multiples:
        label = LABELS[name]
        own, own_reason = multiple_of(company, name)
        rows = [PeerMultiple(p.ticker, *multiple_of(p, name)) for p in peers]
        values = [row.value for row in rows if row.value is not None]
        peer_median = median(values) if len(values) >= MIN_PEER_VALUES else None

        applicable, reason, implied = True, None, None
        if own is None:
            applicable, reason = False, f"not meaningful for {company.ticker}: {own_reason}"
        elif peer_median is None:
            applicable, reason = False, (
                f"only {_peers(len(values))} a usable {label}; at least {MIN_PEER_VALUES} are "
                "needed to describe the group rather than one or two companies")
        else:
            implied = implied_value_per_share(company, name, peer_median)
            if implied is None or implied <= 0:
                applicable, reason, implied = False, (
                    "at the peers' typical multiple, this company's debt would be larger than "
                    "the value of its business, leaving no positive value per share"), None

        results.append(MultipleResult(
            name=name, label=label, company_value=own, company_reason=own_reason, peers=rows,
            peer_median=peer_median, peer_count=len(values),
            premium_to_median=(own / peer_median - 1) if own is not None and peer_median else None,
            implied_value_per_share=implied, applicable=applicable, reason=reason,
        ))

    implied_values = [r.implied_value_per_share for r in results if r.applicable]
    central = median(implied_values) if implied_values else None
    price = company.price
    return RelativeResult(
        multiples=results,
        central_value_per_share=central,
        low_value_per_share=min(implied_values) if implied_values else None,
        high_value_per_share=max(implied_values) if implied_values else None,
        current_price=price,
        central_vs_price=(central / price - 1) if central is not None and price else None,
    )
