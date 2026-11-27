"""The valuation counter, which the client cannot lower.

WHY THE SERVER WRITES THIS AND THE APP DOES NOT
-----------------------------------------------
A limit the client enforces is not a limit. So the count lives in
Firestore under the caller's own uid, and this module writes it from
behind the API.

WHY THERE IS STILL NO SERVICE-ACCOUNT KEY
-----------------------------------------
`identity.py` explains at length why this backend holds no Firebase
credential, and warns that one server-side Firestore write would add a
private key to Render. This is that write, and it does not need one.

The Firestore REST API accepts a Firebase ID TOKEN as a bearer credential,
and requests that carry one are evaluated against the Security Rules -
exactly as the app's own writes are. So the server verifies the caller's
token locally (no key), then writes to Firestore AS THAT CALLER, and the
rules decide what is allowed. An OAuth service-account token would bypass
the rules and need a key; an ID token does neither.

The caller's App Check token is forwarded on the same request, so turning
on App Check enforcement for Firestore does not lock this backend out.

WHAT STOPS THE CALLER WRITING IT THEMSELVES
-------------------------------------------
Nothing - and nothing needs to. The rules allow a user exactly two things
on their own counter: read it, and increment it by one in the same atomic
commit that creates a marker that does not already exist. They cannot
lower it, cannot delete it (except with their whole account), and cannot
create a marker without the matching +1. Writing it themselves spends
their own allowance, which is not an attack.

THE MARKER IS THE "WHAT COUNTS" RULE
------------------------------------
One marker per (uid, ticker, UTC day), created with an "it must not
already exist" precondition. The commit is atomic, so:

    marker did not exist  ->  created, counter +1, this was a valuation
    marker already there  ->  whole commit fails, counter untouched

which is why dragging a slider, switching to the relative view, reopening
the company or exporting it cost nothing: same ticker, same day, marker
already there.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

import entitlement

log = logging.getLogger(__name__)

API_ROOT = "https://firestore.googleapis.com/v1"

#: Firestore's name for the one database a Firebase project gets.
DATABASE = "(default)"

#: Short. This sits in the request path of a valuation the reader is
#: waiting on, and a counter that is slow to answer must not make the app
#: feel slow. A timeout here is handled, not fatal - see `count`.
TIMEOUT_SECONDS = 6

ENV_DISABLE = "USAGE_COUNTER_DISABLED"


class UsageUnavailable(RuntimeError):
    """The counter could not be read or written.

    Distinct from "the caller is over their limit", and treated very
    differently: see `api.py`, which lets the valuation through.
    """


@dataclass(frozen=True)
class Usage:
    """What the counter says about this caller."""

    lifetime: int = 0
    day: str | None = None
    day_count: int = 0

    def used(self, window: str, *, key: str) -> int:
        """How many count against *window*.

        A day counter whose stored day is not today reads as zero rather
        than being reset by a write. Nothing has to run at midnight, and
        two readers in different requests cannot race to reset it.
        """
        if window == entitlement.WINDOW_LIFETIME:
            return self.lifetime
        return self.day_count if self.day == key else 0


def disabled() -> bool:
    """Whether counting is switched off entirely.

    An escape hatch for the dashboard: if the counter is implicated in an
    incident, turning it off degrades to "everyone is unmetered" rather
    than to "nobody can value anything".
    """
    return (os.environ.get(ENV_DISABLE) or "").strip().lower() in {
        "1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Firestore's wire format
# ---------------------------------------------------------------------------
#
# Firestore REST wraps every value in a type tag. These two functions are
# the whole of the translation, kept together so the shape is readable in
# one place rather than inline at four call sites.


def _to_value(value) -> dict:
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, str):
        return {"stringValue": value}
    if value is None:
        return {"nullValue": None}
    raise TypeError(f"no Firestore mapping for {type(value).__name__}")


def _from_fields(fields: dict) -> dict:
    out = {}
    for key, wrapped in (fields or {}).items():
        if "integerValue" in wrapped:
            out[key] = int(wrapped["integerValue"])
        elif "stringValue" in wrapped:
            out[key] = wrapped["stringValue"]
        elif "booleanValue" in wrapped:
            out[key] = bool(wrapped["booleanValue"])
        else:
            out[key] = None
    return out


class UsageStore:
    """Reads and increments one caller's valuation counter."""

    def __init__(self, project: str, *, session: requests.Session | None = None,
                 root: str = API_ROOT, timeout: int = TIMEOUT_SECONDS) -> None:
        self.project = project
        self.root = root
        self.timeout = timeout
        self._session = session or requests.Session()

    # -- paths ------------------------------------------------------------

    @property
    def _documents(self) -> str:
        return (f"{self.root}/projects/{self.project}/databases/"
                f"{DATABASE}/documents")

    def _usage_path(self, uid: str) -> str:
        return (f"projects/{self.project}/databases/{DATABASE}/documents/"
                f"usage/{uid}")

    def _marker_path(self, uid: str, marker: str) -> str:
        return f"{self._usage_path(uid)}/valued/{marker}"

    # -- plumbing ---------------------------------------------------------

    def _headers(self, id_token: str, app_check_token: str | None) -> dict:
        headers = {"Authorization": f"Bearer {id_token}"}
        if app_check_token:
            # The same header Google's own services read, so one token
            # serves both this call and the app's direct Firestore writes.
            headers["X-Firebase-AppCheck"] = app_check_token
        return headers

    # -- reading ----------------------------------------------------------

    def read(self, uid: str, *, id_token: str,
             app_check_token: str | None = None) -> Usage:
        """The counter as it stands. A caller with no row has used nothing."""
        try:
            response = self._session.get(
                f"{self._documents}/usage/{uid}",
                headers=self._headers(id_token, app_check_token),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise UsageUnavailable(f"reading usage/{uid}: {exc}") from exc

        if response.status_code == 404:
            return Usage()
        if response.status_code != 200:
            raise UsageUnavailable(
                f"reading usage/{uid}: HTTP {response.status_code}")

        fields = _from_fields(response.json().get("fields", {}))
        return Usage(
            lifetime=int(fields.get("lifetime") or 0),
            day=fields.get("day"),
            day_count=int(fields.get("day_count") or 0),
        )

    # -- counting ---------------------------------------------------------

    def count(self, uid: str, ticker: str, *, id_token: str,
              app_check_token: str | None = None,
              now: datetime | None = None,
              current: Usage | None = None) -> tuple[bool, Usage]:
        """Count one valuation of *ticker*, unless it is already counted.

        Returns whether this call counted, and the counter afterwards.

        The two writes go in ONE commit: the counter's +1 and the marker's
        creation. Firestore applies a commit atomically, so there is no
        instant in which one exists without the other, and the rules
        require each to be accompanied by the other. A client cannot
        pre-create markers to dodge counting, and this server cannot
        increment without recording what for.
        """
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        marker = entitlement.marker_id(ticker, now=moment)
        today = entitlement.window_key(entitlement.WINDOW_DAY, now=moment)

        usage = current if current is not None else self.read(
            uid, id_token=id_token, app_check_token=app_check_token)

        after = Usage(
            lifetime=usage.lifetime + 1,
            day=today,
            day_count=(usage.day_count + 1) if usage.day == today else 1,
        )

        body = {
            "writes": [
                {
                    "update": {
                        "name": self._usage_path(uid),
                        "fields": {
                            "lifetime": _to_value(after.lifetime),
                            "day": _to_value(after.day),
                            "day_count": _to_value(after.day_count),
                            # Names the marker this +1 is for, so the
                            # rule on this document can require that the
                            # marker exists after the commit.
                            "last_marker": _to_value(marker),
                        },
                    },
                },
                {
                    "update": {
                        "name": self._marker_path(uid, marker),
                        "fields": {"ticker": _to_value(
                            ticker.strip().upper())},
                    },
                    # The dedupe, enforced by the database rather than by
                    # a read followed by a hopeful write.
                    "currentDocument": {"exists": False},
                },
            ],
        }

        try:
            response = self._session.post(
                f"{self._documents}:commit",
                headers=self._headers(id_token, app_check_token),
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise UsageUnavailable(f"counting {ticker} for {uid}: {exc}") from exc

        if response.status_code == 200:
            return True, after

        # 409 is Firestore's answer to a failed precondition, which here
        # means the marker was already there: this ticker was valued today
        # and does not count again. The counter is untouched, so what the
        # caller has used is what the read said.
        if response.status_code in (409, 412):
            return False, usage

        raise UsageUnavailable(
            f"counting {ticker} for {uid}: HTTP {response.status_code} "
            f"{response.text[:200]}")

    # -- deletion ---------------------------------------------------------

    def delete(self, uid: str, *, id_token: str,
               app_check_token: str | None = None) -> int:
        """Remove the counter and every marker under it.

        Used when an anonymous account is discarded after its holder turns
        out to have a Google account already. The app deletes its own
        usage with the rest of its data on the normal deletion path; this
        is for the uid the app is about to stop being.

        Returns how many documents were deleted.
        """
        headers = self._headers(id_token, app_check_token)
        deleted = 0

        # Markers first. Deleting the parent does not delete a
        # subcollection in Firestore - a document and its subcollections
        # are independent, and orphaned markers would be exactly the
        # "data we said we deleted" that the deletion page promises are
        # gone.
        page_token = None
        while True:
            params = {"pageSize": 300}
            if page_token:
                params["pageToken"] = page_token
            try:
                listing = self._session.get(
                    f"{self._documents}/usage/{uid}/valued",
                    headers=headers, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                raise UsageUnavailable(f"listing markers for {uid}: {exc}") from exc
            if listing.status_code == 404:
                break
            if listing.status_code != 200:
                raise UsageUnavailable(
                    f"listing markers for {uid}: HTTP {listing.status_code}")
            payload = listing.json()
            for document in payload.get("documents", []) or []:
                name = document.get("name")
                if not name:
                    continue
                self._session.delete(f"{self.root}/{name}", headers=headers,
                                     timeout=self.timeout)
                deleted += 1
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        try:
            response = self._session.delete(
                f"{self._documents}/usage/{uid}", headers=headers,
                timeout=self.timeout)
        except requests.RequestException as exc:
            raise UsageUnavailable(f"deleting usage/{uid}: {exc}") from exc
        if response.status_code in (200, 404):
            deleted += 1 if response.status_code == 200 else 0
            return deleted
        raise UsageUnavailable(
            f"deleting usage/{uid}: HTTP {response.status_code}")
