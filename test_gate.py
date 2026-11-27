"""The decision, and the backstop behind it.

`gate.check` is pure, which is the point of it existing separately: every
branch here is a rule somebody will want to change, and a rule that needs
a network to test is a rule that gets tested less.

The property that matters most is in TestTheCounterBeingDownDoesNotStopTheApp.
A Firebase incident must degrade to "a few uncounted valuations", never to
"nobody can value anything".

Run with:  python -m pytest test_gate.py -v
"""
from datetime import datetime, timezone

import pytest

import entitlement as E
import gate as G
import identity as I

NOON = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TOMORROW = datetime(2026, 11, 26, 0, 1, tzinfo=timezone.utc)

IP = "203.0.113.7"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (E.ENV_ANON_LIMIT, E.ENV_FREE_DAILY, E.ENV_ANON_IP_CAP):
        monkeypatch.delenv(name, raising=False)
    G.reset()
    yield
    G.reset()


def anon():
    return E.resolve(I.Identity(uid="anon-1", provider="anonymous"))


def signed_in():
    return E.resolve(I.Identity(uid="user-1", provider="google.com"))


# ---------------------------------------------------------------------------
# The ordinary path
# ---------------------------------------------------------------------------

class TestAnAnonymousInstallation:
    def test_is_allowed_while_it_has_some_left(self):
        allowance, refusal = G.check(anon(), used=2, address=IP, now=NOON)
        assert refusal is None
        assert allowance.remaining == 1
        assert allowance.limit == 3

    def test_is_refused_on_the_fourth(self):
        allowance, refusal = G.check(anon(), used=3, address=IP, now=NOON)
        assert refusal is not None
        assert refusal.code == "valuation_limit"
        assert refusal.limit == 3
        assert refusal.window == E.WINDOW_LIFETIME
        assert refusal.sign_in_lifts_limit is True
        assert allowance.remaining == 0

    def test_the_refusal_says_signing_in_is_free(self):
        """The gate's whole job is to make the next step obvious. A
        refusal that does not say the fix is free reads as a paywall."""
        _, refusal = G.check(anon(), used=3, address=IP, now=NOON)
        assert "free" in refusal.message.lower()
        assert "google" in refusal.message.lower()


class TestASignedInReader:
    def test_is_never_refused_while_unmetered(self):
        _, refusal = G.check(signed_in(), used=10_000, address=IP, now=NOON)
        assert refusal is None

    def test_is_not_subject_to_the_per_address_cap(self):
        """Somebody else behind the same NAT, campus or carrier must
        never cost an attributable reader their valuation."""
        for _ in range(100):
            G.record_anonymous_ip(IP, now=NOON)
        _, refusal = G.check(signed_in(), used=0, address=IP, now=NOON)
        assert refusal is None

    def test_is_refused_once_a_daily_limit_is_configured(self, monkeypatch):
        monkeypatch.setenv(E.ENV_FREE_DAILY, "3")
        _, refusal = G.check(signed_in(), used=3, address=IP, now=NOON)
        assert refusal is not None
        assert refusal.window == E.WINDOW_DAY
        # Not a sign-in prompt: they are signed in.
        assert refusal.sign_in_lifts_limit is False
        assert "midnight utc" in refusal.message.lower()


# ---------------------------------------------------------------------------
# The per-address cap
# ---------------------------------------------------------------------------

class TestThePerAddressCap:
    def test_counts_only_what_it_is_told_to_count(self):
        assert G.anonymous_ip_used(IP, now=NOON) == 0
        G.record_anonymous_ip(IP, now=NOON)
        G.record_anonymous_ip(IP, now=NOON)
        assert G.anonymous_ip_used(IP, now=NOON) == 2

    def test_refuses_an_address_over_the_cap(self, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "2")
        G.record_anonymous_ip(IP, now=NOON)
        G.record_anonymous_ip(IP, now=NOON)
        _, refusal = G.check(anon(), used=0, address=IP, now=NOON)
        assert refusal is not None
        assert refusal.code == "anonymous_ip_limit"
        assert refusal.sign_in_lifts_limit is True

    def test_is_checked_before_the_allowance(self, monkeypatch):
        """A caller over the address cap but with valuations left should
        be told about the network, not told to sign in because they are
        out - the second message would simply be false."""
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "1")
        G.record_anonymous_ip(IP, now=NOON)
        _, refusal = G.check(anon(), used=0, address=IP, now=NOON)
        assert refusal.code == "anonymous_ip_limit"

    def test_resets_at_utc_midnight_without_anything_running(self):
        G.record_anonymous_ip(IP, now=NOON)
        assert G.anonymous_ip_used(IP, now=NOON) == 1
        assert G.anonymous_ip_used(IP, now=TOMORROW) == 0

    def test_one_address_does_not_spend_anothers(self):
        G.record_anonymous_ip(IP, now=NOON)
        assert G.anonymous_ip_used("198.51.100.4", now=NOON) == 0

    def test_an_unset_cap_means_no_cap(self, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "")
        for _ in range(500):
            G.record_anonymous_ip(IP, now=NOON)
        _, refusal = G.check(anon(), used=0, address=IP, now=NOON)
        assert refusal is None


# ---------------------------------------------------------------------------
# Degraded
# ---------------------------------------------------------------------------

class TestTheCounterBeingDownDoesNotStopTheApp:
    def test_an_unreadable_counter_lets_the_valuation_through(self):
        _, refusal = G.check(anon(), used=0, address=IP, now=NOON,
                             degraded=True)
        assert refusal is None

    def test_even_when_the_last_known_count_was_over_the_limit(self):
        """`used` is not to be trusted in this state, so it is not used.
        Refusing on a number we have just admitted we cannot read would
        turn a Firebase incident into a broken app."""
        _, refusal = G.check(anon(), used=99, address=IP, now=NOON,
                             degraded=True)
        assert refusal is None

    def test_but_the_address_cap_still_applies(self, monkeypatch):
        """Degraded is not unlimited. This is the part that stops an
        outage being an invitation."""
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "2")
        G.record_anonymous_ip(IP, now=NOON)
        G.record_anonymous_ip(IP, now=NOON)
        _, refusal = G.check(anon(), used=0, address=IP, now=NOON,
                             degraded=True)
        assert refusal is not None
        assert refusal.code == "anonymous_ip_limit"

    def test_the_app_is_told_the_number_is_a_guess(self):
        allowance, _ = G.check(anon(), used=0, address=IP, now=NOON,
                               degraded=True)
        assert allowance.degraded is True
        assert allowance.headers()["X-Valuations-Degraded"] == "1"


# ---------------------------------------------------------------------------
# What the app is told
# ---------------------------------------------------------------------------

class TestTheCourtesyHeaders:
    def test_carry_the_count_for_a_metered_caller(self):
        allowance, _ = G.check(anon(), used=1, address=IP, now=NOON)
        headers = allowance.headers()
        assert headers["X-Valuations-Remaining"] == "2"
        assert headers["X-Valuations-Limit"] == "3"
        assert headers["X-Valuations-Window"] == "lifetime"
        assert headers["X-Plan"] == "anonymous"

    def test_say_none_rather_than_a_number_for_an_unmetered_caller(self):
        """"none" and a large number are different claims, and the app
        shows a pill for one and nothing for the other."""
        allowance, _ = G.check(signed_in(), used=5, address=IP, now=NOON)
        headers = allowance.headers()
        assert headers["X-Valuations-Limit"] == "none"
        assert "X-Valuations-Remaining" not in headers

    def test_omit_the_degraded_flag_when_everything_is_fine(self):
        allowance, _ = G.check(anon(), used=0, address=IP, now=NOON)
        assert "X-Valuations-Degraded" not in allowance.headers()

    def test_the_refusal_body_is_machine_readable(self):
        """The app branches on `code`, never on the wording."""
        _, refusal = G.check(anon(), used=3, address=IP, now=NOON)
        body = refusal.as_dict()
        assert body["code"] == "valuation_limit"
        assert body["limit"] == 3
        assert body["window"] == "lifetime"
        assert body["sign_in_lifts_limit"] is True
        assert isinstance(body["message"], str) and body["message"]
