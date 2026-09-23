"""The public front door.

The only service on the internet. It authenticates, then relays a strictly
filtered subset of /api/ws to michael-hermes, which is reachable only on the
private network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from michael.gate import sessions as session_store
from michael.gate import users as user_store
from michael.gate.allowlist import decide
from michael.gate.cookies import COOKIE_NAME, LIFETIME, mint, verify
from michael.gate.passwords import verify_password
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
    """Return (user_id, role) on success, None on any failure."""
    account, by_address = user_store.recent_attempts(email, address)
    if is_locked_out(account, by_address, now=datetime.now(UTC)):
        return None
    user = user_store.find_by_email(email)
    if user is None:
        user_store.record_attempt(email, address, "no_such_user")
        return None
    if user.disabled_at is not None or not verify_password(password, user.password_hash):
        user_store.record_attempt(email, address, "bad_password")
        return None
    user_store.record_attempt(email, address, "ok")
    return user.id, user.role


def _claim_new_session(text: str, user_id: int) -> None:
    """Record ownership when the gateway reports a newly created session.

    ``session.create`` returns *two* identifiers for the same conversation: a
    live ``session_id`` and a durable ``stored_session_id``. ``session.resume``
    later sends the stored id under the parameter name ``session_id``, so both
    must be claimed here or every resume of a reclaimed session is refused by
    ``decide()`` as "not your session" (see ``sessions.py``'s module docstring).
    """
    try:
        frame = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(frame, dict):
        return
    result = frame.get("result")
    if not isinstance(result, dict):
        return
    title = str(result.get("title", ""))
    for key in ("session_id", "stored_session_id"):
        identifier = result.get(key)
        if isinstance(identifier, str) and identifier:
            session_store.claim(user_id, identifier, title)


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
        body = await request.json()
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            ticket = await fetch_ws_ticket(upstream, client)
        async with websockets.connect(
            ws_url(upstream, ticket),
            subprotocols=[websockets.Subprotocol(GATEWAY_PROTOCOL)],
            additional_headers={"Authorization": basic_auth_header(upstream)},
        ) as up:
            owned = session_store.owned_by(payload.user_id)

            async def client_to_upstream() -> None:
                nonlocal owned
                while True:
                    raw = await socket.receive_text()
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
                    _claim_new_session(text, payload.user_id)
                    owned = session_store.owned_by(payload.user_id)
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
