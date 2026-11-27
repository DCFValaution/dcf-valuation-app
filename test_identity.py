"""Verifying who is asking, with no Firebase credential on this server.

A Firebase ID token is an ordinary RS256 JWT signed by Google, so
verification needs Google's PUBLIC keys and the project id and nothing
else. These tests mint tokens with a throwaway key pair and hand the
verifier the matching public key, which is exactly the shape of the real
thing with the network removed.

The property worth protecting above all others is in
TestAnUnverifiableTokenIsAnonymous: a request that cannot be verified is
treated as SIGNED OUT, never refused. Valuing a company without an account
is the product. Refusing a stale token would turn a sleeping phone or a
rotated key into a broken app for somebody who was entitled to use it
signed out anyway.

Run with:  python -m pytest test_identity.py -v
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import identity as I

PROJECT = "intrinsic-test"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(autouse=True)
def _patch_keys(keypair, monkeypatch):
    """Hand the verifier the public half, in place of Google's endpoint."""
    _, public = keypair

    class FakeSigningKey:
        key = public

    class FakeClient:
        def get_signing_key_from_jwt(self, token):
            header = jwt.get_unverified_header(token)
            if header.get("kid") != KID:
                raise jwt.PyJWKClientError(f"unknown kid {header.get('kid')}")
            return FakeSigningKey()

    monkeypatch.setattr(I, "_jwk_client", lambda: FakeClient())
    monkeypatch.setenv(I.PROJECT_ENV, PROJECT)
    yield
    I.reset()


def mint(keypair, *, project=PROJECT, sub="uid-123", email="name@example.com",
         issuer=None, exp_in=3600, iat_offset=0, kid=KID, **extra):
    private, _ = keypair
    now = int(time.time())
    claims = {
        "iss": issuer if issuer is not None else f"{I.ISSUER_PREFIX}{project}",
        "aud": extra.pop("aud", project),
        "sub": sub,
        "iat": now + iat_offset,
        "exp": now + iat_offset + exp_in,
        "email": email,
        "email_verified": True,
        **extra,
    }
    return jwt.encode(claims, private, algorithm="RS256",
                      headers={"kid": kid})


# ---------------------------------------------------------------------------
# The happy path, and the three claims that make a token ours
# ---------------------------------------------------------------------------

class TestAGoodToken:
    def test_it_yields_the_subject_and_the_email(self, keypair):
        who = I.verify(mint(keypair))
        assert who.uid == "uid-123"
        assert who.email == "name@example.com"
        assert who.email_verified is True

    def test_the_subject_is_what_everything_is_keyed_on(self, keypair):
        """Not the email. An email can change hands; a Firebase subject
        cannot, and the saved data lives under it."""
        who = I.verify(mint(keypair, sub="uid-abc", email="other@example.com"))
        assert who.uid == "uid-abc"


class TestTheClaimsThatMakeItOurs:
    def test_a_token_for_another_firebase_project_is_refused(self, keypair):
        """A perfectly valid token, signed by Google, for somebody else's
        app. Google signs millions of these, and both the issuer and the
        audience name the project - so this is refused on whichever is
        checked first, and the test says only that it IS refused."""
        token = mint(keypair, project="someone-elses-app")
        with pytest.raises(jwt.InvalidTokenError):
            I.verify(token)

    def test_the_audience_is_checked_in_its_own_right(self, keypair):
        """Isolated from the issuer, which would otherwise be the only
        thing this suite proved. A build that passed `audience=` the wrong
        value - or dropped it - would still satisfy every other test
        here."""
        token = mint(keypair, aud="another-project",
                     issuer=f"{I.ISSUER_PREFIX}{PROJECT}")
        with pytest.raises(jwt.InvalidAudienceError):
            I.verify(token)

    def test_the_issuer_must_be_firebase_for_this_project(self, keypair):
        token = mint(keypair, issuer="https://evil.example.com/")
        with pytest.raises(jwt.InvalidIssuerError):
            I.verify(token)

    def test_an_expired_token_is_refused(self, keypair):
        token = mint(keypair, exp_in=-10, iat_offset=-3600)
        with pytest.raises(jwt.ExpiredSignatureError):
            I.verify(token)

    def test_an_empty_subject_is_refused(self, keypair):
        with pytest.raises(jwt.InvalidTokenError):
            I.verify(mint(keypair, sub=""))

    def test_a_token_signed_by_the_wrong_key_is_refused(self, keypair):
        """The signature check is the whole foundation: without it every
        claim above is just text somebody typed."""
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        forged = jwt.encode(
            {"iss": f"{I.ISSUER_PREFIX}{PROJECT}", "aud": PROJECT,
             "sub": "uid-123", "iat": now, "exp": now + 3600},
            other, algorithm="RS256", headers={"kid": KID})
        with pytest.raises(jwt.InvalidSignatureError):
            I.verify(forged)

    def test_an_unsigned_token_is_refused(self, keypair):
        """The alg:none attack, which is the first thing anyone tries."""
        now = int(time.time())
        unsigned = jwt.encode(
            {"iss": f"{I.ISSUER_PREFIX}{PROJECT}", "aud": PROJECT,
             "sub": "uid-123", "iat": now, "exp": now + 3600},
            key="", algorithm="none")
        with pytest.raises(Exception):
            I.verify(unsigned)

    def test_a_small_clock_difference_is_forgiven(self, keypair):
        """Google's clock and this container's are not the same clock, and
        a phone that signed in four seconds in the future is not an
        attack."""
        assert I.verify(mint(keypair, iat_offset=10)).uid == "uid-123"


class TestWithoutAProjectId:
    def test_it_says_the_verifier_cannot_run_rather_than_failing_the_token(
            self, keypair, monkeypatch):
        """A misconfiguration of ours and a bad token from someone else are
        completely different events, and must not look the same."""
        monkeypatch.delenv(I.PROJECT_ENV, raising=False)
        with pytest.raises(I.IdentityUnavailable):
            I.verify(mint(keypair))


# ---------------------------------------------------------------------------
# The property that matters most
# ---------------------------------------------------------------------------

class TestAnUnverifiableTokenIsAnonymous:
    """`identify` never raises and never refuses. Valuing without an
    account is the product."""

    def test_no_header_is_simply_anonymous(self):
        assert I.identify(None) is None
        assert I.identify("") is None

    def test_a_good_header_is_the_caller(self, keypair):
        who = I.identify(f"Bearer {mint(keypair)}")
        assert who is not None and who.uid == "uid-123"

    def test_the_scheme_is_checked_without_complaint(self, keypair):
        assert I.identify(f"Basic {mint(keypair)}") is None
        assert I.identify("Bearer") is None
        assert I.identify("Bearer    ") is None

    @pytest.mark.parametrize("why, make", [
        ("expired", lambda kp: mint(kp, exp_in=-10, iat_offset=-3600)),
        ("another project", lambda kp: mint(kp, project="elsewhere")),
        ("unknown signing key", lambda kp: mint(kp, kid="rotated-away")),
        ("not a token at all", lambda kp: "not.a.token"),
    ])
    def test_every_bad_token_is_anonymous_not_an_error(self, keypair, why, make):
        assert I.identify(f"Bearer {make(keypair)}") is None, why

    def test_a_missing_project_id_is_anonymous_too(self, keypair, monkeypatch):
        """Loudly in the log, quietly to the caller: a deploy that forgot
        the variable must not take the app down for everyone."""
        monkeypatch.delenv(I.PROJECT_ENV, raising=False)
        assert I.identify(f"Bearer {mint(keypair)}") is None


# ---------------------------------------------------------------------------
# How they signed in, which is now what the allowance depends on
# ---------------------------------------------------------------------------

class TestTheProviderClaim:
    """`firebase.sign_in_provider` decides the plan, so it has to come from
    the SIGNED part of the token. A client that could name its own provider
    could name its own allowance."""

    def test_an_anonymous_token_says_so(self, keypair):
        token = mint(keypair, firebase={"sign_in_provider": "anonymous"})
        who = I.verify(token, project=PROJECT)
        assert who.provider == "anonymous"
        assert who.is_anonymous is True

    def test_a_google_token_says_so(self, keypair):
        token = mint(keypair, firebase={"sign_in_provider": "google.com"})
        who = I.verify(token, project=PROJECT)
        assert who.provider == "google.com"
        assert who.is_anonymous is False

    def test_a_token_with_no_firebase_claim_has_no_provider(self, keypair):
        """Unknown, not anonymous. Guessing "anonymous" would hand a
        three-valuation limit to a signed-in reader; guessing the other way
        would hand an unlimited one to a signed-out one. Neither guess is
        acceptable, so the field is None and the resolver decides."""
        who = I.verify(mint(keypair), project=PROJECT)
        assert who.provider is None

    def test_a_malformed_firebase_claim_is_ignored_rather_than_fatal(
            self, keypair):
        for bad in ("anonymous", 42, [], {"sign_in_provider": ""},
                    {"sign_in_provider": 7}):
            token = mint(keypair, firebase=bad)
            assert I.verify(token, project=PROJECT).provider is None

    def test_the_provider_survives_identify_as_well_as_verify(self, keypair):
        token = mint(keypair, firebase={"sign_in_provider": "anonymous"})
        who = I.identify(f"Bearer {token}")
        assert who is not None and who.is_anonymous
