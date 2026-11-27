"""
Cross-origin access, which the web (PWA) build depends on.

A browser will not let the web app read a response from this API unless the
API names its origin. These tests pin the three things that matter: an allowed
origin is answered, an unknown one is not, and a preflight is never throttled
(a 429 on a preflight surfaces in the browser as an unexplained CORS failure).

The Android app is not a browser and none of this applies to it.
"""

import pytest
from fastapi.testclient import TestClient

import api
from api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    api.reset_rate_limits()
    yield
    api.reset_rate_limits()


LOCAL = "http://localhost:8080"


def test_an_allowed_origin_may_read_the_response():
    r = client.get("/health", headers={"Origin": LOCAL})

    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == LOCAL


def test_a_phone_on_the_same_wifi_is_allowed():
    # The LAN address changes from network to network, so it is matched by
    # shape rather than listed.
    for origin in ("http://192.168.1.42:8080", "http://10.0.0.7:8080"):
        r = client.get("/health", headers={"Origin": origin})
        assert r.headers.get("access-control-allow-origin") == origin, origin


def test_an_unknown_origin_is_not_named_in_the_response():
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})

    # The request itself still succeeds - CORS is enforced in the browser -
    # but without this header the page cannot read what came back.
    assert "access-control-allow-origin" not in r.headers


def test_the_api_does_not_open_itself_to_every_origin():
    assert "*" not in api.ALLOWED_ORIGINS


def test_a_preflight_is_answered_for_a_post_endpoint():
    r = client.options(
        "/valuation/relative",
        headers={
            "Origin": LOCAL,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == LOCAL
    assert "POST" in r.headers["access-control-allow-methods"]


def test_a_preflight_is_never_rate_limited():
    headers = {"Origin": LOCAL, "Access-Control-Request-Method": "POST"}

    # Well past the per-minute limit for real requests.
    for _ in range(api.RATE_LIMIT_REQUESTS + 5):
        r = client.options("/valuation/relative", headers=headers)
        assert r.status_code == 200


def test_the_excel_filename_is_readable_by_the_web_app():
    # The export arrives as a download; without this the browser hides the
    # filename the API chose.
    r = client.get("/health", headers={"Origin": LOCAL})

    assert "Content-Disposition" in r.headers.get("access-control-expose-headers", "")


# ---------------------------------------------------------------------------
# The signed request, and the headers that carry the allowance
# ---------------------------------------------------------------------------
#
# These exist because of a real outage rather than a hypothetical one. Counting
# valuations per account made the client sign every request, which turned an
# ordinary GET into a preflighted one. `allow_headers` still said
# `["Content-Type"]`, the preflight was refused, and the browser never sent the
# request at all - so the web build reported "something went wrong while
# contacting the server" for every valuation while the API was perfectly
# healthy and curl saw nothing wrong with it.
#
# The tests above passed throughout. They preflighted `content-type`.

import io
import re
from pathlib import Path

import gate

APP_LIB = Path(__file__).parent / "app" / "lib"


def test_a_signed_valuation_is_allowed_through_the_preflight():
    r = client.options(
        "/valuation/AAPL",
        headers={
            "Origin": LOCAL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-firebase-appcheck",
        },
    )

    assert r.status_code == 200, (
        "the browser asked whether it may send the headers the app signs its "
        "requests with, and was told no, so no valuation can be made from a "
        "browser at all")
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "x-firebase-appcheck" in allowed


def test_the_page_can_read_the_allowance_it_was_sent():
    # Passing the preflight is only half of it: a response header the page is
    # not allowed to read is invisible to it even when the request succeeded,
    # which would leave the counter and the gate reading nothing.
    r = client.get("/health", headers={"Origin": LOCAL})

    exposed = r.headers.get("access-control-expose-headers", "").lower()
    for name in ("x-plan", "x-valuations-limit", "x-valuations-remaining",
                 "x-valuations-window", "x-valuations-degraded"):
        assert name in exposed, f"the page cannot read {name}"


# ---------------------------------------------------------------------------
# The drift tests
# ---------------------------------------------------------------------------
#
# The two above would have caught this bug. They would not catch the NEXT one:
# the day somebody adds a third signed header to the Dart client, or a sixth
# allowance header to `gate`, and does not come back to `api.py`. The defect
# was never a wrong list - it was a list in one file falling out of step with
# code in another, and a test that restates the list by hand drifts with it.
#
# So these two read the source that actually produces the headers.

def _headers_the_client_sends() -> set[str]:
    """Every header name the Flutter client attaches to an API request.

    Read out of the Dart rather than restated here, so that adding one to
    `AuthedClient` and forgetting `api.py` fails in Python.
    """
    source = io.open(APP_LIB / "account_service.dart", encoding="utf-8").read()

    # Anchor on the getter that produces them, not on a header name. Reading
    # the whole file would pass for the wrong reason the first time a header
    # name appeared in a doc comment or an unrelated Firestore call - and this
    # file mentions "Authorization" in a comment four lines above the map.
    ANCHOR = "Map<String, String> get headers => {"
    assert ANCHOR in source, (
        "AuthTokens.headers has moved or been renamed; this test can no "
        "longer see what the client sends, so fix it rather than deleting it")
    block = source[source.index(ANCHOR) + len(ANCHOR):]
    block = block[:block.index("};")]

    found = set(re.findall(r"'([A-Za-z][A-Za-z0-9-]*)'\s*:", block))
    assert found, "no header names found - has the signing block moved?"
    return found


def test_every_header_the_client_sends_is_allowed():
    allowed = {name.lower() for name in api.ALLOWED_REQUEST_HEADERS}

    for name in _headers_the_client_sends():
        assert name.lower() in allowed, (
            f"the Flutter client sends {name!r} and api.ALLOWED_REQUEST_HEADERS "
            f"does not list it, so the browser will refuse the preflight and "
            f"every signed request will fail as a network error")


def test_every_allowance_header_the_api_sets_is_readable():
    # Ask `gate` itself what it writes, in both shapes: a metered plan, which
    # carries a limit and a remaining count, and an unmetered one, which does
    # not. A header that only appears in one of them is the easiest to miss.
    produced: set[str] = set()
    for allowance in (
        gate.Allowance(plan="anonymous", window="lifetime", limit=3,
                       remaining=2, uid="u"),
        gate.Allowance(plan="unlimited", window="lifetime", limit=None,
                       remaining=None, uid="u", degraded=True),
    ):
        produced |= set(allowance.headers())

    exposed = {name.lower() for name in api.EXPOSED_RESPONSE_HEADERS}
    for name in produced:
        assert name.lower() in exposed, (
            f"gate sets {name!r} and api.EXPOSED_RESPONSE_HEADERS does not "
            f"list it, so the page is sent it and cannot read it")
