"""
The data provider is not named in anything a person reads.

Not a secret - the code, the comments and /diagnostics all name it, because
answering "is the provider blocking this server?" needs the name. But a
message on screen should say what went wrong, not advertise whose API it came
from, and "the market data provider" is what a reader needs to know anyway.

These tests walk the real modules rather than a list of known strings, so a
message added later is covered without anyone remembering to come back here.

Run with:  python -m pytest test_provider_anonymity.py -v
"""

import ast
import pathlib

import pytest
from fastapi.testclient import TestClient

import analysis
import api
import market_data
from api import app
from test_relative_api import LOSSMAKING, PROFITABLE, _stubbed, client  # noqa: F401

FORBIDDEN = ("yahoo", "yfinance")

# Code, not prose: endpoint URLs, the library's own name, the keys this module
# looks data up by. Renaming these would rename what the server calls, not what
# anyone reads.
def _is_code(value: str) -> bool:
    return (value.startswith("http")
            or "/" in value
            or not any(c.isspace() for c in value))


# /health states the data source for whoever runs the server. It is not shown
# in the app, and an operator needs to know which service is being called.
ALLOWED = {
    "Yahoo Finance (yfinance)",
    # Labels in the /diagnostics probe, which reports on the provider by name
    # because that is the question it exists to answer.
    "yfinance income_stmt",
}

# The modules whose strings can reach a screen. api.py is excluded on purpose:
# its /diagnostics endpoint reports on the provider by name, which is its whole
# job, and /health states the data source for operators rather than users.
USER_FACING_MODULES = [
    "market_data.py", "analysis.py", "assumptions.py", "ddm_assumptions.py",
    "ddm.py", "dcf.py", "relative.py", "speculative.py",
    "speculative_assumptions.py", "hypothetical.py", "plain_language.py",
    "excel_export.py",
]

ROOT = pathlib.Path(__file__).resolve().parent


def message_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    """
    Every string literal in *path* that is not a docstring.

    Docstrings and comments are documentation for whoever maintains this, and
    are deliberately left naming the provider.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if ast.get_docstring(node) is not None and node.body:
                docstrings.add(id(node.body[0].value))

    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


@pytest.mark.parametrize("module", USER_FACING_MODULES)
def test_no_message_in_the_source_names_the_provider(module):
    offenders = [
        f"{module}:{line}: {value[:80]!r}"
        for line, value in message_literals(ROOT / module)
        if any(word in value.lower() for word in FORBIDDEN)
        and not _is_code(value) and value not in ALLOWED
    ]

    assert not offenders, "the provider is named in user-facing text:\n" + "\n".join(offenders)


def _assert_clean(payload, where):
    text = str(payload).lower()
    for word in FORBIDDEN:
        assert word not in text, f"{where} names the provider: {payload}"


def test_a_refusal_never_names_the_provider(client):
    _assert_clean(client.get(f"/valuation/{LOSSMAKING}").json(), "the DCF refusal")
    _assert_clean(client.get(f"/valuation/{LOSSMAKING}/speculative").json(),
                  "the speculative refusal")


def test_a_valuation_never_names_the_provider(client):
    body = client.get(f"/valuation/{PROFITABLE}").json()

    # Covers the note, the warnings and every assumption's provenance detail,
    # which is where the provider's name used to sit.
    _assert_clean(body, "a valuation")


def test_a_relative_view_never_names_the_provider(client):
    body = client.get(f"/valuation/{PROFITABLE}/relative").json()

    _assert_clean(body, "the relative view")


def test_the_peer_rule_still_explains_itself_without_the_name(client):
    body = client.get(f"/valuation/{PROFITABLE}/relative").json()
    block = body.get("relative_valuation") or {}
    rule = (block.get("peer_selection") or {}).get("rule", "")

    # The explanation has to survive losing the name: a reader should still
    # learn what makes these companies peers.
    assert "commonly watched alongside" in rule
    assert "same industry" in rule


def test_an_upstream_failure_reads_as_a_sentence_without_the_name(monkeypatch, client):
    def unavailable(*args, **kwargs):
        raise market_data.DataUnavailableError(
            "The market data provider did not return financial statements for "
            "'TEST' just now. This is usually temporary - try again shortly.")

    monkeypatch.setattr(analysis, "fetch_financials", unavailable)

    body = client.get("/valuation/TEST").json()

    _assert_clean(body, "an upstream failure")
    assert "market data provider" in body["message"]


def test_the_operator_endpoints_still_name_it():
    # The point is not secrecy: whoever runs this must be able to ask whether
    # the provider is blocking the server, and that question needs the name.
    with TestClient(app) as c:
        assert any(word in str(c.get("/health").json()).lower() for word in FORBIDDEN)

    source = (ROOT / "market_data.py").read_text(encoding="utf-8")
    assert "Yahoo" in source, "the code and its comments still say what this talks to"
