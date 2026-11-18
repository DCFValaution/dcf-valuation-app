"""How much of a company's own beta its own regression has earned.

SANE_BETA asks whether a beta is a plausible SIZE. That is not the same
question as whether it was measured, and for five years of monthly returns
against a tech-heavy index it is the weaker of the two: Merck's 0.115 and
Lockheed's 0.087 both came out of regressions whose slope could not be told
apart from zero, and both passed every check this model had.

So a second test: the slope over its own standard error. It is a ramp and
not a threshold, because the companies nearest a threshold are precisely
the ones whose side of it is arbitrary - Chevron measured t = 2.00, Procter
& Gamble 2.05, Duke 2.19, and a single cut-off would move them between
their own beta and their industry's in one jump on a few weeks of prices.

    w = clamp((|t| - 1.5) / (2.5 - 1.5), 0, 1)
    beta = w x (raw, Blume-adjusted) + (1 - w) x (industry, relevered)

Nothing below |t| = 1.5, everything above 2.5, proportionally between.
"""
from datetime import date

import pytest

import analysis as An
import assumptions as A
import industry_betas as I
from assumptions import (BETA_RAMP_HIGH_T, BETA_RAMP_LOW_T,
                         derive_assumptions, measured_weight)
from market_data import CompanyFinancials


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    """The rate is not what these tests are about."""
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(ticker="TEST", beta=1.0, t_statistic=6.0, std_error=None,
             *, source=None, long_term_debt=1_000e6,
             market_cap=1_000_000e6) -> CompanyFinancials:
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 10_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "interestExpense": 40e6, "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": long_term_debt,
        })
        cashflow.append({
            "fiscalYear": str(2025 - i),
            "depreciationAndAmortization": revenue * 0.05,
            "capitalExpenditure": -revenue * 0.04,
        })
    profile = {"companyName": "Test Co", "beta": beta,
               "marketCap": market_cap, "price": 100.0,
               "sector": "Technology", "exchange": "NASDAQ"}
    if t_statistic is not None:
        profile["betaTStatistic"] = t_statistic
        profile["betaStdError"] = (std_error if std_error is not None
                                   else abs(beta / t_statistic)
                                   if t_statistic else 0.0)
    elif std_error is not None:
        profile["betaStdError"] = std_error
    if source is not None:
        profile["betaSource"] = source
    return CompanyFinancials(ticker=ticker, profile=profile, income=income,
                             balance=balance, cashflow=cashflow)


def beta_of(**kwargs):
    return derive_assumptions(make_fin(**kwargs)).wacc_inputs["beta"]


# --- The gate itself -------------------------------------------------------

class TestTheWeightCurve:
    """`measured_weight` alone, before anything is done with it."""

    def test_nothing_below_the_low_end(self):
        assert measured_weight(0.0) == 0.0
        assert measured_weight(1.0) == 0.0
        assert measured_weight(BETA_RAMP_LOW_T) == 0.0

    def test_everything_above_the_high_end(self):
        assert measured_weight(BETA_RAMP_HIGH_T) == 1.0
        assert measured_weight(8.0) == 1.0

    def test_the_midpoint_is_half_and_half(self):
        """Which is the conventional 5% threshold, so the rule still agrees
        with convention where convention is confident."""
        assert measured_weight(2.0) == pytest.approx(0.5)

    def test_it_rises_in_a_straight_line_between(self):
        assert measured_weight(1.75) == pytest.approx(0.25)
        assert measured_weight(2.25) == pytest.approx(0.75)
        assert measured_weight(2.05) == pytest.approx(0.55)

    def test_it_never_leaves_nought_to_one(self):
        for t in (-100.0, -2.0, 0.0, 1.9, 2.4, 100.0):
            assert 0.0 <= measured_weight(t) <= 1.0, t

    def test_the_sign_does_not_decide_it(self):
        """A reliably NEGATIVE beta is a measurement, not a failure. Yahoo
        publishes -0.248 for Shell and -0.258 for BP; neither is
        significant, but a company that genuinely moved against the market
        would be, and its own figure should stand."""
        assert measured_weight(-4.0) == 1.0
        assert measured_weight(-2.0) == pytest.approx(measured_weight(2.0))

    def test_no_t_statistic_earns_nothing(self):
        """A beta quoted by the data provider arrives with no standard
        error, so there is no way to tell a measurement from noise, and
        treating the unknown as precise is the assumption that put a 0.115
        beta into Merck's discount rate."""
        assert measured_weight(None) == 0.0
        assert measured_weight(float("nan")) == 0.0

    def test_an_infinite_t_statistic_is_full_weight_not_a_crash(self):
        """A perfect fit divides by a zero standard error, and the
        regression reports inf rather than refusing."""
        assert measured_weight(float("inf")) == 1.0


class TestWhichBetaIsUsed:
    def test_a_well_measured_beta_is_the_company_s_own(self):
        prov = beta_of(beta=1.4, t_statistic=6.0)
        assert prov.source == "derived"
        assert prov.value == pytest.approx(A.blume_adjusted(1.4))

    def test_a_noisy_beta_is_replaced_by_its_industry(self):
        """Lockheed's case: a slope of 0.09 with a standard error of 0.26."""
        prov = beta_of(beta=0.42, t_statistic=1.2)
        assert prov.source == "derived (industry)"
        assert prov.value != pytest.approx(A.blume_adjusted(0.42))

    def test_a_middling_one_is_a_blend_of_the_two(self):
        """Procter & Gamble's case, at t = 2.05."""
        fin = make_fin(ticker="LMT", beta=0.42, t_statistic=2.05)
        prov = derive_assumptions(fin).wacc_inputs["beta"]
        industry, _sic, _mapped = I.industry_for("LMT")
        own = A.blume_adjusted(0.42)
        theirs = I.relevered_beta(industry, A._market_debt_to_equity(fin))
        want = 0.55 * own + 0.45 * theirs
        assert prov.value == pytest.approx(want, abs=1e-6)
        # And it lies strictly between the two, which a weight of exactly
        # zero or one would not produce.
        assert min(own, theirs) < prov.value < max(own, theirs)

    def test_the_ends_of_the_ramp_are_the_pure_cases(self):
        at_top = beta_of(beta=0.6, t_statistic=BETA_RAMP_HIGH_T)
        assert at_top.source == "derived"
        assert at_top.value == pytest.approx(A.blume_adjusted(0.6))

        at_bottom = beta_of(beta=0.6, t_statistic=BETA_RAMP_LOW_T)
        assert at_bottom.source == "derived (industry)"
        assert "too imprecise to use" in at_bottom.detail

    def test_there_is_no_jump_across_the_top_of_the_ramp(self):
        """The whole point of the ramp. A hundredth of a t-statistic must
        not move the beta by anything a reader would notice."""
        just_under = beta_of(beta=0.6, t_statistic=BETA_RAMP_HIGH_T - 0.01)
        at_top = beta_of(beta=0.6, t_statistic=BETA_RAMP_HIGH_T)
        assert just_under.value == pytest.approx(at_top.value, abs=0.005)

    def test_more_precision_always_means_more_of_the_company_s_own(self):
        """Monotonic, so a company whose regression improves never moves
        further from its own measured figure."""
        own = A.blume_adjusted(0.42)
        distances = []
        for t in (1.5, 1.75, 2.0, 2.25, 2.5):
            value = beta_of(ticker="LMT", beta=0.42, t_statistic=t).value
            distances.append(abs(value - own))
        assert distances == sorted(distances, reverse=True), distances

    def test_a_beta_with_no_error_estimate_is_not_trusted(self):
        prov = beta_of(beta=1.1, t_statistic=None,
                       source="quoted by the market data provider")
        assert prov.source == "derived (industry)"
        assert "no estimate of its error" in prov.detail

    def test_no_beta_at_all_lands_on_the_industry_too(self):
        prov = beta_of(beta=0.0, t_statistic=None,
                       source="unavailable - neither computable nor quoted")
        assert prov.source == "derived (industry)"
        assert "unavailable" in prov.detail

    def test_an_implausible_beta_earns_no_weight_however_well_measured(self):
        """Both tests apply. A precisely measured 99 is still not a beta,
        so the ramp is not consulted at all."""
        prov = beta_of(beta=99.0, t_statistic=40.0)
        assert prov.source == "derived (industry)"
        assert "outside plausible range" in prov.detail
        assert "blended" not in prov.detail

    def test_imprecision_is_blamed_before_implausibility(self):
        """Lockheed's 0.09 is below SANE_BETA's floor AND insignificant.
        Calling it implausible blames the figure for being small, when what
        is wrong is that it was never measured - and sends whoever reads
        the message looking for bad data rather than for noise."""
        prov = beta_of(beta=0.05, t_statistic=0.3)
        assert "too imprecise to use" in prov.detail
        assert "outside plausible range" not in prov.detail

    def test_an_override_still_beats_the_whole_mechanism(self):
        """An override is a statement, not a measurement."""
        d = derive_assumptions(make_fin(beta=0.05, t_statistic=0.2),
                               overrides={"beta": 1.5})
        assert d.wacc_inputs["beta"].value == 1.5
        assert d.wacc_inputs["beta"].source == "override"


# --- What the industry beta is -------------------------------------------

class TestTheIndustryBeta:
    def test_blume_is_not_applied_on_top_of_it(self):
        """An industry average is already an average of betas, so it is
        already pulled toward the market. Shrinking it again would shrink a
        figure that has been shrunk."""
        fin = make_fin(ticker="LMT", beta=0.05, t_statistic=0.3)
        prov = derive_assumptions(fin).wacc_inputs["beta"]
        industry, _sic, mapped = I.industry_for("LMT")
        assert mapped
        want = I.relevered_beta(industry, A._market_debt_to_equity(fin))
        assert prov.value == pytest.approx(want)
        assert prov.value != pytest.approx(A.blume_adjusted(want))

    def test_it_is_relevered_on_this_company_s_own_borrowing(self):
        """Two companies in one industry do not share a beta if they do not
        share a balance sheet."""
        geared = beta_of(ticker="LMT", beta=0.05, t_statistic=0.3,
                         long_term_debt=400_000e6).value
        ungeared = beta_of(ticker="LMT", beta=0.05, t_statistic=0.3,
                           long_term_debt=0.0).value
        assert geared > ungeared

    def test_a_company_with_no_sic_code_gets_the_whole_market(self):
        prov = beta_of(ticker="TEST", beta=0.05, t_statistic=0.3)
        assert "whole-market beta" in prov.detail
        assert "no SIC code on file" in prov.detail

    def test_an_unmapped_sic_code_says_that_rather_than_the_other_thing(
            self, monkeypatch):
        """"No code on file" and "a code this model cannot place" are
        different failures and point at different fixes."""
        monkeypatch.setitem(I.COMPANY_SIC, "TEST", "9999")
        prov = beta_of(ticker="TEST", beta=0.05, t_statistic=0.3)
        assert "not one this model maps to an industry" in prov.detail

    def test_absurd_leverage_is_capped_and_the_cap_is_declared(self):
        """Relevering on a D/E of hundreds turns a 0.9 industry beta into
        triple figures, and SANE_BETA would then reject it and leave the
        company with nothing."""
        prov = beta_of(ticker="LMT", beta=0.05, t_statistic=0.3,
                       long_term_debt=1_000_000e6, market_cap=1_000e6)
        assert prov.value < A.SANE_BETA[1]
        assert "capped at" in prov.detail


# --- What the provenance says ---------------------------------------------

class TestTheBlendedProvenance:
    """A blend's reader asks "how much of each", not "which figure"."""

    def test_it_states_the_weight_both_ways(self):
        prov = beta_of(ticker="LMT", beta=0.42, t_statistic=2.05)
        assert "blended 55% measured, 45% industry" in prov.detail

    def test_it_names_both_figures_that_went_into_it(self):
        prov = beta_of(ticker="LMT", beta=0.42, std_error=0.20,
                       t_statistic=2.1)
        assert f"{A.blume_adjusted(0.42):.2f}" in prov.detail
        assert "raw 0.42" in prov.detail
        assert "0.20" in prov.detail
        assert "Aerospace/Defense" in prov.detail

    def test_a_pure_case_does_not_claim_to_be_a_blend(self):
        assert "blended" not in beta_of(ticker="LMT", beta=0.05,
                                        t_statistic=0.3).detail
        assert "blended" not in beta_of(beta=1.4, t_statistic=6.0).detail

    def test_the_weight_in_the_words_is_the_weight_in_the_number(self):
        """A provenance line that rounded differently from the arithmetic
        would be a lie that looked like a rounding."""
        for t in (1.75, 2.0, 2.3):
            fin = make_fin(ticker="LMT", beta=0.42, t_statistic=t)
            prov = derive_assumptions(fin).wacc_inputs["beta"]
            industry, _sic, _mapped = I.industry_for("LMT")
            own = A.blume_adjusted(0.42)
            theirs = I.relevered_beta(industry,
                                      A._market_debt_to_equity(fin))
            stated = int(prov.detail.split("blended ")[1].split("%")[0])
            rebuilt = (stated / 100) * own + (1 - stated / 100) * theirs
            assert prov.value == pytest.approx(rebuilt, abs=0.005), t


class TestTheProvenance:
    def test_it_names_the_measured_beta_and_its_error(self):
        """A reader who disagrees has to be able to see what was rejected
        and on what grounds."""
        prov = beta_of(ticker="LMT", beta=0.42, std_error=0.26,
                       t_statistic=1.6)
        assert "0.42" in prov.detail
        assert "0.26" in prov.detail

    def test_it_names_the_industry_the_company_and_the_source_date(self):
        prov = beta_of(ticker="LMT", beta=0.05, t_statistic=0.3)
        assert "Aerospace/Defense" in prov.detail
        assert "Test Co" in prov.detail, "the company is named, not the ticker"
        assert "Damodaran" in prov.detail
        assert I.as_of_text() in prov.detail

    def test_it_says_how_many_companies_the_average_covers(self):
        """An average over four firms is not the same evidence as one over
        five hundred, and the reader cannot tell without being told."""
        prov = beta_of(ticker="LMT", beta=0.05, t_statistic=0.3)
        industry, _sic, _mapped = I.industry_for("LMT")
        assert f"{industry.firms:,} companies" in prov.detail

    def test_it_does_not_blame_the_data_provider_for_our_own_regression(self):
        prov = beta_of(beta=0.42, t_statistic=1.2)
        assert "measured" in prov.detail
        assert "reported" not in prov.detail


# --- How loudly it is said -------------------------------------------------

class TestTheWarning:
    def _warnings(self, **kwargs):
        d = derive_assumptions(make_fin(**kwargs))
        return {w.kind: w for w in An.input_source_warnings(d, dcf=True)}

    def test_an_industry_beta_is_info_not_caution(self):
        """It is a reasoned estimate from the company's own industry and
        balance sheet, not a guess. Saying it in the caution tier would
        tell the reader the valuation is unsound, which it is not."""
        got = self._warnings(ticker="LMT", beta=0.05,
                             t_statistic=0.3)["beta_industry"]
        assert got.severity == An.SEVERITY_INFO
        assert got.kind == "beta_industry"

    def test_a_blend_is_info_too_and_says_it_was_a_blend(self):
        """Telling a reader their company's own figure "could not be
        measured" when more than half the beta came from it would be
        false."""
        got = self._warnings(ticker="LMT", beta=0.42,
                             t_statistic=2.05)["beta_industry"]
        assert got.severity == An.SEVERITY_INFO
        assert got.detail["blended"] is True
        assert "part its own" in got.headline
        assert "could not be measured" not in got.headline

    def test_a_pure_industry_beta_does_not_say_it_was_a_blend(self):
        got = self._warnings(ticker="LMT", beta=0.05,
                             t_statistic=0.3)["beta_industry"]
        assert got.detail["blended"] is False
        assert "could not be measured reliably" in got.headline

    def test_the_warning_carries_the_number_and_the_reasoning(self):
        got = self._warnings(ticker="LMT", beta=0.05,
                             t_statistic=0.3)["beta_industry"]
        assert "industry" in got.headline
        assert got.detail["beta"] > 0
        assert "Aerospace/Defense" in got.detail["why"]

    def test_a_well_measured_beta_raises_nothing(self):
        assert "beta_industry" not in self._warnings(beta=1.4,
                                                     t_statistic=6.0)
        assert "beta_default" not in self._warnings(beta=1.4, t_statistic=6.0)

    def test_the_old_flat_market_beta_warning_is_still_a_caution(self):
        """Unreachable from the derivation now, and kept as a guard: this
        reads provenance rather than calling the derivation, so a future
        path that defaults a beta gets a warning rather than silence."""
        class Derived:
            wacc_inputs = {"beta": A.Provenance("beta", 1.0, "default",
                                                "used market beta of 1.0")}
            capm_inputs: dict = {}
            provenance: dict = {}
            diagnostics: dict = {}

        got = {w.kind: w for w in An.input_source_warnings(Derived(),
                                                           dcf=True)}
        assert got["beta_default"].severity == An.SEVERITY_CAUTION
        assert "beta_industry" not in got
