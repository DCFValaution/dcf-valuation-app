"""
Tests for deriving DDM inputs from a dividend record (ddm_assumptions.py).

The dividend events are synthetic but shaped like Yahoo's chart events, and
the cases mirror what probing real data turned up: JPM raising a quarterly
dividend mid-year, Costco's $15 special among ~$1 payments, an ex-date that
puts five payments in one calendar year, and companies that pay nothing.

Run with:  python -m pytest test_ddm_assumptions.py -v
"""

from datetime import date, datetime, timezone

import pytest

import assumptions as A
import ddm_assumptions as D
from market_data import CompanyFinancials

TODAY = date(2026, 9, 11)

# Quarterly dividend per share, by year: steady growth of ~10% a year.
STEADY = {2020: 0.40, 2021: 0.45, 2022: 0.50, 2023: 0.55,
          2024: 0.60, 2025: 0.65, 2026: 0.70}


def ts(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, 14, 30, tzinfo=timezone.utc).timestamp())


def quarterly(per_quarter: dict[int, float], through: date = TODAY) -> list[tuple[int, float]]:
    """Payments on the 1st of Mar/Jun/Sep/Dec for each year, up to *through*."""
    return [(ts(year, month, 1), amount)
            for year, amount in per_quarter.items()
            for month in (3, 6, 9, 12)
            if date(year, month, 1) <= through]


def bank(*, net_income=1_000e6, equity=10_000e6, dividends_paid=-300e6,
         buybacks=-100e6, beta=1.0, years=4) -> CompanyFinancials:
    """ROE 10% and a 30% payout by default: 7% sustainable growth."""
    return CompanyFinancials(
        ticker="BANK",
        profile={"companyName": "Test Bank Corp.", "sector": "Financial Services",
                 "beta": beta, "price": 50.0},
        income=[{"fiscalYear": str(2025 - i), "netIncomeCommon": net_income}
                for i in range(years)],
        balance=[{"fiscalYear": str(2025 - i), "commonStockEquity": equity}
                 for i in range(years)],
        cashflow=[{"fiscalYear": str(2025 - i), "dividendsPaid": dividends_paid,
                   "stockRepurchased": buybacks} for i in range(years)],
    )


def derive(fin=None, events=None, overrides=None):
    record = D.analyse_dividends(quarterly(STEADY) if events is None else events,
                                 today=TODAY)
    return D.derive_ddm_assumptions(fin or bank(), record, overrides=overrides)


@pytest.fixture(autouse=True)
def _pinned_risk_free_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


# ---------------------------------------------------------------------------
# Reading the dividend record
# ---------------------------------------------------------------------------

def test_indicated_rate_is_the_last_regular_payment_times_frequency():
    """JPM's shape: $1.40 twice, then raised to $1.50. Yahoo's rate is $6.00."""
    events = [(ts(2025, 10, 6), 1.40), (ts(2026, 1, 6), 1.40),
              (ts(2026, 4, 6), 1.50), (ts(2026, 7, 6), 1.50)]
    record = D.analyse_dividends(events, today=TODAY)
    assert record.payments_per_year == 4
    assert record.current_dividend == pytest.approx(6.00)
    assert record.last_regular_date == date(2026, 7, 6)
    # The trailing sum is reported but not used: it lags the raise.
    assert record.trailing_twelve_months == pytest.approx(5.80)


@pytest.mark.parametrize("gap_days, expected", [(30, 12), (91, 4), (182, 2), (365, 1)])
def test_frequency_is_inferred_from_the_gap_between_payments(gap_days, expected):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    events = [(int(start + i * gap_days * 86_400), 1.0) for i in range(8)]
    assert D.analyse_dividends(events, today=TODAY).payments_per_year == expected


def test_a_special_dividend_is_excluded_from_the_run_rate():
    """Costco's $15.00 special among ~$1 regular payments would otherwise
    annualise to a $60 dividend."""
    events = [(ts(2023, 5, 1), 1.02), (ts(2023, 8, 1), 1.02), (ts(2023, 11, 1), 1.02),
              (ts(2023, 12, 27), 15.00),
              (ts(2024, 2, 1), 1.02), (ts(2024, 5, 1), 1.16), (ts(2024, 8, 1), 1.16),
              (ts(2024, 11, 1), 1.16), (ts(2025, 2, 1), 1.16), (ts(2025, 5, 1), 1.30),
              (ts(2025, 8, 1), 1.30), (ts(2025, 11, 1), 1.30), (ts(2026, 2, 1), 1.30),
              (ts(2026, 5, 1), 1.47), (ts(2026, 8, 1), 1.47)]
    record = D.analyse_dividends(events, today=TODAY)
    assert record.specials == [(date(2023, 12, 27), 15.00)]
    assert all(amount < 2 for _, amount in record.regular)
    assert record.payments_per_year == 4
    assert record.current_dividend == pytest.approx(1.47 * 4)


def test_a_genuine_raise_is_not_mistaken_for_a_special():
    record = D.analyse_dividends(quarterly({2024: 1.00, 2025: 1.30, 2026: 1.30}), today=TODAY)
    assert record.specials == []


def test_a_long_overdue_dividend_is_treated_as_suspended():
    events = quarterly({2023: 0.50, 2024: 0.50, 2025: 0.50}, through=date(2025, 3, 1))
    record = D.analyse_dividends(events, today=TODAY)
    assert record.suspended is True
    assert record.days_since_last > 182


def test_a_recent_quarterly_payment_is_not_suspended():
    assert D.analyse_dividends(quarterly(STEADY), today=TODAY).suspended is False


def test_annual_history_uses_complete_calendar_years_only():
    record = D.analyse_dividends(quarterly(STEADY), today=TODAY)
    assert [year for year, _ in record.annual_history] == [2020, 2021, 2022, 2023, 2024, 2025]
    assert dict(record.annual_history)[2025] == pytest.approx(0.65 * 4)


def test_a_partial_first_year_is_left_out_not_read_as_a_cut():
    events = [e for e in quarterly(STEADY) if e[0] >= ts(2020, 9, 1)]   # 2020: two payments
    record = D.analyse_dividends(events, today=TODAY)
    assert record.annual_history[0][0] == 2021


def test_an_extra_ex_date_in_a_year_does_not_inflate_that_year():
    """An instalment falling a few days early puts five payments in one year."""
    events = quarterly({2023: 0.50, 2024: 0.50, 2025: 0.50, 2026: 0.50})
    events.append((ts(2024, 12, 30), 0.50))
    record = D.analyse_dividends(events, today=TODAY)
    assert dict(record.annual_history)[2024] == pytest.approx(2.00)


def progressive(through: date = TODAY) -> list[tuple[int, float]]:
    """
    Progressive's shape: $0.10 a quarter, with a January event that Yahoo
    reports as the regular dividend and a variable one combined. As in the
    real record there is no variable dividend in 2023, which breaks a naive
    "every calendar year" rule.
    """
    variable = {2022: (date(2022, 1, 6), 1.40), 2024: (date(2024, 1, 18), 0.75),
                2025: (date(2025, 1, 10), 4.50), 2026: (date(2026, 1, 2), 13.50)}
    events = []
    for year in range(2021, 2027):
        if year in variable:
            paid, amount = variable[year]
            events.append((ts(paid.year, paid.month, paid.day), 0.10 + amount))
        else:
            events.append((ts(year, 1, 5), 0.10))
        events += [(ts(year, month, 2), 0.10) for month in (4, 7, 10)
                   if date(year, month, 2) <= through]
    return events


def test_a_recurring_variable_dividend_is_counted_not_excluded():
    """The PGR bug: $0.40 a year was being discounted instead of the real dividend."""
    record = D.analyse_dividends(progressive(), today=TODAY)
    assert record.specials == [], "a recurring payment is not a one-off"
    assert [net for _, _, net in record.variable_dividends] == pytest.approx(
        [1.40, 0.75, 4.50, 13.50])
    assert record.variable_recurring_years == 3          # 2024, 2025, 2026
    assert record.regular_dividend == pytest.approx(0.40)
    assert record.variable_average_years == 5
    # A five-year average, with 2023 - no variable dividend - counting as zero.
    assert record.variable_annual_dividend == pytest.approx((1.40 + 0.75 + 4.50 + 13.50) / 5)
    assert record.current_dividend == pytest.approx(0.40 + 4.03)
    assert record.trailing_twelve_months == pytest.approx(0.30 + 13.60)


def test_a_variable_dividend_reported_with_the_regular_one_is_not_double_counted():
    """Yahoo's $13.60 event is the $0.10 regular dividend plus $13.50 variable."""
    paid, gross, net = D.analyse_dividends(progressive(), today=TODAY).variable_dividends[-1]
    assert paid == date(2026, 1, 2)
    assert gross == pytest.approx(13.60)
    assert net == pytest.approx(13.50)


def test_a_variable_dividend_paid_on_its_own_date_is_counted_whole():
    events = quarterly({2022: 0.25, 2023: 0.25, 2024: 0.25, 2025: 0.25, 2026: 0.25})
    events += [(ts(year, 12, 20), 2.00) for year in (2023, 2024, 2025)]
    record = D.analyse_dividends(events, today=TODAY)
    assert [net for _, _, net in record.variable_dividends] == pytest.approx([2.00, 2.00, 2.00])
    assert record.variable_annual_dividend == pytest.approx(2.00)   # three years paying


def test_a_genuine_one_off_special_is_still_excluded():
    """One special among regular payments - Costco's shape - is not a pattern."""
    events = quarterly(STEADY) + [(ts(2025, 12, 20), 5.00)]
    record = D.analyse_dividends(events, today=TODAY)
    assert record.specials == [(date(2025, 12, 20), 5.00)]
    assert record.variable_dividends == []
    assert record.current_dividend == pytest.approx(0.70 * 4)


def test_specials_years_apart_are_not_an_annual_pattern():
    """Costco's specials came 2017, 2020, 2023: three of them, but not yearly."""
    events = quarterly({year: 1.00 for year in range(2017, 2027)})
    events += [(ts(2019, 5, 8), 7.00), (ts(2022, 12, 1), 10.00), (ts(2025, 12, 27), 15.00)]
    record = D.analyse_dividends(events, today=TODAY)
    assert record.variable_dividends == []
    assert [amount for _, amount in record.specials] == [7.00, 10.00, 15.00]
    assert record.current_dividend == pytest.approx(4.00)


def test_a_lapsed_variable_dividend_is_not_treated_as_recurring():
    """Annual variable dividends that stopped years ago are history, not policy."""
    events = quarterly({year: 0.25 for year in range(2019, 2027)})
    events += [(ts(year, 12, 20), 2.00) for year in (2019, 2020, 2021)]
    record = D.analyse_dividends(events, today=TODAY)
    assert record.variable_dividends == []
    assert len(record.specials) == 3
    assert record.current_dividend == pytest.approx(1.00)


def test_growth_for_a_variable_payer_is_measured_on_the_regular_dividend():
    derived = derive(events=progressive())
    prov = derived.provenance["dividend_growth"]
    assert prov.value == pytest.approx(0.0)
    assert "regular dividend only" in prov.detail
    assert "variable dividend averaging $4.03" in derived.diagnostics["current_dividend_detail"]


def test_no_dividends_gives_an_empty_record():
    record = D.analyse_dividends([], today=TODAY)
    assert record.payments == [] and record.current_dividend is None
    assert record.payments_per_year is None and record.annual_history == []


# ---------------------------------------------------------------------------
# Dividend growth
# ---------------------------------------------------------------------------

def test_growth_is_the_complete_year_dividend_per_share_cagr():
    # ROE 30% x retention 70% = 21% sustainable, so the cap does not bind.
    derived = derive(fin=bank(net_income=3_000e6, dividends_paid=-900e6))
    prov = derived.provenance["dividend_growth"]
    assert prov.value == pytest.approx((2.60 / 1.60) ** (1 / 5) - 1)
    assert prov.source == "derived"
    assert "2020-2025" in prov.detail


def test_growth_is_capped_at_what_earnings_can_fund():
    """ROE 10% x retention 70% = 7%, below the ~10.2% historical growth."""
    derived = derive(fin=bank())
    prov = derived.provenance["dividend_growth"]
    assert prov.value == pytest.approx(0.07)
    assert prov.source == "derived (clamped)"
    assert "sustainable growth" in prov.detail
    assert derived.diagnostics["sustainable_growth"] == pytest.approx(0.07)


def test_implausible_growth_is_clamped():
    events = quarterly({2021: 0.10, 2022: 0.20, 2023: 0.40, 2024: 0.80, 2025: 1.60, 2026: 1.60})
    derived = derive(fin=bank(net_income=5_000e6, dividends_paid=-500e6), events=events)
    prov = derived.provenance["dividend_growth"]
    assert prov.value == pytest.approx(D.CLAMP_DIVIDEND_GROWTH[1])
    assert prov.source == "derived (clamped)"


def test_a_shrinking_dividend_is_not_projected_to_keep_shrinking():
    events = quarterly({2021: 1.00, 2022: 0.90, 2023: 0.80, 2024: 0.70, 2025: 0.60, 2026: 0.60})
    prov = derive(events=events).provenance["dividend_growth"]
    assert prov.value == pytest.approx(0.0)
    assert prov.source == "derived (clamped)"


def test_too_little_history_defaults_growth_to_terminal_growth():
    prov = derive(events=quarterly({2025: 0.50, 2026: 0.50})).provenance["dividend_growth"]
    assert prov.source == "default"
    assert prov.value == pytest.approx(A.DEFAULT_TERMINAL_GROWTH)


# ---------------------------------------------------------------------------
# Cost of equity
# ---------------------------------------------------------------------------

def test_cost_of_equity_is_capm_on_exactly_the_dcfs_inputs():
    fin = bank(beta=1.2)
    derived = derive(fin=fin)
    assert derived.assumptions.cost_of_equity == pytest.approx(
        0.045 + 1.2 * A.DEFAULT_EQUITY_RISK_PREMIUM)

    # The same helper the DCF's WACC build-up calls, on the same company.
    def recorder(name, value, source, detail):
        return value

    assert A._capm_inputs(fin, recorder) == (
        derived.capm_inputs["risk_free_rate"].value,
        derived.capm_inputs["equity_risk_premium"].value,
        derived.capm_inputs["beta"].value,
    )


def test_an_implausible_capm_result_falls_back_to_market_beta():
    derived = derive(fin=bank(beta=3.9))      # 4.5% + 3.9 x 5.5% = 25.95%
    prov = derived.provenance["cost_of_equity"]
    assert prov.source == "default"
    assert prov.value == pytest.approx(0.045 + A.DEFAULT_EQUITY_RISK_PREMIUM)


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def test_overrides_win_and_are_labelled():
    derived = derive(overrides={"dividend_growth": 0.03, "high_growth_years": 10})
    assert derived.assumptions.dividend_growth == 0.03
    assert derived.assumptions.high_growth_years == 10
    assert derived.provenance["dividend_growth"].source == "override"
    assert derived.provenance["high_growth_years"].source == "override"


def test_overriding_a_capm_input_moves_the_cost_of_equity():
    base = derive().assumptions.cost_of_equity
    moved = derive(overrides={"equity_risk_premium": 0.045}).assumptions.cost_of_equity
    assert moved == pytest.approx(base - 1.0 * 0.01)


def test_overriding_cost_of_equity_directly_wins_over_capm():
    derived = derive(overrides={"cost_of_equity": 0.12, "beta": 2.0})
    assert derived.assumptions.cost_of_equity == 0.12


def test_a_dcf_only_override_names_the_method_it_belongs_to():
    with pytest.raises(ValueError, match="applies to the DCF"):
        derive(overrides={"revenue_growth": 0.05})


def test_an_unknown_override_is_rejected():
    with pytest.raises(ValueError, match="Unknown assumption override"):
        derive(overrides={"nonsense": 1})


@pytest.mark.parametrize("years", [-1, 31, 2.5])
def test_high_growth_years_must_be_a_sensible_whole_number(years):
    with pytest.raises(ValueError, match="high_growth_years"):
        derive(overrides={"high_growth_years": years})
