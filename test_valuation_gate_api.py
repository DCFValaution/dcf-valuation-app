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


# ---------------------------------------------------------------------------
# Health says whether the accounts are configured at all
# ---------------------------------------------------------------------------

class TestHealthReportsConfiguration:
    """A missing environment variable is invisible from outside, and the
    free allowance degrades rather than breaks when it cannot attribute a
    caller - so a misconfigured instance looks healthy from every angle
    except the one nobody checks. Twice that cost a deploy cycle."""

    def test_says_when_firebase_is_not_configured(
            self, client, monkeypatch):
        monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
        body = client.get("/health").json()
        assert body["accounts"]["firebase_project_configured"] is False

    def test_says_when_it_is(self, client, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        body = client.get("/health").json()
        assert body["accounts"]["firebase_project_configured"] is True

    def test_reports_app_check_and_the_counter(self, client, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_NUMBER", "123456")
        monkeypatch.setenv(app_check.ENFORCED_ENV, "1")
        monkeypatch.setenv("USAGE_COUNTER_DISABLED", "1")
        accounts = client.get("/health").json()["accounts"]
        assert accounts["app_check_number_configured"] is True
        assert accounts["app_check_enforced"] is True
        assert accounts["usage_counter_disabled"] is True

    def test_never_reveals_the_values(self, client, monkeypatch):
        """Booleans only. This endpoint is public."""
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-secret-id")
        monkeypatch.setenv("FIREBASE_PROJECT_NUMBER", "999888777")
        raw = client.get("/health").text
        assert "intrinsic-secret-id" not in raw
        assert "999888777" not in raw

    def test_health_still_needs_no_upstream(self, client, monkeypatch):
        """It is the wake-up ping for a sleeping instance. Anything that
        reaches a network here is paid for by a user watching a spinner."""
        def boom(*args, **kwargs):
            raise AssertionError("health must not touch the network")

        monkeypatch.setattr(analysis, "fetch_financials", boom)
        assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# WHY a valuation went uncounted
# ---------------------------------------------------------------------------

class TestTheDegradedReason:
    """Three different faults all produce the same symptom - a valuation
    that works and is not counted - so from outside they are
    indistinguishable, and chasing them cost two deploy cycles. The
    category says which.

    It is a CATEGORY. This endpoint is public: no tokens, no uids, no
    exception text."""

    def reason(self, client):
        return client.get("/health").json()["accounts"]["last_degraded_reason"]

    def test_nothing_to_report_before_anything_happens(self, client):
        assert self.reason(client) is None

    def test_no_firebase_project(self, client, store, monkeypatch):
        monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
        monkeypatch.setattr(api, "_usage", lambda: None)
        signed_in_as(monkeypatch, "anonymous")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) == "no_project_id"

    def test_counter_switched_off(self, client, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        monkeypatch.setenv("USAGE_COUNTER_DISABLED", "1")
        monkeypatch.setattr(api, "_usage", lambda: None)
        signed_in_as(monkeypatch, "anonymous")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) == "counter_disabled"

    def test_the_caller_sent_no_token(self, client, store, monkeypatch):
        """The ordinary case for a reader whose anonymous sign-in has not
        happened yet. Not a fault, and must not be reported as one."""
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        monkeypatch.setattr(api.identity, "identify",
                            lambda header, project=None: None)
        monkeypatch.setattr(api.identity, "last_failure", lambda: "no_token")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) == "no_token"

    def test_googles_certificates_could_not_be_fetched(
            self, client, store, monkeypatch):
        """The third cause, and the one invisible from outside: the code
        is right, the token is right, and the server cannot reach the
        endpoint that proves it."""
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        monkeypatch.setattr(api.identity, "identify",
                            lambda header, project=None: None)
        monkeypatch.setattr(api.identity, "last_failure",
                            lambda: "unavailable")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) == "certs_unavailable"

    def test_a_token_that_does_not_verify(self, client, store, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        monkeypatch.setattr(api.identity, "identify",
                            lambda header, project=None: None)
        monkeypatch.setattr(api.identity, "last_failure", lambda: "rejected")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) == "token_invalid"

    def test_firestore_refused_the_read(self, client, store, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        signed_in_as(monkeypatch, "anonymous")
        store.fail_with = U.UsageUnavailable("permission denied")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) == "firestore_error"

    def test_a_counted_valuation_reports_nothing(
            self, client, store, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        signed_in_as(monkeypatch, "anonymous")
        assert value(client, "AAA").status_code == 200
        assert self.reason(client) is None

    def test_never_leaks_a_token_a_uid_or_an_error(
            self, client, store, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "intrinsic-test")
        signed_in_as(monkeypatch, "anonymous")
        store.fail_with = U.UsageUnavailable(
            "PERMISSION_DENIED for uid anon-install-1 with token eyJhbGciOi")
        value(client, "AAA")
        raw = client.get("/health").text
        assert "eyJhbGciOi" not in raw
        assert "anon-install-1" not in raw
        assert "PERMISSION_DENIED" not in raw
