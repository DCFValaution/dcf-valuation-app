"""What a caller is allowed, now that there is something to allow.

The previous version of this module metered nobody and these tests said
so. It meters anonymous installations now, so the properties worth
protecting have changed, and the two that matter most are:

  - HOW you signed in decides the plan, not WHETHER. Every installation is
    signed in now, so "anonymous" has to mean the provider rather than the
    absence of a token.
  - A limit and the absence of a limit are different claims. None is not
    zero, and zero is not None. A code path that conflated them would give
    an unlimited allowance to exactly the caller it was meant to stop.

Run with:  python -m pytest test_entitlement.py -v
"""
from datetime import datetime, timezone

import pytest

import entitlement as E
import identity as I


def anonymous(uid="anon-1"):
    return I.Identity(uid=uid, provider="anonymous")


def google(uid="user-1", email="name@example.com"):
    return I.Identity(uid=uid, email=email, provider="google.com")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start from the compiled-in defaults, whatever the shell had."""
    for name in (E.ENV_ANON_LIMIT, E.ENV_FREE_DAILY, E.ENV_ANON_IP_CAP):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Which plan
# ---------------------------------------------------------------------------

class TestTheProviderDecides:
    def test_an_anonymous_installation_gets_three_for_its_lifetime(self):
        got = E.resolve(anonymous())
        assert got.plan == E.PLAN_ANONYMOUS
        assert got.valuations == 3
        assert got.window == E.WINDOW_LIFETIME
        assert got.metered is True

    def test_a_google_account_is_unmetered_for_now(self):
        got = E.resolve(google())
        assert got.plan == E.PLAN_FREE
        assert got.valuations is None
        assert got.window == E.WINDOW_DAY
        assert got.metered is False

    def test_the_window_is_a_day_even_while_the_limit_is_none(self):
        """The window is part of the plan whether or not it bites yet.
        If turning the limit on also changed which counter is read, the
        first day of metering would read a counter nobody had been
        writing."""
        assert E.resolve(google()).window == E.WINDOW_DAY

    def test_a_caller_we_could_not_verify_is_treated_as_anonymous(self):
        """Not as signed in. Verification that could not run must fail
        towards the smaller allowance, or a broken verifier becomes an
        unlimited one."""
        got = E.resolve(None)
        assert got.plan == E.PLAN_ANONYMOUS
        assert got.valuations == 3
        assert got.uid is None

    def test_the_subject_and_provider_travel_with_the_entitlement(self):
        got = E.resolve(google(uid="uid-123"))
        assert got.uid == "uid-123"
        assert got.provider == "google.com"

    def test_a_paid_plan_resolves_above_both(self):
        got = E.resolve(google(), subscription=E.PLAN_UNLIMITED)
        assert got.plan == E.PLAN_UNLIMITED
        assert got.valuations is None

    def test_an_unknown_plan_falls_back_to_free_not_to_unlimited(self):
        """A billing record this build does not understand must not
        silently grant more - and must not lock a paying customer out."""
        got = E.resolve(google(), subscription="platinum-2029")
        assert got.plan == E.PLAN_FREE
        assert got.valuations is None

    def test_a_paid_plan_outranks_being_anonymous(self):
        """Nonsensical today and cheap to get right: a subscription is
        attached to a uid, and if one ever is attached to an anonymous
        uid, charging them and metering them would be the worst answer."""
        got = E.resolve(anonymous(), subscription=E.PLAN_UNLIMITED)
        assert got.valuations is None


# ---------------------------------------------------------------------------
# The limits are configuration
# ---------------------------------------------------------------------------

class TestLimitsComeFromTheEnvironment:
    def test_the_anonymous_limit_can_be_changed_without_a_deploy(
            self, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "5")
        assert E.resolve(anonymous()).valuations == 5

    def test_the_signed_in_daily_limit_is_the_change_we_are_building_for(
            self, monkeypatch):
        """The whole point of the shape. Setting one variable turns
        signed-in free users into 3-a-day without touching code."""
        monkeypatch.setenv(E.ENV_FREE_DAILY, "3")
        got = E.resolve(google())
        assert got.valuations == 3
        assert got.window == E.WINDOW_DAY
        assert got.metered is True
        # And the paid plan above it still is not metered.
        assert E.resolve(google(), subscription=E.PLAN_UNLIMITED).valuations \
            is None

    def test_an_empty_value_means_no_limit_rather_than_the_default(
            self, monkeypatch):
        """`ANON_VALUATION_LIMIT=` is how you turn a limit OFF. If an
        empty value fell back to the default, there would be no way to
        unmeter anonymous callers from the dashboard in an incident."""
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "")
        assert E.resolve(anonymous()).valuations is None

    def test_a_typo_falls_back_to_the_default_rather_than_crashing(
            self, monkeypatch):
        """Refusing to start is the one failure mode worse than the limit
        being wrong."""
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "three")
        assert E.resolve(anonymous()).valuations == 3

    def test_a_negative_limit_falls_back_rather_than_meaning_anything(
            self, monkeypatch):
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "-1")
        assert E.resolve(anonymous()).valuations == 3

    def test_zero_is_honoured_because_it_is_a_real_answer(self, monkeypatch):
        """Zero means "metered, and you may make none" - the switch that
        stops anonymous valuations entirely. Different from empty."""
        monkeypatch.setenv(E.ENV_ANON_LIMIT, "0")
        got = E.resolve(anonymous())
        assert got.valuations == 0
        assert got.metered is True
        assert E.allows(got, used=0) is False

    def test_the_per_ip_cap_is_generous_because_it_catches_bystanders(self):
        """A tight per-address cap punishes the wrong people. Singapore's
        mobile carriers run carrier-grade NAT, and a school is one address
        for the whole building, so a reader who has valued nothing can meet
        this before their own three. The number is set where a household,
        a classroom or a small office will not reach it."""
        assert E.anonymous_ip_cap() >= 100

    def test_the_per_ip_cap_is_configurable_too(self, monkeypatch):
        assert E.anonymous_ip_cap() == 200
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "100")
        assert E.anonymous_ip_cap() == 100
        monkeypatch.setenv(E.ENV_ANON_IP_CAP, "")
        assert E.anonymous_ip_cap() is None


# ---------------------------------------------------------------------------
# Counting against the limit
# ---------------------------------------------------------------------------

class TestAllows:
    def test_no_limit_allows_anything(self):
        assert E.allows(E.resolve(google()), used=10_000) is True

    def test_the_boundary_is_the_limit_itself(self):
        who = E.resolve(anonymous())
        assert E.allows(who, used=2) is True
        assert E.allows(who, used=3) is False

    def test_remaining_never_goes_negative(self):
        """"-2 valuations left" is not something a reader can act on."""
        who = E.resolve(anonymous())
        assert who.remaining(0) == 3
        assert who.remaining(3) == 0
        assert who.remaining(9) == 0

    def test_remaining_is_none_when_there_is_no_limit(self):
        assert E.resolve(google()).remaining(500) is None


# ---------------------------------------------------------------------------
# Windows and markers
# ---------------------------------------------------------------------------

NOON = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class TestWindows:
    def test_a_lifetime_window_has_one_key_for_ever(self):
        assert E.window_key(E.WINDOW_LIFETIME, now=NOON) == "lifetime"

    def test_a_day_window_is_keyed_by_the_utc_date(self):
        assert E.window_key(E.WINDOW_DAY, now=NOON) == "2026-11-25"

    def test_the_day_is_utc_regardless_of_the_callers_clock(self):
        """A local-midnight reset would happen at a different moment for
        every reader, and the only honest way to show that in the app
        would be a timezone nobody asked about."""
        from datetime import timedelta
        tokyo = NOON.astimezone(timezone(timedelta(hours=9)))
        assert E.window_key(E.WINDOW_DAY, now=tokyo) == "2026-11-25"


class TestMarkers:
    def test_the_marker_is_the_whole_what_counts_rule(self):
        assert E.marker_id("AAPL", now=NOON) == "2026-11-25__AAPL"

    def test_the_same_company_the_same_day_is_the_same_marker(self):
        later = NOON.replace(hour=23, minute=59)
        assert E.marker_id("AAPL", now=NOON) == E.marker_id("AAPL", now=later)

    def test_a_new_utc_day_is_a_new_marker(self):
        tomorrow = NOON.replace(day=26)
        assert E.marker_id("AAPL", now=NOON) != E.marker_id("AAPL",
                                                            now=tomorrow)

    def test_case_and_padding_cannot_buy_a_second_valuation(self):
        """A ticker is case-insensitive to the reader, so `aapl` must not
        be a different company from AAPL."""
        assert E.marker_id(" aapl ", now=NOON) == E.marker_id("AAPL", now=NOON)
