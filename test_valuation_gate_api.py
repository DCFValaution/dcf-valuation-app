"""The free allowance, through a real request.

The units are tested elsewhere - gate.py decides, usage.py counts,
entitlement.py says what a plan allows. This file tests the wiring, which
is the part that was never exercised by any of those: that the dependency
runs on the right routes, that the refusal comes back with the shape the
app branches on, and above all that A FAILED VALUATION DOES NOT COUNT.

Firestore is replaced by a fake store; identity by a stub. Neither is an
optimisation - the offline suite cannot reach the network at all.

Run with:  python -m pytest test_valuation_gate_api.py -v
"""
import pytest
from fastapi.testclient import TestClient

import analysis
import api
import app_check
import assumptions as A
import entitlement as E
import identity as I
import usage as U
from api import app
from market_data import RateLimitedError
from test_api import LOSSMAKING, THROTTLED, make_fin

ANON_UID = "anon-install-1"


def any_company(ticker: str, years: int = 5):
    """A fake that knows every ticker.

    test_api's fake knows four companies by name, which is right for
    testing the model and wrong here: these tests need several DIFFERENT
    tickers, because one marker per (uid, ticker, day) is the whole of the
    counting rule and valuing the same company twice proves nothing.
    """
    name = ticker.upper()
    if name == LOSSMAKING:
        return make_fin(name, "Lossmaking Inc.", operating_margin=-0.60)
    if name == THROTTLED:
        raise RateLimitedError("The market data provider is rate-limiting.")
    return make_fin(name, f"{name} Corp.")


class FakeStore:
    """A counter that lives in a dict, with the same surface as the real one."""

    def __init__(self):
        self.project = "intrinsic-test"
        self.rows: dict[str, U.Usage] = {}
        self.markers: set[tuple[str, str]] = set()
        self.counts = 0
        self.fail_with: Exception | None = None

    def read(self, uid, *, id_token=None, app_check_token=None):
        if self.fail_with:
            raise self.fail_with
        return self.rows.get(uid, U.Usage())

    def count(self, uid, ticker, *, id_token=None, app_check_token=None,
              now=None, current=None):
        if self.fail_with:
            raise self.fail_with
        self.counts += 1
        marker = E.marker_id(ticker, now=now)
        before = self.rows.get(uid, U.Usage())
        if (uid, marker) in self.markers:
            return False, before          # already valued today
        self.markers.add((uid, marker))
        today = E.window_key(E.WINDOW_DAY, now=now)
        after = U.Usage(
            lifetime=before.lifetime + 1,
            day=today,
            day_count=(before.day_count + 1) if before.day == today else 1,
        )
        self.rows[uid] = after
        return True, after


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(api, "_usage", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(analysis, "fetch_financials", any_company)
    monkeypatch.setattr(A, "fetch_risk_free_rate",
                        lambda tenor="year10": (0.045, "pinned for test"))
    api.reset_rate_limits()
    api.reset_metering()
    for name in (E.ENV_ANON_LIMIT, E.ENV_FREE_DAILY, E.ENV_ANON_IP_CAP,
                 app_check.ENFORCED_ENV):
        monkeypatch.delenv(name, raising=False)


def signed_in_as(monkeypatch, provider, uid=ANON_UID):
    monkeypatch.setattr(
        I, "identify",
        lambda header, project=None: I.Identity(uid=uid, provider=provider))
    monkeypatch.setattr(
        api.identity, "identify",
        lambda header, project=None: I.Identity(uid=uid, provider=provider))


@pytest.fixture
def client():
    return TestClient(app)


def value(client, ticker="AAA"):
    return client.get(f"/valuation/{ticker}",
                      headers={"Authorization": "Bearer stub-token"})


# ---------------------------------------------------------------------------
# The three, then the gate
# ---------------------------------------------------------------------------

class TestAnAnonymousInstallation:
    def test_gets_three_and_is_refused_the_fourth(
            self, client, store, monkeypatch):
        signed_in_as(monkeypatch, "anonymous")

        for n, ticker in enumerate(["AAA", "BBB", "CCC"], start=1):
            r = value(client, ticker)
            assert r.status_code == 200, f"valuation {n} should be allowed"
            assert r.headers["X-Valuations-Remaining"] == str(3 - n)

        refused = value(client, "DDD")
        assert refused.status_code == 403
        body = refused.json()
        assert body["code"] == "valuation_limit"
        assert body["limit"] == 3
        assert body["window"] == "lifetime"
        assert body["sign_in_lifts_limit"] is True

    def test_is_told_how_many_are_left_on_every_response(
            self, client, store, monkeypatch):
        signed_in_as(monkeypatch, "anonymous")
        r = value(client, "AAA")
        assert r.headers["X-Plan"] == "anonymous"
        assert r.headers["X-Valuations-Limit"] == "3"
        assert r.headers["X-Valuations-Window"] == "lifetime"

    def test_the_same_company_twice_in_a_day_costs_one(
            self, client, store, monkeypatch):
        """The marker, through the whole stack: reopening a company,
        dragging a slider and switching views are all the same ticker on
        the same day."""
        signed_in_as(monkeypatch, "anonymous")
        value(client, "AAA")
        for _ in range(5):
            assert value(client, "AAA").status_code == 200
        assert store.rows[ANON_UID].lifetime == 1

    def test_a_different_limit_is_a_dashboard_edit(
            self, client, store, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "1")
        signed_in_as(monkeypatch, "anonymous")
        assert value(client, "AAA").status_code == 200
        assert value(client, "BBB").status_code == 403


class TestASignedInReader:
    def test_is_not_metered_and_gets_no_remaining_header(
            self, client, store, monkeypatch):
        signed_in_as(monkeypatch, "google.com", uid="google-user-1")
        for ticker in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
            r = value(client, ticker)
            assert r.status_code == 200
        assert r.headers["X-Plan"] == "free"
        assert r.headers["X-Valuations-Limit"] == "none"
        assert "X-Valuations-Remaining" not in r.headers
        assert store.counts == 0, "an unmetered caller need not be counted"

    def test_becomes_metered_the_day_a_daily_limit_is_configured(
            self, client, store, monkeypatch):
        monkeypatch.setenv(E.ENV_FREE_DAILY, "2")
        signed_in_as(monkeypatch, "google.com", uid="google-user-1")
        assert value(client, "AAA").status_code == 200
        assert value(client, "BBB").status_code == 200
        refused = value(client, "CCC")
        assert refused.status_code == 403
        assert refused.json()["window"] == "day"
        assert refused.json().get("sign_in_lifts_limit") is None


# ---------------------------------------------------------------------------
# The property that matters most
# ---------------------------------------------------------------------------

class TestAFailedValuationDoesNotCount:
    def test_a_company_the_model_refuses_costs_nothing(
            self, client, store, monkeypatch):
        """A refusal is a 422, not a 200, so the middleware never counts
        it. Spending one of three on "we cannot value this company" would
        be the most annoying possible bug."""
        signed_in_as(monkeypatch, "anonymous")
        refused = value(client, LOSSMAKING)
        assert refused.status_code != 200
        assert store.counts == 0
        assert ANON_UID not in store.rows

        # And the three are all still there.
        for ticker in ["AAA", "BBB", "CCC"]:
            assert value(client, ticker).status_code == 200

    def test_an_upstream_failure_costs_nothing(
            self, client, store, monkeypatch):
        from market_data import RateLimitedError

        def throttled(*args, **kwargs):
            raise RateLimitedError("Yahoo says no")

        signed_in_as(monkeypatch, "anonymous")
        monkeypatch.setattr(analysis, "fetch_financials", throttled)
        assert value(client, "AAA").status_code == 429
        assert store.counts == 0


# ---------------------------------------------------------------------------
# Degrading rather than breaking
# ---------------------------------------------------------------------------

class TestWhenTheCounterIsDown:
    def test_the_valuation_still_happens(self, client, store, monkeypatch):
        signed_in_as(monkeypatch, "anonymous")
        store.fail_with = U.UsageUnavailable("Firestore is having a day")
        r = value(client, "AAA")
        assert r.status_code == 200
        assert r.headers["X-Valuations-Degraded"] == "1"

    def test_and_keeps_happening_past_the_limit(
            self, client, store, monkeypatch):
        """A Firebase incident must not become "nobody can value
        anything"."""
        signed_in_as(monkeypatch, "anonymous")
        store.fail_with = U.UsageUnavailable("down")
        for ticker in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
            assert value(client, ticker).status_code == 200

    def test_but_the_per_address_cap_still_bites(
            self, client, store, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "2")
        signed_in_as(monkeypatch, "anonymous")
        store.fail_with = U.UsageUnavailable("down")
        assert value(client, "AAA").status_code == 200
        assert value(client, "BBB").status_code == 200
        refused = value(client, "CCC")
        assert refused.status_code == 403
        assert refused.json()["code"] == "anonymous_ip_limit"


# ---------------------------------------------------------------------------
# What is not metered
# ---------------------------------------------------------------------------

class TestPublicRoutes:
    def test_health_is_never_gated(self, client, store, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "0")
        signed_in_as(monkeypatch, "anonymous")
        assert client.get("/health").status_code == 200

    def test_search_is_never_gated(self, client, store, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "0")
        signed_in_as(monkeypatch, "anonymous")
        assert client.get("/search", params={"q": "app"}).status_code != 403

    def test_assumptions_is_never_gated(self, client, store, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "0")
        signed_in_as(monkeypatch, "anonymous")
        assert client.get("/assumptions").status_code == 200


# ---------------------------------------------------------------------------
# App Check
# ---------------------------------------------------------------------------

class TestAppCheck:
    def test_is_not_enforced_by_default(self, client, store, monkeypatch):
        """The server ships before the clients that send the header."""
        signed_in_as(monkeypatch, "anonymous")
        assert value(client, "AAA").status_code == 200

    def test_refuses_an_unattested_request_once_enforced(
            self, client, store, monkeypatch):
        monkeypatch.setenv(app_check.ENFORCED_ENV, "1")
        signed_in_as(monkeypatch, "anonymous")
        r = value(client, "AAA")
        assert r.status_code == 403
        assert r.json()["code"] == "app_check_required"

    def test_accepts_an_attested_request(self, client, store, monkeypatch):
        monkeypatch.setenv(app_check.ENFORCED_ENV, "1")
        signed_in_as(monkeypatch, "anonymous")
        monkeypatch.setattr(
            api.app_check, "attest",
            lambda header, number=None: app_check.Attestation(app_id="1:x"))
        assert value(client, "AAA").status_code == 200
