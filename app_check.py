"""Which APP is asking, as opposed to which person.

WHAT THIS IS FOR
----------------
The free allowance is three valuations per installation, and the decision
was made not to fingerprint devices - so reinstalling earns another three.
That is acceptable for a person and unacceptable at scale, and the
difference between the two is whether the caller is the real app at all.

Firebase App Check answers that. The app asks the platform to attest
itself - Play Integrity on Android, reCAPTCHA Enterprise on the web - and
Firebase mints a short-lived token saying "this came from a build of your
app on a genuine device". A script with a stolen ID token cannot get one.

NO SECRET HERE EITHER, FOR THE SAME REASON AS identity.py
---------------------------------------------------------
An App Check token is an ordinary RS256 JWT signed by Google and verified
against Google's PUBLIC keys. The only configuration is the project
NUMBER, which appears in every copy of google-services.json that ships
inside the app and is not a secret. So this backend still holds no Firebase
credential, and `verify` here is the same shape as `identity.verify`.

ENFORCEMENT IS A SEPARATE SWITCH, AND DEFAULTS OFF
--------------------------------------------------
Because the order of events matters. The server is deployed before the
clients that send the header are in everyone's hands, and an app on an
old version has no token to send. Turning enforcement on the moment the
code lands would refuse every reader who had not updated.

So: this module always VERIFIES a token that arrives, and the result is
always reported. Whether a missing or bad token REFUSES the request is
`APP_CHECK_ENFORCED`, flipped in the Render dashboard once the clients
have shipped and the logs show attested traffic arriving.

Firestore enforcement is configured in the Firebase console and is
separate again. Note that the API's own writes to Firestore are made with
the caller's ID token AND the caller's App Check token, forwarded from the
app - so turning Firestore enforcement on does not lock this backend out.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Google's public keys for App Check tokens, in JWK form.
JWKS_URL = "https://firebaseappcheck.googleapis.com/v1/jwks"

ISSUER_PREFIX = "https://firebaseappcheck.googleapis.com/"

#: The project NUMBER - the long numeric one, not the project id. It is in
#: google-services.json as `project_number` and is not a secret, but it is
#: environment-specific, so it lives in the dashboard.
PROJECT_NUMBER_ENV = "FIREBASE_PROJECT_NUMBER"

#: "1", "true" or "yes" to refuse unattested requests.
ENFORCED_ENV = "APP_CHECK_ENFORCED"

#: The header the app sends, which is also the header Google's own
#: services read, so the app can use one token for both.
HEADER = "X-Firebase-AppCheck"

LEEWAY_SECONDS = 30


class AppCheckUnavailable(RuntimeError):
    """Verification could not be performed, as opposed to failing.

    Same distinction as `identity.IdentityUnavailable`, for the same
    reason: a token that fails is someone else's problem and a verifier
    that cannot run is ours, and the two must not look alike in the logs.
    """


@dataclass(frozen=True)
class Attestation:
    """A verified App Check token."""

    #: The Firebase app id the token was minted for - which platform's
    #: build of the app this is. Useful in logs; not an identity.
    app_id: str


def project_number() -> str | None:
    return (os.environ.get(PROJECT_NUMBER_ENV) or "").strip() or None


def enforced() -> bool:
    return (os.environ.get(ENFORCED_ENV) or "").strip().lower() in {
        "1", "true", "yes", "on"}


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


def verify(token: str, *, number: str | None = None) -> Attestation:
    """The attestation *token* proves, or an exception.

    The audience check is the one that makes a token OURS: App Check
    tokens are minted for `projects/<number>`, and one minted for a
    different project is a perfectly good token that must be refused here.
    """
    import jwt

    audience = number or project_number()
    if not audience:
        raise AppCheckUnavailable(
            f"{PROJECT_NUMBER_ENV} is not set, so no App Check token can be "
            "verified. Set it in the Render dashboard.")

    signing_key = _jwk_client().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=[f"projects/{audience}"],
        issuer=f"{ISSUER_PREFIX}{audience}",
        leeway=LEEWAY_SECONDS,
        options={"require": ["exp", "iat", "aud", "iss", "sub"]},
    )

    app_id = (claims.get("sub") or "").strip()
    if not app_id:
        raise jwt.InvalidTokenError("the token carries no subject")
    return Attestation(app_id=app_id)


def attest(header_value: str | None, *,
           number: str | None = None) -> Attestation | None:
    """The attestation behind a header, or None. Never raises.

    None covers three different situations on purpose - no header, an
    unverifiable one, and a verifier that could not run - because the
    caller's decision is the same in all three and depends on `enforced()`
    rather than on which it was. The three are distinguished in the log,
    where the difference is actionable.
    """
    if not header_value or not header_value.strip():
        return None
    try:
        return verify(header_value.strip(), number=number)
    except AppCheckUnavailable as exc:
        log.warning("app-check: %s", exc)
        return None
    except Exception as exc:
        log.debug("app-check: token rejected (%s)", exc.__class__.__name__)
        return None
