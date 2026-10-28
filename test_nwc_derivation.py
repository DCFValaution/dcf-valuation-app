"""Working capital is derived from its level, not from a two-endpoint delta.

The delta was the same shape of mistake the revenue CAGR used to make: two
endpoints cannot tell a structural ratio from a transitional drift, and when
a company's working capital is drifting the delta reports the drift as if it
were the structure.

Alibaba is the case that exposed it, so its real figures are the regression
test. They are written out here rather than fetched, because a test that
needs Yahoo to be up is not a test of this.
"""

from statistics import median

import pytest

from assumptions import CLAMP_NWC_PCT, _net_working_capital


def ratios(balance, revenues):
    """What the derivation now does, in one line."""
    return median([_net_working_capital(b) / r
                   for b, r in zip(balance, revenues) if r > 0])


def endpoint(balance, revenues):
    """What it used to do, kept here to assert the difference."""
    delta = revenues[0] - revenues[-1]
    return ((_net_working_capital(balance[0])
             - _net_working_capital(balance[-1])) / delta) if delta > 0 else 0.0


def row(current_assets, cash, short_investments,
        current_liabilities, short_debt):
    return {"totalCurrentAssets": current_assets,
            "cashAndCashEquivalents": cash,
            "shortTermInvestments": short_investments,
            "totalCurrentLiabilities": current_liabilities,
            "shortTermDebt": short_debt}


# Alibaba, newest first, as reported and converted at a single spot rate.
BABA_BALANCE = [
    row(91_635_503_625, 19_733_840_113, 27_810_716_480, 71_475_419_767, 4_234_531_311),
    row(101_129_591_683, 21_827_850_654, 42_400_225_187, 65_316_265_169, 3_385_044_481),
    row(112_954_442_352, 37_226_937_413, 48_446_254_906, 63_239_958_522, 4_351_106_950),
    row(104_717_930_876, 28_969_271_284, 49_718_534_721, 57_815_365_478, 1_840_304_743),
]
BABA_REVENUES = [153_570_458_166, 149_471_475_458, 141_193_549_651, 130_319_986_512]


class TestTheAlibabaRegression:
    def test_its_working_capital_is_negative_every_single_year(self):
        """A marketplace collects from buyers before it pays merchants, so
        growth is funded by the float rather than consuming cash."""
        for b, r in zip(BABA_BALANCE, BABA_REVENUES):
            assert _net_working_capital(b) < 0
            assert -0.25 < _net_working_capital(b) / r < -0.10

    def test_the_old_method_got_the_sign_backwards(self):
        """The bug, pinned so it cannot quietly return.

        Every year is negative and the delta still reported +0.292, because
        the float had been eroding and two endpoints cannot tell erosion
        from structure.
        """
        assert endpoint(BABA_BALANCE, BABA_REVENUES) == pytest.approx(0.292, abs=0.002)

    def test_the_level_gets_it_right(self):
        coefficient = ratios(BABA_BALANCE, BABA_REVENUES)
        assert coefficient < 0, "growth frees cash for this business"
        assert coefficient == pytest.approx(-0.196, abs=0.005)

    def test_the_two_methods_disagree_in_sign(self):
        """Which is the whole finding, stated once."""
        assert endpoint(BABA_BALANCE, BABA_REVENUES) > 0
        assert ratios(BABA_BALANCE, BABA_REVENUES) < 0


class TestStability:
    def test_dropping_any_one_year_barely_moves_it(self):
        """The property the endpoint method did not have.

        Measured across fourteen companies, dropping a single year moved the
        old coefficient by 0.136 on average and the new one by 0.018.
        """
        full = ratios(BABA_BALANCE, BABA_REVENUES)
        for drop in range(len(BABA_REVENUES)):
            b = [x for i, x in enumerate(BABA_BALANCE) if i != drop]
            r = [x for i, x in enumerate(BABA_REVENUES) if i != drop]
            assert abs(ratios(b, r) - full) < 0.08

    def test_the_endpoint_method_was_far_more_sensitive(self):
        """Dropping the oldest year is enough to change its mind entirely."""
        full = endpoint(BABA_BALANCE, BABA_REVENUES)
        without_oldest = endpoint(BABA_BALANCE[:-1], BABA_REVENUES[:-1])
        assert abs(without_oldest - full) > abs(
            ratios(BABA_BALANCE[:-1], BABA_REVENUES[:-1])
            - ratios(BABA_BALANCE, BABA_REVENUES))

    def test_one_freak_year_cannot_carry_it(self):
        """A median, not a mean, for exactly this reason."""
        freak = BABA_BALANCE + [row(500_000_000_000, 0, 0, 1_000_000, 0)]
        revenues = BABA_REVENUES + [120_000_000_000]
        assert ratios(freak, revenues) < 0


class TestClamp:
    def test_it_still_rails_the_absurd(self):
        extreme = [row(10_000, 0, 0, 0, 0)]
        assert ratios(extreme, [1]) > CLAMP_NWC_PCT[1]

    def test_the_bounds_admit_real_companies(self):
        """Recalibrated with the derivation: the old floor of -10% sat above
        sixteen of forty-four measured companies, so for more than a third
        the clamp was the answer rather than a rail."""
        low, high = CLAMP_NWC_PCT
        assert low <= -0.35, "Salesforce measures -32.7%"
        assert high >= 0.30, "Caterpillar measures +27.7%"
        assert ratios(BABA_BALANCE, BABA_REVENUES) > low
