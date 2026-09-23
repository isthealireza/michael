"""The public front door.

The only service on the internet. It authenticates, then relays a strictly
filtered subset of /api/ws to michael-hermes, which is reachable only on the
private network.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from michael.gate import sessions as session_store
from michael.gate import users as user_store
from michael.gate.allowlist import decide
from michael.gate.cookies import COOKIE_NAME, LIFETIME, mint, verify
from michael.gate.passwords import hash_password, verify_password
from michael.gate.ratelimit import is_locked_out
from michael.gate.upstream import (
    GATEWAY_PROTOCOL,
    UpstreamConfig,
    basic_auth_header,
    fetch_ws_ticket,
    ws_url,
)

#: One message, whatever the cause. Distinguishing "no such user" from "wrong
#: password" turns the login form into an address-enumeration oracle.
INVALID_CREDENTIALS = "invalid email or password"

#: A single hash verified against on every failure branch that has no real
#: hash of its own to check ("no such account", "locked out"), so that branch
#: pays the same argon2id cost as a real "wrong password" check. Without this,
#: the memory-hard work itself — tens of milliseconds, orders of magnitude
#: above network jitter — tells an attacker which addresses are registered,
#: even though the response body is identical.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))

LOGIN_PAGE = """<!doctype html><html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Michael</title>
</head><body><h1>Michael</h1><form id="f">
<label>Email <input name="email" type="email" required autocomplete="username"></label>
<label>Password <input name="password" type="password" required
  autocomplete="current-password"></label>
<button>Sign in</button></form><p id="e" role="alert"></p><script>
document.getElementById("f").onsubmit = async (ev) => {
  ev.preventDefault();
  const data = Object.fromEntries(new FormData(ev.target));
  const r = await fetch("/auth/login", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  if (r.ok) location.href = "/";
  else document.getElementById("e").textContent = (await r.json()).detail;
};
</script></body></html>"""


def _authenticate(email: str, password: str, address: str) -> tuple[int, str] | None:
    """Return (user_id, role) on success, None on any failure.

    Every failure branch pays the same argon2id cost, including the ones with
    no real password hash to check. A short-circuited branch (returning before
    calling verify_password, or skipping it via boolean short-circuit) is a
    distinguishable timing class, and the difference is large enough to be
    measurable from the open internet in a handful of requests — an identical
    response body does not help if the clock does not match.
    """
    account, by_address = user_store.recent_attempts(email, address)
    if is_locked_out(account, by_address, now=datetime.now(UTC)):
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    user = user_store.find_by_email(email)
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        user_store.record_attempt(email, address, "no_such_user")
        return None
    # Computed unconditionally (not `disabled_at is not None or not
    # verify_password(...)`) so a disabled account is not a fourth
    # distinguishable timing class either.
    password_ok = verify_password(password, user.password_hash)
    if user.disabled_at is not None or not password_ok:
        user_store.record_attempt(email, address, "bad_password")
        return None
    user_store.record_attempt(email, address, "ok")
    return user.id, user.role


def _claim_new_session(frame: object, user_id: int) -> frozenset[str]:
    """Record ownership when the gateway reports a newly created session.

    Returns the identifiers actually claimed rather than making the caller
    re-read ``session_store.owned_by()``: relay()'s upstream_to_client runs
    once per frame the upstream sends — once per streamed answer token during
    a prompt.submit response — and owned_by() opens a brand-new, unpooled
    Postgres connection (see ``michael.db.writable``). Re-reading it there
    would open one new connection per token, synchronously, on the event loop.

    ``session.create`` returns *two* identifiers for the same conversation: a
    live ``session_id`` and a durable ``stored_session_id``. ``session.resume``
    later sends the stored id under the parameter name ``session_id``, so both
    must be claimed here or every resume of a reclaimed session is refused by
    ``decide()`` as "not your session" (see ``sessions.py``'s module docstring).
    """
    if not isinstance(frame, dict):
        return frozenset()
    result = frame.get("result")
    if not isinstance(result, dict):
        return frozenset()
    title = str(result.get("title", ""))
    claimed: set[str] = set()
    for key in ("session_id", "stored_session_id"):
        identifier = result.get(key)
        if isinstance(identifier, str) and identifier:
            session_store.claim(user_id, identifier, title)
            claimed.add(identifier)
    return frozenset(claimed)


def build_app(*, secret: str, upstream: UpstreamConfig, web_root: Path) -> Starlette:
    def _session(request: Request) -> tuple[int, str] | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        payload = verify(token, secret=secret, now=datetime.now(UTC))
        return (payload.user_id, payload.role) if payload else None

    async def chat_page(request: Request) -> Response:
        if _session(request) is None:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(web_root / "michael.html")

    async def login_page(request: Request) -> Response:
        return Response(LOGIN_PAGE, media_type="text/html")

    async def chat_script(request: Request) -> Response:
        # Referenced by web/michael.html as a page-relative "michael.js", which
        # a browser requests as /michael.js. Served unauthenticated: it is
        # static client code, not user data — the real boundary is /api/ws.
        return FileResponse(web_root / "michael.js", media_type="text/javascript")

    async def login(request: Request) -> Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"detail": "invalid request body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"detail": "invalid request body"}, status_code=400)
        email = str(body.get("email", ""))
        password = str(body.get("password", ""))
        address = request.client.host if request.client else "unknown"
        result = _authenticate(email, password, address)
        if result is None:
            return JSONResponse({"detail": INVALID_CREDENTIALS}, status_code=401)
        user_id, role = result
        response = Response(status_code=204)
        response.set_cookie(
            COOKIE_NAME,
            mint(user_id, role, secret=secret, now=datetime.now(UTC)),
            max_age=int(LIFETIME.total_seconds()),
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return response

    async def logout(request: Request) -> Response:
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    async def relay(socket: WebSocket) -> None:
        token = socket.cookies.get(COOKIE_NAME)
        payload = verify(token, secret=secret, now=datetime.now(UTC)) if token else None
        if payload is None:
            await socket.close(code=4401)
            return
        await socket.accept(subprotocol=GATEWAY_PROTOCOL)

        try:
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                ticket = await fetch_ws_ticket(upstream, http_client)
        except Exception:
            # Hermes being unreachable is a routine Railway event, not a bug
            # in the gate. Fail closed with no detail rather than let the
            # exception escape into the ASGI server and log a full traceback
            # per attempt.
            print(
                f"gate: could not obtain an upstream ticket for user={payload.user_id}",
                flush=True,
            )
            await socket.close(code=4503)
            return

        upstream_cm = websockets.connect(
            ws_url(upstream, ticket),
            subprotocols=[websockets.Subprotocol(GATEWAY_PROTOCOL)],
            additional_headers={"Authorization": basic_auth_header(upstream)},
        )
        try:
            up = await upstream_cm.__aenter__()
        except Exception:
            print(
                f"gate: could not reach the upstream gateway for user={payload.user_id}",
                flush=True,
            )
            await socket.close(code=4503)
            return

        try:
            owned = session_store.owned_by(payload.user_id)

            async def client_to_upstream() -> None:
                nonlocal owned
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason"))
                    raw = message.get("text")
                    if not isinstance(raw, str):
                        # A binary frame, or anything else with no text
                        # payload: refuse rather than guess at its shape.
                        await socket.close(code=4400)
                        return
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        await socket.close(code=4400)
                        return
                    verdict = decide(frame, owned_session_ids=owned)
                    if not verdict.allowed:
                        # Logged with the user, never echoed back in detail.
                        print(
                            f"gate: refused user={payload.user_id} reason={verdict.reason}",
                            flush=True,
                        )
                        await socket.close(code=4403)
                        return
                    await up.send(raw)

            async def upstream_to_client() -> None:
                nonlocal owned
                async for raw in up:
                    text = raw if isinstance(raw, str) else raw.decode()
                    try:
                        frame = json.loads(text)
                    except json.JSONDecodeError:
                        frame = None
                    claimed = await run_in_threadpool(_claim_new_session, frame, payload.user_id)
                    if claimed:
                        # Merged in memory rather than re-read from
                        # session_store.owned_by(): a stale `owned` is safe
                        # ONLY because claims are monotonic (INSERT ... ON
                        # CONFLICT DO NOTHING, and sessions.py exposes no
                        # revocation), so staleness can only under-permit,
                        # never over-permit. If session un-claiming is ever
                        # added, this becomes fail-open and must be revisited.
                        owned = owned | claimed
                    await socket.send_text(text)

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except WebSocketDisconnect:
                    # A client hanging up mid-conversation is routine, not an
                    # error: let it end the relay quietly rather than surface
                    # as an untraceable "Task exception was never retrieved".
                    pass
        finally:
            await upstream_cm.__aexit__(None, None, None)

    routes = [
        Route("/", chat_page),
        Route("/login", login_page),
        Route("/michael.js", chat_script),
        Route("/auth/login", login, methods=["POST"]),
        Route("/auth/logout", logout, methods=["POST"]),
        WebSocketRoute("/api/ws", relay),
    ]
    app = Starlette(routes=routes)
    app.mount("/static", StaticFiles(directory=web_root), name="static")
    return app
