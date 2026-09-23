"""Talking to michael-hermes.

The gate holds the single Hermes dashboard credential and signs in with it
upstream. A chat user never sees it, which is the point: the credential is
all-or-nothing, so it must not leave this process.

The dashboard authenticates API calls with a SESSION, not with HTTP Basic.
This module originally sent an `Authorization: Basic` header and was unit
tested against a fake client that returned a ticket unconditionally, so the
mistake survived every test and only surfaced the first time a real question
was asked through the gate: `/api/auth/ws-ticket` answered 401 and every
socket closed 4503.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

#: web/michael.js requests this subprotocol; the upstream socket must match or
#: the gateway refuses the connection.
GATEWAY_PROTOCOL = "hermes-gateway-v1"

LOGIN_PATH = "/auth/password-login"
TICKET_PATH = "/api/auth/ws-ticket"
#: The ticket alone authenticates the upstream socket -- verified against the
#: deployed dashboard, which accepts it with no session cookie attached.
WS_PATH = "/api/ws"


@dataclass(frozen=True)
class UpstreamConfig:
    base_url: str
    username: str
    password: str = field(repr=False)


def _normalised_base(config: UpstreamConfig) -> str:
    if not config.base_url.startswith(("http://", "https://")):
        raise ValueError(
            f"upstream base_url must start with http:// or https://, got {config.base_url!r}"
        )
    return config.base_url.rstrip("/")


def ws_url(config: UpstreamConfig, ticket: str) -> str:
    base = _normalised_base(config)
    scheme = "wss://" if base.startswith("https://") else "ws://"
    host = base.split("://", 1)[1]
    return f"{scheme}{host}{WS_PATH}?ticket={quote(ticket, safe='')}"


async def open_upstream_session(config: UpstreamConfig, client: httpx.AsyncClient) -> None:
    """Sign in to the Hermes dashboard, leaving its session cookies on `client`.

    The dashboard authenticates API calls with a session, not with HTTP Basic:
    posting the credential to this route sets `hermes_session_at` and friends,
    and `/api/auth/ws-ticket` reads those. `web/michael.js` did exactly this
    before the gate existed, which is the only reason the shape is known.
    """
    response = await client.post(
        _normalised_base(config) + LOGIN_PATH,
        json={"provider": "basic", "username": config.username, "password": config.password},
    )
    response.raise_for_status()


async def fetch_ws_ticket(config: UpstreamConfig, client: httpx.AsyncClient) -> str:
    """Obtain a single-use WebSocket ticket from the gateway.

    Tries the ticket first and signs in only on a 401, so a client that already
    carries a live session spends one request rather than two. Verified against
    the deployed dashboard: HTTP Basic on this route answers 401, a session
    answers 200 with {"ticket", "ttl_seconds"} -- which is why the gate's first
    end-to-end attempt closed every socket with 4503.
    """
    url = _normalised_base(config) + TICKET_PATH
    response = await client.post(url, json={})
    if response.status_code == 401:
        await open_upstream_session(config, client)
        response = await client.post(url, json={})
    response.raise_for_status()
    ticket = response.json().get("ticket")
    if not isinstance(ticket, str) or not ticket:
        raise ValueError("upstream returned no ws ticket")
    return ticket
