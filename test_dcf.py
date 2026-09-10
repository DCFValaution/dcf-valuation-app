"""Tests that the DCF engine reproduces the AAPL_DCF_Model.xlsx figures exactly."""

import pytest
from dcf import BaseYearData, Assumptions, run_dcf

# --- Apple FY2025 actuals & market data (from spreadsheet) ---
AAPL_BASE = BaseYearData(
    revenue=416_161,
    total_debt=98_657,
    cash=132_420,
    shares=14_773.3,
    current_price=296.42,
)

AAPL_ASSUMPTIONS = Assumptions(
    revenue_growth=0.06,
    operating_margin=0.32,
    tax_rate=0.16,
    da_pct=0.028,
    capex_pct=0.031,
    nwc_pct=0.03,
    wacc=0.085,
    terminal_growth=0.03,
    projection_years=5,
)

# Tolerance: $0.01 per share is sufficient given the spreadsheet uses rounded intermediates
SHARE_PRICE_TOL = 0.01


@pytest.fixture(scope="module")
def result():
    return run_dcf(AAPL_BASE, AAPL_ASSUMPTIONS)


# ---------------------------------------------------------------------------
# Revenue projection
# ---------------------------------------------------------------------------

def test_revenue_year1(result):
    assert abs(result.years[0].revenue - 441_131) < 1

def test_revenue_year5(result):
    assert abs(result.years[4].revenue - 556_917) < 1


# ---------------------------------------------------------------------------
# UFCF line items (Year 1)
# ---------------------------------------------------------------------------

def test_ebit_year1(result):
    assert abs(result.years[0].ebit - 141_162) < 1

def test_nopat_year1(result):
    assert abs(result.years[0].nopat - 118_576) < 1

def test_ufcf_year1(result):
    assert abs(result.years[0].ufcf - 116_503) < 1

def test_ufcf_year5(result):
    assert abs(result.years[4].ufcf - 147_083) < 1


# ---------------------------------------------------------------------------
# Discounting
# ---------------------------------------------------------------------------

def test_discount_factor_year1(result):
    assert abs(result.years[0].discount_factor - 0.922) < 0.001

def test_pv_ufcf_year1(result):
    assert abs(result.years[0].pv_ufcf - 107_376) < 1

def test_pv_ufcf_sum(result):
    assert abs(result.pv_ufcf_sum - 512_705) < 1


# ---------------------------------------------------------------------------
# Terminal value
# ---------------------------------------------------------------------------

def test_terminal_value(result):
    assert abs(result.terminal_value - 2_754_462) < 1

def test_pv_terminal_value(result):
    assert abs(result.pv_terminal_value - 1_831_842) < 1


# ---------------------------------------------------------------------------
# Enterprise & equity value
# ---------------------------------------------------------------------------

def test_enterprise_value(result):
    assert abs(result.enterprise_value - 2_344_547) < 1

def test_equity_value(result):
    assert abs(result.equity_value - 2_378_310) < 1

def test_intrinsic_value_per_share(result):
    assert abs(result.intrinsic_value_per_share - 160.99) <= SHARE_PRICE_TOL

def test_upside_downside(result):
    # -45.7% implied downside
    assert abs(result.upside_downside - (-0.457)) < 0.001

def test_tv_pct_of_ev(result):
    assert abs(result.tv_pct_of_ev - 0.781) < 0.001


# ---------------------------------------------------------------------------
# Sensitivity table  (WACC rows x g columns)
# Spreadsheet values: rows 7.5%→9.5%, cols 2.0%→4.0%
# ---------------------------------------------------------------------------

EXPECTED_SENSITIVITY = [
    [166.57, 180.12, 196.69, 217.40, 244.02],  # WACC 7.5%
    [152.66, 163.75, 177.05, 193.31, 213.64],  # WACC 8.0%
    [140.89, 150.10, 160.99, 174.05, 190.01],  # WACC 8.5%
    [130.81, 138.56, 147.60, 158.29, 171.11],  # WACC 9.0%
    [122.08, 128.67, 136.28, 145.16, 155.65],  # WACC 9.5%
]

def test_sensitivity_shape(result):
    assert len(result.sensitivity) == 5
    assert all(len(row) == 5 for row in result.sensitivity)

def test_sensitivity_values(result):
    for i, row in enumerate(EXPECTED_SENSITIVITY):
        for j, expected in enumerate(row):
            actual = result.sensitivity[i][j]
            assert abs(actual - expected) <= SHARE_PRICE_TOL, (
                f"Sensitivity mismatch at WACC={result.sensitivity_waccs[i]:.1%}, "
                f"g={result.sensitivity_gs[j]:.1%}: "
                f"expected {expected}, got {actual:.2f}"
            )
