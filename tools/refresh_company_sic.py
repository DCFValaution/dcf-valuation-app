"""Regenerate `company_sic_data.py`: ticker to the SEC's assigned SIC code.

    python tools/refresh_company_sic.py [--extra TSM,NVO,...] [--check]

A company's SIC code is the only industry label that arrives with its own
filings, and it is the join the industry-beta map needs. It lives in the
registrant's submissions record at data.sec.gov, which means one request per
company - so the codes are baked into the repository rather than looked up
during a valuation. That also keeps the SEC out of the path a valuation
depends on: a report is reproducible whether or not data.sec.gov answers.

The universe is the S&P 500 plus whatever `--extra` names. Anything outside
it has no SIC code here and the caller falls back to the whole-market beta,
which is the honest answer rather than a guessed industry.

Needs PUBLIC_DATA_CONTACT set, as every SEC request in this project does.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
TARGET = "company_sic_data.py"

# The tickers this project is checked against, which are not all in the
# index: foreign private issuers, a REIT, a meme stock and a crypto
# exchange are all here because each one broke something once.
EXTRA = ["LMT", "SHEL", "XOM", "JNJ", "KO", "PG", "VZ", "DUK", "GIS", "CVX",
         "BP", "MRK", "PFE", "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
         "TSLA", "JPM", "COIN", "TSM", "O", "PLD", "BRK-B", "NKE", "CRM",
         "GME", "TM", "NVO", "SAP", "BABA"]

PREAMBLE = '''"""The SEC's assigned SIC code per ticker. GENERATED - do not edit by hand.

Regenerate with `python tools/refresh_company_sic.py`. The universe is the
S&P 500 plus the tickers this project is checked against; a ticker absent
from here has no SIC code on file and the caller uses the whole-market beta
rather than guessing an industry.

The code is the SEC's classification of the REGISTRANT, which is not always
a good description of the business - 3M files as a surgical instruments
maker, Amazon as a mail-order house. `industry_betas.SIC_JUDGEMENTS` records
where that bites.

    source:  {submissions}
    index:   {wiki}
    fetched: {fetched}
    size:    {count} tickers, {codes} distinct codes
"""
from __future__ import annotations

COMPANY_SIC: dict[str, str] = {{
'''


def sp500() -> list[dict]:
    from public_data import _get

    html = _get(WIKI, timeout=60)
    if not html:
        raise SystemExit(f"{WIKI} returned nothing.")
    start = html.index('id="constituents"')
    table = html[start:html.index("</table>", start)]
    clean = lambda cell: re.sub(r"<[^>]+>", "", cell).replace("&amp;",
                                                              "&").strip()
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)[1:]:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        if len(cells) < 7:
            continue
        try:
            cik = f"{int(clean(cells[6])):010d}"
        except ValueError:
            continue
        out.append({"ticker": clean(cells[0]), "cik": cik})
    if len(out) < 400:
        raise SystemExit(f"only {len(out)} constituents parsed; the page's "
                         "layout has changed and this needs a look.")
    return out


def sic_of(cik: str) -> str:
    import edgar_data as E

    body = E._fetch(SUBMISSIONS.format(cik=cik))
    if not body:
        return ""
    try:
        return str(json.loads(body).get("sic") or "")
    except ValueError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", default=",".join(EXTRA))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    import edgar_data as E

    wanted: dict[str, str] = {row["ticker"]: row["cik"] for row in sp500()}
    for ticker in (t.strip().upper() for t in args.extra.split(",")):
        if not ticker or ticker in wanted:
            continue
        found = E.ciks_for(ticker)
        if not found:
            print(f"  {ticker}: not in the SEC's ticker list, skipped")
            continue
        wanted[ticker] = found[0][0]

    table: dict[str, str] = {}
    for i, (ticker, cik) in enumerate(sorted(wanted.items())):
        code = sic_of(cik)
        if code:
            table[ticker] = code
        else:
            print(f"  {ticker} (CIK {cik}): no SIC code on file, skipped")
        if i and i % 100 == 0:
            print(f"  {i}/{len(wanted)}...")

    body = PREAMBLE.format(
        submissions=SUBMISSIONS.format(cik="##########"), wiki=WIKI,
        fetched=datetime.now(timezone.utc).date().isoformat(),
        count=len(table), codes=len(set(table.values())))
    for ticker, code in sorted(table.items()):
        body += f'    {ticker!r}: {code!r},\n'
    body += "}\n"

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, TARGET)

    if args.check:
        current = ""
        if os.path.exists(path):
            with io.open(path, encoding="utf-8") as handle:
                current = handle.read()
        strip = lambda text: re.sub(r"fetched: .*", "", text)
        if strip(current) == strip(body):
            print(f"{TARGET} matches the SEC ({len(table)} tickers).")
            return 0
        print(f"{TARGET} differs from the SEC.")
        return 1

    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    print(f"wrote {path}: {len(table)} tickers, "
          f"{len(set(table.values()))} distinct SIC codes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
