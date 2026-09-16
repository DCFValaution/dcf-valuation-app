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
