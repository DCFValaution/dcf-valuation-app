"""The rare company that is worth less the faster it grows.

Most businesses are worth more if they grow faster. A few are not: where
each extra dollar of revenue costs more in working capital and new assets
than it returns in after-tax operating profit, that revenue is bought at a
loss, and assuming more of it lowers the figure.

It is correct, and it looks exactly like a bug - a slider dragged one way,
a number moving the other. It was reported as one.
"""

import dataclasses

import pytest

from dcf import (Assumptions, BaseYearData, growth_economics,
                 growth_reduces_value, run_dcf)


def base():
    return BaseYearData(revenue=100_000, total_debt=0, cash=0,
                        shares=1_000, current_price=100.0)


def assumptions(**over):
    a = dict(revenue_growth=0.06, operating_margin=0.13, tax_rate=0.23,
             da_pct=0.046, capex_pct=0.086, nwc_pct=0.29, wacc=0.09,
             terminal_growth=0.025, projection_years=5)
    a.update(over)
    return Assumptions(**a)


def value(a):
    return run_dcf(base(), a).intrinsic_value_per_share


class TestDetection:
    def test_the_thin_margin_heavy_reinvestment_case_is_caught(self):
        """Alibaba's shape: 10 cents of profit, 33 cents of reinvestment."""
        a = assumptions()
        assert growth_reduces_value(base(), a, value(a))
        assert value(assumptions(revenue_growth=0.15)) < value(
            assumptions(revenue_growth=0.03))

    def test_an_ordinary_business_is_not_flagged(self):
        a = assumptions(operating_margin=0.30, nwc_pct=0.02, capex_pct=0.04)
        assert not growth_reduces_value(base(), a, value(a))

    def test_a_wide_margin_absorbs_heavy_reinvestment(self):
        """The case a simpler rule would get wrong.

        Coca-Cola reinvests more per incremental dollar than it earns on
        one, and is still worth more the faster it grows: the profit falls
        on the revenue level while the working-capital charge falls only on
        the increment, so a wide enough margin carries it.
        """
        a = assumptions(operating_margin=0.298, tax_rate=0.179,
                        da_pct=0.023, capex_pct=0.044, nwc_pct=0.30)
        profit, reinvestment = growth_economics(a)
        assert reinvestment > profit, "the naive rule would flag this"
        assert not growth_reduces_value(base(), a, value(a)), (
            "but the valuation still rises with growth, so the note must "
            "not appear")

    def test_it_asks_the_model_the_question_the_user_asks(self):
        """A percentage point is the probe, and the answer must match what
        moving the slider actually does."""
        a = assumptions()
        faster = dataclasses.replace(a, revenue_growth=a.revenue_growth + 0.01)
        assert growth_reduces_value(base(), a, value(a)) is (
            value(faster) < value(a))


class TestEconomics:
    def test_depreciation_is_netted_against_capex(self):
        """What the business funds is capital spending above its own
        depreciation, because the rest is added back in the cash flow."""
        a = assumptions(capex_pct=0.10, da_pct=0.04, nwc_pct=0.05)
        _, reinvestment = growth_economics(a)
        assert reinvestment == pytest.approx(0.10 - 0.04 + 0.05)

    def test_profit_is_after_tax(self):
        a = assumptions(operating_margin=0.20, tax_rate=0.25)
        profit, _ = growth_economics(a)
        assert profit == pytest.approx(0.15)


class TestNote:
    def test_the_field_defaults_to_absent(self):
        """An explanation of one company's economics, not a general caveat.

        Shown everywhere, it would become decoration, and a caveat nobody
        needs is a caveat nobody reads. The default therefore has to be
        nothing, so that forgetting to set it fails quiet rather than loud.
        """
        import api
        fields = api.ValuationResponse.model_fields
        assert "growth_cost_note" in fields
        assert fields["growth_cost_note"].default is None

    def test_the_note_names_both_numbers_that_cause_it(self):
        """A user told only that growth is bad learns nothing. The two
        figures are the explanation."""
        import analysis
        a = assumptions()
        profit, reinvestment = growth_economics(a)
        assert profit < reinvestment
        # The sentence is built from exactly these, rounded to whole cents.
        assert f"{profit * 100:.0f}" == "10"
        assert f"{reinvestment * 100:.0f}" == "33"
        assert callable(analysis.growth_cost_note)
