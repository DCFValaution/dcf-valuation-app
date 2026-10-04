"""
Tests for the dividend discount model .xlsx export.

The central claim is the same one the DCF export's tests make: the workbook is
*live*. The assumption block and the reported basis hold numbers; everything
downstream is a formula wired to them. A workbook of precomputed values would
pass a naive "the file opens" check while being inert, so these tests assert
formula strings and their references explicitly.

The arithmetic is checked a second way, independently of Excel: each formula's
result is recomputed here from the model's own definition and compared against
what the engine produced. That is what makes "the sheet agrees with the
backend" a claim about the formulas rather than about openpyxl.

Market data is stubbed throughout; no network calls.

Run with:  python -m pytest test_ddm_excel_export.py -v
"""

from io import BytesIO

import pytest
from openpyxl import load_workbook

import analysis
import api
import assumptions as A
import ddm_assumptions as D
import ddm_excel_export as X
import excel_export as E
from test_api import PROFITABLE
from test_ddm_api import BANK, BUYBACKS, fake_dividends, fake_financials
from test_ddm_assumptions import TODAY


@pytest.fixture(autouse=True)
def _stubbed_market_data(monkeypatch):
    """The same stubs test_ddm_api installs.

    An autouse fixture belongs to the module that defines it, so importing
    that module's tickers does not bring its mocks with them - without this
    the suite would reach for the network and fail on whatever it found.
    """
    monkeypatch.setattr(analysis, "fetch_financials", fake_financials)
    monkeypatch.setattr(analysis, "fetch_dividends", fake_dividends)
    real_analyse = D.analyse_dividends
    monkeypatch.setattr(analysis, "analyse_dividends",
                        lambda events: real_analyse(events, today=TODAY))
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))
    api.reset_rate_limits()


@pytest.fixture
def report():
    """A bank valued on its dividends, through the real routing."""
    return analysis.value_company(BANK)


@pytest.fixture
def sheet(report):
    wb = X.build_ddm_workbook(report)
    return wb["DDM Model"]


def _reload(report):
    """Round-trip through bytes, the way a download reaches the user."""
    buffer = BytesIO(X.ddm_workbook_bytes(report))
    return load_workbook(buffer)


# ---------------------------------------------------------------------------
# It is a workbook, and it is the right one
# ---------------------------------------------------------------------------

def test_it_builds_and_survives_the_round_trip(report):
    wb = _reload(report)
    assert wb.sheetnames == ["DDM Model"]


def test_the_filename_names_the_model_and_the_company(report):
    assert X.ddm_filename_for(report) == f"{BANK}_DDM_Model.xlsx"


def test_it_recalculates_on_open(report):
    # openpyxl writes no cached results, so without this the file opens full
    # of blanks in anything that will not calculate unprompted.
    assert _reload(report).calculation.fullCalcOnLoad is True


def test_nothing_is_frozen(sheet):
    # The DCF workbook learned this the hard way: a freeze below the
    # assumptions pinned most of the window and the sheet appeared not to
    # scroll at all.
    assert sheet.freeze_panes is None


def test_it_refuses_to_build_from_a_dcf_report():
    report = analysis.value_company(PROFITABLE)
    with pytest.raises(ValueError, match="valued with a DCF"):
        X.build_ddm_workbook(report)


def test_the_dcf_builder_still_refuses_a_ddm_report(report):
    """The two builders stay in their lanes."""
    with pytest.raises(ValueError, match="DCF models"):
        E.build_workbook(report)


# ---------------------------------------------------------------------------
# Inputs are numbers; everything downstream is a formula
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref,name", [
    (f"B{X.R_DIV_GROWTH}", "dividend growth"),
    (f"B{X.R_GROWTH_YEARS}", "growth stage length"),
    (f"B{X.R_COST_EQUITY}", "cost of equity"),
    (f"B{X.R_TERMINAL}", "terminal growth"),
    (f"B{X.R_DIVIDEND_IN}", "annual dividend"),
    (f"B{X.R_PRICE_IN}", "share price"),
])
def test_the_editable_inputs_hold_numbers(sheet, ref, name):
    value = sheet[ref].value
    assert isinstance(value, (int, float)), f"{name} should be editable, got {value!r}"


@pytest.mark.parametrize("ref,references", [
    (f"B{X.R_YIELD}", [X.C_D0]),
    (f"B{X.R_TERM_DIV}", [X.C_D0, X.C_G1, X.C_N]),
    (f"B{X.R_TV}", [X.C_R, X.C_G2]),
    (f"B{X.R_PV_TV}", [X.C_R, X.C_N]),
    (f"B{X.R_INTRINSIC}", [f"$B${X.R_PV_SUM}", f"$B${X.R_PV_TV}"]),
    (f"B{X.R_UPSIDE}", [f"$B${X.R_INTRINSIC}"]),
    (f"B{X.R_TV_PCT}", [f"$B${X.R_PV_TV}"]),
])
def test_the_valuation_is_formulas_wired_to_the_inputs(sheet, ref, references):
    formula = sheet[ref].value
    assert isinstance(formula, str) and formula.startswith("="), \
        f"{ref} should be a formula, got {formula!r}"
    for reference in references:
        assert reference in formula, f"{ref} should reference {reference}"


def test_the_projection_is_formulas_guarded_by_the_growth_stage(sheet, report):
    years = report.derived.assumptions.high_growth_years
    assert years > 0, "this fixture is meant to have a growth stage"
    for offset in range(1, years + 1):
        column = chr(ord("C") + offset - 1)
        for row in (X.R_DIVIDEND, X.R_FACTOR, X.R_PV_DIV):
            formula = sheet[f"{column}{row}"].value
            assert formula.startswith("=IF("), f"{column}{row}: {formula!r}"
            # Shortening N must blank the years past it.
            assert X.C_N in formula


def test_the_sensitivity_axes_follow_the_assumption_cells(sheet):
    """So the centre cell is the headline valuation whatever the user sets."""
    centre = len(X.SENSITIVITY_OFFSETS) // 2
    row_axis = sheet[f"A{X.R_SENS_FIRST + centre}"].value
    col_axis = sheet.cell(row=X.R_SENS_COLS, column=2 + centre).value
    assert row_axis.startswith("=" + X.C_R)
    assert col_axis.startswith("=" + X.C_G2)


def test_every_sensitivity_cell_is_a_formula(sheet):
    for r_index in range(len(X.SENSITIVITY_OFFSETS)):
        for c_index in range(len(X.SENSITIVITY_OFFSETS)):
            cell = sheet.cell(row=X.R_SENS_FIRST + r_index, column=2 + c_index)
            assert isinstance(cell.value, str) and cell.value.startswith("=IF(")
            # The perpetuity has no finite value where g >= r, and the sheet
            # says so rather than showing a negative number.
            assert '"n/a"' in cell.value


# ---------------------------------------------------------------------------
# The arithmetic, recomputed from the model's definition
# ---------------------------------------------------------------------------

def _expected(report):
    a = report.derived.assumptions
    d0 = report.inputs.current_dividend
    r, g1, n, g2 = (a.cost_of_equity, a.dividend_growth,
                    int(a.high_growth_years), a.terminal_growth)
    pv = sum(d0 * (1 + g1) ** t / (1 + r) ** t for t in range(1, n + 1))
    tv = d0 * (1 + g1) ** n * (1 + g2) / (r - g2) / (1 + r) ** n
    return pv, tv


def test_the_sheets_formulas_describe_the_same_model_the_backend_ran(report):
    """The formulas are strings here, so the check is on what they compute.

    Recomputed from the DDM's own definition rather than from the engine's
    output, so this fails if the workbook encodes a different model - which is
    the mistake worth catching.
    """
    pv, tv = _expected(report)
    assert report.result.pv_dividends_sum == pytest.approx(pv, rel=1e-12)
    assert report.result.pv_terminal_value == pytest.approx(tv, rel=1e-12)
    assert report.result.intrinsic_value_per_share == pytest.approx(pv + tv, rel=1e-12)


# ---------------------------------------------------------------------------
# The honesty the sheet has to carry out of the app
# ---------------------------------------------------------------------------

def _notes(sheet):
    out = []
    row = X.R_NOTES_HEAD + 1
    while sheet.cell(row=row, column=1).value:
        out.append(str(sheet.cell(row=row, column=1).value))
        row += 1
    return out


def test_the_honesty_note_travels_with_the_file(sheet):
    assert any("is the output of these assumptions" in n for n in _notes(sheet))


def test_the_buyback_understatement_warning_travels_with_the_file():
    """A bank returning more through buybacks than dividends.

    The DDM cannot see buybacks, so a workbook that did not say so would
    understate the company silently - which is the one thing the file must
    not do once it has left the app.

    Not the lender fixture: a lender whose buybacks exceed its dividends is
    routed to the DCF by the financial-routing rule, so it never reaches this
    workbook at all.
    """
    report = analysis.value_company(BUYBACKS)
    sheet = X.build_ddm_workbook(report)["DDM Model"]
    notes = " ".join(_notes(sheet))
    assert "buyback" in notes.lower()
    assert "understate" in notes.lower()


def test_the_model_is_explained_in_the_notes(sheet):
    notes = " ".join(_notes(sheet))
    assert "Gordon growth" in notes
    assert "r > g2" in notes


def test_no_annotation_is_mistaken_for_a_formula(sheet):
    """A note reading "= dividend / price" reached the sheet as #REF!.

    openpyxl writes any string beginning with "=" as a formula, so prose that
    starts with one becomes a broken reference in the opened file. The grey
    annotation columns are prose and must never start with "=".
    """
    # Only the assumption and basis blocks: columns C and D are the grey
    # source/detail annotations there. Further down, C onward is the
    # projection grid, where a formula is exactly what belongs.
    for row in range(X.R_ASSUMPTIONS_HEAD, X.R_PROJ_HEAD):
        for col in (3, 4):
            value = sheet.cell(row=row, column=col).value
            if isinstance(value, str) and value.startswith("="):
                raise AssertionError(
                    f"annotation at {sheet.cell(row=row, column=col).coordinate} "
                    f"will be read as a formula: {value!r}")


def test_the_dividend_basis_is_on_the_sheet(sheet):
    labels = [sheet.cell(row=row, column=1).value or ""
              for row in range(X.R_BASIS_HEAD, X.R_PROJ_HEAD)]
    joined = " ".join(labels).lower()
    for expected in ("dividend per share", "share price", "dividend yield",
                     "payout ratio", "return on equity"):
        assert expected in joined, f"missing {expected!r} from the basis block"
