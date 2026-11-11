"""No test reaches the network unless it says it means to.

Two tests in test_terminal_growth.py took a synthetic `profitable` fixture,
never injected it, and then valued `analysis.value_company("GOOD")` - and
"GOOD" is a real ticker, Gladstone Commercial, a REIT. They had been
valuing a live company over the internet for as long as they had existed,
and they passed because that company happened to produce a figure. They
started failing only when a capex fix changed what Yahoo's data said about
it, which is the worst possible way to find out: a true change in the model
looked like two unrelated tests breaking.

A test that reaches the network is slow, is flaky, depends on a company's
results and on someone else's uptime, and - the real cost - silently stops
testing what it claims to test. So the offline suite cannot reach the
network at all. Anything that tries fails immediately and says what to do
about it.

Tests that genuinely want the network opt in, either by living in a
`*_live.py` file or by carrying `@pytest.mark.live`.
"""

import socket
import urllib.request

import pytest


class NetworkAccessDenied(RuntimeError):
    """Raised instead of letting an offline test reach out."""


_ADVICE = (
    "This test tried to reach the network.\n\n"
    "The offline suite must not: a test that depends on live market data "
    "is flaky, slow, and quietly stops testing what it says it tests when "
    "the data moves.\n\n"
    "Fix it by injecting a fake, as the other tests do:\n"
    "    monkeypatch.setattr(analysis, \"fetch_financials\", "
    "fake_fetch_financials)\n\n"
    "If this test really is meant to hit the network, move it into a "
    "*_live.py file or mark it @pytest.mark.live."
)


def _denied(*args, **kwargs):
    raise NetworkAccessDenied(_ADVICE)


def _denied_session():
    """A real session that refuses to make a request.

    Duck typing is not enough: yfinance checks the session's type and its
    `.cache` before using it, so a stand-in object fails the test with
    yfinance's error instead of this file's, hiding the reason. Subclassing
    the genuine session passes both checks and still goes nowhere.

    Constructing one performs no I/O - the handshake happens on the first
    request, which is exactly what is denied.
    """
    from curl_cffi import requests as creq

    class DeniedSession(creq.Session):
        request = _denied
        get = _denied
        post = _denied
        put = _denied
        head = _denied

    return DeniedSession()


# Loopback has to stay open. The asyncio event loop that FastAPI's TestClient
# runs on builds its self-pipe out of a real socket pair on Windows, so
# denying connect() outright takes down every API test with it - which is how
# the first version of this guard "found" 238 failures that were all itself.
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "", None}


def _is_loopback(address) -> bool:
    if isinstance(address, (tuple, list)) and address:
        host = address[0]
        return host in _LOOPBACK or str(host).startswith("127.")
    return False


def _guard(original):
    """Allow a call through to loopback, deny it anywhere else."""
    def guarded(*args, **kwargs):
        address = args[1] if len(args) > 1 else kwargs.get("address")
        if _is_loopback(address):
            return original(*args, **kwargs)
        raise NetworkAccessDenied(_ADVICE)
    return guarded


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: test deliberately reaches the network")


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Block the ways out of the process, for every test but the live ones."""
    if request.node.get_closest_marker("live"):
        return
    if request.path.name.endswith("_live.py"):
        return

    # The chokepoint: every Yahoo request in market_data goes through this
    # one shared session, and curl_cffi talks to the network below Python's
    # socket module, so the socket guards below would never see it.
    #
    # The session is handed out rather than refused, and only a request on
    # it is denied. Several tests fake yf.Ticker but still let
    # fetch_financials build the session it passes in and never uses -
    # refusing to hand one over would fail tests that never go anywhere.
    import market_data
    monkeypatch.setattr(market_data, "_browser_session",
                        _denied_session)

    # ...and the generic ways out, for anything that does not. Loopback is
    # left open so the event loop under the API tests can still build its
    # own plumbing.
    monkeypatch.setattr(socket.socket, "connect", _guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex",
                        _guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "create_connection",
                        _guard(socket.create_connection))
    monkeypatch.setattr(urllib.request, "urlopen", _denied)
