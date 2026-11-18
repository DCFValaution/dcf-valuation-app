"""Regenerate `industry_beta_data.py` from Damodaran's published betas.

    python tools/refresh_industry_betas.py [--check]

Damodaran recomputes his industry averages in the first week of January and
leaves them at the same URL all year, so this is an annual job and the
figures are baked into the repository rather than fetched at valuation time.
Baking them in is also what makes the number in a report reproducible: the
same ticker on the same day gives the same answer whether or not NYU's
server is up.

`--check` prints what would change and exits non-zero if anything would,
which is how a test can assert the committed table matches its source
without hitting the network.

The sheet's "Unlevered beta corrected for cash" is the column taken. An
unlevered beta is the industry's business risk with its own financing
stripped out, which is what can be relevered onto a different company's
capital structure; correcting for cash removes the drag of balance-sheet
cash, which is a financing choice rather than a business one.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
SHEET = "Industry Averages"
HEADER_ROW = 9          # the sheet carries nine rows of title and notes
TARGET = "industry_beta_data.py"

COLUMNS = {
    "firms": "Number of firms",
    "levered": "Beta",
    "unlevered": "Unlevered beta corrected for cash",
    "debt_to_equity": "D/E Ratio",
    "tax_rate": "Effective Tax rate",
}

PREAMBLE = '''"""Damodaran's industry average betas. GENERATED - do not edit by hand.

Regenerate with `python tools/refresh_industry_betas.py`, which fetches the
spreadsheet below and rewrites this file. The hand-written part of this
subject - which SIC code belongs to which of these industries - is in
`industry_betas.py`, and is deliberately kept out of a generated file.

Each entry holds the industry's unlevered beta corrected for cash, which is
the figure that can be relevered onto a particular company's capital
structure, together with the averages it was computed from so a reader can
see how thin the average is. `firms` is the one to look at first: an average
over four companies is not the same kind of evidence as an average over five
hundred.

    source:  {url}
    as of:   {as_of}
    fetched: {fetched}
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class IndustryBeta:
    """One row of Damodaran's industry table."""

    name: str
    firms: int
    levered_beta: float
    unlevered_beta: float       # corrected for cash
    debt_to_equity: float
    tax_rate: float


# Damodaran dates the table by the data it was computed from, which is the
# start of the calendar year, not the day it was downloaded.
DAMODARAN_BETAS_AS_OF = date({year}, {month}, {day})
DAMODARAN_BETAS_URL = ({url!r})

# The whole-market row, used when a company's SIC code has no industry in
# this table - which is an honest "we do not know this company's industry"
# and not a guess at one.
TOTAL_MARKET = "Total Market"

INDUSTRY_BETAS: dict[str, IndustryBeta] = {{
'''


def fetch() -> bytes:
    from public_data import _get

    body = _get(BETAS_URL, timeout=90, binary=True)
    if not body:
        raise SystemExit(f"{BETAS_URL} returned nothing.")
    return body


def as_of_from(raw: bytes) -> date:
    """The date Damodaran's own title row carries, not today's."""
    import pandas as pd

    head = pd.read_excel(io.BytesIO(raw), sheet_name=SHEET, header=None,
                         nrows=HEADER_ROW)
    # "Date updated:" in the first cell, the date itself beside it. Pandas
    # hands that back as a Timestamp, so the common case needs no parsing.
    for row in head.values:
        cells = list(row)
        for i, cell in enumerate(cells[:-1]):
            if str(cell).strip().lower().startswith("date updated"):
                beside = cells[i + 1]
                if hasattr(beside, "date"):
                    return beside.date()
    text = " ".join(str(cell) for row in head.values for cell in row
                    if str(cell) != "nan")
    # "Date of Analysis: 05-Jan-26", "January 2026" and "1/5/2026" have all
    # appeared in this cell over the years, so several shapes are tried and
    # the absence of one is an error rather than a silent default to today.
    for pattern, fmt in ((r"(\d{1,2}-[A-Za-z]{3}-\d{2})\b", "%d-%b-%y"),
                         (r"(\d{1,2}/\d{1,2}/\d{4})", "%m/%d/%Y"),
                         (r"([A-Za-z]+ \d{4})", "%B %Y")):
        found = re.search(pattern, text)
        if found:
            return datetime.strptime(found.group(1), fmt).date()
    raise SystemExit("No date of analysis in the sheet's header rows; "
                     "Damodaran changed the layout and this needs a look.")


def rows(raw: bytes) -> list[dict]:
    import pandas as pd

    frame = pd.read_excel(io.BytesIO(raw), sheet_name=SHEET, header=HEADER_ROW)
    frame = frame.rename(columns=lambda c: str(c).strip())
    name_column = next(c for c in frame.columns if c.startswith("Industry"))
    out = []
    for _, row in frame.iterrows():
        name = str(row[name_column]).strip()
        if not name or name == "nan":
            continue
        entry = {"name": name}
        for key, column in COLUMNS.items():
            value = row.get(column)
            if value is None or value != value:      # NaN
                entry = {}
                break
            entry[key] = float(value)
        if entry:
            out.append(entry)
    return out


def render(table: list[dict], as_of: date) -> str:
    body = PREAMBLE.format(
        url=BETAS_URL, as_of=as_of.isoformat(),
        fetched=datetime.now(timezone.utc).date().isoformat(),
        year=as_of.year, month=as_of.month, day=as_of.day)
    for entry in table:
        body += (f'    {entry["name"]!r}: IndustryBeta(\n'
                 f'        {entry["name"]!r}, {int(entry["firms"])}, '
                 f'{entry["levered"]:.4f}, {entry["unlevered"]:.4f},\n'
                 f'        {entry["debt_to_equity"]:.4f}, '
                 f'{entry["tax_rate"]:.4f}),\n')
    body += "}\n"
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="report what would change and exit 1 if any")
    args = parser.parse_args()

    raw = fetch()
    table = rows(raw)
    as_of = as_of_from(raw)
    if len(table) < 50:
        raise SystemExit(f"only {len(table)} industries parsed; the sheet's "
                         "layout has changed and this needs a look.")
    fresh = render(table, as_of)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, TARGET)

    if args.check:
        current = ""
        if os.path.exists(path):
            with io.open(path, encoding="utf-8") as handle:
                current = handle.read()
        # The fetched-on line is the one difference that is not a change in
        # the data, so it is excluded from the comparison.
        strip = lambda text: re.sub(r"fetched: .*", "", text)
        if strip(current) == strip(fresh):
            print(f"{TARGET} matches {BETAS_URL} ({as_of}, "
                  f"{len(table)} industries).")
            return 0
        print(f"{TARGET} differs from {BETAS_URL} ({as_of}).")
        return 1

    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(fresh)
    print(f"wrote {path}: {len(table)} industries, as of {as_of}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
