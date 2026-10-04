"""
Workbooks for the two figures that are not valuations: the speculative
path-to-profitability estimate, and the user-built hypothetical.

WHY THIS FILE IS DIFFERENT FROM THE OTHER TWO
---------------------------------------------
The DCF and DDM workbooks carry a figure the app stands behind. These carry
figures it explicitly refuses to stand behind, produced only because somebody
asked. On screen that framing is unmissable - a warning header, an opt-in the
user had to pass through, a disclaimer above the number.

A file has none of that. It outlives the screen it came from, opens in Excel
looking exactly as official as any other spreadsheet, and can be mailed to
somebody who never saw the refusal. So the framing has to be *in the sheet*,
at the top, before the number - not a footnote under it.

Hence, in both workbooks:

  * rows 1-3 are a banner: NOT A VALUATION, in white on red, before anything
  * the headline is labelled "Speculative estimate" or "User-built
    hypothetical". Never "intrinsic value", never "valuation"
  * there is no upside/downside against the market price. The market price is
    shown as context, but the sheet will not compute a gap between a price and
    a figure this uncertain - that number would be the one screenshotted
  * the full disclaimer is written out in the sheet, not summarised
  * the file is named TICKER_Speculative.xlsx / TICKER_Hypothetical.xlsx

For the hypothetical there is one more: a reality-contrast block, the user's
assumption beside the company's actual figure, with the ones that contradict
marked. The whole point of that screen is that the user is assuming something
the filings do not show, and the file has to say so too.

EVERYTHING ELSE IS A FORMULA
----------------------------
Same standard as the DCF and DDM exports. The path's revenue, margin, EBIT,
cash flow, discount factors and present values are live formulas off the
assumption block, so the recipient can argue with the projection - which is
the only honest use for a file like this.
"""

from __future__ import annotations

import datetime as _dt
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from analysis import (HypotheticalReport, SpeculativeReport,
                      hypothetical_disclaimer, speculative_disclaimer)

FONT_NAME = "Arial"

BLUE = "0000FF"
BLACK = "000000"
GREY = "595959"
WHITE = "FFFFFF"
RED = "C00000"
INPUT_FILL = PatternFill("solid", start_color="FFF2CC")
HEADER_FILL = PatternFill("solid", start_color="D9E1F2")
# Red rather than the other workbooks' navy. This is the one visual cue that
# says, before any text is read, that the file is not like the others.
WARNING_FILL = PatternFill("solid", start_color=RED)
CONTRADICT_FILL = PatternFill("solid", start_color="FCE4E4")
USER_FILL = PatternFill("solid", start_color="FFF2CC")

MONEY = '$#,##0;($#,##0);"-"'
PER_SHARE = '$#,##0.00;($#,##0.00);"-"'
PERCENT_2DP = '0.00%'
PERCENT_1DP = '0.0%'
FACTOR = '0.000'

THIN_TOP = Border(top=Side(style="thin", color="808080"))

# The banner occupies the first three rows in both workbooks.
R_BANNER = 1
R_BANNER_2 = 2
R_BANNER_3 = 3


def _style(cell, *, bold=False, color=BLACK, size=10, fmt=None,
           fill=None, align=None, italic=False, wrap=False):
    cell.font = Font(name=FONT_NAME, bold=bold, color=color, size=size, italic=italic)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    if align or wrap:
        cell.alignment = Alignment(horizontal=align, wrap_text=wrap, vertical="center")
    return cell


def _label(ws, row, text, *, bold=False, indent=0, color=BLACK, size=10, italic=False):
    cell = ws.cell(row=row, column=1, value=("   " * indent) + text)
    return _style(cell, bold=bold, color=color, size=size, italic=italic)


def _input(ws, row, value, fmt, *, note=None, source=None, fill=INPUT_FILL):
    cell = ws.cell(row=row, column=2, value=value)
    _style(cell, color=BLUE, fmt=fmt, fill=fill)
    if source:
        _style(ws.cell(row=row, column=3, value=source), color=GREY, size=9)
    if note:
        _style(ws.cell(row=row, column=4, value=note), color=GREY, size=9)
    return cell


def _formula(ws, row, col, formula, fmt, *, bold=False, top_border=False):
    cell = ws.cell(row=row, column=col, value=formula)
    _style(cell, bold=bold, fmt=fmt)
    if top_border:
        cell.border = THIN_TOP
    return cell


def _section(ws, row, text, last_col):
    cell = _label(ws, row, text, bold=True, size=11)
    cell.fill = HEADER_FILL
    for col in range(2, last_col + 1):
        ws.cell(row=row, column=col).fill = HEADER_FILL


def _banner(ws, last_col: int, headline: str, second: str, third: str) -> None:
    """The three-row warning that opens the file.

    Before the company name, before the figure, before anything. Written as
    three separate rows rather than one wrapped cell so it survives a reader
    that ignores row heights, and filled across every column in use so it
    cannot be mistaken for a stray label in column A.
    """
    for row, text, size in ((R_BANNER, headline, 14),
                            (R_BANNER_2, second, 11),
                            (R_BANNER_3, third, 10)):
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = Font(name=FONT_NAME, bold=True, size=size, color=WHITE)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        for col in range(1, last_col + 1):
            ws.cell(row=row, column=col).fill = WARNING_FILL
    ws.row_dimensions[R_BANNER].height = 24
    ws.row_dimensions[R_BANNER_2].height = 18


def _headline(ws, row: int, label: str, formula: str, caption: str,
              last_col: int) -> int:
    """The figure, its label, and a warning that cannot be cropped off.

    The banner at the top of the sheet is only seen on opening: the figure sits
    some forty rows below it, so a reader parked on the number - or taking a
    screenshot of it - sees none of the red. This puts the warning back beside
    the number, in the same red, and makes the label itself larger and red so
    it does not read as just another row in the block above.

    Returns the row after the caption.
    """
    _label(ws, row, label, bold=True, size=12, color=RED)
    _formula(ws, row, 2, formula, PER_SHARE, bold=True, top_border=True)
    ws.cell(row=row, column=2).font = Font(name=FONT_NAME, bold=True, size=12)

    caption_row = row + 1
    cell = ws.cell(row=caption_row, column=1, value=caption)
    cell.font = Font(name=FONT_NAME, bold=True, size=10, color=WHITE)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    for col in range(1, last_col + 1):
        ws.cell(row=caption_row, column=col).fill = WARNING_FILL
    return caption_row + 1


# Company names end in abbreviations that look exactly like a sentence end,
# and splitting on them cut warnings in half mid-name ("Warning: On this path
# Rivian Automotive, Inc." / "burns $9.9bn...").
_DOT = "<<DOT>>"
_ABBREVIATIONS = (" Inc.", " Co.", " Corp.", " Ltd.", " plc.", " S.A.",
                  " N.V.", " A.G.", " L.P.", " Jr.", " St.", " approx.")


def _split_sentences(text: str, width: int = 110) -> list[str]:
    """Break *text* into lines at sentence ends, never inside a name."""
    guarded = text
    for abbreviation in _ABBREVIATIONS:
        guarded = guarded.replace(abbreviation, abbreviation[:-1] + _DOT)
    out, current = [], ""
    for part in guarded.split(". "):
        candidate = (current + ". " + part) if current else part
        if current and len(candidate) > width:
            out.append(current)
            current = part
        else:
            current = candidate
    if current:
        out.append(current)
    return [line.replace(_DOT, ".") for line in out]


def _wrapped_block(ws, row: int, text: str, last_col: int, *,
                   label: str | None = None) -> int:
    """Write *text* across the sheet's width, one paragraph per row.

    Returns the next free row. Long disclaimers are split on sentence
    boundaries rather than written into one enormous cell, so every word is
    visible without the reader having to widen a column.
    """
    if label:
        _label(ws, row, label, bold=True)
        row += 1
    sentences = _split_sentences(text)
    for sentence in sentences:
        body = sentence.strip()
        if body and not body.endswith("."):
            body += "."
        _style(ws.cell(row=row, column=1, value=body), color=BLACK, size=9)
        row += 1
    return row


# ---------------------------------------------------------------------------
# The path projection, shared by both workbooks
# ---------------------------------------------------------------------------

def _write_path(ws, report, first_row: int, last_col: int,
                r_growth: str, r_margin: str, r_years: str,
                r_revenue: str, r_tax: str, r_da: str, r_capex: str,
                r_nwc: str, r_wacc: str, r_start_margin: str,
                r_mature_capex: str, r_mature_nwc: str) -> dict:
    """Write the live path-to-profitability grid. Returns its row map.

    The margin ramps linearly from where the company is now to the target the
    assumptions name, over the years they name - the same shape the engine
    runs, written as formulas so the recipient can move any of it.
    """
    result = report.result
    years = len(result.path)
    rows = {
        "head": first_row,
        "cols": first_row + 1,
        "period": first_row + 2,
        "revenue": first_row + 3,
        "margin": first_row + 4,
        "ebit": first_row + 5,
        "tax": first_row + 6,
        "da": first_row + 7,
        "capex": first_row + 8,
        "nwc": first_row + 9,
        "ufcf": first_row + 10,
        "factor": first_row + 11,
        "pv": first_row + 12,
    }
    _section(ws, rows["head"],
             "PATH TO PROFITABILITY   (projected, not reported)", last_col)

    _label(ws, rows["cols"], "Year", bold=True)
    _label(ws, rows["period"], "Period (t)", indent=1, color=GREY)
    _label(ws, rows["revenue"], "Revenue", indent=1)
    _label(ws, rows["margin"], "Operating margin (ramped to target)", indent=1)
    _label(ws, rows["ebit"], "EBIT", indent=1)
    _label(ws, rows["tax"], "Less: tax", indent=1)
    _label(ws, rows["da"], "Add: D&A", indent=1)
    _label(ws, rows["capex"], "Less: capex", indent=1)
    _label(ws, rows["nwc"], "Less: change in NWC", indent=1)
    _label(ws, rows["ufcf"], "Unlevered free cash flow", indent=1)
    _label(ws, rows["factor"], "Discount factor", indent=1)
    _label(ws, rows["pv"], "PV of cash flow", indent=1)

    for offset in range(1, years + 1):
        col = 2 + offset
        letter = get_column_letter(col)
        prev = get_column_letter(col - 1)
        t = str(offset)
        # How far along the ramp this year is. The engine moves every ratio
        # from today's to its mature level in a straight line over N years,
        # not just the margin - D&A, capex and working capital all travel too.
        progress = t + "/$B$" + str(r_years)

        _style(ws.cell(row=rows["cols"], column=col, value="Year " + t),
               bold=True, align="center")
        _style(ws.cell(row=rows["period"], column=col, value=offset),
               color=GREY, fmt="0", align="center")

        _formula(ws, rows["revenue"], col,
                 "=$B$" + str(r_revenue) + "*(1+$B$" + str(r_growth) + ")^" + t, MONEY)

        _formula(ws, rows["margin"], col,
                 "=$B$" + str(r_start_margin) + "+($B$" + str(r_margin)
                 + "-$B$" + str(r_start_margin) + ")*" + progress, PERCENT_2DP)

        _formula(ws, rows["ebit"], col,
                 "=" + letter + str(rows["revenue"]) + "*" + letter + str(rows["margin"]),
                 MONEY)
        _formula(ws, rows["tax"], col,
                 "=-MAX(0," + letter + str(rows["ebit"]) + ")*$B$" + str(r_tax),
                 MONEY)
        _formula(ws, rows["da"], col,
                 "=" + letter + str(rows["revenue"]) + "*($B$" + str(r_da)
                 + "+($B$" + str(r_mature_capex) + "-$B$" + str(r_da) + ")*"
                 + progress + ")", MONEY)
        _formula(ws, rows["capex"], col,
                 "=-" + letter + str(rows["revenue"]) + "*($B$" + str(r_capex)
                 + "+($B$" + str(r_mature_capex) + "-$B$" + str(r_capex) + ")*"
                 + progress + ")", MONEY)
        base = ("$B$" + str(r_revenue)) if offset == 1 else (prev + str(rows["revenue"]))
        _formula(ws, rows["nwc"], col,
                 "=-(" + letter + str(rows["revenue"]) + "-" + base + ")*($B$"
                 + str(r_nwc) + "+($B$" + str(r_mature_nwc) + "-$B$" + str(r_nwc)
                 + ")*" + progress + ")", MONEY)
        _formula(ws, rows["ufcf"], col,
                 "=SUM(" + letter + str(rows["ebit"]) + ":" + letter + str(rows["nwc"]) + ")",
                 MONEY, top_border=True)
        _formula(ws, rows["factor"], col,
                 "=1/(1+$B$" + str(r_wacc) + ")^" + t, FACTOR)
        _formula(ws, rows["pv"], col,
                 "=" + letter + str(rows["ufcf"]) + "*" + letter + str(rows["factor"]),
                 MONEY)
    return rows


# ---------------------------------------------------------------------------
# Speculative
# ---------------------------------------------------------------------------

def build_speculative_workbook(report: SpeculativeReport) -> Workbook:
    """A workbook for the speculative path-to-profitability estimate."""
    if getattr(report, "method", None) != "speculative":
        raise ValueError(
            "Refusing to build a speculative workbook for " + report.ticker
            + ": this report is a " + str(getattr(report, "method", "unknown")).upper() + ".")
    if report.result is None:
        raise ValueError(
            "Refusing to build a workbook for " + report.ticker + ": even a "
            "speculative estimate does not apply to this company.")

    a = report.derived.assumptions
    result = report.result
    diagnostics = report.derived.diagnostics or {}
    today = _dt.date.today().isoformat()
    years = len(result.path)
    last_col = max(2 + years, 6)

    wb = Workbook()
    ws = wb.active
    ws.title = "Speculative estimate"
    # Red in the tab strip too: in a workbook someone has scrolled, or saved
    # beside other sheets, the tab is all that is left of the warning.
    ws.sheet_properties.tabColor = RED
    wb.calculation.fullCalcOnLoad = True

    _banner(ws, last_col,
            "SPECULATIVE ESTIMATE - NOT A VALUATION",
            report.company_name + " (" + report.ticker + ") loses money. "
            "This is not a price target and not a recommendation.",
            "Produced only because it was explicitly requested. It rests on a "
            "future that has not happened.")

    row = 5
    _label(ws, row, report.company_name + "  (" + report.ticker + ")",
           bold=True, size=12)
    row += 1
    _style(ws.cell(row=row, column=1,
                   value="$ in millions except per-share data.  Basis: FY"
                         + str(report.fiscal_year) + ".  White cells are live "
                         "formulas - edit the shaded inputs and the path "
                         "recalculates."),
           italic=True, color=GREY, size=9)
    row += 2

    # --- Why the standard valuation was refused ---------------------------
    _section(ws, row, "WHY A STANDARD VALUATION WAS REFUSED", last_col)
    row += 1
    for reason in report.why_standard_refused:
        row = _wrapped_block(ws, row, reason, last_col)
    row += 1

    # --- Assumptions -------------------------------------------------------
    _section(ws, row, "ASSUMPTIONS   (shaded blue-text cells are editable)", last_col)
    row += 1
    provenance = report.derived.provenance
    r_growth, r_margin, r_years = row, row + 1, row + 2
    for offset, (text, name, fmt) in enumerate([
        ("Revenue growth a year, until profitable", "speculative_revenue_growth", PERCENT_2DP),
        ("Target operating margin", "target_operating_margin", PERCENT_2DP),
        ("Years to reach that margin", "years_to_profitability", "0"),
    ]):
        _label(ws, row + offset, text)
        prov = provenance.get(name)
        _input(ws, row + offset, getattr(a, name), fmt,
               source=prov.source if prov else None,
               note=prov.detail if prov else None)
    row += 3

    spec = report.derived.assumptions
    inputs = report.derived.inputs
    (r_tax, r_da, r_capex, r_nwc,
     r_mature_capex, r_mature_nwc, r_wacc) = (row, row + 1, row + 2, row + 3,
                                              row + 4, row + 5, row + 6)
    for offset, (text, value, fmt) in enumerate([
        ("Tax rate", spec.tax_rate, PERCENT_2DP),
        ("D&A today (% of revenue)", inputs.da_pct, PERCENT_2DP),
        ("Capex today (% of revenue)", inputs.capex_pct, PERCENT_2DP),
        ("Change in NWC today (% of revenue growth)", inputs.nwc_pct, PERCENT_2DP),
        ("Capex and D&A once mature (% of revenue)", spec.mature_capex_pct, PERCENT_2DP),
        ("Change in NWC once mature (%)", spec.mature_nwc_pct, PERCENT_2DP),
        ("WACC (discount rate)", spec.wacc, PERCENT_2DP),
    ]):
        _label(ws, row + offset, text)
        _input(ws, row + offset, value, fmt)
    row += 7

    # --- Base figures ------------------------------------------------------
    _section(ws, row, "WHERE IT STARTS   (as reported)", last_col)
    row += 1
    r_revenue = row
    _label(ws, row, "Revenue")
    _input(ws, row, report.derived.inputs.revenue, MONEY)
    row += 1
    _label(ws, row, "Operating margin today")
    r_start_margin = row
    margin_now = report.derived.inputs.operating_margin
    if margin_now is None:
        _style(ws.cell(row=row, column=2, value="not reported"), color=GREY, size=9)
    else:
        _input(ws, row, margin_now, PERCENT_2DP)
    row += 1
    _label(ws, row, "Current share price (context only)")
    _input(ws, row, result.current_price, PER_SHARE)
    r_price = row
    row += 2

    # --- The path ----------------------------------------------------------
    path_rows = _write_path(ws, report, row, last_col,
                            r_growth, r_margin, r_years, r_revenue,
                            r_tax, r_da, r_capex, r_nwc, r_wacc, r_start_margin,
                            r_mature_capex, r_mature_nwc)
    row = path_rows["pv"] + 2

    # --- The figure --------------------------------------------------------
    _section(ws, row, "THE FIGURE   (a speculative estimate, not a valuation)", last_col)
    row += 1
    first = get_column_letter(3)
    last = get_column_letter(2 + years)
    _label(ws, row, "PV of the path's cash flows")
    r_pv_path = row
    _formula(ws, row, 2,
             "=SUM($" + first + "$" + str(path_rows["pv"]) + ":$" + last + "$"
             + str(path_rows["pv"]) + ")", MONEY)
    row += 1

    _label(ws, row, "Cumulative cash burn along the path")
    _formula(ws, row, 2,
             "=-SUMIF($" + first + "$" + str(path_rows["ufcf"]) + ":$" + last + "$"
             + str(path_rows["ufcf"]) + ',"<0")', MONEY)
    row += 1

    # The value once profitable is the engine's own DCF off year N. It is not
    # re-derived here: this sheet projects the path, and reproducing a whole
    # second DCF would add rows without adding honesty.
    _label(ws, row, "Value once profitable, discounted to today")
    _input(ws, row, result.pv_value_at_profitability, MONEY,
           note="the engine's DCF from the year the margin is reached")
    r_after = row
    row += 1

    _label(ws, row, "Enterprise value")
    # r_pv_path, not the row above: the row between them is the cash burn,
    # which is a fact about the path rather than a term in the sum.
    _formula(ws, row, 2, "=$B$" + str(r_pv_path) + "+$B$" + str(r_after), MONEY,
             top_border=True)
    r_ev = row
    row += 1

    _label(ws, row, "Equity value")
    _input(ws, row, result.equity_value, MONEY, note="after the net cash bridge")
    r_eq = row
    row += 1

    _label(ws, row, "Shares outstanding (millions)")
    shares = report.derived.inputs.shares
    _input(ws, row, shares, '#,##0.0')
    r_shares = row
    row += 1

    row = _headline(
        ws, row, "SPECULATIVE ESTIMATE PER SHARE",
        "=$B$" + str(r_eq) + "/$B$" + str(r_shares),
        "NOT A VALUATION - a speculative estimate resting on assumptions about "
        "a future that has not happened. Not a price target.",
        last_col)
    _style(ws.cell(row=row, column=1,
                   value="This is not intrinsic value. No upside or downside "
                         "against the market price is shown, because a gap "
                         "between a price and a figure this uncertain would be "
                         "the most misleading number on the sheet."),
           italic=True, color=GREY, size=9)
    row += 2

    # --- The disclaimer, in full -------------------------------------------
    _section(ws, row, "THE DISCLAIMER THIS FIGURE CARRIES", last_col)
    row += 1
    disclaimer = speculative_disclaimer(report)
    if disclaimer:
        row = _wrapped_block(ws, row, disclaimer, last_col)
    row += 1

    for warning in report.suitability.warnings:
        row = _wrapped_block(ws, row, "Warning: " + warning, last_col)

    _style(ws.cell(row=row + 1, column=1,
                   value="Generated " + today + "."), color=GREY, size=9)

    _present(ws, last_col)
    return wb


# ---------------------------------------------------------------------------
# Hypothetical
# ---------------------------------------------------------------------------

def build_hypothetical_workbook(report: HypotheticalReport) -> Workbook:
    """A workbook for the user-built hypothetical."""
    if getattr(report, "method", None) != "hypothetical":
        raise ValueError(
            "Refusing to build a hypothetical workbook for " + report.ticker
            + ": this report is a " + str(getattr(report, "method", "unknown")).upper() + ".")

    u = report.user
    result = report.result
    diagnostics = report.derived.diagnostics or {}
    today = _dt.date.today().isoformat()
    years = len(result.path)
    last_col = max(2 + years, 6)

    wb = Workbook()
    ws = wb.active
    ws.title = "User-built hypothetical"
    # Red in the tab strip too: in a workbook someone has scrolled, or saved
    # beside other sheets, the tab is all that is left of the warning.
    ws.sheet_properties.tabColor = RED
    wb.calculation.fullCalcOnLoad = True

    _banner(ws, last_col,
            "USER-BUILT HYPOTHETICAL - NOT A VALUATION",
            "The key assumptions below were supplied by the person who "
            "generated this file, not derived from "
            + report.company_name.rstrip(".") + "'s filings.",
            "The company's own figures contradict them. This is not a price "
            "target, not a recommendation, and not an estimate of value.")

    row = 5
    _label(ws, row, report.company_name + "  (" + report.ticker + ")",
           bold=True, size=12)
    row += 1
    _style(ws.cell(row=row, column=1,
                   value="$ in millions except per-share data.  Basis: FY"
                         + str(report.fiscal_year) + ".  White cells are live "
                         "formulas - edit the shaded inputs and the path "
                         "recalculates."),
           italic=True, color=GREY, size=9)
    row += 2

    # --- The contrast, before anything else --------------------------------
    _section(ws, row,
             "WHAT WAS ASSUMED, AND WHAT THE COMPANY ACTUALLY REPORTS", last_col)
    row += 1
    _style(ws.cell(row=row, column=1, value="Assumption"), bold=True, size=9)
    _style(ws.cell(row=row, column=2, value="Assumed (by you)"), bold=True, size=9)
    _style(ws.cell(row=row, column=3, value="Actually reported"), bold=True, size=9)
    _style(ws.cell(row=row, column=4, value="Contradicts?"), bold=True, size=9)
    _style(ws.cell(row=row, column=5, value="What that means"), bold=True, size=9)
    row += 1
    for contrast in report.reality:
        # Years is not a percentage, and the figure the engine carries beside
        # it is the margin rather than a number of years - the contrast there
        # lives in the sentence, not in a column of like for like. Showing a
        # margin under "actually reported" next to "6" would invent a
        # comparison the data does not make.
        is_years = contrast.name == "years_to_target"
        fmt = "0" if is_years else PERCENT_2DP

        _label(ws, row, contrast.label)
        _style(ws.cell(row=row, column=2, value=contrast.assumed),
               color=BLUE, fmt=fmt, fill=USER_FILL)
        if is_years or contrast.actual is None:
            _style(ws.cell(row=row, column=3, value="-"), color=GREY, size=9,
                   align="center")
        else:
            _style(ws.cell(row=row, column=3, value=contrast.actual),
                   fmt=PERCENT_2DP)

        if contrast.contradicts:
            _style(ws.cell(row=row, column=4, value="YES"), bold=True, size=9,
                   color="C00000")
            for col in range(1, 6):
                ws.cell(row=row, column=col).fill = CONTRADICT_FILL
            _style(ws.cell(row=row, column=2, value=contrast.assumed),
                   color=BLUE, fmt=fmt).fill = CONTRADICT_FILL
        else:
            _style(ws.cell(row=row, column=4, value="no"), size=9, color=GREY)

        _style(ws.cell(row=row, column=5, value=contrast.statement), size=9,
               color=GREY)
        row += 1
    row += 1

    # --- Assumptions -------------------------------------------------------
    _section(ws, row,
             "ASSUMPTIONS   (the first three are yours, not the company's)",
             last_col)
    row += 1
    r_growth, r_margin, r_years = row, row + 1, row + 2
    for offset, (text, value, fmt) in enumerate([
        ("Revenue growth a year  [SUPPLIED BY YOU]", u.revenue_growth, PERCENT_2DP),
        ("Target operating margin  [SUPPLIED BY YOU]",
         u.target_operating_margin, PERCENT_2DP),
        ("Years to reach that margin  [SUPPLIED BY YOU]", u.years_to_target, "0"),
    ]):
        _label(ws, row + offset, text, bold=True)
        _input(ws, row + offset, value, fmt, source="your input", fill=USER_FILL)
    row += 3

    spec = report.derived.assumptions
    inputs = report.derived.inputs
    (r_tax, r_da, r_capex, r_nwc,
     r_mature_capex, r_mature_nwc, r_wacc) = (row, row + 1, row + 2, row + 3,
                                              row + 4, row + 5, row + 6)
    for offset, (text, value, fmt) in enumerate([
        ("Tax rate", spec.tax_rate, PERCENT_2DP),
        ("D&A today (% of revenue)", inputs.da_pct, PERCENT_2DP),
        ("Capex today (% of revenue)", inputs.capex_pct, PERCENT_2DP),
        ("Change in NWC today (% of revenue growth)", inputs.nwc_pct, PERCENT_2DP),
        ("Capex and D&A once mature (% of revenue)", spec.mature_capex_pct, PERCENT_2DP),
        ("Change in NWC once mature (%)", spec.mature_nwc_pct, PERCENT_2DP),
        ("WACC (discount rate)", spec.wacc, PERCENT_2DP),
    ]):
        _label(ws, row + offset, text)
        _input(ws, row + offset, value, fmt)
    row += 7

    _section(ws, row, "WHERE IT STARTS   (as reported)", last_col)
    row += 1
    r_revenue = row
    _label(ws, row, "Revenue")
    _input(ws, row, report.derived.inputs.revenue, MONEY)
    row += 1
    _label(ws, row, "Operating margin today")
    r_start_margin = row
    margin_now = report.derived.inputs.operating_margin
    if margin_now is None:
        _style(ws.cell(row=row, column=2, value="not reported"), color=GREY, size=9)
    else:
        _input(ws, row, margin_now, PERCENT_2DP)
    row += 1
    _label(ws, row, "Current share price (context only)")
    _input(ws, row, result.current_price, PER_SHARE)
    row += 2

    path_rows = _write_path(ws, report, row, last_col,
                            r_growth, r_margin, r_years, r_revenue,
                            r_tax, r_da, r_capex, r_nwc, r_wacc, r_start_margin,
                            r_mature_capex, r_mature_nwc)
    row = path_rows["pv"] + 2

    _section(ws, row,
             "THE FIGURE   (a hypothetical you built, not a valuation)", last_col)
    row += 1
    first = get_column_letter(3)
    last = get_column_letter(2 + years)
    _label(ws, row, "PV of the path's cash flows")
    _formula(ws, row, 2,
             "=SUM($" + first + "$" + str(path_rows["pv"]) + ":$" + last + "$"
             + str(path_rows["pv"]) + ")", MONEY)
    row += 1
    _label(ws, row, "Value once profitable, discounted to today")
    _input(ws, row, result.pv_value_at_profitability, MONEY,
           note="the engine's DCF from the year the margin is reached")
    row += 1
    _label(ws, row, "Equity value")
    _input(ws, row, result.equity_value, MONEY, note="after the net cash bridge")
    r_eq = row
    row += 1
    _label(ws, row, "Shares outstanding (millions)")
    shares = report.derived.inputs.shares
    _input(ws, row, shares, '#,##0.0')
    r_shares = row
    row += 1
    row = _headline(
        ws, row, "USER-BUILT HYPOTHETICAL PER SHARE",
        "=$B$" + str(r_eq) + "/$B$" + str(r_shares),
        "NOT A VALUATION - arithmetic on assumptions supplied by whoever "
        "generated this file, which the company's own figures contradict.",
        last_col)
    _style(ws.cell(row=row, column=1,
                   value="This is not intrinsic value and not a price target. "
                         "No upside or downside against the market price is "
                         "shown: the figure rests on assumptions the company's "
                         "own filings contradict."),
           italic=True, color=GREY, size=9)
    row += 2

    _section(ws, row, "THE DISCLAIMER THIS FIGURE CARRIES", last_col)
    row += 1
    row = _wrapped_block(ws, row, hypothetical_disclaimer(report), last_col)
    row += 1
    for warning in report.warnings:
        row = _wrapped_block(ws, row, "Warning: " + warning, last_col)

    _style(ws.cell(row=row + 1, column=1, value="Generated " + today + "."),
           color=GREY, size=9)

    _present(ws, last_col, wide_note_col="E")
    return wb


def _present(ws, last_col: int, *, wide_note_col: str | None = None) -> None:
    ws.column_dimensions["A"].width = 52
    ws.column_dimensions["B"].width = 18
    for col in range(3, last_col + 2):
        ws.column_dimensions[get_column_letter(col)].width = 15
    if wide_note_col:
        ws.column_dimensions[wide_note_col].width = 70
    # No frozen panes, for the same reason as the DCF and DDM workbooks - and
    # here it matters more: a freeze below the banner could scroll the warning
    # out of view, which is the one thing this sheet must never do.
    ws.freeze_panes = None
    ws.sheet_view.showGridLines = False


def speculative_workbook_bytes(report: SpeculativeReport) -> bytes:
    buffer = BytesIO()
    build_speculative_workbook(report).save(buffer)
    return buffer.getvalue()


def hypothetical_workbook_bytes(report: HypotheticalReport) -> bytes:
    buffer = BytesIO()
    build_hypothetical_workbook(report).save(buffer)
    return buffer.getvalue()


def speculative_filename_for(report: SpeculativeReport) -> str:
    return report.ticker + "_Speculative.xlsx"


def hypothetical_filename_for(report: HypotheticalReport) -> str:
    return report.ticker + "_Hypothetical.xlsx"
