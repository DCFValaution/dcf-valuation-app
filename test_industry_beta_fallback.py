"""What stands in when a company has no usable beta of its own.

SANE_BETA's band is what "usable" means: a beta inside it is a number a
company can have, and it is used, Blume-adjusted, exactly as before. A beta
outside it, or no beta at all, used to become a flat 1.0 - a figure that
said nothing about the company. It now becomes the company's own industry
beta, relevered on the company's own borrowing.

The thing these tests are most careful about is what does NOT happen. 1.0.32
weighted a company's own beta against its industry's by the t-statistic of
its own regression, and that was wrong: the t-statistic tests a beta against
ZERO, while the question is whether the company's figure beats the
industry's. Johnson & Johnson's 0.183 +/- 0.15 cannot rule out zero and
rules out the 0.96 industry beta by five standard errors, so discarding it
threw away the better of the two estimates. And because t = beta / se with
standard errors clustered in one band, the test was close to a test of
whether the beta was LARGE - so it pushed every defensive company upward.

So: a measured beta inside the band is never overridden, however imprecise.
"""
from datetime import date

import pytest

import analysis as An
import assumptions as A
import industry_betas as I
from assumptions import derive_assumptions
from market_data import CompanyFinancials


@pytest.fixture(autouse=True)
def _pin_the_rate(monkeypatch):
    """The rate is not what these tests are about."""
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))


def make_fin(ticker="TEST", beta=1.0, std_error=None, t_statistic=None,
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
    # Carried when a test supplies them, because the profile does carry
    # them in production - and nothing may read them.
    if std_error is not None:
        profile["betaStdError"] = std_error
    if t_statistic is not None:
        profile["betaTStatistic"] = t_statistic
    if source is not None:
        profile["betaSource"] = source
    return CompanyFinancials(ticker=ticker, profile=profile, income=income,
                             balance=balance, cashflow=cashflow)


def beta_of(**kwargs):
    return derive_assumptions(make_fin(**kwargs)).wacc_inputs["beta"]


# --- Which beta is used ----------------------------------------------------

class TestWhichBetaIsUsed:
    def test_a_measured_beta_inside_the_band_is_the_company_s_own(self):
        prov = beta_of(beta=1.4)
        assert prov.source == "derived"
        assert prov.value == pytest.approx(A.blume_adjusted(1.4))

    def test_a_beta_below_the_band_falls_back_to_the_industry(self):
        """Lockheed measures 0.087, Shell 0.091, General Mills -0.042."""
        prov = beta_of(beta=0.05)
        assert prov.source == "derived (industry)"
        assert "outside plausible range" in prov.detail

    def test_a_beta_above_the_band_falls_back_too(self):
        prov = beta_of(beta=99.0)
        assert prov.source == "derived (industry)"
        assert "outside plausible range" in prov.detail

    def test_the_band_edges_are_inside_it(self):
        for edge in A.SANE_BETA:
            assert beta_of(beta=edge).source == "derived", edge

    def test_no_beta_at_all_falls_back_and_says_why(self):
        prov = beta_of(beta=0.0,
                       source="unavailable - neither computable nor quoted")
        assert prov.source == "derived (industry)"
        assert "unavailable" in prov.detail

    def test_a_missing_beta_key_does_not_crash(self):
        fin = make_fin(beta=1.0)
        del fin.profile["beta"]
        prov = derive_assumptions(fin).wacc_inputs["beta"]
        assert prov.source == "derived (industry)"

    def test_an_override_beats_everything(self):
        """An override is a statement, not a measurement."""
        d = derive_assumptions(make_fin(beta=0.05),
                               overrides={"beta": 1.5})
        assert d.wacc_inputs["beta"].value == 1.5
        assert d.wacc_inputs["beta"].source == "override"

    def test_the_flat_market_beta_is_no_longer_used_for_a_beta(self):
        """DEFAULT_BETA survives only as Blume's shrink target."""
        for beta in (0.0, 0.05, 99.0):
            prov = beta_of(beta=beta)
            assert prov.value != pytest.approx(A.DEFAULT_BETA), beta
            assert prov.source != "default", beta


# --- What precision must NOT do --------------------------------------------

class TestPrecisionIsNotAGate:
    """The 1.0.32 mistake, with a test each so it cannot come back.

    A regression's t-statistic tests its slope against ZERO. The question
    this code faces is whether the company's own figure beats its
    industry's, and a measurement can answer that while failing the other.
    """

    def test_an_imprecise_beta_inside_the_band_is_still_the_company_s_own(
            self):
        """Johnson & Johnson: 0.183 +/- 0.15, a t-statistic of 1.25."""
        prov = beta_of(beta=0.183, std_error=0.15, t_statistic=1.25)
        assert prov.source == "derived"
        assert prov.value == pytest.approx(A.blume_adjusted(0.183))

    def test_a_beta_that_cannot_rule_out_zero_still_rules_out_the_industry(
            self):
        """Which is the whole argument. J&J's standard error of 0.15 puts
        the industry's 0.96 more than five standard errors away, so the
        measurement is precise ENOUGH - just not precise enough to answer
        a question nobody asked."""
        fin = make_fin(ticker="LMT", beta=0.183, std_error=0.15,
                       t_statistic=1.25)
        industry, _sic, _mapped = I.industry_for("LMT")
        theirs = I.relevered_beta(industry, A._market_debt_to_equity(fin))
        distance = abs(theirs - 0.183) / 0.15
        assert distance > 4.0, (
            f"the industry beta is only {distance:.1f} standard errors "
            "away, so this fixture no longer makes the point")
        prov = derive_assumptions(fin).wacc_inputs["beta"]
        assert prov.source == "derived"

    def test_a_very_imprecise_beta_is_still_not_overridden(self):
        """Merck: 0.115 +/- 0.19, a t-statistic of 0.59. Inside the band,
        so it is used. Being a bad measurement is not grounds for
        substituting a figure measured on other companies."""
        prov = beta_of(beta=0.115, std_error=0.19, t_statistic=0.59)
        assert prov.source == "derived"

    def test_precision_changes_nothing_at_all(self):
        """Same beta, every precision from hopeless to perfect."""
        values = {beta_of(beta=0.5, std_error=se, t_statistic=0.5 / se).value
                  for se in (0.01, 0.1, 0.25, 0.5, 1.0)}
        assert len(values) == 1, values

    def test_no_beta_is_ever_a_blend_of_the_two(self):
        """1.0.32 produced figures strictly between the company's own and
        its industry's. Nothing may now."""
        for beta, se in ((0.183, 0.15), (0.3, 0.15), (0.5, 0.24)):
            fin = make_fin(ticker="LMT", beta=beta, std_error=se,
                           t_statistic=beta / se)
            prov = derive_assumptions(fin).wacc_inputs["beta"]
            assert "blended" not in prov.detail
            assert prov.value == pytest.approx(A.blume_adjusted(beta))

    def test_the_decision_does_not_read_the_precision_fields_at_all(self):
        """Structural, not behavioural: the branch must not consult them,
        so no future edit can reintroduce the dependency quietly."""
        import inspect
        source = inspect.getsource(A._capm_inputs)
        beta_section = source.split("--- Beta ---")[1]
        for field in ("betaStdError", "betaTStatistic", "measured_weight",
                      "BETA_RAMP"):
            assert field not in beta_section, field


# --- What the industry beta is -------------------------------------------

class TestTheIndustryBeta:
    def test_blume_is_not_applied_on_top_of_it(self):
        """An industry average is already an average of betas, so it is
        already pulled toward the market. Shrinking it again would shrink a
        figure that has been shrunk."""
        fin = make_fin(ticker="LMT", beta=0.05)
        prov = derive_assumptions(fin).wacc_inputs["beta"]
        industry, _sic, mapped = I.industry_for("LMT")
        assert mapped
        want = I.relevered_beta(industry, A._market_debt_to_equity(fin))
        assert prov.value == pytest.approx(want)
        assert prov.value != pytest.approx(A.blume_adjusted(want))

    def test_it_is_relevered_on_this_company_s_own_borrowing(self):
        """Two companies in one industry do not share a beta if they do not
        share a balance sheet."""
        geared = beta_of(ticker="LMT", beta=0.05,
                         long_term_debt=400_000e6).value
        ungeared = beta_of(ticker="LMT", beta=0.05,
                           long_term_debt=0.0).value
        assert geared > ungeared

    def test_a_company_with_no_sic_code_gets_the_whole_market(self):
        prov = beta_of(ticker="TEST", beta=0.05)
        assert "whole-market beta" in prov.detail
        assert "no SIC code on file" in prov.detail

    def test_an_unmapped_sic_code_says_that_rather_than_the_other_thing(
            self, monkeypatch):
        """"No code on file" and "a code this model cannot place" are
        different failures and point at different fixes."""
        monkeypatch.setitem(I.COMPANY_SIC, "TEST", "9999")
        prov = beta_of(ticker="TEST", beta=0.05)
        assert "not one this model maps to an industry" in prov.detail

    def test_absurd_leverage_is_capped_and_the_cap_is_declared(self):
        """Relevering on a D/E of hundreds turns a 0.9 industry beta into
        triple figures, and SANE_BETA would then reject it and leave the
        company with nothing."""
        prov = beta_of(ticker="LMT", beta=0.05,
                       long_term_debt=1_000_000e6, market_cap=1_000e6)
        assert prov.value < A.SANE_BETA[1]
        assert "capped at" in prov.detail


# --- What the provenance says ---------------------------------------------

class TestTheProvenance:
    def test_it_names_the_rejected_beta(self):
        """A reader who disagrees has to be able to see what was rejected
        and on what grounds."""
        prov = beta_of(ticker="LMT", beta=0.05)
        assert "0.05" in prov.detail

    def test_it_names_the_industry_the_company_and_the_source_date(self):
        prov = beta_of(ticker="LMT", beta=0.05)
        assert "Aerospace/Defense" in prov.detail
        assert "Test Co" in prov.detail, "the company is named, not the ticker"
        assert "Damodaran" in prov.detail
        assert I.as_of_text() in prov.detail

    def test_it_says_how_many_companies_the_average_covers(self):
        """An average over four firms is not the same evidence as one over
        five hundred, and the reader cannot tell without being told."""
        prov = beta_of(ticker="LMT", beta=0.05)
        industry, _sic, _mapped = I.industry_for("LMT")
        assert f"{industry.firms:,} companies" in prov.detail

    def test_it_does_not_blame_the_data_provider_for_our_own_regression(self):
        prov = beta_of(beta=0.05)
        assert "measured" in prov.detail
        assert "reported" not in prov.detail

    def test_a_used_beta_still_shows_both_numbers(self):
        prov = beta_of(beta=2.31, source="computed from 59 monthly returns")
        assert "Blume-adjusted" in prov.detail
        assert "2.31" in prov.detail
        assert "computed from 59" in prov.detail


# --- How loudly it is said -------------------------------------------------

class TestTheWarning:
    def _warnings(self, **kwargs):
        d = derive_assumptions(make_fin(**kwargs))
        return {w.kind: w for w in An.input_source_warnings(d, dcf=True)}

    def test_an_industry_beta_is_info_not_caution(self):
        """It is a reasoned estimate from the company's own industry and
        balance sheet, not a guess. Saying it in the caution tier would
        tell the reader the valuation is unsound, which it is not."""
        got = self._warnings(ticker="LMT", beta=0.05)["beta_industry"]
        assert got.severity == An.SEVERITY_INFO
        assert got.kind == "beta_industry"

    def test_the_warning_carries_the_number_and_the_reasoning(self):
        got = self._warnings(ticker="LMT", beta=0.05)["beta_industry"]
        assert "industry" in got.headline
        assert got.detail["beta"] > 0
        assert "Aerospace/Defense" in got.detail["why"]

    def test_a_usable_beta_raises_nothing(self):
        for kwargs in ({"beta": 1.4}, {"beta": 0.183, "std_error": 0.15,
                                       "t_statistic": 1.25}):
            got = self._warnings(**kwargs)
            assert "beta_industry" not in got, kwargs
            assert "beta_default" not in got, kwargs

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
