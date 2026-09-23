import asyncio

import pytest

from michael.gate.upstream import (
    GATEWAY_PROTOCOL,
    UpstreamConfig,
    fetch_ws_ticket,
    open_upstream_session,
    ws_url,
)


class _FakeHTTPError(Exception):
    """Stands in for httpx.HTTPStatusError without importing it here."""


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self._payload


class _FakeDashboard:
    """Models how the real Hermes dashboard actually behaves.

    This matters more than it looks. The previous fake returned the ticket
    payload for ANY url with an implied 200, so it could not tell the login
    route from the ticket route and had no notion of a session. The gate
    shipped sending `Authorization: Basic`, every unit test passed, and the
    mistake only surfaced the first time a real question was asked through the
    gate — the dashboard answered 401 and every socket closed 4503.

    So: the ticket route refuses until a password-login has happened.
    """

    def __init__(self, payload: object, *, already_signed_in: bool = False) -> None:
        self._payload = payload
        self.signed_in = already_signed_in
        self.calls: list[str] = []
        self.login_body: object = None

    async def post(
        self, url: str, *, json: object = None, headers: dict[str, str] | None = None
    ) -> _FakeResponse:
        self.calls.append(url)
        if url.endswith("/auth/password-login"):
            self.login_body = json
            self.signed_in = True
            return _FakeResponse({}, 200)
        if url.endswith("/api/auth/ws-ticket"):
            if not self.signed_in:
                return _FakeResponse({"detail": "unauthorised"}, 401)
            return _FakeResponse(self._payload, 200)
        return _FakeResponse({}, 404)


CONFIG = UpstreamConfig(
    base_url="http://michael-hermes.railway.internal:9119",
    username="michael",
    password="s3cret",
)


def test_subprotocol_matches_the_one_michael_js_requests() -> None:
    assert GATEWAY_PROTOCOL == "hermes-gateway-v1"


def test_ws_url_upgrades_http_to_ws() -> None:
    assert ws_url(CONFIG, "tkt").startswith("ws://michael-hermes.railway.internal:9119/api/ws")


def test_ws_url_upgrades_https_to_wss() -> None:
    secure = UpstreamConfig(base_url="https://example.invalid", username="u", password="p")
    assert ws_url(secure, "tkt").startswith("wss://example.invalid/api/ws")


def test_ws_url_percent_encodes_the_ticket() -> None:
    assert "ticket=a%2Fb%3Fc" in ws_url(CONFIG, "a/b?c")


def test_trailing_slash_on_base_url_does_not_double() -> None:
    trailing = UpstreamConfig(base_url="http://host:1/", username="u", password="p")
    assert "//api/ws" not in ws_url(trailing, "t").removeprefix("ws://")


@pytest.mark.parametrize("bad", ["", "ftp://host", "host-without-scheme"])
def test_an_unusable_base_url_is_refused_at_construction(bad: str) -> None:
    with pytest.raises(ValueError, match="must start with http"):
        ws_url(UpstreamConfig(base_url=bad, username="u", password="p"), "t")


def test_repr_does_not_leak_the_password() -> None:
    """The gate holds an all-or-nothing credential; a stray repr must not print it."""
    assert "s3cret" not in repr(CONFIG)
    assert "michael" in repr(CONFIG)  # the rest of the config is still useful in a log


def test_a_session_is_established_before_the_ticket_is_accepted() -> None:
    """The regression test for the defect that reached production: the ticket
    route refuses an unauthenticated caller, so the gate must sign in first."""
    client = _FakeDashboard({"ticket": "tkt-123"})

    ticket = asyncio.run(fetch_ws_ticket(CONFIG, client))  # type: ignore[arg-type]

    assert ticket == "tkt-123"
    assert client.calls == [
        "http://michael-hermes.railway.internal:9119/api/auth/ws-ticket",
        "http://michael-hermes.railway.internal:9119/auth/password-login",
        "http://michael-hermes.railway.internal:9119/api/auth/ws-ticket",
    ]


def test_the_login_sends_the_body_the_dashboard_expects() -> None:
    client = _FakeDashboard({"ticket": "t"})
    asyncio.run(fetch_ws_ticket(CONFIG, client))  # type: ignore[arg-type]

    assert client.login_body == {
        "provider": "basic",
        "username": "michael",
        "password": "s3cret",
    }


def test_a_client_that_already_has_a_session_does_not_sign_in_again() -> None:
    """One request, not two, for every turn after the first on a live client."""
    client = _FakeDashboard({"ticket": "tkt"}, already_signed_in=True)

    assert asyncio.run(fetch_ws_ticket(CONFIG, client)) == "tkt"  # type: ignore[arg-type]
    assert client.calls == [
        "http://michael-hermes.railway.internal:9119/api/auth/ws-ticket"
    ]


def test_open_upstream_session_posts_the_credential() -> None:
    client = _FakeDashboard({})
    asyncio.run(open_upstream_session(CONFIG, client))  # type: ignore[arg-type]

    assert client.signed_in
    assert client.calls == [
        "http://michael-hermes.railway.internal:9119/auth/password-login"
    ]


@pytest.mark.parametrize("bad_payload", [{}, {"ticket": ""}, {"ticket": 123}])
def test_fetch_ws_ticket_invalid_response(bad_payload: object) -> None:
    """Raises ValueError if the response carries no usable ticket."""
    client = _FakeDashboard(bad_payload, already_signed_in=True)
    with pytest.raises(ValueError, match="no ws ticket"):
        asyncio.run(fetch_ws_ticket(CONFIG, client))  # type: ignore[arg-type]
