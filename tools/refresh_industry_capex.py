"""Regenerate `industry_capex_data.py` from Damodaran's published capex table.

    python tools/refresh_industry_capex.py [--check]

The sibling of `refresh_industry_betas.py`, and the same annual job: Damodaran
recomputes these in the first week of January and leaves them at the same URL
all year, so the figures are baked into the repository rather than fetched at
valuation time. Baking them in is what makes a report reproducible.

`--check` prints what would change and exits non-zero if anything would.

WHAT IS TAKEN, AND WHY
----------------------
Three published ratios, and nothing derived:

  capex_to_depreciation       spent for every dollar depreciated
  net_capex_to_sales          net investment as a share of revenue
  sales_to_invested_capital   Damodaran's sales-to-capital ratio

The last is not used by the convergence path but belongs with the others:
it is the figure his own models reinvest by, and the one his NVIDIA model
uses at 1.21 - which is this table's Semiconductor row.

An earlier version of this script recovered each industry's sales from the
dollar totals (sales = net capex / (net capex / sales)) and computed gross
ratios from it. It dropped 21 of 96 industries, Pharmaceuticals and
Aerospace among them, because an industry whose capital expenditure and
depreciation are close has a net figure near zero, and that division turns
rounding into nonsense. The published ratios need no such division, so
every industry survives.

The judgement - what a mature company's investment and depreciation settle
at, and the clamps that keep the answer a steady state - is deliberately NOT
here. It lives in `reinvestment.py`, where it can be read and argued with.
A generated file should hold what was published and nothing else.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import ssl
import sys
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAPEX_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/capex.xls"
SHEET = "Industry Averages"
HEADER_ROW = 7
TARGET = "industry_capex_data.py"

COLUMNS = {
    "firms": "Number of Firms",
    "capex_to_depreciation": "Cap Ex/Deprecn",
    "net_to_sales": "Net Cap Ex/Sales",
    "sales_to_capital": "Sales/ Invested Capital (LTM)",
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "dcf-valuation-app/industry-capex"})
    with urllib.request.urlopen(
            request, timeout=60,
            context=ssl.create_default_context()) as response:
        return response.read()


def rows(data: bytes) -> list[dict]:
    import xlrd

    sheet = xlrd.open_workbook(file_contents=data).sheet_by_name(SHEET)
    header = [str(v).strip() for v in sheet.row_values(HEADER_ROW)]
    missing = [name for name in COLUMNS.values() if name not in header]
    if missing:
        raise SystemExit(
            "The sheet's columns have changed upstream; this script is "
            "reading the wrong ones. Missing: " + ", ".join(missing))
    index = {key: header.index(name) for key, name in COLUMNS.items()}

    out = []
    for r in range(HEADER_ROW + 1, sheet.nrows):
        row = sheet.row_values(r)
        name = str(row[0]).strip()
        if not name:
            continue
        try:
            capex_to_depreciation = float(row[index["capex_to_depreciation"]])
            net_to_sales = float(row[index["net_to_sales"]])
            firms = int(float(row[index["firms"]]))
            sales_to_capital = float(row[index["sales_to_capital"]])
        except (TypeError, ValueError):
            continue
        if capex_to_depreciation <= 0:
            continue

        out.append({
            "name": name,
            "firms": firms,
            "capex_to_depreciation": capex_to_depreciation,
            "net_capex_to_sales": net_to_sales,
            "sales_to_invested_capital": sales_to_capital,
        })
    if len(out) < 50:
        raise SystemExit(f"Only {len(out)} industries parsed; the sheet's "
                         "shape has probably changed.")
    return out


def as_of(data: bytes) -> date:
    import xlrd

    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_name(SHEET)
    value = sheet.cell_value(0, 1)
    y, m, d, *_ = xlrd.xldate_as_tuple(float(value), book.datemode)
    return date(y, m, d)


def render(parsed: list[dict], source_date: date) -> str:
    lines = [
        '"""Damodaran\'s industry investment intensities. GENERATED - do not '
        'edit by hand.',
        "",
        "Regenerate with `python tools/refresh_industry_capex.py`, which "
        "fetches the",
        "spreadsheet below and rewrites this file. Which SIC code belongs to "
        "which of",
        "these industries is the hand-written part, and it lives in "
        "`industry_betas.py`",
        "- the same map serves both tables, because they are the same "
        "industries.",
        "",
        "Published ratios only. What a mature company's investment and "
        "depreciation",
        "settle at is a judgement, and it lives in `reinvestment.py` rather "
        "than in a",
        "generated file.",
        "",
        "    source:  " + CAPEX_URL,
        "    as of:   " + source_date.isoformat(),
        "    fetched: " + datetime.now(timezone.utc).date().isoformat(),
        '"""',
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from datetime import date",
        "",
        "",
        "@dataclass(frozen=True)",
        "class IndustryCapex:",
        '    """One row of Damodaran\'s capital-expenditure table."""',
        "",
        "    name: str",
        "    firms: int",
        "    capex_to_depreciation: float",
        "    net_capex_to_sales: float",
        "    sales_to_invested_capital: float",
        "",
        "",
        f"DAMODARAN_CAPEX_AS_OF = date({source_date.year}, "
        f"{source_date.month}, {source_date.day})",
        f"DAMODARAN_CAPEX_URL = ({CAPEX_URL!r})",
        "",
        "# The whole-market row, used when a company's SIC code has no "
        "industry in",
        "# this table - an honest \"we do not know this company's industry\" "
        "rather",
        "# than a guess at one.",
        'TOTAL_MARKET = "Total Market"',
        "",
        "INDUSTRY_CAPEX: dict[str, IndustryCapex] = {",
    ]
    for row in sorted(parsed, key=lambda r: r["name"]):
        lines.append(f"    {row['name']!r}: IndustryCapex(")
        lines.append(f"        {row['name']!r}, {row['firms']},")
        lines.append(f"        {row['capex_to_depreciation']:.6f}, "
                     f"{row['net_capex_to_sales']:.6f},")
        lines.append(f"        {row['sales_to_invested_capital']:.6f}),")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    data = fetch(CAPEX_URL)
    rendered = render(rows(data), as_of(data))
    target = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), TARGET)

    existing = ""
    if os.path.exists(target):
        with io.open(target, encoding="utf-8") as handle:
            existing = handle.read()

    # The fetched date changes every run and says nothing about the data, so
    # it is ignored when deciding whether anything moved.
    def without_fetched(text: str) -> str:
        return re.sub(r"    fetched: \d{4}-\d{2}-\d{2}\n", "", text)

    if without_fetched(existing) == without_fetched(rendered):
        print(f"{TARGET} is up to date.")
        return 0

    if args.check:
        print(f"{TARGET} differs from its source.")
        return 1

    with io.open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(f"Wrote {TARGET}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
