"""Which year's operating margin the forecast is built on.

A three-year median shipped before this, and it dragged improving companies
back to their past: Palantir's margin went -8.5% -> 5.4% -> 10.8% -> 31.6%
and the median handed the forecast 8.1%, which is most of why it valued a
$143 share at $7.

The replacement is three steps over a four-year window, and it is symmetric -
it treats a company that improved and one that declined the same way:

  1. monotone across the window  -> the LATEST year
  2. latest inside the earlier years' range -> the LATEST year
  3. otherwise -> the MEDIAN of the window

A mean was considered and is the worst of the candidates, not the safest.
3M's four-year mean is 0.6% against a current 19.2%, because a PFAS and
earplug charge put -43.6% two years back, and 3M on the mean is worth MINUS
$25.62 a share. The tests below pin that the mean is not what happens.

The companies named are real and their figures were measured, not invented:
a 46-name scan found which ones actually have a one-off in the window, which
is how the four-year width was chosen - 3M's, Merck's and Pfizer's all sit
two years back, so a three-year window would have missed all three.
"""
import pytest

import assumptions as A
from assumptions import (MARGIN_TREND_DISCLOSE, MARGIN_WINDOW,
                         derive_assumptions, margin_from_history,
                         margin_trend_span)
from market_data import CompanyFinancials, base_year_from


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(margins, *, revenue=1_000e6):
    """A company whose operating margin was *margins*, newest first.

    Revenue is held flat so the margin is the only thing moving: a test about
    which margin is chosen should not also be a test about growth.
    """
    income, balance, cashflow = [], [], []
    for i, margin in enumerate(margins):
        year = 2025 - i
        income.append({
            "fiscalYear": str(year), "date": f"{year}-12-31",
            "revenue": revenue,
            "operatingIncome": revenue * margin,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "interestExpense": 40e6, "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(year),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
        })
        cashflow.append({
            "fiscalYear": str(year),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "beta": 1.0,
                 "marketCap": 1_000_000e6, "price": 100.0,
                 "sector": "Technology", "exchange": "NASDAQ"},
        income=income, balance=balance, cashflow=cashflow)


def derived(margins):
    return derive_assumptions(make_fin(margins))


def chosen(margins):
    """(margin, rule) as the full derivation records them."""
    d = derived(margins)
    return (d.assumptions.operating_margin,
            d.diagnostics.get("operating_margin_rule"))


# --- The three steps -------------------------------------------------------

class TestTheRule:
    def test_a_margin_that_improved_every_year_takes_the_latest(self):
        """Palantir's shape. Four years one way is a level reached, not a
        spike, and the median would hand the forecast the worst of them."""
        margin, rule = chosen([0.316, 0.108, 0.054, -0.085])
        assert rule == "latest"
        assert margin == pytest.approx(0.316)

    def test_a_margin_that_declined_every_year_takes_the_latest_too(self):
        """The same rule in the other direction, which is the point of
        calling it symmetric: a deteriorating company is not given back a
        margin it has stopped earning."""
        margin, rule = chosen([0.06, 0.11, 0.16, 0.21])
        assert rule == "latest"
        assert margin == pytest.approx(0.06)

    def test_an_ordinary_year_inside_the_earlier_range_takes_the_latest(self):
        """3M's shape. The latest 19.2% sits inside the range of the years
        around it, so nothing unusual happened in it - even though the window
        contains a year that is wildly unusual."""
        margin, rule = chosen([0.192, 0.207, -0.436, 0.181])
        assert rule == "latest"
        assert margin == pytest.approx(0.192)

    def test_a_year_outside_that_range_takes_the_median(self):
        """The charge lands in the LATEST year instead. Now the rule has no
        reason to believe it, and the median is the figure one extreme year
        cannot move."""
        margin, rule = chosen([-0.436, 0.192, 0.207, 0.181])
        assert rule == "median"
        assert margin == pytest.approx((0.192 + 0.181) / 2)

    def test_a_spike_upwards_is_treated_the_same_as_a_spike_down(self):
        margin, rule = chosen([0.62, 0.19, 0.21, 0.18])
        assert rule == "median"
        assert margin == pytest.approx((0.19 + 0.21) / 2)


# --- What it is not --------------------------------------------------------

class TestWhatItIsNot:
    def test_it_is_never_the_mean_of_the_window(self):
        """3M's real four-year figures. The mean is 0.6%, and a forecast
        built on it values the company below zero."""
        series = [0.192, 0.207, -0.436, 0.181]
        margin, _rule = chosen(series)
        assert margin == pytest.approx(0.192)
        assert margin != pytest.approx(sum(series) / len(series), abs=1e-4)

    def test_it_is_not_the_old_three_year_median(self):
        """Palantir again. The median of its last three years is 10.8%, and
        the rule that shipped before this one took it."""
        margin, _rule = chosen([0.316, 0.108, 0.054, -0.085])
        assert margin != pytest.approx(0.108)

    def test_the_window_is_four_years_not_three(self):
        """Not a tautology: it is asserted through behaviour. The one-off is
        in the third year, so a three-year window would see 19.2% as the
        high end of 19.2/20.7/-43.6 - still inside - but a window of two
        prior years would read differently, and 3M, Merck and Pfizer all
        keep their charge exactly this far back."""
        assert MARGIN_WINDOW == 4
        wide = chosen([0.192, 0.207, -0.436, 0.181])
        narrow = chosen([0.192, 0.207, -0.436])
        assert wide[1] == narrow[1] == "latest"
        # And the fourth year is read: dropping it changes the median the
        # rule WOULD fall back to, which a later year moving can expose.
        assert margin_from_history([-0.436, 0.192, 0.207, 0.181])[0] != \
            pytest.approx(margin_from_history([-0.436, 0.192, 0.207])[0])

    def test_a_fifth_year_is_outside_the_window_and_changes_nothing(self):
        four = chosen([0.192, 0.207, -0.436, 0.181])
        five = chosen([0.192, 0.207, -0.436, 0.181, -0.90])
        assert five == four


# --- Too little history ----------------------------------------------------

class TestTooLittleHistory:
    def test_two_years_cannot_tell_a_pattern_from_its_exception(self):
        margin, rule, why = margin_from_history([0.30, 0.10])
        assert rule == "latest"
        assert margin == pytest.approx(0.30)
        assert "only 2 years of history" in why

    def test_one_year_says_year_not_years(self):
        _m, _r, why = margin_from_history([0.30])
        assert "only 1 year of history" in why

    def test_three_years_is_enough_to_apply_the_rule(self):
        _m, rule, why = margin_from_history([0.62, 0.19, 0.21])
        assert rule == "median"
        assert "only" not in why


# --- The provenance says which step ran ------------------------------------

class TestWhatTheReaderIsTold:
    def test_a_monotone_window_says_it_moved_one_way(self):
        prov = derived([0.316, 0.108, 0.054, -0.085]).provenance[
            "operating_margin"]
        assert "moved one way for 4 years" in prov.detail
        assert "improving" in prov.detail

    def test_a_declining_window_says_declining(self):
        prov = derived([0.06, 0.11, 0.16, 0.21]).provenance["operating_margin"]
        assert "declining" in prov.detail

    def test_an_ordinary_year_names_the_range_it_sits_inside(self):
        prov = derived([0.192, 0.207, -0.436, 0.181]).provenance[
            "operating_margin"]
        assert "inside the previous years' own range" in prov.detail
        assert "-43.6%" in prov.detail and "20.7%" in prov.detail

    def test_the_median_says_which_pattern_was_broken(self):
        prov = derived([-0.436, 0.192, 0.207, 0.181]).provenance[
            "operating_margin"]
        assert "broke a pattern" in prov.detail
        assert "-43.6%" in prov.detail

    def test_a_year_with_no_operating_income_is_counted_out_loud(self):
        fin = make_fin([0.192, 0.207, 0.181, 0.20])
        del fin.income[2]["operatingIncome"]
        prov = derive_assumptions(fin).provenance["operating_margin"]
        assert "1 more reported no figure" in prov.detail

    def test_the_window_is_recorded_so_the_choice_can_be_checked(self):
        d = derived([0.192, 0.207, -0.436, 0.181])
        assert d.diagnostics["operating_margin_window"] == pytest.approx(
            [0.192, 0.207, -0.436, 0.181])


# --- The trend disclosure --------------------------------------------------

class TestTheTrendWarning:
    def test_a_steep_improvement_reports_its_span(self):
        span = margin_trend_span([0.316, 0.108, 0.054, -0.085])
        assert span == pytest.approx(0.401)
        assert span > MARGIN_TREND_DISCLOSE

    def test_a_steep_decline_reports_a_negative_span(self):
        assert margin_trend_span([0.06, 0.11, 0.16, 0.21]) == \
            pytest.approx(-0.15)

    def test_a_window_that_went_up_and_down_has_no_trend_to_warn_about(self):
        """There is no "it has moved steadily from" sentence to write for a
        company that did not move steadily."""
        assert margin_trend_span([0.192, 0.207, -0.436, 0.181]) is None

    def test_a_gentle_trend_is_a_trend_but_not_a_disclosure(self):
        span = margin_trend_span([0.21, 0.20, 0.19, 0.18])
        assert span == pytest.approx(0.03)
        assert abs(span) < MARGIN_TREND_DISCLOSE

    def test_too_little_history_has_no_span_either(self):
        assert margin_trend_span([0.30, 0.10]) is None

    def test_the_span_is_the_whole_window_not_the_latest_step(self):
        """A window that crawled up and then jumped must report the jump plus
        the crawl, because what the reader is being told is how far the
        margin has travelled."""
        assert margin_trend_span([0.40, 0.12, 0.11, 0.10]) == \
            pytest.approx(0.30)


# --- The reader is told when the latest margin is a recent arrival ---------
#
# Not a reason to use a different figure: a company that has improved for
# four years is more likely to hold its gain than to hand it back. A reason
# to say so, at caution tier, because the forecast is resting on a level the
# company reached rather than one it has a record of earning.

class TestTheWarning:
    @staticmethod
    def warnings_for(margins):
        from analysis import assess_suitability
        fin = make_fin(margins)
        d = derive_assumptions(fin)
        base, meta = base_year_from(fin)
        return assess_suitability(fin, d, base, meta=meta).warnings

    def kinds(self, margins):
        return [w.kind for w in self.warnings_for(margins)]

    def test_a_forty_point_improvement_is_disclosed(self):
        """Palantir: -8.5% to 31.6% in four years, which is the one company
        in the sixteen-name scan that trips this."""
        warned = [w for w in self.warnings_for([0.316, 0.108, 0.054, -0.085])
                  if w.kind == "margin_trend"]
        assert len(warned) == 1
        assert warned[0].severity == "caution"
        assert "latest operating margin of 31.6%" in str(warned[0])
        assert "moved steadily from -8.5%" in str(warned[0])
        assert "being kept rather than on a long record" in str(warned[0])

    def test_a_steep_decline_is_disclosed_in_the_other_words(self):
        warned = [w for w in self.warnings_for([0.06, 0.11, 0.16, 0.21])
                  if w.kind == "margin_trend"]
        assert len(warned) == 1
        assert "fallen steadily from 21.0%" in str(warned[0])
        assert "assumes the decline stops here" in str(warned[0])
        # Not the improvement wording, which would read as reassurance.
        assert "being kept" not in str(warned[0])

    def test_a_gentle_trend_is_not_worth_interrupting_for(self):
        assert "margin_trend" not in self.kinds([0.21, 0.20, 0.19, 0.18])

    def test_a_window_with_no_trend_is_not_warned_about(self):
        assert "margin_trend" not in self.kinds([0.192, 0.207, -0.436, 0.181])

    def test_the_median_rule_is_never_warned_about(self):
        """The warning says "the forecast assumes the latest margin holds",
        which is only true when the latest margin is what was taken."""
        margins = [-0.436, 0.192, 0.207, 0.181]
        assert chosen(margins)[1] == "median"
        assert "margin_trend" not in self.kinds(margins)

    def test_the_threshold_is_a_real_boundary(self):
        """Both sides, so the comparison cannot be inverted unnoticed."""
        just_under = [0.10 + MARGIN_TREND_DISCLOSE - 0.005, 0.105, 0.102, 0.10]
        just_over = [0.10 + MARGIN_TREND_DISCLOSE + 0.005, 0.105, 0.102, 0.10]
        assert "margin_trend" not in self.kinds(just_under)
        assert "margin_trend" in self.kinds(just_over)
