"""
Plain-language wording for warnings the app shows to people.

The model records how every assumption was reached in precise, technical
terms - "nwc_pct was clamped: aggregate change in NWC / change in revenue,
FY2022-FY2025; raw -28.7% clamped to [-10%, 30%]". That record stays as it is:
it belongs in the workbook and the API, where exactness matters. This module
writes the version for someone reading a phone: what was adjusted, why, and
what it means for the figure, without variable names, brackets or raw ratios.

Every message is written from the structured facts recorded alongside the
technical detail (Provenance.raw, .limit, .why), never by parsing that text.
"""

from __future__ import annotations

from assumptions import Provenance


def _pct(value: float) -> str:
    """A percentage as a person would say it: 15%, 2.5%, not 15.0% or 2.50%."""
    text = f"{value * 100:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + "%"


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def clamp_warning(prov: Provenance, company: str) -> str:
    """Explain, in plain terms, an assumption held to a realistic limit."""
    name, raw, limit, why = prov.name, prov.raw, prov.limit, prov.why
    whose = _possessive(company)

    # Without the structured facts (a clamp recorded by code that predates
    # them) fall back to a general sentence rather than leak the raw record.
    if raw is None or limit is None:
        label = _LABELS.get(name, "one of the assumptions")
        return (f"The model adjusted {label} for {company} to keep it within a realistic "
                "range, because the company's recent figures were unusually extreme.")

    above = raw > limit

    if name == "nwc_pct":
        if above:
            return (f"{whose} recent figures show it tying up an unusually large amount of "
                    "cash in stock and unpaid customer bills as it grew. That is unlikely to "
                    "continue at the same pace, so the forecast assumes a more typical level "
                    "to keep the estimate realistic.")
        return (f"{whose} recent figures show it freeing up an unusually large amount of "
                "cash from stock, customer payments and supplier bills as it grew. A company "
                "can rarely keep doing that indefinitely, so the forecast assumes a more "
                "typical level to keep the estimate realistic.")

    if name == "mature_nwc_pct":
        if above:
            return (f"{company} currently ties up an unusually large amount of cash in stock "
                    "and unpaid customer bills as it grows. The estimate assumes this settles "
                    "to a more typical level once the business matures.")
        return (f"{company} currently frees up cash from stock, customer payments and "
                "supplier bills as it grows, which a maturing business rarely keeps doing. "
                "The estimate assumes this stops once the business matures.")

    if name == "revenue_growth":
        if above:
            return (f"{whose} revenue grew {_pct(raw)} a year over the period used - faster "
                    "than a company can usually keep up for a whole forecast. The forecast "
                    f"uses {_pct(limit)} a year instead, to keep the estimate realistic.")
        return (f"{whose} revenue shrank over the period used. The forecast assumes it holds "
                "steady rather than projecting the decline forever, which is worth keeping "
                "in mind: if the decline continues, the estimate will be too high.")

    if name == "operating_margin":
        if above:
            return (f"{whose} operating margin of {_pct(raw)} is higher than almost any "
                    f"business sustains over time. The forecast uses {_pct(limit)} instead, "
                    "to keep the estimate realistic.")
        return (f"{whose} recent losses were so deep that the forecast limits how negative "
                "its profit margin can be, to keep the model working.")

    if name == "tax_rate":
        if above:
            return (f"{whose} recent tax rate looked unusually high ({_pct(raw)}), most "
                    f"likely because of one-off charges. The forecast uses {_pct(limit)}.")
        return (f"{whose} recent tax figures showed a net tax credit, which will not keep "
                "happening. The forecast assumes no tax benefit instead.")

    if name == "da_pct":
        return (f"{whose} depreciation was unusually large relative to its revenue. The "
                f"forecast uses {_pct(limit)} of revenue instead, to keep the estimate "
                "realistic.")

    if name == "capex_pct":
        return (f"{company} spent an unusually large share of its revenue on investment "
                f"recently ({_pct(raw)}). The forecast uses {_pct(limit)} instead, since "
                "spending at that level rarely lasts.")

    if name == "dividend_growth":
        if why == "sustainable":
            return (f"{whose} dividend grew {_pct(raw)} a year recently - faster than its "
                    f"profits can support for long. The model uses {_pct(limit)} a year, "
                    "the rate the company can pay for out of the earnings it keeps.")
        if above:
            return (f"{whose} dividend grew {_pct(raw)} a year recently, faster than a "
                    f"dividend can usually keep growing. The model uses {_pct(limit)} a year "
                    "instead.")
        return (f"{whose} dividend has been shrinking. The model assumes it holds steady "
                "rather than continuing to fall - if cuts continue, the estimate will be "
                "too high.")

    if name == "speculative_revenue_growth":
        if above:
            return (f"{whose} recent revenue growth ({_pct(raw)}) is faster than can be "
                    "assumed to last all the way to profitability. The estimate uses "
                    f"{_pct(limit)} a year instead.")
        return (f"{whose} revenue has been shrinking. The estimate assumes it stops falling, "
                "which is optimistic.")

    if name == "years_to_profitability":
        if above:
            return (f"At the pace assumed, {company} would take {raw:.0f} years to become "
                    f"profitable. The estimate is limited to {limit:.0f} years, because a "
                    "longer path is too uncertain to model at all.")
        return (f"{company} is close to profitability, but the estimate still allows at "
                f"least {limit:.0f} years to get there, since the last stretch is rarely "
                "quick.")

    if name == "wacc" and why == "floor":
        return (f"{whose} calculated discount rate was {_pct(raw)}. For a company that has "
                f"never made a profit, the estimate uses at least {_pct(limit)}, to reflect "
                "the extra risk of it never getting there.")

    label = _LABELS.get(name, "one of the assumptions")
    return (f"The model adjusted {label} for {company} to keep it within a realistic range, "
            "because the company's recent figures were unusually extreme.")


_LABELS = {
    "revenue_growth": "revenue growth",
    "operating_margin": "operating margin",
    "tax_rate": "the tax rate",
    "da_pct": "depreciation",
    "capex_pct": "investment spending",
    "nwc_pct": "working capital",
    "mature_nwc_pct": "working capital",
    "dividend_growth": "dividend growth",
    "speculative_revenue_growth": "revenue growth",
    "years_to_profitability": "the years to profitability",
    "wacc": "the discount rate",
}


def dividend_growth_default_warning(prov: Provenance, company: str) -> str:
    """Dividend growth could not be measured, so a long-run rate was assumed."""
    return (f"{company} does not have enough years of dividend history to measure how fast "
            f"its dividend grows, so the model assumes it grows at a long-run rate of "
            f"{_pct(prov.value)} a year. Adjust it if you expect otherwise.")


def growth_below_terminal_warning(company: str, revenue_growth: float,
                                  terminal_growth: float, forecast_years: int) -> str:
    """The forecast assumes faster growth after the forecast period than during it."""
    return (f"The forecast has {company} growing {_pct(revenue_growth)} a year for the next "
            f"{forecast_years} years, then {_pct(terminal_growth)} a year forever after. "
            "Growing faster in the long run than in the near term is unusual, so it is worth "
            "checking whether either assumption is too low or too high.")
