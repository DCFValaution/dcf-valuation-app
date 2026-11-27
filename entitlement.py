"""What a caller is allowed, resolved per request.

THE SHAPE IS THE POINT
----------------------
`Entitlement` is a record, not a flag, and a limit is a pair - a number and
a WINDOW - rather than a bare count. That pair is what lets the two cases
this app has coexist without a special case anywhere:

    anonymous   3 valuations, window "lifetime"
    signed in   no limit,     window "day"

and it is what makes the change you will want later - signed-in free users
metered at 3 a day, with a paid plan above them - a different number in a
different environment variable rather than a different code path.

A LIMIT THE CLIENT ENFORCES IS NOT A LIMIT
------------------------------------------
So the counter is keyed on the verified `uid` from the ID token and lives
behind the API, in `usage.py`. The app may show a remaining count for
courtesy; it is never the thing that decides.

EVERY CALLER NOW HAS A uid
--------------------------
This is the change from the first version of this file. It used to say an
anonymous caller has no stable key and must be governed by the per-IP rate
limit instead. That is no longer true: the app signs every installation in
anonymously with Firebase before it asks for anything, so a signed-out
reader still arrives with a verified subject id. Anonymous auth is what
makes a per-installation allowance possible at all.

It is a per-INSTALLATION allowance, not a per-person one, and the
difference is not papered over: reinstalling the app or opening a private
window earns another three, and the deliberate decision is to accept that
rather than to fingerprint the device. The defences against someone doing
it at scale are App Check and a per-IP daily cap, neither of which is this
module's job.

CONFIGURATION, NOT CODE
-----------------------
The three numbers below are read from the environment at call time, so
changing one is a Render dashboard edit and a restart rather than a
deploy - the same way `PRICE_SOURCE` and `ALLOWED_ORIGINS` already work.
Read at call time rather than at import so a test can set one without
reimporting the module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

#: Counted once per installation, for ever. What an anonymous caller gets.
WINDOW_LIFETIME = "lifetime"

#: Counted per UTC day. What a signed-in caller gets, even while the limit
#: is None - the window is part of the plan whether or not it bites yet, so
#: turning the limit on does not also change which counter is read.
WINDOW_DAY = "day"

PLAN_ANONYMOUS = "anonymous"
PLAN_FREE = "free"
PLAN_UNLIMITED = "unlimited"

#: Firebase's name for the provider on an anonymous sign-in, as it appears
#: in the `firebase.sign_in_provider` claim.
PROVIDER_ANONYMOUS = "anonymous"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENV_ANON_LIMIT = "ANON_VALUATION_LIMIT"
ENV_FREE_DAILY = "FREE_DAILY_VALUATIONS"
ENV_ANON_IP_CAP = "ANON_IP_DAILY_CAP"

DEFAULT_ANON_LIMIT = 3
DEFAULT_FREE_DAILY: int | None = None      # None means no limit
#: Deliberately generous, because this cap catches the WRONG PEOPLE when
#: it is tight. Singapore's mobile carriers put large numbers of customers
#: behind one carrier-grade NAT address, and a school or office network is
#: one address for the whole building - so a reader who has valued nothing
#: at all can meet this before their own three. At 20 that was likely; this
#: is set where a household, a classroom or a small office will not reach
#: it, and a script running flat out still will.
DEFAULT_ANON_IP_CAP = 200


def _env_int(name: str, default: int | None) -> int | None:
    """An integer from the environment, or *default*.

    An empty value means "no limit", which is why this returns None rather
    than falling back to the default for it: `FREE_DAILY_VALUATIONS=` is
    how you turn a limit OFF, and it must not quietly mean "use 3".

    A value that is not a number is ignored and logged by the caller
    rather than crashing the service. A typo in the dashboard should not
    take the API down, and refusing to start would be the one failure mode
    worse than the limit being wrong.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def anonymous_ip_cap() -> int | None:
    """How many anonymous valuations one address may make in a UTC day.

    Signed-out traffic only. This is the backstop behind the per-
    installation allowance: reinstalling earns another three, so without
    this the limit is a speed bump for anyone willing to script it.
    """
    return _env_int(ENV_ANON_IP_CAP, DEFAULT_ANON_IP_CAP)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entitlement:
    """What this caller may do."""

    plan: str = PLAN_ANONYMOUS

    # None means no limit. Not zero, and not a large number standing in for
    # infinity: a limit that exists and a limit that does not are different
    # claims, and the API says which.
    valuations: int | None = None
    window: str = WINDOW_LIFETIME

    saves_limit: int | None = None
    watchlist_limit: int | None = None

    # Who this belongs to. None only when verification could not run at
    # all, which is a degraded mode rather than a normal one.
    uid: str | None = None
    provider: str | None = None

    @property
    def metered(self) -> bool:
        """Whether a valuation limit is actually enforced for this caller."""
        return self.valuations is not None

    def remaining(self, used: int) -> int | None:
        """How many are left, or None when there is no limit.

        Never negative. A caller who is somehow over the limit is shown
        zero rather than a negative number, because "-2 valuations left"
        is not a thing the reader can act on.
        """
        if self.valuations is None:
            return None
        return max(0, self.valuations - used)


def plans() -> dict[str, Entitlement]:
    """The plans this build knows about, with limits from the environment.

    Built per call rather than at import so that changing the environment
    changes the answer - which is what makes these numbers configuration.
    """
    return {
        PLAN_ANONYMOUS: Entitlement(
            plan=PLAN_ANONYMOUS,
            valuations=_env_int(ENV_ANON_LIMIT, DEFAULT_ANON_LIMIT),
            window=WINDOW_LIFETIME,
        ),
        PLAN_FREE: Entitlement(
            plan=PLAN_FREE,
            valuations=_env_int(ENV_FREE_DAILY, DEFAULT_FREE_DAILY),
            window=WINDOW_DAY,
        ),
        # Where a paid plan arrives. Present now so that selling one is a
        # billing webhook writing `subscriptions/{uid}` and nothing else.
        PLAN_UNLIMITED: Entitlement(
            plan=PLAN_UNLIMITED,
            valuations=None,
            window=WINDOW_DAY,
        ),
    }


def resolve(identity=None, *, subscription: str | None = None) -> Entitlement:
    """The entitlement for a caller.

    The plan comes from HOW they signed in, not from whether they did:
    every caller is signed in now, and an anonymous Firebase account is
    still an account. `firebase.sign_in_provider` is the discriminator and
    it is part of the verified token, so a client cannot choose its own
    plan by claiming to be signed in.

    A paying caller resolves above both. An unknown plan name resolves to
    the free plan rather than to unlimited - a billing record this build
    does not understand must not silently grant more - and never below it,
    so a paying customer is never locked out by a bad record.
    """
    table = plans()
    provider = getattr(identity, "provider", None)
    uid = getattr(identity, "uid", None)

    if subscription:
        plan = table.get(subscription, table[PLAN_FREE])
    elif identity is None or provider == PROVIDER_ANONYMOUS:
        plan = table[PLAN_ANONYMOUS]
    else:
        plan = table[PLAN_FREE]

    return replace(plan, uid=uid, provider=provider)


def allows(entitlement: Entitlement, *, used: int = 0) -> bool:
    """Whether this caller may make one more valuation."""
    limit = entitlement.valuations
    if limit is None:
        return True
    return used < limit


# ---------------------------------------------------------------------------
# Windows, as keys
# ---------------------------------------------------------------------------


def window_key(window: str, *, now: datetime | None = None) -> str:
    """The counter key for a window.

    UTC, deliberately. A local-midnight reset would mean the limit resets
    at a different moment for every reader and twice a year for some of
    them, and the only honest way to show that in the app would be a
    timezone the reader never asked about.
    """
    if window == WINDOW_LIFETIME:
        return WINDOW_LIFETIME
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).date().isoformat()


def marker_id(ticker: str, *, now: datetime | None = None,
              today: date | None = None) -> str:
    """The id of the "already counted this" marker for a ticker today.

    One valuation per (uid, ticker, UTC day), which is the whole of the
    "what counts" rule expressed as a document id. Reopening the same
    company, dragging a slider - which re-confirms against the backend
    when the drag ends - switching to the relative or speculative view,
    and exporting are all the same ticker on the same day, so they all
    land on a marker that already exists and cost nothing.

    Upper-cased because a ticker is case-insensitive to the reader and
    `aapl` must not buy a second valuation of AAPL.
    """
    day = today or (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc).date()
    return f"{day.isoformat()}__{ticker.strip().upper()}"
