"""The SIC-to-industry map, and the relevering that turns it into a beta.

Most of these are completeness checks on data rather than behaviour tests,
and that is deliberate: the ways this goes wrong are a SIC code with no
industry, an industry name that does not exist in Damodaran's table, and a
judgement recorded for a code that is no longer mapped. All three are
silent at runtime - the first falls through to the whole market, the second
and third are simply never read - so each gets a test that fails loudly.
"""
import pytest

import industry_betas as I
from company_sic_data import COMPANY_SIC
from industry_beta_data import INDUSTRY_BETAS, TOTAL_MARKET


# --- The map is complete and points at things that exist ------------------

class TestTheMapIsSound:
    def test_every_industry_named_exists_in_damodarans_table(self):
        """A typo here is silent: the lookup misses and the whole market
        stands in, which looks like a company with no industry."""
        unknown = sorted(set(I.SIC_TO_INDUSTRY.values()) - set(INDUSTRY_BETAS))
        assert unknown == [], f"industries not in the table: {unknown}"

    def test_every_sic_code_in_use_is_mapped(self):
        """The universe is the S&P 500 plus the tickers this project is
        checked against. A code in use with no mapping would send a real
        company to the whole-market beta without anyone noticing."""
        unmapped = sorted({code for code in COMPANY_SIC.values()
                           if code not in I.SIC_TO_INDUSTRY})
        assert unmapped == [], f"SIC codes in use with no industry: {unmapped}"

    def test_every_judgement_is_about_a_code_that_is_mapped(self):
        """A judgement left behind after its code was remapped is a note
        describing a decision that is no longer taken."""
        orphans = sorted(set(I.SIC_JUDGEMENTS) - set(I.SIC_TO_INDUSTRY))
        assert orphans == [], f"judgements for unmapped codes: {orphans}"

    def test_the_codes_are_four_digit_strings(self):
        """SIC codes are strings, not integers: '0100' is agriculture and
        100 is nothing, and the SEC serves the leading zero."""
        wrong = sorted(code for code in I.SIC_TO_INDUSTRY
                       if not (isinstance(code, str) and len(code) == 4
                               and code.isdigit()))
        assert wrong == []

    def test_the_whole_market_row_is_there_to_fall_back_to(self):
        assert TOTAL_MARKET in INDUSTRY_BETAS
        assert INDUSTRY_BETAS[TOTAL_MARKET].firms > 1000

    def test_the_table_is_dated_and_the_date_is_readable(self):
        assert I.DAMODARAN_BETAS_AS_OF.year >= 2026
        assert I.as_of_text() == f"{I.DAMODARAN_BETAS_AS_OF:%B %Y}"


# --- Relevering ------------------------------------------------------------

class TestRelevering:
    def test_it_is_the_textbook_formula(self):
        industry = INDUSTRY_BETAS["Aerospace/Defense"]
        got = I.relevered_beta(industry, 0.20)
        want = industry.unlevered_beta * (1 + (1 - I.RELEVER_TAX_RATE) * 0.20)
        assert got == pytest.approx(want)

    def test_no_debt_leaves_the_unlevered_beta_alone(self):
        industry = INDUSTRY_BETAS["Semiconductor"]
        assert I.relevered_beta(industry, 0.0) == pytest.approx(
            industry.unlevered_beta)

    def test_more_debt_means_more_beta(self):
        industry = INDUSTRY_BETAS["Food Processing"]
        assert (I.relevered_beta(industry, 0.5)
                > I.relevered_beta(industry, 0.1))

    def test_a_negative_leverage_ratio_is_treated_as_none(self):
        """It cannot be right, and propagating it would REDUCE the beta -
        a company with impossible accounts coming out safer than one
        without any debt."""
        industry = INDUSTRY_BETAS["Machinery"]
        assert I.relevered_beta(industry, -2.0) == pytest.approx(
            industry.unlevered_beta)

    def test_absurd_leverage_is_capped_rather_than_amplified(self):
        """A company whose equity has nearly vanished has a D/E of
        hundreds, and relevering on it turns a 0.9 industry beta into
        triple figures."""
        industry = INDUSTRY_BETAS["Machinery"]
        assert I.relevered_beta(industry, 500.0) == pytest.approx(
            I.relevered_beta(industry, I.MAX_DEBT_TO_EQUITY))
        assert I.relevered_beta(industry, 500.0) < 4.0


# --- Looking a company up --------------------------------------------------

class TestIndustryFor:
    def test_a_known_ticker_gets_its_own_industry(self):
        industry, sic, mapped = I.industry_for("LMT")
        assert mapped
        assert sic == "3760"            # guided missiles and space vehicles
        assert industry.name == "Aerospace/Defense"

    def test_the_ticker_is_matched_without_regard_to_case_or_space(self):
        assert I.industry_for(" lmt ")[0].name == "Aerospace/Defense"

    def test_an_unknown_ticker_gets_the_whole_market_and_says_so(self):
        industry, sic, mapped = I.industry_for("ZZZZ")
        assert not mapped
        assert sic is None
        assert industry.name == TOTAL_MARKET

    def test_an_empty_ticker_does_not_raise(self):
        assert I.industry_for("")[0].name == TOTAL_MARKET
        assert I.industry_for(None)[0].name == TOTAL_MARKET

    def test_a_mapped_ticker_whose_code_was_removed_falls_back(self,
                                                               monkeypatch):
        """Not hypothetical: Damodaran renames industries between years,
        and a rename that is not mirrored here must not raise."""
        monkeypatch.setitem(I.SIC_TO_INDUSTRY, "3760", "No Such Industry")
        industry, sic, mapped = I.industry_for("LMT")
        assert not mapped
        assert sic == "3760"            # the code is still reported
        assert industry.name == TOTAL_MARKET


# --- The particular calls that were hard -----------------------------------

class TestTheJudgementsThatMatter:
    def test_the_thin_industries_are_recorded_as_thin(self):
        """Oil/Gas (Integrated) averages four companies and is the prior
        for Exxon, Chevron and BP. Whoever reads this map next has to be
        told that before they trust it."""
        assert INDUSTRY_BETAS["Oil/Gas (Integrated)"].firms < 10
        assert "2911" in I.SIC_JUDGEMENTS
        assert "FOUR" in I.SIC_JUDGEMENTS["2911"]

    def test_the_catch_all_codes_are_flagged(self):
        """7389 holds Visa, Uber and seventeen others; 6021 holds JPMorgan
        and seven regional banks. Neither can be split by SIC code."""
        for code in ("7389", "6021", "7370", "2080"):
            assert code in I.SIC_JUDGEMENTS, code

    def test_amazon_lands_somewhere_and_the_choice_is_written_down(self):
        """Damodaran has no online-retail industry, so this is a choice
        rather than a lookup."""
        industry, sic, mapped = I.industry_for("AMZN")
        assert sic == "5961"
        assert mapped
        assert "5961" in I.SIC_JUDGEMENTS

    def test_a_judgement_says_what_was_rejected_and_not_only_what_was_picked(
            self):
        """A note that records only the answer is not a judgement, it is a
        restatement of the map."""
        for code, why in I.SIC_JUDGEMENTS.items():
            assert len(why) > 80, code
            assert any(word in why for word in
                       ("rejected", "arguable", "alternative", "instead",
                        "chosen", "Left literal", "left literal")), code
