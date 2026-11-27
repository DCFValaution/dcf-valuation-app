"""Which app is asking - verified the same way, with no credential here.

An App Check token is an ordinary RS256 JWT signed by Google, so these
tests mint one with a throwaway key pair and hand the verifier the
matching public key. Same shape as the real thing with the network
removed, and the same reason it needs no secret: the project NUMBER is in
every copy of google-services.json that ships inside the app.

The property worth protecting is in TestEnforcementIsASeparateSwitch: the
server ships before the clients that send the header, so verifying and
REFUSING have to be two decisions. Turning them into one would refuse
every reader who had not updated yet.

Run with:  python -m pytest test_app_check.py -v
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import app_check as A

NUMBER = "123456789012"
KID = "app-check-key-1"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(autouse=True)
def _patch_keys(keypair, monkeypatch):
    _, public = keypair

    class FakeSigningKey:
        key = public

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey()

    monkeypatch.setattr("jwt.PyJWKClient", FakeClient)
    A.reset()
    monkeypatch.setenv(A.PROJECT_NUMBER_ENV, NUMBER)
    monkeypatch.delenv(A.ENFORCED_ENV, raising=False)
    yield
    A.reset()


def mint(keypair, *, number=NUMBER, sub="1:123:android:abc",
         issuer=None, exp_in=1800, iat_offset=0, **extra):
    private, _ = keypair
    now = int(time.time())
    claims = {
        "iss": issuer if issuer is not None else f"{A.ISSUER_PREFIX}{number}",
        "aud": extra.pop("aud", [f"projects/{number}"]),
        "sub": sub,
        "iat": now + iat_offset,
        "exp": now + iat_offset + exp_in,
        **extra,
    }
    return jwt.encode(claims, private, algorithm="RS256",
                      headers={"kid": KID})


class TestAGenuineToken:
    def test_is_accepted_and_names_the_app(self, keypair):
        got = A.verify(mint(keypair))
        assert got.app_id == "1:123:android:abc"

    def test_survives_the_header_wrapper(self, keypair):
        assert A.attest(mint(keypair)) is not None


class TestATokenThatIsNotOurs:
    def test_minted_for_another_project_is_refused(self, keypair):
        """A perfectly valid token for somebody else's project. This is
        the check that makes a token OURS rather than merely signed."""
        other = mint(keypair, number="999999999999")
        assert A.attest(other) is None

    def test_with_the_wrong_issuer_is_refused(self, keypair):
        assert A.attest(mint(keypair, issuer="https://example.com/")) is None

    def test_that_has_expired_is_refused(self, keypair):
        assert A.attest(mint(keypair, iat_offset=-7200, exp_in=60)) is None

    def test_with_no_subject_is_refused(self, keypair):
        assert A.attest(mint(keypair, sub="")) is None

    def test_that_is_not_a_token_at_all_is_refused(self):
        for rubbish in ("", "   ", "not-a-jwt", "a.b.c"):
            assert A.attest(rubbish) is None

    def test_an_absent_header_is_simply_absent(self):
        assert A.attest(None) is None


class TestEnforcementIsASeparateSwitch:
    def test_defaults_to_off_so_a_deploy_cannot_lock_anyone_out(self):
        """The server ships before the clients that send the header, and
        an app on an old version has no token to send."""
        assert A.enforced() is False

    def test_turns_on_from_the_dashboard(self, monkeypatch):
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv(A.ENFORCED_ENV, truthy)
            assert A.enforced() is True

    def test_anything_else_is_off(self, monkeypatch):
        for falsy in ("0", "false", "no", "", "maybe"):
            monkeypatch.setenv(A.ENFORCED_ENV, falsy)
            assert A.enforced() is False

    def test_verification_still_happens_while_enforcement_is_off(
            self, keypair):
        """Which is what makes the rollout observable: traffic arrives
        attested for a while before anything is refused."""
        assert A.attest(mint(keypair)) is not None


class TestAMisconfiguredVerifier:
    def test_without_the_project_number_cannot_run(self, monkeypatch):
        monkeypatch.delenv(A.PROJECT_NUMBER_ENV, raising=False)
        with pytest.raises(A.AppCheckUnavailable):
            A.verify("anything")

    def test_and_attest_reports_it_as_unattested_rather_than_crashing(
            self, keypair, monkeypatch):
        """Logged loudly, because it is our misconfiguration - but the
        request still gets an answer."""
        monkeypatch.delenv(A.PROJECT_NUMBER_ENV, raising=False)
        assert A.attest(mint(keypair)) is None
