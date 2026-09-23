import asyncio
import base64

import pytest

from michael.gate.upstream import (
    GATEWAY_PROTOCOL,
    UpstreamConfig,
    basic_auth_header,
    fetch_ws_ticket,
    ws_url,
)


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post(
        self, url: str, *, headers: dict[str, str], json: object
    ) -> _FakeResponse:
        self.calls.append((url, headers))
        return _FakeResponse(self._payload)


CONFIG = UpstreamConfig(
    base_url="http://michael-hermes.railway.internal:9119",
    username="michael",
    password="s3cret",
)


def test_subprotocol_matches_the_one_michael_js_requests() -> None:
    assert GATEWAY_PROTOCOL == "hermes-gateway-v1"


def test_basic_auth_header_is_well_formed() -> None:
    header = basic_auth_header(CONFIG)
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
    assert decoded == "michael:s3cret"


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


def test_fetch_ws_ticket_happy_path() -> None:
    """fetch_ws_ticket uses the correct URL, header, and returns the ticket."""
    client = _FakeClient({"ticket": "tkt-123"})
    ticket = asyncio.run(fetch_ws_ticket(CONFIG, client))  # type: ignore[arg-type]

    assert ticket == "tkt-123"
    assert len(client.calls) == 1
    url, headers = client.calls[0]
    assert url == "http://michael-hermes.railway.internal:9119/api/auth/ws-ticket"
    assert headers["Authorization"] == basic_auth_header(CONFIG)


@pytest.mark.parametrize("bad_payload", [{}, {"ticket": ""}, {"ticket": 123}])
def test_fetch_ws_ticket_invalid_response(bad_payload: object) -> None:
    """fetch_ws_ticket raises ValueError if the response has no usable ticket."""
    client = _FakeClient(bad_payload)
    with pytest.raises(ValueError, match="no ws ticket"):
        asyncio.run(fetch_ws_ticket(CONFIG, client))  # type: ignore[arg-type]
