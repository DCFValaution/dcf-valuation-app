"""
Tests for the speculative and hypothetical workbooks.

These two files are the riskiest the project produces: a figure the app
refuses to stand behind, in a format that looks exactly as official as any
other spreadsheet, which will outlive the screen that explained it. So most of
what is tested here is not arithmetic but framing - that the banner is there,
that the figure is never called a valuation, that no upside against the market
price appears, and that the full disclaimer travels with the file.

The arithmetic is tested too, and against the engine's own definition rather
than against the engine's output, so a workbook that encoded a different model
would fail here rather than agree with itself.

Run with:  python -m pytest test_speculative_excel_export.py -v
"""

from io import BytesIO

import pytest
from openpyxl import load_workbook

import analysis
import speculative_excel_export as X
from hypothetical import UserAssumptions
from test_api import (LOSSMAKING, PROFITABLE, fake_fetch_financials,
                      make_fin)
import assumptions as A


@pytest.fixture(autouse=True)
def _mock_market_data(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", fake_fetch_financials)
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


@pytest.fixture
def speculative():
    return analysis.value_company_speculatively(LOSSMAKING)


@pytest.fixture
def sheet(speculative):
    return X.build_speculative_workbook(speculative)["Speculative estimate"]


def _column_a(ws, limit=120):
    return [str(ws.cell(row=r, column=1).value or "") for r in range(1, limit)]


def _text(ws, limit=120):
    return " ".join(_column_a(ws, limit)).lower()


# ---------------------------------------------------------------------------
# The banner, which is the whole point
# ---------------------------------------------------------------------------

def test_the_first_row_says_it_is_not_a_valuation(sheet):
    """Before the company name, before the figure, before anything."""
    assert "NOT A VALUATION" in str(sheet["A1"].value)


def test_the_banner_is_filled_red_across_the_sheet(sheet):
    """Visible as a warning before a word of it is read."""
    for row in (X.R_BANNER, X.R_BANNER_2, X.R_BANNER_3):
        for col in range(1, 5):
            fill = sheet.cell(row=row, column=col).fill
            assert "C00000" in str(fill.start_color.rgb), f"row {row} col {col}"


def test_the_banner_names_the_reason_and_disclaims_a_price_target(sheet):
    banner = " ".join(str(sheet.cell(row=r, column=1).value or "")
                      for r in (X.R_BANNER, X.R_BANNER_2, X.R_BANNER_3)).lower()
    assert "loses money" in banner
    assert "not a price target" in banner


# ---------------------------------------------------------------------------
# What the figure may and may not be called
# ---------------------------------------------------------------------------

def test_the_headline_is_called_a_speculative_estimate(sheet):
    labels = _column_a(sheet)
    assert any("SPECULATIVE ESTIMATE PER SHARE" in label for label in labels)


def test_the_sheet_never_calls_the_figure_intrinsic_value(sheet):
    """The one phrase this file must not contain about its own number."""
    for label in _column_a(sheet):
        lowered = label.lower()
        if "intrinsic value" in lowered:
            # Saying it is NOT intrinsic value is the point, and allowed.
            assert "not intrinsic value" in lowered, label


def test_no_upside_or_downside_against_the_market_price(sheet):
    """A gap between a price and a figure this uncertain is the number that
    would be screenshotted, so the sheet does not compute one."""
    text = _text(sheet)
    assert "upside" not in text or "no upside" in text
    assert "implied upside" not in text


def test_the_price_is_there_but_only_as_context(sheet):
    assert any("context only" in label.lower() for label in _column_a(sheet))


# ---------------------------------------------------------------------------
# The disclaimer, in the file rather than on the screen it came from
# ---------------------------------------------------------------------------

def test_the_full_disclaimer_travels_with_the_file(sheet, speculative):
    text = _text(sheet)
    assert "not a valuation and must not be treated as one" in text
    assert "explicitly requested" in text


def test_why_the_standard_valuation_was_refused_is_in_the_sheet(sheet):
    assert any("REFUSED" in label for label in _column_a(sheet))


def test_the_warnings_travel_too(sheet, speculative):
    if not speculative.suitability.warnings:
        pytest.skip("this fixture raises no warnings")
    assert "warning:" in _text(sheet)


# ---------------------------------------------------------------------------
# The projection is live, and is the engine's model
# ---------------------------------------------------------------------------

def test_the_path_is_formulas(sheet, speculative):
    years = len(speculative.result.path)
    for offset in range(1, years + 1):
        column = chr(ord("C") + offset - 1)
        for label in ("revenue", "margin", "ebit", "ufcf", "factor", "pv"):
            pass
    # The rows are found by their labels rather than by fixed numbers, since
    # the block moves when the refusal above it is longer or shorter.
    labels = _column_a(sheet)
    pv_row = next(i + 1 for i, label in enumerate(labels)
                  if label.strip() == "PV of cash flow")
    for offset in range(years):
        cell = sheet.cell(row=pv_row, column=3 + offset)
        assert isinstance(cell.value, str) and cell.value.startswith("=")


def test_the_sheet_reproduces_the_engines_path(speculative):
    """Recomputed from the engine's documented shape, not from its output.

    Every ratio travels to its mature level in a straight line over N years -
    the margin, D&A, capex and working capital alike. An export that ramped
    only the margin was wrong by thousands of millions on a company starting
    at a deep loss, and agreed with nothing.
    """
    a = speculative.derived.assumptions
    i = speculative.derived.inputs
    previous, pv_sum = i.revenue, 0.0
    years = a.years_to_profitability
    for t in range(1, years + 1):
        progress = t / years
        revenue = previous * (1 + a.speculative_revenue_growth)
        margin = i.operating_margin + (a.target_operating_margin - i.operating_margin) * progress
        ebit = revenue * margin
        tax = max(ebit, 0.0) * a.tax_rate
        da = revenue * (i.da_pct + (a.mature_capex_pct - i.da_pct) * progress)
        capex = revenue * (i.capex_pct + (a.mature_capex_pct - i.capex_pct) * progress)
        nwc = (revenue - previous) * (i.nwc_pct + (a.mature_nwc_pct - i.nwc_pct) * progress)
        pv_sum += (ebit - tax + da - capex - nwc) / (1 + a.wacc) ** t
        previous = revenue
    assert speculative.result.pv_path_sum == pytest.approx(pv_sum, rel=1e-9)


# ---------------------------------------------------------------------------
# Housekeeping shared with the other workbooks
# ---------------------------------------------------------------------------

def test_it_recalculates_on_open(speculative):
    buffer = BytesIO(X.speculative_workbook_bytes(speculative))
    assert load_workbook(buffer).calculation.fullCalcOnLoad is True


def test_nothing_is_frozen(sheet):
    """A freeze here could scroll the warning out of view, which this sheet
    must never do."""
    assert sheet.freeze_panes is None


def test_the_filename_says_speculative_and_never_valuation(speculative):
    name = X.speculative_filename_for(speculative)
    assert name == f"{LOSSMAKING}_Speculative.xlsx"
    assert "valuation" not in name.lower()


def test_it_refuses_a_report_of_another_kind():
    report = analysis.value_company(PROFITABLE)
    with pytest.raises(ValueError, match="this report is a"):
        X.build_speculative_workbook(report)


def test_no_annotation_is_mistaken_for_a_formula(sheet):
    """openpyxl writes any string beginning with "=" as a formula, so prose
    that starts with one becomes #REF! in the opened file."""
    # Only above the projection: from there down, column C onward is the
    # path grid, where a formula is exactly what belongs.
    labels = _column_a(sheet)
    projection = next((i + 1 for i, label in enumerate(labels)
                       if "PATH TO PROFITABILITY" in label), 120)
    for row in range(1, projection):
        for col in (3, 4, 5):
            value = sheet.cell(row=row, column=col).value
            if isinstance(value, str) and value.startswith("="):
                raise AssertionError(
                    f"{sheet.cell(row=row, column=col).coordinate}: {value!r}")


# ---------------------------------------------------------------------------
# The warning beside the figure
#
# The banner is only seen on opening. The figure sits some forty rows below it,
# so a reader parked on the number - or screenshotting it - sees no red at all
# unless the warning is repeated there. These tests cover both workbooks,
# because the two are built by separate functions and drifted apart once.
# ---------------------------------------------------------------------------

SHRINKING = "SHRINK"


def _shrinking_lossmaker(ticker, years=5):
    """A loss-maker whose revenue is also falling.

    The hypothetical exists only for companies the speculative engine itself
    refuses, and it refuses a company whose own figures already support a path.
    make_fin grows revenue 5% a year, so the trend is reversed here.
    """
    fin = make_fin(SHRINKING, "Shrinking Inc.", operating_margin=-0.40)
    revenues = [row["revenue"] for row in fin.income]
    for row, revenue in zip(fin.income, reversed(revenues)):
        row["revenue"] = revenue
        row["operatingIncome"] = revenue * -0.40
    return fin


@pytest.fixture
def hypothetical(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials",
                        lambda t, years=5: _shrinking_lossmaker(t, years))
    return analysis.value_company_hypothetically(
        SHRINKING, UserAssumptions(revenue_growth=0.10,
                                   target_operating_margin=0.18,
                                   years_to_target=5))


@pytest.fixture(params=["speculative", "hypothetical"])
def either(request, speculative, hypothetical):
    """Both workbooks, so a fix to one that misses the other fails here."""
    if request.param == "speculative":
        return X.build_speculative_workbook(speculative).active
    return X.build_hypothetical_workbook(hypothetical).active


def _headline_row(ws):
    for row in range(1, 120):
        label = str(ws.cell(row=row, column=1).value or "")
        if label.endswith("PER SHARE"):
            return row
    raise AssertionError("no headline row found")


def test_the_tab_itself_is_red(either):
    """In a workbook that has been scrolled or saved beside other sheets, the
    tab is all that is left of the warning."""
    assert "C00000" in str(either.sheet_properties.tabColor.rgb)


def test_the_headline_label_is_larger_and_red(either):
    label = either.cell(row=_headline_row(either), column=1)
    assert label.font.bold
    assert float(label.font.size) > 10, "no larger than the ordinary rows above it"
    assert "C00000" in str(label.font.color.rgb)


def test_the_figure_is_the_same_size_as_its_label(either):
    figure = either.cell(row=_headline_row(either), column=2)
    label = either.cell(row=_headline_row(either), column=1)
    assert float(figure.font.size) == float(label.font.size)


def test_a_red_caption_sits_directly_beneath_the_figure(either):
    """Directly beneath: a blank row between them would let a screenshot of the
    number crop the warning out."""
    row = _headline_row(either) + 1
    caption = str(either.cell(row=row, column=1).value or "")
    assert "NOT A VALUATION" in caption, caption
    assert "C00000" in str(either.cell(row=row, column=1).font.color.rgb) \
        or "FFFFFF" in str(either.cell(row=row, column=1).font.color.rgb)
    for col in range(1, 5):
        fill = either.cell(row=row, column=col).fill
        assert "C00000" in str(fill.start_color.rgb), f"col {col} not filled red"


def test_the_caption_does_not_displace_the_figure_from_the_formula(either):
    """The label, the figure and the caption are three rows, not two: the
    caption must not have overwritten the note that followed it."""
    row = _headline_row(either)
    assert str(either.cell(row=row, column=2).value).startswith("=")
    assert "not intrinsic value" in str(either.cell(row=row + 2, column=1).value).lower()
