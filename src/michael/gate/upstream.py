"""Talking to michael-hermes.

The gate holds the single Hermes basic_auth credential and presents it upstream.
A chat user never sees it, which is the point: the credential is all-or-nothing,
so it must not leave this process.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

#: web/michael.js requests this subprotocol; the upstream socket must match or
#: the gateway refuses the connection.
GATEWAY_PROTOCOL = "hermes-gateway-v1"

TICKET_PATH = "/api/auth/ws-ticket"
WS_PATH = "/api/ws"


@dataclass(frozen=True)
class UpstreamConfig:
    base_url: str
    username: str
    password: str = field(repr=False)


def _normalised_base(config: UpstreamConfig) -> str:
    if not config.base_url.startswith(("http://", "https://")):
        raise ValueError(
            f"upstream base_url must start with http:// or https://, "
            f"got {config.base_url!r}"
        )
    return config.base_url.rstrip("/")


def basic_auth_header(config: UpstreamConfig) -> str:
    raw = f"{config.username}:{config.password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def ws_url(config: UpstreamConfig, ticket: str) -> str:
    base = _normalised_base(config)
    scheme = "wss://" if base.startswith("https://") else "ws://"
    host = base.split("://", 1)[1]
    return f"{scheme}{host}{WS_PATH}?ticket={quote(ticket, safe='')}"


async def fetch_ws_ticket(config: UpstreamConfig, client: httpx.AsyncClient) -> str:
    """Obtain a single-use WebSocket ticket from the gateway."""
    response = await client.post(
        _normalised_base(config) + TICKET_PATH,
        headers={"Authorization": basic_auth_header(config)},
        json={},
    )
    response.raise_for_status()
    ticket = response.json().get("ticket")
    if not isinstance(ticket, str) or not ticket:
        raise ValueError("upstream returned no ws ticket")
    return ticket
