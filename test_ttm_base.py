"""The trailing-twelve-month base year.

The latest complete fiscal year is audited and internally consistent, and up
to eleven months old. NVIDIA's FY2026 revenue is $216bn against a trailing
$303bn, so the model was projecting forward from a company 40% smaller than
the one that exists. The trailing figures close that gap.

Four things are protected here, and each of them has already gone wrong
somewhere in this codebase or in the probe that preceded this change.

The currency. The trailing series arrive in the REPORTING currency while the
annual statements have already been restated into the trading one. TSM's
trailing revenue is 4.9 trillion TWD beside an annual $116bn USD; read
against each other that is a 4,156% error. Every monetary field goes through
the one scalar, and no share count does.

The debt definition. Yahoo's quarterlyTotalDebt folds capital lease
obligations in; the annual path deliberately excludes them. Taking the
convenient field would have redefined debt mid-model by $98.8bn on AMZN.

The vintage. A debt figure from one quarter against cash from another is not
a balance sheet.

And the fallbacks, which are per field. Alibaba publishes no trailing capital
expenditure at all, and losing its whole trailing base over one display
figure would be the wrong trade.

Run with:  python -m pytest test_ttm_base.py -v
"""

import pytest

import market_data as M
from market_data import CompanyFinancials, TTMBase


@pytest.fixture(autouse=True)
def _clear_caches():
    M.clear_caches()
    yield
    M.clear_caches()


# A quarter-end every field shares unless a test says otherwise.
QE = "2026-09-30"

FULL = {
    "revenue": 400_000e6,
    "capex": -80_000e6,
    "shares_diluted": 10_000e6,
    "shares_ordinary": 9_900e6,
    "current_debt": 5_000e6,
    "long_term_debt": 45_000e6,
    "cash_and_short_term": 30_000e6,
    "investments": 20_000e6,
    "minority_interest": 3_000e6,
}


def ttm(values=None, as_of=None, currency="USD", notes=None, period_end=QE):
    vals = dict(FULL if values is None else values)
    dates = {name: QE for name in vals}
    dates.update(as_of or {})
    return TTMBase(values=vals, as_of=dates, period_end=period_end,
                   currency=currency, notes=list(notes or []))


def company(ttm_base, *, reporting="USD", trading="USD", fx=None, price=100.0):
    """A company whose annual statements say something DIFFERENT.

    The annual figures are deliberately nothing like the trailing ones, so a
    test that passes can only have read the trailing ones.
    """
    return CompanyFinancials(
        ticker="TEST",
        profile={"companyName": "Test Co", "sector": "Technology",
                 "industry": "Software", "price": price, "currency": trading},
        income=[{"revenue": 1.0e6, "fiscalYear": "2025",
                 "weightedAverageShsOutDil": 1.0e6, "date": "2025-12-31"}],
        balance=[{"currentDebt": 1.0e6, "longTermDebt": 1.0e6,
                  "cashAndCashEquivalents": 1.0e6, "totalInvestments": 1.0e6,
                  "minorityInterest": 1.0e6, "fiscalYear": "2025"}],
        cashflow=[{"capitalExpenditure": -1.0e6}],
        reporting_currency=reporting,
        trading_currency=trading,
        fx=fx,
        ttm=ttm_base,
    )


class TestItIsUsedAtAll:
    def test_the_trailing_figures_become_the_base(self):
        base, meta = M.base_year_from(company(ttm()))
        assert base.revenue == pytest.approx(400_000.0)
        assert meta["base_period"] == "ttm"
        assert meta["base_label"] == f"TTM as of {QE}"

    def test_without_them_the_annual_statements_are_the_base(self):
        base, meta = M.base_year_from(company(None))
        assert base.revenue == pytest.approx(1.0)
        assert meta["base_period"] == "annual"
        assert meta["base_label"] == "FY2025"

    def test_base_year_from_makes_no_request_of_its_own(self, monkeypatch):
        """It is a pure function of what it is handed, and has to stay one.

        It was not, briefly. Every test that fakes a company does it by
        faking fetch_financials, so a second I/O path further down walked
        straight past that: an offline suite reached the network, and the
        placeholder ticker "GOOD" - Gladstone Commercial, a real REIT - was
        valued with its real quarterly balance sheet against another
        company's annual statements.
        """
        def explode(*args, **kwargs):
            raise AssertionError("base_year_from reached the network")

        monkeypatch.setattr(M, "fetch_ttm_base", explode)
        monkeypatch.setattr(M, "_get_json", explode)
        monkeypatch.setattr(M, "_browser_session", explode)

        M.base_year_from(company(ttm()))
        M.base_year_from(company(None))


class TestCurrency:
    """One scalar, on every monetary field, and on nothing else."""

    RATE = 0.03048  # TWD -> USD

    def _converted(self):
        fx = {"rate": self.RATE, "pair": "TWDUSD=X", "as_of": "2026-11-09"}
        return M.base_year_from(
            company(ttm(currency="TWD"), reporting="TWD", trading="USD", fx=fx))

    def test_every_monetary_field_is_converted(self):
        base, _ = self._converted()
        assert base.revenue == pytest.approx(400_000e6 * self.RATE / 1e6)
        assert base.total_debt == pytest.approx(50_000e6 * self.RATE / 1e6)
        assert base.cash == pytest.approx(50_000e6 * self.RATE / 1e6)
        assert base.minority_interest == pytest.approx(3_000e6 * self.RATE / 1e6)

    def test_the_base_capex_is_converted_too(self):
        _, meta = self._converted()
        assert meta["base_capex"] == pytest.approx(80_000e6 * self.RATE / 1e6)

    def test_the_share_count_is_not_converted(self):
        """A count is not an amount of money.

        This is the one field that must NOT move, and converting it would be
        invisible in every ratio while quietly scaling value per share.
        """
        base, _ = self._converted()
        assert base.shares == pytest.approx(10_000.0)

    def test_one_rate_for_all_of_them(self):
        """The fields must not drift apart.

        A ratio built from two fields converted at different rates is wrong
        in a way no single figure reveals, which is how the income-statement
        conversion bug survived as long as it did.
        """
        base, _ = self._converted()
        plain, _ = M.base_year_from(company(ttm()))
        for converted, unconverted in ((base.revenue, plain.revenue),
                                       (base.total_debt, plain.total_debt),
                                       (base.cash, plain.cash),
                                       (base.minority_interest,
                                        plain.minority_interest)):
            assert converted == pytest.approx(unconverted * self.RATE)

    def test_a_currency_mismatch_refuses_the_trailing_base(self, monkeypatch):
        """No safe rate exists, so the annual base is used instead.

        The scalar that converted the statements is only the right scalar if
        the trailing figures arrived in the currency it was built from.
        """
        monkeypatch.setattr(M, "fetch_ttm_base",
                            lambda symbol: ttm(currency="JPY"))
        assert M._usable_ttm("TEST", "TWD") is None

    def test_a_matching_currency_is_accepted(self, monkeypatch):
        monkeypatch.setattr(M, "fetch_ttm_base",
                            lambda symbol: ttm(currency="twd"))
        assert M._usable_ttm("TEST", "TWD") is not None


class TestDebtIsLeaseFree:
    def test_it_sums_the_components(self):
        base, _ = M.base_year_from(company(ttm()))
        assert base.total_debt == pytest.approx(50_000.0)

    def test_it_never_reads_yahoos_total_debt(self):
        """quarterlyTotalDebt includes capital leases; the annual convention
        excludes them. $98.8bn of difference on AMZN, 12.8 points of its
        per-share value."""
        assert "quarterlyTotalDebt" not in M._TTM_SERIES.values()
        assert M._TTM_SERIES["current_debt"] == "quarterlyCurrentDebt"
        assert M._TTM_SERIES["long_term_debt"] == "quarterlyLongTermDebt"

    def test_a_missing_half_is_nil_and_is_said_so(self):
        values = {k: v for k, v in FULL.items() if k != "current_debt"}
        base, meta = M.base_year_from(company(ttm(values)))
        assert base.total_debt == pytest.approx(45_000.0)
        assert any("current debt" in note for note in meta["base_notes"])


class TestPerFieldFallback:
    def test_no_trailing_capex_keeps_the_base(self):
        """Alibaba's case. The base survives; only the display figure is
        absent, and the projection never used it."""
        values = {k: v for k, v in FULL.items() if k != "capex"}
        base, meta = M.base_year_from(company(ttm(values)))
        assert base.revenue == pytest.approx(400_000.0)
        assert meta["base_period"] == "ttm"
        assert meta["base_capex"] is None
        assert any("capital expenditure" in note for note in meta["base_notes"])

    def test_capex_is_reported_as_a_positive_amount_spent(self):
        _, meta = M.base_year_from(company(ttm()))
        assert meta["base_capex"] == pytest.approx(80_000.0)

    def test_absent_minority_interest_is_nil_and_says_which(self):
        values = {k: v for k, v in FULL.items() if k != "minority_interest"}
        base, meta = M.base_year_from(company(ttm(values)))
        assert base.minority_interest == 0.0
        assert meta["minority_interest_reported"] is False
        assert "inferred" in meta["minority_interest_source"]

    def test_absent_long_term_investments_are_nil(self):
        values = {k: v for k, v in FULL.items() if k != "investments"}
        base, meta = M.base_year_from(company(ttm(values)))
        assert base.cash == pytest.approx(30_000.0)
        assert any("investments" in note for note in meta["base_notes"])

    def test_shares_fall_back_to_the_quarterly_count(self):
        values = {k: v for k, v in FULL.items() if k != "shares_diluted"}
        base, meta = M.base_year_from(company(ttm(values)))
        assert base.shares == pytest.approx(9_900.0)
        assert "ordinary shares" in meta["shares_source"]

    def test_with_no_trailing_count_at_all_the_annual_one_is_used(self):
        values = {k: v for k, v in FULL.items()
                  if k not in ("shares_diluted", "shares_ordinary")}
        base, meta = M.base_year_from(company(ttm(values)))
        assert base.shares == pytest.approx(1.0)
        assert any("share count" in note for note in meta["base_notes"])


class TestVintage:
    """One balance sheet, not several."""

    def _fetch(self, monkeypatch, rows):
        monkeypatch.setattr(M, "_ttm_rows", lambda ticker: rows)
        return M.fetch_ttm_base("TEST")

    def _rows(self, **dates):
        out = {}
        for name, series in M._TTM_SERIES.items():
            if name not in FULL:
                continue
            out[series] = {
                "reportedValue": {"raw": FULL[name]},
                "asOfDate": dates.get(name, QE),
                "currencyCode": "USD",
            }
        return out

    def test_fields_sharing_a_quarter_end_are_all_kept(self, monkeypatch):
        result = self._fetch(monkeypatch, self._rows())
        assert result.period_end == QE
        assert result.notes == []
        assert "current_debt" in result.values

    def test_a_stale_component_is_dropped_with_a_note(self, monkeypatch):
        """Alphabet's case: quarterlyCurrentDebt last reported 2025-03-31,
        548 days before the balance sheet it would have been read against."""
        result = self._fetch(monkeypatch,
                             self._rows(current_debt="2025-03-31"))
        assert result.period_end == QE
        assert "current_debt" not in result.values
        assert any("2025-03-31" in note for note in result.notes)

    def test_one_quarter_of_slack_is_tolerated(self, monkeypatch):
        """Reporting calendars are not perfectly aligned, and refusing over a
        few weeks would reject companies whose data is fine."""
        result = self._fetch(monkeypatch, self._rows(cash_and_short_term="2026-08-31"))
        assert "cash_and_short_term" in result.values
        assert result.notes == []

    def test_without_cash_there_is_no_trailing_base(self, monkeypatch):
        rows = self._rows()
        del rows[M._TTM_SERIES["cash_and_short_term"]]
        assert self._fetch(monkeypatch, rows) is None

    def test_without_any_debt_there_is_no_trailing_base(self, monkeypatch):
        rows = self._rows()
        del rows[M._TTM_SERIES["current_debt"]]
        del rows[M._TTM_SERIES["long_term_debt"]]
        assert self._fetch(monkeypatch, rows) is None

    def test_without_revenue_there_is_no_trailing_base(self, monkeypatch):
        rows = self._rows()
        del rows[M._TTM_SERIES["revenue"]]
        assert self._fetch(monkeypatch, rows) is None

    def test_a_share_count_does_not_vote_on_the_currency(self, monkeypatch):
        """Yahoo tags share counts with a currency, which is meaningless on a
        count. Letting it vote would make a single-currency company look
        mixed and refuse a perfectly good base."""
        rows = self._rows()
        rows[M._TTM_SERIES["shares_diluted"]]["currencyCode"] = "EUR"
        result = self._fetch(monkeypatch, rows)
        assert result.currency == "USD"
