"""Headers that cost nothing and close off whole classes of attack.

None of these defend against a flaw in this service. They stop a browser
being talked into misusing a correct response - framing a page whose whole
output is a number someone might act on, sniffing JSON as HTML, or carrying
the URL a user came from to an upstream host.
"""

import pytest
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client():
    api.reset_rate_limits()
    return TestClient(api.app)


@pytest.mark.parametrize("path", ["/health", "/valuation/NOPE", "/search?q=a"])
def test_every_response_carries_them(client, path):
    """Set in middleware rather than per route, so a new endpoint cannot be
    added without them."""
    r = client.get(path)
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "geolocation=()" in r.headers["Permissions-Policy"]


def test_json_endpoints_permit_nothing(client):
    """An API that returns JSON has no origin to allow."""
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_the_docs_get_what_they_actually_need(client):
    """Swagger UI loads from a CDN, so the strict policy would blank it."""
    csp = client.get("/docs").headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'none'" in csp, "still never framed"


def test_an_error_response_is_not_an_exception(client):
    """A refusal is as framable as a success if the headers are skipped."""
    r = client.get("/valuation/NOPE")
    assert r.status_code >= 400
    assert r.headers["X-Frame-Options"] == "DENY"


class TestDiagnosticsGate:
    """The diagnostics endpoint reports Yahoo's raw answers to this machine.

    No credentials leak through it - there are none - but it does describe
    our upstream plumbing and will fetch any symbol a caller names, which
    makes it a free probe of someone else's service wearing our address.
    """

    def test_unset_means_disabled_not_open(self, client, monkeypatch):
        """Failing closed is the only safe default.

        A missing variable is far more likely to be an oversight than a
        decision to publish.
        """
        monkeypatch.setattr(api, "DIAGNOSTICS_TOKEN", "")
        r = client.get("/diagnostics/upstream?ticker=AAPL")
        assert r.status_code == 404
        assert r.json()["code"] == "diagnostics_disabled"

    def test_the_wrong_token_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(api, "DIAGNOSTICS_TOKEN", "the-real-one")
        r = client.get("/diagnostics/upstream?ticker=AAPL",
                       headers={"X-Diagnostics-Token": "not-it"})
        assert r.status_code == 401
        assert r.json()["code"] == "diagnostics_unauthorised"

    def test_no_token_at_all_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(api, "DIAGNOSTICS_TOKEN", "the-real-one")
        assert client.get("/diagnostics/upstream").status_code == 401

    def test_the_token_never_travels_in_the_url(self, client, monkeypatch):
        """A URL reaches server logs, browser history and Referer headers.

        A secret that reaches any of those is no longer one, so the query
        string must not be an accepted way to present it.
        """
        monkeypatch.setattr(api, "DIAGNOSTICS_TOKEN", "the-real-one")
        r = client.get("/diagnostics/upstream?token=the-real-one")
        assert r.status_code == 401


def test_health_says_only_that_it_is_up(client):
    """It is the load balancer's probe, not an inventory of the machine."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    for leaky in ("version", "python", "platform", "path", "env",
                  "hostname", "commit", "token"):
        assert leaky not in body, f"/health should not publish {leaky}"
