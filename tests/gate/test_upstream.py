import base64

import pytest

from michael.gate.upstream import (
    GATEWAY_PROTOCOL,
    UpstreamConfig,
    basic_auth_header,
    ws_url,
)

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
