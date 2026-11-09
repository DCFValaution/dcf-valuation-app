"""The capex fade: where the projection starts, and where it settles.

A three-year median applied flat to every projected year understates a
company mid-buildout by construction - the median of a rising series is its
middle year, a level already left behind. That understatement was worth 71%
of Alphabet's value and 114% of Meta's. Holding the current rate forever is
the opposite error, and worth more: Alphabet at $4.47 a share.

So neither flat answer is a defensible default, which is why this fade is on
for every company rather than opt-in like the growth fade. It is a near no-op
where the start and the anchor agree, which is most companies, and it only
bites where a company is genuinely ramping.

Run with:  python -m pytest test_capex_fade.py -v
"""

from dataclasses import replace

import pytest

from dcf import Assumptions, BaseYearData, capex_path, run_dcf

BASE = BaseYearData(revenue=100_000, total_debt=5_000, cash=10_000,
                    shares=1_000, current_price=100.0)


def assume(capex_pct=0.30, capex_terminal_pct=None, years=5, **kw):
    return Assumptions(
        revenue_growth=0.08, operating_margin=0.30, tax_rate=0.18,
        da_pct=0.05, capex_pct=capex_pct,
        capex_terminal_pct=capex_terminal_pct,
        nwc_pct=0.02, wacc=0.10, terminal_growth=0.025,
        projection_years=years, **kw)


class TestThePath:
    def test_without_an_anchor_it_is_the_starting_rate_repeated(self):
        """Exactly what the model did before the fade existed."""
        assert capex_path(assume()) == [0.30] * 5

    def test_year_one_holds_at_the_current_rate(self):
        path = capex_path(assume(0.34, 0.16))
        assert path[0] == pytest.approx(0.34)

    def test_the_final_year_is_the_anchor_exactly(self):
        """Pinned, not merely arrived at. The perpetuity is built from this
        year's cash flow, and a float's width of daylight is a seam."""
        assert capex_path(assume(0.3392, 0.1607))[-1] == 0.1607

    def test_the_anchor_is_hit_bit_for_bit_where_the_arithmetic_drifts(self):
        """The pin is not decoration.

        Linear interpolation at full progress is the anchor in algebra but
        not always in floating point: this pair lands on
        0.08785100000000001. The perpetuity is built from the final year's
        cash flow, so a last bit of daylight between the final rate and the
        normalized one is a seam in the model.
        """
        path = capex_path(assume(0.78147, 0.087851, years=3))
        assert path[-1] == 0.087851
        # And the drift it is guarding against is real.
        start, terminal, steps = 0.78147, 0.087851, 2
        assert start + (terminal - start) * (steps / steps) != terminal

    def test_the_steps_between_are_equal(self):
        path = capex_path(assume(0.40, 0.10))
        steps = [a - b for a, b in zip(path, path[1:])]
        assert all(s == pytest.approx(steps[0]) for s in steps)

    def test_it_reproduces_the_approved_alphabet_glide(self):
        path = capex_path(assume(0.3392, 0.1607))
        assert path == pytest.approx([0.3392, 0.2946, 0.2499, 0.2053, 0.1607],
                                     abs=5e-5)

    def test_it_fades_upward_as_readily_as_down(self):
        """Intel spends less now than its recent average. Same machinery."""
        path = capex_path(assume(0.2288, 0.4010))
        assert path[0] == pytest.approx(0.2288)
        assert path[-1] == 0.4010
        assert path[1] > path[0]

    def test_a_one_year_projection_has_nowhere_to_glide(self):
        assert capex_path(assume(0.30, 0.10, years=1)) == [0.30]

    def test_it_does_not_borrow_the_growth_fades_shape(self):
        """The two fades answer different questions.

        Moving the growth glide out to year four must not quietly hold
        capital spending up for three more years as well - a change the user
        did not ask for and would not see.
        """
        a = assume(0.40, 0.10, fade_enabled=True, fade_start_year=4)
        assert capex_path(a)[1] < 0.40


class TestTheProjection:
    def test_each_year_records_the_intensity_it_applied(self):
        result = run_dcf(BASE, assume(0.30, 0.10))
        assert result.years[0].capex_pct == pytest.approx(0.30)
        assert result.years[-1].capex_pct == 0.10

    def test_the_cash_line_agrees_with_the_rate_beside_it(self):
        result = run_dcf(BASE, assume(0.30, 0.10))
        for y in result.years:
            assert y.capex == pytest.approx(y.revenue * y.capex_pct)

    def test_spending_less_later_is_worth_more(self):
        flat = run_dcf(BASE, assume(0.30)).intrinsic_value_per_share
        faded = run_dcf(BASE, assume(0.30, 0.10)).intrinsic_value_per_share
        assert faded > flat

    def test_the_sensitivity_centre_still_reproduces_the_headline(self):
        """The table has its own inline copy of the bridge. If the fade were
        applied in one build and not the other, the centre cell would
        disagree with the figure printed above it."""
        result = run_dcf(BASE, assume(0.30, 0.10))
        centre = len(result.sensitivity) // 2
        assert result.sensitivity[centre][centre] == pytest.approx(
            result.intrinsic_value_per_share, rel=1e-9)

    def test_a_flat_anchor_is_the_old_model_exactly(self):
        """A company whose current spending already equals its anchor must
        get the number it got before any of this existed."""
        old = run_dcf(BASE, assume(0.22))
        new = run_dcf(BASE, assume(0.22, 0.22))
        assert (new.intrinsic_value_per_share
                == pytest.approx(old.intrinsic_value_per_share))


class TestEngagement:
    """It should bite for ramps and do nothing elsewhere."""

    def _value(self, start, anchor=None):
        return run_dcf(BASE, assume(start, anchor)).intrinsic_value_per_share

    def test_a_near_flat_series_barely_moves_the_value(self):
        """Coca-Cola: 3.81% now against a 4.28% anchor.

        The fade always travels most of the way to its anchor - only one year
        is held, and four glide. What makes it a no-op here is not the
        fraction travelled but the distance: where the two ends are half a
        point apart there is nothing to travel.
        """
        flat = self._value(0.0381)
        faded = self._value(0.0381, 0.0428)
        assert abs(faded / flat - 1) < 0.02

    def test_a_genuine_ramp_moves_a_lot(self):
        """Alphabet: 33.92% now against a 16.07% anchor.

        Measured against the spread between the two flat extremes, because a
        heavy flat capex drives this synthetic company's value negative and a
        percentage off a negative baseline means nothing.
        """
        flat, anchored = self._value(0.3392), self._value(0.1607)
        faded = self._value(0.3392, 0.1607)
        assert abs(faded - flat) > 0.40 * abs(anchored - flat)

    def test_the_faded_answer_sits_between_the_two_flat_extremes(self):
        """The whole argument for a path rather than a point: both flat
        answers are wrong, in opposite directions."""
        harsh = run_dcf(BASE, assume(0.3392)).intrinsic_value_per_share
        generous = run_dcf(BASE, assume(0.1607)).intrinsic_value_per_share
        faded = self._value(0.3392, 0.1607)
        assert harsh < faded < generous
