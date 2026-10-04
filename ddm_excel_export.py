"""
Builds a downloadable .xlsx dividend discount model, the counterpart to
excel_export.py's DCF workbook.

Written as a separate module rather than as branches inside the DCF builder:
the two models share conventions but not a single row of layout, and the DCF
workbook is the one artefact in this project that users already rely on.

EVERYTHING IS A FORMULA
-----------------------
Same standard as the DCF export. Only the assumption block and the company's
reported basis hold literal numbers; every projected dividend, discount
factor, present value, the terminal value, the headline per-share figure and
all 25 sensitivity cells are Excel formulas referencing those inputs. Change
the cost of equity and the whole sheet moves.

THE GROWTH STAGE IS LIVE, DOWNWARD
----------------------------------
The DCF's horizon is structural - the grid has a fixed column count, so the
cell is left unshaded and labelled as such. The DDM's N is different: it is
one of the model's four assumptions, so it is editable, and the projection is
written to respond to it.

Each projected year is guarded by `IF($B$6 >= t, ...)`, so shortening the
growth stage blanks the years past it and the sums follow. The terminal
dividend is D0 x (1+g1)^N in closed form rather than a reference to the last
column, so it tracks N too, as does the factor discounting the terminal value
back. Lengthening N past the generated columns cannot add them, which the
sheet says plainly next to the cell.

Each dividend is written in closed form, D0 x (1+g1)^t, rather than chained
off the previous column. Chaining would make a blanked year propagate its
blank to every year after it even when N is raised again.
"""

from __future__ import annotations

import datetime as _dt
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from analysis import DDMReport, honesty_note
from dcf import SENSITIVITY_GROWTH_STEP, SENSITIVITY_OFFSETS
from ddm import SENSITIVITY_COST_OF_EQUITY_STEP

FONT_NAME = "Arial"

BLUE = "0000FF"
BLACK = "000000"
GREY = "595959"
INPUT_FILL = PatternFill("solid", start_color="FFF2CC")
HEADER_FILL = PatternFill("solid", start_color="D9E1F2")
TITLE_FILL = PatternFill("solid", start_color="1F3864")
CENTRE_FILL = PatternFill("solid", start_color="E2EFDA")

PER_SHARE = '$#,##0.00;($#,##0.00);"-"'
PERCENT_2DP = '0.00%'
FACTOR = '0.000'

THIN_TOP = Border(top=Side(style="thin", color="808080"))

R_TITLE = 1
R_SUBTITLE = 2
R_ASSUMPTIONS_HEAD = 4
R_DIV_GROWTH = 5
R_GROWTH_YEARS = 6
R_COST_EQUITY = 7
R_TERMINAL = 8
R_BASIS_HEAD = 10
R_DIVIDEND_IN = 11
R_PRICE_IN = 12
R_YIELD = 13
R_PAYOUT = 14
R_ROE = 15
R_SUSTAINABLE = 16
R_PROJ_HEAD = 18
R_COLUMN_HEADS = 19
R_PERIOD = 20
R_DIVIDEND = 21
R_FACTOR = 22
R_PV_DIV = 23
R_VAL_HEAD = 25
R_PV_SUM = 26
R_TERM_DIV = 27
R_TV = 28
R_PV_TV = 29
R_INTRINSIC = 30
R_PRICE = 31
R_UPSIDE = 32
R_TV_PCT = 33
R_SENS_HEAD = 35
R_SENS_LEGEND = 36
R_SENS_COLS = 37
R_SENS_FIRST = 38
R_SENS_LAST = R_SENS_FIRST + len(SENSITIVITY_OFFSETS) - 1
R_NOTES_HEAD = R_SENS_LAST + 2

# The cells the formulas lean on, named so the formula strings read the way
# the model does rather than as a wall of coordinates.
C_G1 = "$B$" + str(R_DIV_GROWTH)
C_N = "$B$" + str(R_GROWTH_YEARS)
C_R = "$B$" + str(R_COST_EQUITY)
C_G2 = "$B$" + str(R_TERMINAL)
C_D0 = "$B$" + str(R_DIVIDEND_IN)


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


def _input(ws, row, value, fmt, *, note=None, source=None):
    """A hardcoded, user-editable input: blue text on a highlighted fill."""
    cell = ws.cell(row=row, column=2, value=value)
    _style(cell, color=BLUE, fmt=fmt, fill=INPUT_FILL)
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


def _stage_one_pv(periods_range: str, rate: str) -> str:
    """Present value of the stage-one dividends at *rate*, as a formula body.

    Used by the sensitivity grid, where the discount rate is the axis cell
    rather than the model's own. The mask keeps the sum honest when the growth
    stage has been shortened: the period row always holds 1..N_generated, so
    without it a shortened stage would still be summed in full here while the
    projection above showed those years blanked.
    """
    if not periods_range:
        return "0"
    return (C_D0 + "*SUMPRODUCT((" + periods_range + "<=" + C_N + ")*"
            "(1+" + C_G1 + ")^" + periods_range + "/(1+" + rate + ")^" + periods_range + ")")


def _terminal_pv(rate: str, growth: str) -> str:
    """Present value of the terminal value at *rate* and *growth*."""
    return (C_D0 + "*(1+" + C_G1 + ")^" + C_N + "*(1+" + growth + ")"
            "/(" + rate + "-" + growth + ")/(1+" + rate + ")^" + C_N)


def build_ddm_workbook(report: DDMReport) -> Workbook:
    """
    Build a live-formula dividend discount model workbook for *report*.

    The caller is responsible for having checked suitability: a workbook for a
    company the guard refused would be exactly the artefact the guard exists
    to prevent, and a file outlives the response that carried it.
    """
    if getattr(report, "method", None) != "ddm":
        raise ValueError(
            "Refusing to build a DDM workbook for " + report.ticker + ": this "
            "report was valued with a " + str(getattr(report, "method", "unknown")).upper() + "."
        )
    if report.result is None:
        raise ValueError(
            "Refusing to build a workbook for " + report.ticker + ": the "
            "suitability guard produced no valuation."
        )

    a = report.derived.assumptions
    provenance = report.derived.provenance
    diagnostics = report.derived.diagnostics or {}
    today = _dt.date.today().isoformat()

    years = int(a.high_growth_years)
    first_col, last_proj_col = 3, 2 + years
    # The sensitivity grid is five columns wide starting at B, so the sheet is
    # never narrower than that even when there is no growth stage to project.
    last_col = max(last_proj_col, 6)
    periods_range = ""
    if years:
        periods_range = ("$C$" + str(R_PERIOD) + ":$"
                         + get_column_letter(last_proj_col) + "$" + str(R_PERIOD))

    wb = Workbook()
    ws = wb.active
    ws.title = "DDM Model"
    wb.calculation.fullCalcOnLoad = True

    # --- Title -------------------------------------------------------------
    title = _label(ws, R_TITLE,
                   report.company_name + "  (" + report.ticker + ")  -  "
                   "Dividend Discount Model (DDM) Valuation",
                   bold=True, size=14)
    title.font = Font(name=FONT_NAME, bold=True, size=14, color="FFFFFF")
    for col in range(1, last_col + 1):
        ws.cell(row=R_TITLE, column=col).fill = TITLE_FILL

    _label(ws, R_SUBTITLE,
           "All figures are per share, in the currency the company reports.  "
           "Basis: FY" + str(report.fiscal_year) + ".  Every white cell is a "
           "formula - edit the shaded input cells and the whole model "
           "recalculates.",
           italic=True, color=GREY, size=9)

    # --- 1. Assumptions ----------------------------------------------------
    _section(ws, R_ASSUMPTIONS_HEAD,
             "1.  ASSUMPTIONS   (shaded blue-text cells are editable inputs)",
             last_col)

    for row, text, name in [
        (R_DIV_GROWTH, "Dividend growth rate, stage one (g1)", "dividend_growth"),
        (R_COST_EQUITY, "Cost of equity (discount rate, r)", "cost_of_equity"),
        (R_TERMINAL, "Terminal growth rate (g2)", "terminal_growth"),
    ]:
        _label(ws, row, text)
        prov = provenance.get(name)
        _input(ws, row, getattr(a, name), PERCENT_2DP,
               source=prov.source if prov else None,
               note=prov.detail if prov else None)

    # Editable, and the projection responds to it - but only downward, since
    # the columns were generated for this many years.
    _label(ws, R_GROWTH_YEARS, "Growth stage length (N, years)")
    prov_years = provenance.get("high_growth_years")
    _input(ws, R_GROWTH_YEARS, years, "0",
           source=prov_years.source if prov_years else None,
           note="shorten to drop years; regenerate the file to lengthen it")

    # --- 2. Dividend basis -------------------------------------------------
    _section(ws, R_BASIS_HEAD, "2.  DIVIDEND BASIS   (as reported)", last_col)

    _label(ws, R_DIVIDEND_IN, "Annual dividend per share (D0)")
    _input(ws, R_DIVIDEND_IN, report.inputs.current_dividend, PER_SHARE,
           note=diagnostics.get("current_dividend_detail"))

    _label(ws, R_PRICE_IN, "Current share price")
    _input(ws, R_PRICE_IN, report.inputs.current_price, PER_SHARE)

    _label(ws, R_YIELD, "Dividend yield")
    _formula(ws, R_YIELD, 2, "=" + C_D0 + "/$B$" + str(R_PRICE_IN), PERCENT_2DP)
    # No leading "=" in the annotation: openpyxl writes any string starting
    # with one as a formula, and "= dividend / price" is not one - it reached
    # the sheet as #REF! until this was written as prose.
    _style(ws.cell(row=R_YIELD, column=3, value="dividend / price"),
           color=GREY, size=9)

    payout = diagnostics.get("payout_ratio")
    roe = diagnostics.get("return_on_equity")

    _label(ws, R_PAYOUT, "Payout ratio (median of recent years)")
    if payout is None:
        _style(ws.cell(row=R_PAYOUT, column=2, value="not reported"),
               color=GREY, size=9)
    else:
        _input(ws, R_PAYOUT, payout, PERCENT_2DP)

    _label(ws, R_ROE, "Return on equity (median of recent years)")
    if roe is None:
        _style(ws.cell(row=R_ROE, column=2, value="not reported"),
               color=GREY, size=9)
    else:
        _input(ws, R_ROE, roe, PERCENT_2DP)

    _label(ws, R_SUSTAINABLE, "Sustainable growth (ROE x retention)")
    if payout is None or roe is None:
        _style(ws.cell(row=R_SUSTAINABLE, column=2,
                       value="needs both ROE and payout"), color=GREY, size=9)
    else:
        _formula(ws, R_SUSTAINABLE, 2,
                 "=$B$" + str(R_ROE) + "*(1-MIN($B$" + str(R_PAYOUT) + ",1))",
                 PERCENT_2DP)
        _style(ws.cell(row=R_SUSTAINABLE, column=3,
                       value="what the retained earnings alone would fund"),
               color=GREY, size=9)

    # --- 3. Dividend projection -------------------------------------------
    _section(ws, R_PROJ_HEAD,
             "3.  DIVIDEND PROJECTION   (stage one: years 1 to N)", last_col)

    if years:
        _label(ws, R_COLUMN_HEADS, "Year", bold=True)
        for offset, col in enumerate(range(first_col, last_proj_col + 1), start=1):
            _style(ws.cell(row=R_COLUMN_HEADS, column=col, value="Year " + str(offset)),
                   bold=True, align="center")

        _label(ws, R_PERIOD, "Period (t)", indent=1, color=GREY)
        _label(ws, R_DIVIDEND, "Dividend per share  = D0 x (1+g1)^t", indent=1)
        _label(ws, R_FACTOR, "Discount factor  = 1 / (1+r)^t", indent=1)
        _label(ws, R_PV_DIV, "PV of dividend", indent=1)

        for offset, col in enumerate(range(first_col, last_proj_col + 1), start=1):
            letter = get_column_letter(col)
            t = str(offset)
            _style(ws.cell(row=R_PERIOD, column=col, value=offset),
                   color=GREY, fmt="0", align="center")
            _formula(ws, R_DIVIDEND, col,
                     '=IF(' + C_N + '>=' + t + ',' + C_D0 + '*(1+' + C_G1 + ')^' + t + ',"")',
                     PER_SHARE)
            _formula(ws, R_FACTOR, col,
                     '=IF(' + C_N + '>=' + t + ',1/(1+' + C_R + ')^' + t + ',"")',
                     FACTOR)
            _formula(ws, R_PV_DIV, col,
                     '=IF(' + C_N + '>=' + t + ',' + letter + str(R_DIVIDEND)
                     + '*' + letter + str(R_FACTOR) + ',"")',
                     PER_SHARE)
    else:
        _label(ws, R_COLUMN_HEADS,
               "No growth stage: N = 0, so this is a single-stage Gordon growth "
               "valuation off D0 directly.",
               italic=True, color=GREY, size=9)

    # --- 4. Valuation ------------------------------------------------------
    _section(ws, R_VAL_HEAD, "4.  VALUATION", last_col)

    _label(ws, R_PV_SUM, "PV of stage-one dividends")
    if years:
        pv_range = ("$C$" + str(R_PV_DIV) + ":$"
                    + get_column_letter(last_proj_col) + "$" + str(R_PV_DIV))
        _formula(ws, R_PV_SUM, 2, "=SUM(" + pv_range + ")", PER_SHARE)
    else:
        _formula(ws, R_PV_SUM, 2, "=0", PER_SHARE)

    _label(ws, R_TERM_DIV, "Dividend in the final year  = D0 x (1+g1)^N")
    _formula(ws, R_TERM_DIV, 2,
             "=" + C_D0 + "*(1+" + C_G1 + ")^" + C_N, PER_SHARE)

    _label(ws, R_TV, "Terminal value  = D_N x (1+g2) / (r - g2)")
    _formula(ws, R_TV, 2,
             "=$B$" + str(R_TERM_DIV) + "*(1+" + C_G2 + ")/(" + C_R + "-" + C_G2 + ")",
             PER_SHARE)

    _label(ws, R_PV_TV, "PV of terminal value")
    _formula(ws, R_PV_TV, 2,
             "=$B$" + str(R_TV) + "/(1+" + C_R + ")^" + C_N, PER_SHARE)

    _label(ws, R_INTRINSIC, "Intrinsic value per share", bold=True)
    _formula(ws, R_INTRINSIC, 2,
             "=$B$" + str(R_PV_SUM) + "+$B$" + str(R_PV_TV), PER_SHARE,
             bold=True, top_border=True)

    _label(ws, R_PRICE, "Current share price")
    _formula(ws, R_PRICE, 2, "=$B$" + str(R_PRICE_IN), PER_SHARE)

    _label(ws, R_UPSIDE, "Implied upside / (downside)")
    _formula(ws, R_UPSIDE, 2,
             "=($B$" + str(R_INTRINSIC) + "-$B$" + str(R_PRICE) + ")/$B$" + str(R_PRICE),
             PERCENT_2DP)

    _label(ws, R_TV_PCT, "Terminal value as % of valuation")
    _formula(ws, R_TV_PCT, 2,
             "=$B$" + str(R_PV_TV) + "/$B$" + str(R_INTRINSIC), PERCENT_2DP)

    # --- 5. Sensitivity ----------------------------------------------------
    _section(ws, R_SENS_HEAD, "5.  SENSITIVITY   (value per share)", last_col)
    _label(ws, R_SENS_LEGEND,
           "Rows: cost of equity.   Columns: terminal growth.   The axes follow "
           "the assumption cells above, so the centre cell is always the "
           "headline valuation.",
           italic=True, color=GREY, size=9)

    _label(ws, R_SENS_COLS, "Cost of equity  \\  terminal growth", bold=True)
    for index, offset in enumerate(SENSITIVITY_OFFSETS):
        col = 2 + index
        step = ("%+.10g" % (offset * SENSITIVITY_GROWTH_STEP))
        _formula(ws, R_SENS_COLS, col, "=" + C_G2 + step, PERCENT_2DP, bold=True)
        ws.cell(row=R_SENS_COLS, column=col).alignment = Alignment(horizontal="center")

    centre = len(SENSITIVITY_OFFSETS) // 2
    for r_index, r_offset in enumerate(SENSITIVITY_OFFSETS):
        row = R_SENS_FIRST + r_index
        step = ("%+.10g" % (r_offset * SENSITIVITY_COST_OF_EQUITY_STEP))
        _formula(ws, row, 1, "=" + C_R + step, PERCENT_2DP, bold=True)
        rate = "$A$" + str(row)
        for c_index in range(len(SENSITIVITY_OFFSETS)):
            col = 2 + c_index
            growth = get_column_letter(col) + "$" + str(R_SENS_COLS)
            body = _stage_one_pv(periods_range, rate) + "+" + _terminal_pv(rate, growth)
            cell = _formula(ws, row, col,
                            '=IF(' + growth + '>=' + rate + ',"n/a",' + body + ')',
                            PER_SHARE)
            if r_index == centre and c_index == centre:
                cell.font = Font(name=FONT_NAME, bold=True, size=10)
                cell.fill = CENTRE_FILL

    # --- Notes -------------------------------------------------------------
    _label(ws, R_NOTES_HEAD, "NOTES", bold=True)
    notes = [
        "A dividend discount model values a share as the present value of the "
        "dividends it is expected to pay: V = sum D_t/(1+r)^t + TV_N/(1+r)^N.",
        "Terminal value uses Gordon growth off the final-year dividend: "
        "TV = D_N x (1+g2) / (r - g2). It requires r > g2.",
        "Shortening the growth stage (N) blanks the years past it and the sums "
        "follow. Lengthening it past the generated columns needs the file "
        "regenerated.",
        "The highlighted centre cell of the sensitivity grid is the headline "
        "valuation.",
        "Blue figures are inputs; every black figure is a live formula.",
    ]
    note = honesty_note(report)
    if note:
        notes.append(note)
    # A doubt about the method goes first: the workbook is read top down, and
    # this qualifies every figure in it.
    for warning in report.suitability.method_fit:
        notes.append("This model may not fit this company: " + warning)
    for warning in report.suitability.warnings:
        notes.append("Warning: " + warning)
    notes.append(
        "Generated " + today + ". Assumption sources are shown beside each "
        "input in section 1."
    )
    for index, text in enumerate(notes):
        _style(ws.cell(row=R_NOTES_HEAD + 1 + index, column=1, value="-  " + text),
               color=GREY, size=9)

    # --- Presentation ------------------------------------------------------
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 18
    for col in range(3, last_col + 2):
        ws.column_dimensions[get_column_letter(col)].width = 15

    # No frozen panes, for the same reason as the DCF workbook: the sheet is
    # read top to bottom in five short sections, and a freeze below the
    # assumptions would pin most of the window. Explicit rather than omitted,
    # so the intent is not mistaken for an oversight.
    ws.freeze_panes = None
    ws.sheet_view.showGridLines = False

    return wb


def ddm_workbook_bytes(report: DDMReport) -> bytes:
    """Serialise the DDM workbook for *report* to bytes for an HTTP response."""
    buffer = BytesIO()
    build_ddm_workbook(report).save(buffer)
    return buffer.getvalue()


def ddm_filename_for(report: DDMReport) -> str:
    return report.ticker + "_DDM_Model.xlsx"
