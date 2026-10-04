"""
A workbook for the relative (peer multiples) view.

NOT A MODEL, A TABLE
--------------------
The other exports project something forward and discount it back, so almost
every cell is a formula. There is nothing to project here. A relative view is
a comparison of figures that already exist: this company's trailing multiples
beside each peer's, the group's medians, and what those medians imply if
applied to this company.

So this workbook is a table, and most of it is reported numbers rather than
formulas. The cells that ARE formulas are the ones that genuinely derive from
others and that a reader might want to argue with:

  * the peer median of each multiple
  * the company's premium or discount to that median
  * the implied value per share from each multiple
  * the central figure, the median of those implied values

Edit a peer's multiple and the median, the premium and the implied value all
move. That is the whole of the arithmetic a relative view contains, and it is
live.

WHAT IT REFUSES TO DO
---------------------
If the relative view declined on screen - too few peers, too few usable
multiples - there is no figure, and this builder raises rather than writing a
sheet with a blank where the number goes. A spreadsheet that looks like a
valuation with one empty cell is worse than no spreadsheet.

The framing travels too: a relative figure is market-based, inherits the
market's own mispricing, and depends on a judgement about who the peers are.
That is written into the sheet, not left on the screen it came from.
"""

from __future__ import annotations

import datetime as _dt
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from analysis import RelativeReport, relative_note

FONT_NAME = "Arial"

BLUE = "0000FF"
BLACK = "000000"
GREY = "595959"
WHITE = "FFFFFF"
INPUT_FILL = PatternFill("solid", start_color="FFF2CC")
HEADER_FILL = PatternFill("solid", start_color="D9E1F2")
TITLE_FILL = PatternFill("solid", start_color="1F3864")
OWN_FILL = PatternFill("solid", start_color="E2EFDA")

PER_SHARE = '$#,##0.00;($#,##0.00);"-"'
MULTIPLE = '0.0"x";(0.0"x");"-"'
PERCENT_1DP = '0.0%;(0.0%);"-"'

THIN_TOP = Border(top=Side(style="thin", color="808080"))


def _style(cell, *, bold=False, color=BLACK, size=10, fmt=None,
           fill=None, align=None, italic=False, wrap=False):
    cell.font = Font(name=FONT_NAME, bold=bold, color=color, size=size, italic=italic)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    if align or wrap:
        cell.alignment = Alignment(horizontal=align, wrap_text=wrap)
    return cell


def _label(ws, row, text, *, bold=False, indent=0, color=BLACK, size=10, italic=False):
    cell = ws.cell(row=row, column=1, value=("   " * indent) + text)
    return _style(cell, bold=bold, color=color, size=size, italic=italic)


def _section(ws, row, text, last_col):
    cell = _label(ws, row, text, bold=True, size=11)
    cell.fill = HEADER_FILL
    for col in range(2, last_col + 1):
        ws.cell(row=row, column=col).fill = HEADER_FILL


def _wrapped(ws, row: int, text: str) -> int:
    """One paragraph per row, so nothing is hidden inside a narrow cell."""
    sentences, current = [], ""
    for part in text.split(". "):
        candidate = (current + ". " + part) if current else part
        if len(candidate) > 110:
            sentences.append(current)
            current = part
        else:
            current = candidate
    if current:
        sentences.append(current)
    for sentence in sentences:
        body = sentence.strip()
        if body and not body.endswith("."):
            body += "."
        _style(ws.cell(row=row, column=1, value=body), color=GREY, size=9)
        row += 1
    return row


def build_relative_workbook(report: RelativeReport) -> Workbook:
    """
    Build the peer-comparison workbook for *report*.

    Refuses when the relative view declined, for the same reason the screen
    shows no figure there: a sheet laid out like a valuation with the number
    missing invites the reader to fill it in themselves.
    """
    if getattr(report, "method", None) != "relative":
        raise ValueError(
            "Refusing to build a relative workbook for " + report.ticker
            + ": this report is a " + str(getattr(report, "method", "unknown")).upper() + ".")
    if report.result is None:
        reasons = "; ".join(report.suitability.reasons) or "the comparison did not stand up"
        raise ValueError(
            "Refusing to build a relative workbook for " + report.ticker
            + ": " + reasons)

    result = report.result
    peers = report.peers
    today = _dt.date.today().isoformat()
    # Column A labels, B the company, then one column per peer, then median.
    first_peer_col = 3
    last_peer_col = 2 + len(peers)
    median_col = last_peer_col + 1
    implied_col = median_col + 1
    last_col = max(implied_col, 6)

    wb = Workbook()
    ws = wb.active
    ws.title = "Relative view"
    wb.calculation.fullCalcOnLoad = True

    # --- Title -------------------------------------------------------------
    title = _label(ws, 1,
                   report.company_name + "  (" + report.ticker + ")  -  "
                   "Relative Valuation (peer multiples)",
                   bold=True, size=14)
    title.font = Font(name=FONT_NAME, bold=True, size=14, color=WHITE)
    for col in range(1, last_col + 1):
        ws.cell(row=1, column=col).fill = TITLE_FILL

    _style(ws.cell(row=2, column=1,
                   value="A market-based second opinion, not an estimate of "
                         "intrinsic value.  Peer multiples are trailing.  The "
                         "medians, premiums and implied values are live "
                         "formulas - edit a peer's multiple and they move."),
           italic=True, color=GREY, size=9)

    row = 4

    # --- The comparison table ---------------------------------------------
    _section(ws, row, "1.  MULTIPLES   (this company beside its peers)", last_col)
    row += 1

    header = row
    _style(ws.cell(row=header, column=1, value="Multiple"), bold=True, size=9)
    own = _style(ws.cell(row=header, column=2, value=report.ticker), bold=True,
                 size=9, align="center")
    own.fill = OWN_FILL
    for index, peer in enumerate(peers):
        _style(ws.cell(row=header, column=first_peer_col + index, value=peer.ticker),
               bold=True, size=9, align="center")
    _style(ws.cell(row=header, column=median_col, value="Peer median"),
           bold=True, size=9, align="center")
    _style(ws.cell(row=header, column=implied_col, value="Implied value / share"),
           bold=True, size=9, align="center")
    row += 1

    applied_rows: list[int] = []
    for multiple in result.multiples:
        _label(ws, row, multiple.label
               + ("" if multiple.applicable else "   (not applied)"),
               color=BLACK if multiple.applicable else GREY)

        # The company's own multiple.
        if multiple.company_value is None:
            _style(ws.cell(row=row, column=2, value="n/m"), color=GREY, size=9,
                   align="center")
        else:
            cell = _style(ws.cell(row=row, column=2, value=multiple.company_value),
                          color=BLUE, fmt=MULTIPLE)
            cell.fill = OWN_FILL

        # Each peer's.
        by_ticker = {p.ticker: p for p in multiple.peers}
        for index, peer in enumerate(peers):
            col = first_peer_col + index
            entry = by_ticker.get(peer.ticker)
            if entry is None or entry.value is None:
                _style(ws.cell(row=row, column=col, value="n/m"), color=GREY,
                       size=9, align="center")
            else:
                _style(ws.cell(row=row, column=col, value=entry.value),
                       color=BLUE, fmt=MULTIPLE)

        # The median, live over whatever the peer cells hold.
        span = ("$" + get_column_letter(first_peer_col) + "$" + str(row) + ":$"
                + get_column_letter(last_peer_col) + "$" + str(row))
        if multiple.peer_median is None:
            _style(ws.cell(row=row, column=median_col, value="too few peers"),
                   color=GREY, size=9, align="center")
        else:
            _style(ws.cell(row=row, column=median_col,
                           value="=MEDIAN(" + span + ")"), fmt=MULTIPLE, bold=True)

        # What that median implies for this company, where it was applied.
        if multiple.applicable and multiple.implied_value_per_share is not None:
            # Written as a literal: the bridge from a multiple to a per-share
            # value runs through the company's own trailing figure, net debt
            # and share count, which are not on this sheet. Presenting a
            # formula built from cells that are not here would be a fiction.
            _style(ws.cell(row=row, column=implied_col,
                           value=multiple.implied_value_per_share),
                   fmt=PER_SHARE, bold=True)
            applied_rows.append(row)
        else:
            _style(ws.cell(row=row, column=implied_col,
                           value=multiple.reason or "not applied"),
                   color=GREY, size=9)
        row += 1

    # --- Premium / discount ------------------------------------------------
    row += 1
    _section(ws, row, "2.  WHERE THIS COMPANY TRADES AGAINST THE GROUP", last_col)
    row += 1
    for index, multiple in enumerate(result.multiples):
        multiple_row = header + 1 + index
        _label(ws, row, multiple.label + " premium / (discount) to the median")
        if multiple.company_value is None or multiple.peer_median is None:
            _style(ws.cell(row=row, column=2, value="n/m"), color=GREY, size=9)
        else:
            _style(ws.cell(row=row, column=2,
                           value="=B" + str(multiple_row) + "/"
                                 + get_column_letter(median_col) + str(multiple_row)
                                 + "-1"),
                   fmt=PERCENT_1DP)
        row += 1

    # --- The figure --------------------------------------------------------
    row += 1
    _section(ws, row, "3.  THE RELATIVE FIGURE", last_col)
    row += 1
    _label(ws, row, "Central value per share (median of the applied multiples)",
           bold=True)
    if applied_rows:
        refs = ",".join(get_column_letter(implied_col) + str(r) for r in applied_rows)
        _style(ws.cell(row=row, column=2, value="=MEDIAN(" + refs + ")"),
               fmt=PER_SHARE, bold=True).border = THIN_TOP
    else:
        _style(ws.cell(row=row, column=2, value="no multiple could be applied"),
               color=GREY, size=9)
    row += 1

    if result.low_value_per_share is not None and applied_rows:
        refs = ",".join(get_column_letter(implied_col) + str(r) for r in applied_rows)
        _label(ws, row, "Lowest of the applied multiples")
        _style(ws.cell(row=row, column=2, value="=MIN(" + refs + ")"), fmt=PER_SHARE)
        row += 1
        _label(ws, row, "Highest of the applied multiples")
        _style(ws.cell(row=row, column=2, value="=MAX(" + refs + ")"), fmt=PER_SHARE)
        row += 1

    if result.current_price is not None:
        _label(ws, row, "Current share price")
        _style(ws.cell(row=row, column=2, value=result.current_price),
               color=BLUE, fmt=PER_SHARE, fill=INPUT_FILL)
        row += 1

    # --- The peer group ----------------------------------------------------
    row += 1
    _section(ws, row, "4.  THE PEER GROUP   (change it and the figure changes)",
             last_col)
    row += 1
    _label(ws, row, "Selected: " + report.selection_mode, italic=True, color=GREY)
    row += 1
    _style(ws.cell(row=row, column=1, value="Ticker"), bold=True, size=9)
    _style(ws.cell(row=row, column=2, value="Company"), bold=True, size=9)
    _style(ws.cell(row=row, column=3, value="Industry"), bold=True, size=9)
    _style(ws.cell(row=row, column=4, value="How it got here"), bold=True, size=9)
    row += 1
    for peer in peers:
        _style(ws.cell(row=row, column=1, value=peer.ticker), size=9)
        _style(ws.cell(row=row, column=2, value=peer.name or ""), size=9)
        _style(ws.cell(row=row, column=3, value=peer.industry or ""), size=9)
        _style(ws.cell(row=row, column=4, value=peer.provenance), size=9,
               color=GREY)
        row += 1

    if report.excluded:
        row += 1
        _label(ws, row, "Considered and left out", bold=True, size=9)
        row += 1
        for excluded in report.excluded:
            _style(ws.cell(row=row, column=1, value=excluded.ticker), size=9)
            _style(ws.cell(row=row, column=2, value=excluded.name or ""), size=9)
            _style(ws.cell(row=row, column=4, value=excluded.reason), size=9,
                   color=GREY)
            row += 1

    # --- What this figure is, and is not -----------------------------------
    row += 1
    _section(ws, row, "WHAT THIS FIGURE IS, AND WHAT IT IS NOT", last_col)
    row += 1
    note = relative_note(report)
    if note:
        row = _wrapped(ws, row, note)
    row += 1
    for warning in report.suitability.warnings:
        row = _wrapped(ws, row, "Warning: " + warning)
    _style(ws.cell(row=row, column=1, value="Generated " + today + "."),
           color=GREY, size=9)

    # --- Presentation ------------------------------------------------------
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 16
    for col in range(3, last_col + 2):
        ws.column_dimensions[get_column_letter(col)].width = 15
    ws.column_dimensions[get_column_letter(implied_col)].width = 22
    # No frozen panes, as in the other workbooks.
    ws.freeze_panes = None
    ws.sheet_view.showGridLines = False

    return wb


def relative_workbook_bytes(report: RelativeReport) -> bytes:
    buffer = BytesIO()
    build_relative_workbook(report).save(buffer)
    return buffer.getvalue()


def relative_filename_for(report: RelativeReport) -> str:
    return report.ticker + "_Relative.xlsx"
