"""The effective tax rate: three sources, and when to stop trusting the first.

The deployed backend valued Amazon at $61.63 a share while the same code on
a laptop valued it at $36.09. Every other input matched to fifteen digits.
The difference was one silent zero: Yahoo serves a datacentre IP a thinner
income statement than it serves a laptop, `num` reads an absent field as
0.0, and three profitable years with no reported tax provision became three
years of paying no tax. Nothing in the output said so - the provenance read
"median effective rate over 3 profitable year(s)", which was true about the
years and false about the rate.

Probing for the shape of it found two more companies whose rate was wrong
on every machine, for a different reason. AMD's provision swings -2.5%,
+19.2%, -70.3% across three profitable years and medians to -2.5%, which
the old clamp lifted to exactly 0%. Qualcomm's swings -44.8%, +56.2%, +2.2%
and medians to 2.2%. Neither is a tax rate; both are averages of one-off
items, and both were being projected into perpetuity.

So the rate now comes from the reported provisions, or - where those are
missing, negative, or measuring accidents rather than a rate - from Yahoo's
own normalized rate, or failing both from the US statutory rate, said out
loud. The one thing it will not do is infer that a company pays nothing.

The hard part is the line between "lumpy" and "genuinely low", because
companies on the second side of it are real: Simon Property pays 0.85%,
Frontline 1.5%, Realty Income 7.1%. Their rates must survive untouched.

Run with:  python -m pytest test_tax_rate.py -v
"""

import pytest

import assumptions as A
from assumptions import (FALLBACK_TAX_RATE, TAX_IMPLAUSIBLY_HIGH,
                         TAX_IMPLAUSIBLY_LOW, TAX_NORMALIZED_DISAGREEMENT,
                         TAX_VOLATILE_SPREAD, derive_assumptions)
from market_data import CompanyFinancials

# "This year's statement carried no tax line at all", as distinct from a
# year that reported a tax of zero. The original bug lives in that
# difference.
OMITTED = object()


def make_fin(rates, *, normalized=None, pretax_margin=0.24) -> CompanyFinancials:
    """A profitable company whose tax lines are whatever the test says.

    `rates` is one entry per year, newest first: a float read as that
    year's effective rate, or OMITTED for a year whose statement carried no
    tax line. `normalized` is the same, for Yahoo's own published rate.
    """
    years = max(len(rates), 3)
    income, balance, cashflow = [], [], []
    for i in range(years):
        revenue = 10_000e6 * (1.05 ** (years - 1 - i))
        pretax = revenue * pretax_margin
        row = {
            "fiscalYear": str(2025 - i),
            "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": pretax,
            "interestExpense": 40e6,
            "weightedAverageShsOutDil": 1_000e6,
        }
        entry = rates[i] if i < len(rates) else 0.20
        if entry is not OMITTED:
            row["incomeTaxExpense"] = pretax * entry
        if normalized is not None:
            n = normalized[i] if i < len(normalized) else normalized[-1]
            if n is not OMITTED:
                row["effectiveTaxRate"] = n
        income.append(row)
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0,
            "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0,
            "longTermDebt": 1_000e6,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": 1.0, "marketCap": 1_000_000e6,
                 "price": 100.0, "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow,
    )


@pytest.fixture(autouse=True)
def _fixed_risk_free(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def tax(fin):
    d = derive_assumptions(fin)
    return d.assumptions.tax_rate, d.provenance["tax_rate"]


# --- Source 1: what the company reported -----------------------------

class TestTheReportedProvision:
    def test_a_reported_rate_is_the_rate(self):
        rate, p = tax(make_fin([0.20, 0.20, 0.20], normalized=[0.31] * 3))
        assert rate == pytest.approx(0.20), "the normalized row is a backstop"
        assert p.source == "derived"
        assert "median effective rate" in p.detail

    def test_it_is_the_median_of_the_window(self):
        rate, _ = tax(make_fin([0.10, 0.20, 0.30]))
        assert rate == pytest.approx(0.20)

    def test_a_loss_year_is_not_a_tax_rate(self):
        """A loss produces a benefit, not a rate, so it is excluded."""
        fin = make_fin([0.25, 0.25, 0.25])
        fin.income[1]["incomeBeforeTax"] = -5_000e6
        fin.income[1]["incomeTaxExpense"] = -1_200e6
        rate, p = tax(fin)
        assert rate == pytest.approx(0.25)
        assert "2 profitable year(s)" in p.detail


# --- The Amazon bug: a missing line is not a zero --------------------

class TestAMissingTaxLine:
    def test_an_omitted_year_is_skipped_not_counted_as_zero(self):
        rate, _ = tax(make_fin([OMITTED, 0.20, 0.20]))
        assert rate == pytest.approx(0.20), "the reported years decide it"

    def test_the_provenance_does_not_claim_the_years_it_skipped(self):
        _, p = tax(make_fin([OMITTED, OMITTED, 0.20]))
        assert "1 profitable year(s)" in p.detail
        assert "2 more reported no tax figure" in p.detail

    def test_a_statement_with_no_tax_line_falls_to_the_normalized_rate(self):
        rate, p = tax(make_fin([OMITTED] * 3, normalized=[0.19] * 3))
        assert rate == pytest.approx(0.19)
        assert "normalized effective rate" in p.detail
        assert "no tax figure reported in 3 profitable year(s)" in p.detail

    def test_with_no_tax_data_at_all_it_says_so_and_uses_statutory(self):
        rate, p = tax(make_fin([OMITTED] * 3))
        assert rate == pytest.approx(FALLBACK_TAX_RATE)
        assert p.source == "default"
        assert "tax data unavailable" in p.detail
        assert "statutory" in p.detail

    def test_it_never_reports_a_derived_zero(self):
        """The failure the deployed backend shipped: a 0% rate, presented
        as a derived finding, with nothing in the output to query."""
        for fin in (make_fin([OMITTED] * 3),
                    make_fin([OMITTED] * 3, normalized=[0.19] * 3)):
            rate, p = tax(fin)
            assert rate > 0.0
            assert not (p.source.startswith("derived") and p.value == 0.0)


# --- Lumpy provisions: AMD and Qualcomm ------------------------------

class TestProvisionsThatMeasureAccidents:
    def test_a_negative_median_is_not_a_rate_a_company_pays(self):
        """AMD: -2.5%, +19.2%, -70.3%. The old clamp lifted the median to
        exactly 0% and projected it forever."""
        rate, p = tax(make_fin([-0.0249, 0.1916, -0.7033],
                               normalized=[0.2557, 0.19, 0.2577]))
        assert rate == pytest.approx(0.2557)
        assert "one-off benefit" in p.detail

    def test_a_low_rate_on_a_wildly_swinging_series_is_not_a_rate(self):
        """Qualcomm: -44.8%, +56.2%, +2.2%, median 2.2%."""
        rate, p = tax(make_fin([-0.4476, 0.5624, 0.0219],
                               normalized=[0.40, 0.2557, 0.021865]))
        assert rate == pytest.approx(0.2557)
        assert "swing" in p.detail

    def test_both_conditions_are_needed_not_either(self):
        """A wide swing around a plausible median is still a rate.

        Disney's window runs -11.9%, +23.7%, +28.9% - a 41-point spread -
        and medians to 23.7%, which is simply what Disney pays. Rejecting
        on volatility alone would move it for no reason.
        """
        rate, p = tax(make_fin([-0.119, 0.237, 0.289], normalized=[0.2557] * 3))
        assert rate == pytest.approx(0.237)
        assert p.source == "derived"

    def test_a_high_volatile_median_is_not_rejected_for_being_volatile(self):
        """The low-side rule must not reach upwards.

        BP's shape - 83.3%, 81.9%, 33.1%, a 50-point spread - is handled
        by the high-side test below, and only because the published rate
        disagrees. Where the published rate agrees, a wide swing around a
        high median is simply a company that pays a lot in a bad year and
        a lot in a good one.
        """
        rate, p = tax(make_fin([0.833, 0.819, 0.331], normalized=[0.80] * 3))
        assert rate == pytest.approx(0.819)
        assert p.source == "derived"


# --- The line that must not move -------------------------------------

class TestGenuinelyLowRates:
    """Companies that really do pay almost nothing, and must keep it."""

    @pytest.mark.parametrize("name,rates,want", [
        ("Simon Property", [0.0066, 0.0085, 0.0303], 0.0085),
        ("Frontline", [0.0156, 0.0152, 0.0003], 0.0152),
        ("Realty Income", [0.0739, 0.0713, 0.0560], 0.0713),
        ("Prologis", [0.0541, 0.0406, 0.0609], 0.0541),
    ])
    def test_a_steady_low_rate_survives(self, name, rates, want):
        rate, p = tax(make_fin(rates, normalized=[0.25] * 3))
        assert rate == pytest.approx(want, abs=1e-6), name
        assert p.source == "derived", f"{name} was overridden"

    def test_the_two_thresholds_leave_room_between_them(self):
        """The widest steady low series seen was 2.4 points and the
        narrowest lumpy one 89.5. If these ever meet, the rule is guessing.
        """
        assert TAX_VOLATILE_SPREAD > 0.05, "wider than any steady series"
        assert TAX_IMPLAUSIBLY_LOW < 0.10, "below what a real payer pays"


# --- The clamp --------------------------------------------------------

class TestTheClamp:
    def test_a_rate_above_the_old_ceiling_passes_through(self):
        """Chevron 35.5%, Shell 39.8%. Clamping a tax rate DOWN inflates
        the valuation, which is the wrong direction for a safety rail."""
        rate, p = tax(make_fin([0.3982, 0.3982, 0.3982]))
        assert rate == pytest.approx(0.3982)
        assert p.source == "derived"

    def test_a_rate_above_everything_is_still_clamped(self):
        rate, p = tax(make_fin([1.5, 1.5, 1.5]))
        assert rate == pytest.approx(1.00)
        assert p.source == "derived (clamped)"


# --- What it was worth ------------------------------------------------

class TestTheValuation:
    def _value(self, fin):
        from dcf import run_dcf
        from market_data import base_year_from
        base, _ = base_year_from(fin)
        return run_dcf(base, derive_assumptions(fin).assumptions
                       ).intrinsic_value_per_share

    def test_a_missing_tax_line_no_longer_inflates_the_answer(self):
        """A company whose tax line did not arrive is valued as one that
        pays the published rate - not as one that pays nothing.

        Run on the real engine rather than on the rate alone, because the
        rate reaches the answer twice: through NOPAT and through the
        after-tax cost of debt in WACC.
        """
        complete = self._value(make_fin([0.19] * 3, normalized=[0.19] * 3))
        stripped = self._value(make_fin([OMITTED] * 3, normalized=[0.19] * 3))
        assert stripped == pytest.approx(complete, rel=1e-9)

    def test_paying_no_tax_would_have_been_worth_far_more(self):
        """The size of the error being prevented."""
        from dataclasses import replace
        from dcf import run_dcf
        from market_data import base_year_from

        fin = make_fin([OMITTED] * 3, normalized=[0.19] * 3)
        base, _ = base_year_from(fin)
        derived = derive_assumptions(fin).assumptions
        honest = run_dcf(base, derived).intrinsic_value_per_share
        untaxed = run_dcf(base, replace(derived, tax_rate=0.0)
                          ).intrinsic_value_per_share
        assert untaxed > honest * 1.15, (
            "if a zero rate were not worth much, none of this would matter")


# --- The normalized rate is a rate, not money -------------------------

def test_the_published_rate_is_not_currency_converted():
    """Both its terms are already in one currency, so the scalar cancels.

    Converting it would invent a tax rate of 0.19 x the exchange rate -
    the same class of bug as the three money fields that were once left
    behind, in the opposite direction.
    """
    from market_data import convert_statements
    income = [{"revenue": 1000.0, "incomeBeforeTax": 240.0,
               "incomeTaxExpense": 45.6, "effectiveTaxRate": 0.19,
               "fiscalYear": "2025"}]
    convert_statements(income, [{}], [{}], 7.3)
    assert income[0]["effectiveTaxRate"] == pytest.approx(0.19)
    assert income[0]["revenue"] == pytest.approx(7300.0), "money did convert"


# --- It has to come off the statement in the first place -------------

def test_the_normalized_rate_is_read_off_the_income_statement():
    """Everything above builds CompanyFinancials by hand, so none of it
    would notice if the field were never extracted from Yahoo's frame.

    The row label is Yahoo's, not ours, and a rename upstream would turn
    the whole second source off silently - leaving the statutory fallback
    to do work the published rate should have done.
    """
    import pandas as pd
    from market_data import _income_row

    periods = [pd.Timestamp("2025-12-31")]
    frame = pd.DataFrame(
        {"Total Revenue": [1000.0],
         "Pretax Income": [240.0],
         "Tax Provision": [45.6],
         "Tax Rate For Calcs": [0.19]},
        index=periods).T

    row = _income_row(frame, 0)
    assert row["effectiveTaxRate"] == pytest.approx(0.19)


# --- The same test, the other way up ---------------------------------

class TestProvisionsInflatedByOneOffs:
    """A median too high to be a rate, confirmed by the published one.

    The mirror of the low-side rule, and it needs its own second signal:
    a high rate is usually real - national petroleum regimes reach 78% -
    so volatility alone would reject companies that simply pay a lot.
    """

    def test_bp_falls_to_the_published_rate(self):
        """83.3%, 81.9%, 33.1%, median 81.9%, against a published 25%."""
        rate, p = tax(make_fin([0.833, 0.819, 0.331], normalized=[0.25] * 3))
        assert rate == pytest.approx(0.25)
        assert "81.9%" in p.detail
        assert "one-off levies" in p.detail
        assert "25.0%" in p.detail

    def test_a_high_rate_the_published_one_agrees_with_is_kept(self):
        """Norway's petroleum rate is 78% and that is simply the rate.

        Without this, the high-side branch would reject every genuinely
        heavily-taxed company instead of only the ones whose provisions
        are carrying something else.
        """
        rate, p = tax(make_fin([0.78, 0.78, 0.78], normalized=[0.77] * 3))
        assert rate == pytest.approx(0.78)
        assert p.source == "derived"

    def test_a_high_rate_with_no_published_opinion_is_kept(self):
        """Nothing to check it against is not evidence against it."""
        rate, p = tax(make_fin([0.819, 0.819, 0.819]))
        assert rate == pytest.approx(0.819)
        assert p.source == "derived"

    @pytest.mark.parametrize("name,rates,want", [
        ("Chevron", [0.368, 0.355, 0.276], 0.355),
        ("Shell", [0.391, 0.448, 0.398], 0.398),
    ])
    def test_rates_under_the_ceiling_stay_on_what_was_reported(
            self, name, rates, want):
        """Both are above the old 35% clamp and below the new test, so
        they must pass through untouched even though the published rate
        disagrees with Shell by more than the margin."""
        rate, p = tax(make_fin(rates, normalized=[0.25] * 3))
        assert rate == pytest.approx(want, abs=1e-6), name
        assert p.source == "derived", f"{name} was overridden"

    def test_the_two_sides_cannot_both_fire(self):
        assert TAX_IMPLAUSIBLY_LOW < TAX_IMPLAUSIBLY_HIGH

    def test_the_disagreement_margin_clears_ordinary_noise(self):
        """Exxon's reported and published rates differ by 4.7 points, 3M's
        by 3.6, Rio Tinto's by 1.9. BP's differ by 57."""
        assert TAX_NORMALIZED_DISAGREEMENT > 0.05
        assert TAX_NORMALIZED_DISAGREEMENT < 0.50

    def test_a_rate_between_the_old_clamp_and_the_ceiling_is_kept(self):
        """40% reported against 20% published: a wide disagreement, and
        still not rejected, because 40% is a rate a company can pay.

        This is what the ceiling is for. Set it much lower and the
        high-side rule starts overriding ordinary heavily-taxed companies
        on the strength of a published figure that simply normalizes
        differently.
        """
        rate, p = tax(make_fin([0.40, 0.40, 0.40], normalized=[0.20] * 3))
        assert rate == pytest.approx(0.40)
        assert p.source == "derived"
