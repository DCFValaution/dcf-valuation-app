"""How loudly each caveat is said, and who decides.

The app used to decide by position. Whatever landed in the bottom list -
below the sensitivity table, under a heading reading "Keep in mind" - was a
footnote, and whatever was pinned under the figure was serious. That put
"terminal value is 109% of enterprise value" in the footnote tier and a
doubt about method fit beside the number, when both are the model saying
the figure does not mean what it looks like it means.

Severity is now decided here, once, and the app only renders it. The point
of these tests is that the decision is made on what the warning *is* rather
than on where it happens to be rendered, and that the two tiers which
change behaviour - serious, and the quiet tier - are not reachable by
accident.

Run with:  python -m pytest test_warning_tiers.py -v
"""

import pytest

import analysis as A
from analysis import (SEVERITY_CAUTION, SEVERITY_INFO, SEVERITY_SERIOUS,
                      Warning, most_severe, severity_of)


# --- The type ---------------------------------------------------------

class TestTheWarningCarriesItsSeverity:
    def test_it_is_still_a_string(self):
        """Fourteen callers treat these as strings - four spreadsheet
        exporters, two report formatters, the API. None of them had to
        change."""
        w = Warning("mind the gap", severity=SEVERITY_SERIOUS)
        assert isinstance(w, str)
        assert w == "mind the gap"
        assert w.upper() == "MIND THE GAP"
        assert f"{w}!" == "mind the gap!"

    def test_a_plain_string_is_read_as_caution(self):
        """Warnings built before this existed still have to land somewhere,
        and the middle tier is the one that neither shouts nor hides."""
        assert severity_of("an old warning") == SEVERITY_CAUTION

    def test_the_headline_falls_back_to_the_full_text(self):
        assert Warning("just this").headline == "just this"

    def test_detail_is_copied_not_shared(self):
        d = {"tv_pct": 1.09}
        w = Warning("x", detail=d)
        d["tv_pct"] = 0.0
        assert w.detail["tv_pct"] == 1.09


# --- Which one goes under the headline --------------------------------

class TestTheLoudestOne:
    def test_serious_outranks_caution(self):
        ws = [Warning("a", severity=SEVERITY_CAUTION),
              Warning("b", severity=SEVERITY_SERIOUS)]
        assert most_severe(ws) == "b"

    def test_caution_outranks_info(self):
        ws = [Warning("a", severity=SEVERITY_INFO),
              Warning("b", severity=SEVERITY_CAUTION)]
        assert most_severe(ws) == "b"

    def test_a_tie_goes_to_the_method_problem(self):
        """No amount of adjusting the inputs answers a complaint about what
        the model can see at all, so that one goes first."""
        ws = [Warning("input", severity=SEVERITY_SERIOUS, kind="negative_eq"),
              Warning("method", severity=SEVERITY_SERIOUS,
                      kind="dividends_only")]
        assert most_severe(ws) == "method"

    def test_nothing_in_means_nothing_out(self):
        assert most_severe([]) is None


# --- Terminal value: the two thresholds -------------------------------

class TestTerminalValueTiers:
    def test_the_thresholds_are_where_they_should_be(self):
        assert A.HIGH_TV_SHARE == pytest.approx(0.85)
        assert A.TV_EXCEEDS_FORECAST == pytest.approx(1.00)

    @pytest.mark.parametrize("share,severity,kind", [
        (0.80, None, None),
        (0.86, SEVERITY_CAUTION, "terminal_value_high"),
        (0.99, SEVERITY_CAUTION, "terminal_value_high"),
        (1.09, SEVERITY_SERIOUS, "terminal_value_over_100"),
    ])
    def test_the_tier_follows_the_share(self, share, severity, kind,
                                        monkeypatch):
        got = _tv_warning_for(share, monkeypatch)
        if severity is None:
            assert got is None, f"{share:.0%} should say nothing"
        else:
            assert got is not None, f"{share:.0%} should warn"
            assert severity_of(got) == severity
            assert got.kind == kind

    def test_a_share_above_one_says_the_forecast_is_worth_less_than_nothing(
            self, monkeypatch):
        w = _tv_warning_for(1.09, monkeypatch)
        assert "less than nothing" in w.headline
        assert w.detail["tv_pct"] == pytest.approx(1.09)

    def test_only_one_terminal_value_warning_is_ever_raised(self, monkeypatch):
        """The two thresholds are a chain, not two tests: a company above
        100% must not also collect the caution one."""
        for share in (0.86, 1.09):
            ws = _warnings_for(share, monkeypatch)
            tv = [w for w in ws if getattr(w, "kind", "").startswith(
                "terminal_value")]
            assert len(tv) == 1, f"{share:.0%} produced {len(tv)}"


# --- The quiet tier ---------------------------------------------------

def test_a_clamped_input_is_quiet():
    """It qualifies one assumption, not the answer."""
    from assumptions import Provenance
    prov = Provenance("capex_pct", 0.40, "derived (clamped)", "x",
                      raw=0.55, limit=0.40, why="range")
    from plain_language import clamp_warning
    w = Warning(clamp_warning(prov, "Test Co"), severity=SEVERITY_INFO,
                kind="clamped")
    assert severity_of(w) == SEVERITY_INFO


def test_the_three_levels_are_ordered():
    assert (A.SEVERITY_ORDER[SEVERITY_SERIOUS]
            < A.SEVERITY_ORDER[SEVERITY_CAUTION]
            < A.SEVERITY_ORDER[SEVERITY_INFO])


# --- Helpers ----------------------------------------------------------

def _fin():
    from market_data import CompanyFinancials
    income, balance, cashflow = [], [], []
    for i in range(3):
        revenue = 10_000e6 * (1.05 ** (2 - i))
        income.append({
            "fiscalYear": str(2025 - i), "revenue": revenue,
            "operatingIncome": revenue * 0.25,
            "incomeBeforeTax": revenue * 0.24,
            "incomeTaxExpense": revenue * 0.24 * 0.21,
            "effectiveTaxRate": 0.21, "interestExpense": 40e6,
            "weightedAverageShsOutDil": 1_000e6,
        })
        balance.append({
            "fiscalYear": str(2025 - i),
            "totalCurrentAssets": revenue * 0.40,
            "totalCurrentLiabilities": revenue * 0.25,
            "cashAndCashEquivalents": revenue * 0.10,
            "shortTermInvestments": 0.0, "totalInvestments": revenue * 0.10,
            "shortTermDebt": 0.0, "longTermDebt": 1_000e6,
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


def _warnings_for(tv_share, monkeypatch):
    """Assess a company whose terminal value is exactly `tv_share` of EV."""
    import assumptions as Asm
    from market_data import base_year_from

    monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.0507, "pinned"))
    fin = _fin()
    base, _ = base_year_from(fin)
    derived = Asm.derive_assumptions(fin)

    real_run = A.run_dcf

    def fake_run(b, a):
        result = real_run(b, a)
        object.__setattr__(result, "tv_pct_of_ev", tv_share)
        return result

    monkeypatch.setattr(A, "run_dcf", fake_run)
    return list(A.assess_suitability(fin, derived, base).warnings)


def _tv_warning_for(tv_share, monkeypatch):
    for w in _warnings_for(tv_share, monkeypatch):
        if getattr(w, "kind", "").startswith("terminal_value"):
            return w
    return None


# --- The real sites, not hand-built warnings --------------------------

class TestTheSeveritiesTheModelActuallyAssigns:
    """The tests above build Warnings by hand, which proves the machinery
    and not the decisions. These reach into the paths that make them."""

    def test_a_clamped_input_is_quiet_in_a_real_derivation(self, monkeypatch):
        import assumptions as Asm
        from market_data import base_year_from
        monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                            lambda tenor="year10": (0.0507, "pinned"))
        fin = _fin()
        # D&A far above its ceiling, so the clamp bites.
        for row in fin.cashflow:
            row["depreciationAndAmortization"] = 9_000e6
        base, _ = base_year_from(fin)
        ws = A.assess_suitability(fin, Asm.derive_assumptions(fin), base).warnings
        clamped = [w for w in ws if getattr(w, "kind", "") == "clamped"]
        assert clamped, "the clamp should have produced a warning"
        assert all(severity_of(w) == SEVERITY_INFO for w in clamped)

    def test_negative_equity_is_serious_in_a_real_derivation(self, monkeypatch):
        import assumptions as Asm
        from market_data import base_year_from
        monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                            lambda tenor="year10": (0.0507, "pinned"))
        fin = _fin()
        for row in fin.balance:
            row["longTermDebt"] = 5_000_000e6      # debt past the whole business
        base, _ = base_year_from(fin)
        ws = A.assess_suitability(fin, Asm.derive_assumptions(fin), base).warnings
        negative = [w for w in ws
                    if getattr(w, "kind", "") == "negative_equity"]
        assert negative, "a negative equity value should warn"
        assert severity_of(negative[0]) == SEVERITY_SERIOUS

    def test_buyback_blindness_is_serious_in_a_real_assessment(
            self, monkeypatch):
        """The dividend model cannot see the larger of the two numbers. That
        is the method being blind, not an input being uncertain."""
        import assumptions as Asm
        import ddm_assumptions as D
        from analysis import assess_ddm_suitability
        from ddm import DDMInputs
        from test_ddm_assumptions import STEADY, TODAY, bank, quarterly

        monkeypatch.setattr(Asm, "fetch_risk_free_rate",
                            lambda tenor="year10": (0.045, "pinned"))
        fin = bank(dividends_paid=-300e6, buybacks=-900e6)
        dividends = D.analyse_dividends(quarterly(STEADY), today=TODAY)
        derived = D.derive_ddm_assumptions(fin, dividends)

        ws = assess_ddm_suitability(
            fin, dividends, derived, DDMInputs(2.80, 50.0)).warnings
        blind = [w for w in ws if getattr(w, "kind", "") == "dividends_only"]
        assert blind, "buybacks above dividends should warn"
        assert severity_of(blind[0]) == SEVERITY_SERIOUS


# --- What the client is handed ----------------------------------------

class TestTheListTheApiBuilds:
    def _report(self, warnings=(), method_fit=(), base_notes=()):
        class _S:
            pass
        s, r = _S(), _S()
        s.warnings = list(warnings)
        s.method_fit = list(method_fit)
        r.suitability = s
        r.meta = {"base_notes": list(base_notes)}
        return r

    def test_base_year_notes_reach_the_client(self):
        """They were returned by the API and rendered nowhere. "Treated as
        nil" is a claim the model made on the reader's behalf."""
        from api import _warning_items
        items = _warning_items(self._report(
            base_notes=["no current debt reported, treated as nil"]))
        assert len(items) == 1
        assert items[0].severity == SEVERITY_INFO
        assert items[0].kind == "base_year"

    def test_a_method_fit_doubt_is_serious(self):
        from api import _warning_items
        items = _warning_items(self._report(method_fit=["the method may not fit"]))
        assert items[0].severity == SEVERITY_SERIOUS
        assert items[0].kind == "method_fit"

    def test_the_list_comes_back_loudest_first(self):
        from api import _warning_items
        items = _warning_items(self._report(
            warnings=[Warning("quiet", severity=SEVERITY_INFO),
                      Warning("middle", severity=SEVERITY_CAUTION),
                      Warning("loud", severity=SEVERITY_SERIOUS)],
            base_notes=["a note"]))
        assert [i.severity for i in items] == [
            SEVERITY_SERIOUS, SEVERITY_CAUTION, SEVERITY_INFO, SEVERITY_INFO]

    def test_a_method_problem_leads_a_tie(self):
        from api import _warning_items
        items = _warning_items(self._report(
            warnings=[Warning("input", severity=SEVERITY_SERIOUS,
                              kind="something_else")],
            method_fit=["method"]))
        assert items[0].kind == "method_fit"

    def test_a_warning_carries_its_headline_and_detail(self):
        from api import _warning_items
        items = _warning_items(self._report(warnings=[
            Warning("the long sentence", severity=SEVERITY_SERIOUS,
                    kind="terminal_value_over_100",
                    headline="the short one", detail={"tv_pct": 1.09})]))
        assert items[0].headline == "the short one"
        assert items[0].detail["tv_pct"] == pytest.approx(1.09)
