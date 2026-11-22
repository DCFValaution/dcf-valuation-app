"""No data provider is named to a reader, and never the wrong one.

WHAT WENT WRONG
---------------
The DCF spreadsheet said "Source: Financial Modeling Prep" on every
base-year figure, and "Generated from Financial Modeling Prep data" in its
notes. The backend stopped using that provider a long time before: the
figures came from somewhere else entirely.

That is worse than naming no one. A reader who takes the file at its word
and checks a revenue figure against that provider finds a different number
and has no way to tell which of the two is wrong - and the file is the
artefact people keep, email and act on, long after the screen it came from
is closed.

THE RULE
--------
Nothing a reader sees names the provider. Not the wrong one, and not the
right one either, which is the rule the app has held since
`provider_anonymity_test.dart`: a message on screen should say what the
figure is and when it was taken, not advertise whose API it came from. The
provider is swappable by design - that is the whole point of
`price_source.py` - so a name baked into a spreadsheet is a fact with an
expiry date on it.

WHAT IS STILL NAMED, ON PURPOSE
-------------------------------
Sources that are cited BECAUSE they are the authority, not because they are
a vendor: Damodaran's equity risk premium and industry betas, the US
Treasury's yield curve, the ECB's, the SEC's filings. Naming those is the
provenance doing its job - a reader who wants to check an equity risk
premium needs to know whose it is - and they are not swappable.

Two things are also deliberately out of scope, and both are operator
surfaces rather than reader surfaces:

  * `/health` reports `data_source` by name. That endpoint exists to answer
    "what is this server talking to", which is the question, and it is read
    by whoever is on call, not by someone valuing a company.
  * `/diagnostics/upstream` reports the provider's raw responses. Same.

Code comments and module docstrings are left alone too. They document
what the backend talks to for whoever maintains it, and no one reads them
by accident.

Run with:  python -m pytest test_provider_attribution.py -v
"""

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import analysis
import ddm_excel_export
import excel_export
import speculative_excel_export
from hypothetical import UserAssumptions
from test_api import PROFITABLE
# The synthetic companies, and the autouse stub that serves them, are
# borrowed from the speculative suite so every exporter here is exercised
# on a company it is actually meant for: a loss-maker for the speculative
# and hypothetical workbooks, a bank for the dividend discount one.
from test_speculative_api import (BANK, GROWER, SHRINKER, _stubbed,  # noqa: F401
                                  client)  # noqa: F401

# Vendors of market data. Any of these appearing in something a reader sees
# is either a false attribution or an unnecessary one, and both are bugs.
#
# "Yahoo" and "yfinance" are on the list although they ARE the current
# source: the rule is that the provider is not named to a reader, and the
# app has held that for its own strings since provider_anonymity_test.dart.
# Keeping them here means the two halves of the product agree.
FORBIDDEN = [
    "financial modeling prep", "financialmodelingprep", "fmp",
    "yahoo", "yfinance",
    "alpha vantage", "alphavantage", "iex", "quandl", "polygon",
    "tiingo", "twelve data", "twelvedata", "refinitiv", "bloomberg",
    "morningstar", "capital iq", "intrinio", "marketstack", "finnhub",
    "eodhd", "factset", "xignite", "barchart",
]

# Named on purpose. Listed so a future reader can see the distinction is
# deliberate rather than an oversight, and so a test that starts failing
# because provenance cites a new authority is read as a decision to make.
NAMED_ON_PURPOSE = ["damodaran", "treasury", "ecb", "sec", "edgar"]


def offenders(text: str) -> list[str]:
    """Which forbidden names appear in *text*, if any."""
    lowered = (text or "").lower()
    return [name for name in FORBIDDEN if name in lowered]


@pytest.fixture
def ddm_bank(monkeypatch):
    """A bank, valued with the dividend discount model.

    Its own stubs, applied after the speculative suite's autouse one, which
    deliberately explodes if dividends are fetched - that assertion exists
    to prove the speculative path never reaches the DDM, and it is right to
    keep. A bank simply needs different scaffolding.
    """
    import test_ddm_api as D

    monkeypatch.setattr(analysis, "fetch_financials", D.fake_financials)
    monkeypatch.setattr(analysis, "fetch_dividends", D.fake_dividends)
    real = D.D.analyse_dividends
    monkeypatch.setattr(analysis, "analyse_dividends",
                        lambda events: real(events, today=D.TODAY))
    report = analysis.value_company(D.BANK)
    assert report.method == "ddm", "fixture sanity"
    return report


def workbook_text(data: bytes) -> list[str]:
    """Every string in every cell of every sheet, as a reader would see it."""
    out = []
    for sheet in load_workbook(BytesIO(data)):
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    out.append(cell.value)
    return out


# ---------------------------------------------------------------------------
# The spreadsheets, which are the artefacts people keep
# ---------------------------------------------------------------------------

def test_the_dcf_workbook_names_no_provider():
    report = analysis.value_company(PROFITABLE)
    for text in workbook_text(excel_export.workbook_bytes(report)):
        assert not offenders(text), f"{offenders(text)} in: {text!r}"


def test_it_still_says_where_the_base_year_came_from_and_when():
    """Anonymity is not silence. A reader has to be able to tell a figure
    the model derived from a figure it was given, and how old it is."""
    report = analysis.value_company(PROFITABLE)
    texts = workbook_text(excel_export.workbook_bytes(report))

    attribution = [t for t in texts if "market data provider" in t.lower()]
    assert attribution, "the base-year figures must say whose they are"
    assert any("as at" in t for t in attribution), \
        "and when they were taken"


def test_the_speculative_workbook_names_no_provider(monkeypatch):
    report = analysis.value_company_speculatively(GROWER)
    for text in workbook_text(speculative_excel_export.speculative_workbook_bytes(report)):
        assert not offenders(text), f"{offenders(text)} in: {text!r}"


def test_the_hypothetical_workbook_names_no_provider():
    report = analysis.value_company_hypothetically(
        SHRINKER, UserAssumptions(0.08, 0.15, 5))
    for text in workbook_text(
            speculative_excel_export.hypothetical_workbook_bytes(report)):
        assert not offenders(text), f"{offenders(text)} in: {text!r}"


def test_the_ddm_workbook_names_no_provider(ddm_bank):
    for text in workbook_text(ddm_excel_export.ddm_workbook_bytes(ddm_bank)):
        assert not offenders(text), f"{offenders(text)} in: {text!r}"


# ---------------------------------------------------------------------------
# The responses the app renders
# ---------------------------------------------------------------------------

def _strings(value) -> list[str]:
    """Every string anywhere in a decoded JSON body."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.mark.parametrize("path, expect", [
    (f"/valuation/{PROFITABLE}", 200),
    (f"/valuation/{GROWER}", 422),
    (f"/valuation/{GROWER}/speculative", 200),
])
def test_no_response_a_reader_sees_names_a_provider(client, path, expect):
    r = client.get(path)
    assert r.status_code == expect, r.text
    for text in _strings(r.json()):
        assert not offenders(text), f"{offenders(text)} in: {text!r}"


def test_the_hypothetical_response_names_no_provider(client):
    r = client.get(f"/valuation/{SHRINKER}/hypothetical",
                   params={"revenue_growth": 0.08,
                           "target_operating_margin": 0.15,
                           "years_to_target": 5})
    assert r.status_code == 200, r.text
    for text in _strings(r.json()):
        assert not offenders(text), f"{offenders(text)} in: {text!r}"


# ---------------------------------------------------------------------------
# The distinction is deliberate
# ---------------------------------------------------------------------------

def test_a_cited_authority_is_not_caught_by_this_rule():
    """Damodaran, the Treasury and the ECB are named BECAUSE they are the
    authority, and this test would be a disaster if it stopped them."""
    for name in NAMED_ON_PURPOSE:
        assert not offenders(name), (
            f"{name!r} is cited on purpose and must not be forbidden")


def test_the_check_would_actually_catch_the_bug_it_exists_for():
    """The attribution that shipped, run through the same check. Without
    this, a typo in FORBIDDEN would make every test above pass vacuously."""
    assert offenders("Source: Financial Modeling Prep, 2026-11-22")
    assert offenders("Generated from Financial Modeling Prep data.")
    assert offenders("Price quoted by Yahoo Finance")
    assert not offenders("Reported by the market data provider, as at "
                         "2026-11-22")


def test_the_operator_endpoints_are_out_of_scope_on_purpose(client):
    """/health answers "what is this server talking to", which is the
    question, for whoever is on call. It is listed here so that someone
    tightening this rule finds the decision rather than the omission."""
    body = client.get("/health").json()
    assert "data_source" in body
    assert offenders(body["data_source"]), (
        "if this stops naming the source, the exemption above is stale and "
        "should be removed rather than left as dead reasoning")


# ---------------------------------------------------------------------------
# The modules that write the sentences
#
# The scans above only see what three fixture companies happen to produce. A
# warning that fires in a rarer state - a stale price, a missing currency -
# is never reached by them, and that is exactly where a provider name would
# survive unnoticed: mutation testing put one in a warning and every test
# above still passed.
#
# So the modules that write prose are read directly. Every string literal is
# checked EXCEPT docstrings, which are documentation for whoever maintains
# this; comments are skipped for free, because they are not in the parse
# tree at all. This mirrors the app's own sweep of lib/ in
# provider_anonymity_test.dart, and for the same reason.
#
# market_data.py, price_source.py and api.py are deliberately absent. The
# first two ARE the provider integration and say so throughout; api.py
# carries the operator endpoints described at the top of this file.
# ---------------------------------------------------------------------------

import ast  # noqa: E402

PROSE_MODULES = [
    "analysis.py", "assumptions.py", "hypothetical.py",
    "speculative_assumptions.py", "ddm_assumptions.py",
    "excel_export.py", "speculative_excel_export.py",
    "ddm_excel_export.py", "relative_excel_export.py",
]


def renderable_strings(path: str):
    """(line, text) for every string literal that is not a docstring."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                docstrings.add(id(node.body[0].value))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings):
            yield node.lineno, node.value


@pytest.mark.parametrize("module", PROSE_MODULES)
def test_no_prose_module_can_name_a_provider(module):
    for line, text in renderable_strings(module):
        assert not offenders(text), \
            f"{module}:{line} names {offenders(text)}: {text!r}"


def test_that_scan_reads_something(monkeypatch):
    """A parse that silently found nothing would make the whole sweep
    vacuous, so the scan is made to prove it is reading real text."""
    found = list(renderable_strings("analysis.py"))
    assert len(found) > 200, len(found)
    assert any("valuation" in text.lower() for _line, text in found)


def test_it_skips_docstrings_and_comments(tmp_path):
    """Both say what the backend talks to, on purpose, for whoever
    maintains it. Only what could reach a reader is checked."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""A module docstring naming Yahoo."""\n'
        '# A comment naming Financial Modeling Prep.\n'
        'def f():\n'
        '    """A function docstring naming Bloomberg."""\n'
        '    return "a rendered string"\n',
        encoding="utf-8")

    texts = [text for _line, text in renderable_strings(str(sample))]
    assert texts == ["a rendered string"]
