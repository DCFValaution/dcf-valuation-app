"""Whether this caller may value another company, and the counting of it.

WHERE THIS SITS
---------------
`entitlement.py` says what a plan allows. `usage.py` reads and writes the
counter. This is the decision made from the two, plus the one defence that
belongs to neither: a per-address daily cap on signed-out traffic.

WHY A PER-IP CAP AS WELL
------------------------
The free allowance is per installation, and reinstalling earns another
three. That was a deliberate choice - the alternative is a device
fingerprint, which is a far worse thing to put in a privacy policy than a
limit someone can dodge. But "a person can dodge it" and "a script can
dodge it ten thousand times" are different problems, and this is the
answer to the second one.

It applies to SIGNED-OUT traffic only. A signed-in reader is attributable
and unmetered, and must never be throttled because somebody else behind
the same NAT, campus or carrier was not.

COUNTING HAPPENS AFTER THE VALUATION SUCCEEDS
---------------------------------------------
Not before. A valuation that fails upstream - Yahoo throttling us, a
company EDGAR has nothing for - must not spend one of three. The check and
the count are therefore separate steps with the work in between.

A COUNTER THAT IS DOWN DOES NOT STOP THE APP
--------------------------------------------
If Firestore cannot be reached, `allowed` returns True and says why. The
alternative is that a Firebase incident becomes "nobody can value
anything", which is a worse outcome than a few uncounted valuations. The
per-IP cap still applies, so the degraded mode is not unlimited.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import entitlement

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The per-address daily cap on signed-out valuations
# ---------------------------------------------------------------------------
#
# In process, like the request rate limiter beside it in api.py, and for
# the same reasons: one instance, no new dependency, and losing the counts
# on a restart is acceptable for a backstop. If this ever runs on more
# than one instance it becomes per-instance, which is a weakening to
# notice rather than a correctness bug.

_lock = threading.Lock()

#: Keyed by a SALTED HASH of the address, never the address itself.
#:
#: The cap needs to tell two addresses apart; it does not need to know
#: what they are. Hashing costs nothing and means a memory dump, a
#: traceback or a future `print` of this table cannot leak somebody's IP -
#: and it means the privacy policy can say we do not store addresses
#: without having to qualify it.
#:
#: The salt is random per process and never written down, so the hashes
#: are not comparable across restarts and cannot be reversed by trying
#: every address in the world. That the table is lost on restart is fine:
#: this is a daily backstop, not an audit trail.
_ip_counts: dict[str, tuple[str, int]] = {}

_SALT = secrets.token_bytes(32)


def _key(address: str) -> str:
    """A stable, unreversible handle for an address, for today only."""
    return hashlib.blake2s(
        address.encode("utf-8", "replace"), key=_SALT, digest_size=16
    ).hexdigest()

#: Keep the table from growing without bound as addresses come and go.
_MAX_TRACKED = 20_000


def reset() -> None:
    """Forget every address. For tests, which share a process."""
    with _lock:
        _ip_counts.clear()


def anonymous_ip_used(address: str, *, now: datetime | None = None) -> int:
    """How many signed-out valuations this address has made today."""
    today = entitlement.window_key(entitlement.WINDOW_DAY, now=now)
    with _lock:
        day, count = _ip_counts.get(_key(address), (today, 0))
        return count if day == today else 0


def record_anonymous_ip(address: str, *, now: datetime | None = None) -> int:
    """Count one signed-out valuation against this address. Returns the total."""
    today = entitlement.window_key(entitlement.WINDOW_DAY, now=now)
    handle = _key(address)
    with _lock:
        day, count = _ip_counts.get(handle, (today, 0))
        count = count + 1 if day == today else 1
        if len(_ip_counts) >= _MAX_TRACKED and handle not in _ip_counts:
            # Drop the oldest-looking entries rather than refuse to track.
            # Imprecise on purpose: this is a backstop, and a backstop that
            # consumes memory without bound is a worse bug than one that
            # occasionally forgets an address.
            for stale in list(_ip_counts)[: _MAX_TRACKED // 10]:
                _ip_counts.pop(stale, None)
        _ip_counts[handle] = (today, count)
        return count


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """Why a valuation was not allowed, in terms the app can act on."""

    code: str
    message: str
    limit: int | None = None
    window: str | None = None
    sign_in_lifts_limit: bool = False

    def as_dict(self) -> dict:
        body = {"code": self.code, "message": self.message}
        if self.limit is not None:
            body["limit"] = self.limit
        if self.window:
            body["window"] = self.window
        if self.sign_in_lifts_limit:
            body["sign_in_lifts_limit"] = True
        return body


@dataclass
class Allowance:
    """What this caller may do, and what to tell them about it."""

    plan: str
    limit: int | None
    window: str
    remaining: int | None
    uid: str | None = None

    #: True when the counter could not be consulted, so `remaining` is a
    #: guess rather than a fact. The app is told, so it can hide the pill
    #: rather than show a number that may be wrong.
    degraded: bool = False

    def headers(self) -> dict[str, str]:
        """The courtesy count, as response headers.

        Headers rather than the response body on purpose: the entitlement
        is metadata about the request, not part of the valuation, and
        putting it in the body would mean adding a field to six Pydantic
        response models that have nothing to do with it.
        """
        out = {
            "X-Plan": self.plan,
            "X-Valuations-Window": self.window,
        }
        if self.limit is None:
            out["X-Valuations-Limit"] = "none"
        else:
            out["X-Valuations-Limit"] = str(self.limit)
        if self.remaining is not None:
            out["X-Valuations-Remaining"] = str(self.remaining)
        if self.degraded:
            out["X-Valuations-Degraded"] = "1"
        return out


def check(ent: entitlement.Entitlement, *, used: int, address: str,
          now: datetime | None = None,
          degraded: bool = False) -> tuple[Allowance, Refusal | None]:
    """Decide, without doing any I/O.

    Pure on purpose: every branch here is a rule somebody will want to
    change, and a rule that needs a network to test is a rule that gets
    tested less.
    """
    allowance = Allowance(
        plan=ent.plan,
        limit=ent.valuations,
        window=ent.window,
        remaining=ent.remaining(used),
        uid=ent.uid,
        degraded=degraded,
    )

    anonymous = ent.plan == entitlement.PLAN_ANONYMOUS

    # The per-address cap first, because it is the one that protects the
    # service rather than the business model, and because a caller over it
    # should be told that rather than told to sign in - signing in is
    # exactly what we want them to do, and it does lift this one too.
    if anonymous:
        cap = entitlement.anonymous_ip_cap()
        if cap is not None and anonymous_ip_used(address, now=now) >= cap:
            return allowance, Refusal(
                code="anonymous_ip_limit",
                message=(
                    "Free valuations from this network are used up for "
                    "today. Signing in with Google is free and lifts the "
                    "limit."),
                limit=cap,
                window=entitlement.WINDOW_DAY,
                sign_in_lifts_limit=True,
            )

    if degraded:
        # The counter could not be read. Let it through; the cap above is
        # still in force, so this is not unlimited.
        return allowance, None

    if entitlement.allows(ent, used=used):
        return allowance, None

    if anonymous:
        return allowance, Refusal(
            code="valuation_limit",
            message=(
                f"You have used your {ent.valuations} free valuations. "
                "Signing in with Google lifts the limit - it is free, and "
                "it keeps your work too."),
            limit=ent.valuations,
            window=ent.window,
            sign_in_lifts_limit=True,
        )

    return allowance, Refusal(
        code="valuation_limit",
        message=(
            f"You have used today's {ent.valuations} valuations. "
            "They reset at midnight UTC."),
        limit=ent.valuations,
        window=ent.window,
    )
