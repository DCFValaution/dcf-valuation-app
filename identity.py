"""Who is asking, when anybody is.

WHY THERE IS NO SERVICE-ACCOUNT KEY HERE
----------------------------------------
A Firebase ID token is an ordinary RS256 JWT signed by Google. Verifying
one needs Google's PUBLIC keys and the project id, both of which are
public - so this backend holds no Firebase credential at all. The Admin SDK
would do the same job and would want a service-account private key in the
environment, which is a secret to store, rotate and leak. The only thing it
would buy is the ability to WRITE to Firestore, and nothing here writes to
Firestore: the app talks to Firestore directly under per-user rules.

That is the whole reason the architecture is shaped the way it is, so it is
worth saying plainly: adding one server-side Firestore write would add a
private key to Render. Think twice before doing that.

WHAT AN UNSIGNED REQUEST MEANS
------------------------------
Anonymous, not refused. Valuing a company without an account is the
product; this exists so that the requests which DO carry a token can be
attributed - to a usage limit today's release does not enforce, and to a
plan it does not sell yet. Attribution has to exist before the meter does,
or turning the meter on becomes a breaking change.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not look the user up, create a record, or care whether they have
ever signed in before. It answers one question - whose token is this - and
the answer is a subject id and an email, because those are the two fields
the entitlement and the quota are keyed on.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Google publishes the public keys for Firebase ID tokens here, in JWK form.
# Public by definition: this is the endpoint every verifier on earth reads.
JWKS_URL = ("https://www.googleapis.com/service_accounts/v1/jwk/"
            "securetoken@system.gserviceaccount.com")

ISSUER_PREFIX = "https://securetoken.google.com/"

# The project id. Not a secret - it appears in the app's own config and in
# every token's audience - but it is environment-specific, so it lives in
# the Render dashboard rather than in this file.
PROJECT_ENV = "FIREBASE_PROJECT_ID"

# A small tolerance for clock skew between Google and this container.
LEEWAY_SECONDS = 30


class IdentityUnavailable(RuntimeError):
    """Verification could not be performed, as opposed to failing.

    Raised when the project id is unset or the key set cannot be fetched.
    The difference matters: a token that fails verification is someone
    else's problem, and a verifier that cannot run is ours.
    """


@dataclass(frozen=True)
class Identity:
    """The verified subject of a request."""

    uid: str
    email: str | None = None
    email_verified: bool = False

    # How they signed in: "anonymous", "google.com", and so on, read from
    # the `firebase.sign_in_provider` claim.
    #
    # This is what the entitlement is chosen by, so it matters that it
    # comes from the SIGNED part of the token. A client that could name
    # its own provider could name its own allowance.
    provider: str | None = None

    @property
    def is_anonymous(self) -> bool:
        return self.provider == "anonymous"


def project_id() -> str | None:
    return (os.environ.get(PROJECT_ENV) or "").strip() or None


# ---------------------------------------------------------------------------
# The key set
# ---------------------------------------------------------------------------
#
# PyJWKClient caches the keys and refetches when it meets an unknown key id,
# which is exactly the behaviour wanted: Google rotates, a token arrives
# signed by the new key, the client notices and refetches once.

_client_lock = threading.Lock()
_client = None


def _jwk_client():
    global _client
    with _client_lock:
        if _client is None:
            from jwt import PyJWKClient
            _client = PyJWKClient(JWKS_URL, cache_keys=True)
        return _client


def reset() -> None:
    """Drop the cached key client. For tests, and after a key rotation."""
    global _client
    with _client_lock:
        _client = None


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(token: str, *, project: str | None = None) -> Identity:
    """The identity *token* proves, or an exception.

    Checks the signature against Google's published keys, then the three
    claims that make a token OURS rather than merely valid: the issuer is
    Firebase for this project, the audience is this project, and the
    subject is non-empty. A token minted for a different Firebase project
    is a perfectly good token and is refused here.
    """
    import jwt

    audience = project or project_id()
    if not audience:
        raise IdentityUnavailable(
            f"{PROJECT_ENV} is not set, so no token can be verified. "
            "Set it in the Render dashboard.")

    signing_key = _jwk_client().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=f"{ISSUER_PREFIX}{audience}",
        leeway=LEEWAY_SECONDS,
        options={"require": ["exp", "iat", "aud", "iss", "sub"]},
    )

    uid = (claims.get("sub") or "").strip()
    if not uid:
        raise jwt.InvalidTokenError("the token carries no subject")

    firebase_claim = claims.get("firebase")
    provider = None
    if isinstance(firebase_claim, dict):
        raw = firebase_claim.get("sign_in_provider")
        provider = raw if isinstance(raw, str) and raw else None

    return Identity(
        uid=uid,
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        provider=provider,
    )


#: Why the most recent `identify` produced nothing. A CATEGORY, never a
#: token, a uid or a provider's message - this is read by a public
#: endpoint, and the whole point of a category is that it is safe to show.
#:
#: Verification failing on the server is invisible from outside: the
#: caller gets a perfectly good valuation that simply was not counted. So
#: the reason has to be recorded where it happens, or it cannot be
#: diagnosed without reading the logs of a machine nobody can reach.
_last_failure: str | None = None


def last_failure() -> str | None:
    """Why the last verification did not produce an identity, by category.

    `None` means the last one succeeded, or none has been attempted.
    """
    return _last_failure


def identify(authorization: str | None, *,
             project: str | None = None) -> Identity | None:
    """The caller behind an Authorization header, or None.

    Never raises for a bad token. A request that presents something
    unverifiable is treated as anonymous rather than refused, because
    anonymous already works: refusing would turn a stale token - a sleeping
    phone, a rotated key, a clock an hour out - into a broken app for
    somebody who was entitled to use it signed out anyway.

    The one thing that IS logged is a verifier that could not run, because
    that is a misconfiguration of ours and would otherwise look exactly
    like a quiet afternoon with no signed-in users.
    """
    global _last_failure

    if not authorization:
        _last_failure = "no_token"
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        _last_failure = "no_token"
        return None

    try:
        who = verify(token.strip(), project=project)
        _last_failure = None
        return who
    except IdentityUnavailable as exc:
        # The project id is unset, or Google's key endpoint could not be
        # reached. Ours to fix either way, which is why it is logged at
        # warning and the others are not.
        _last_failure = "unavailable"
        log.warning("identity: %s", exc)
        return None
    except Exception as exc:
        _last_failure = "rejected"
        log.debug("identity: token rejected (%s)", exc.__class__.__name__)
        return None
