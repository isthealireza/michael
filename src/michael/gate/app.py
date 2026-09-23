"""The public front door.

The only service on the internet. It authenticates, then relays a strictly
filtered subset of /api/ws to michael-hermes, which is reachable only on the
private network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
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
    fetch_ws_ticket,
    ws_url,
)
from michael.gate.users import User

#: mypy --strict enables no_implicit_reexport, which otherwise flags these
#: three re-aliased/whole-module imports as "not explicitly exported" the
#: moment the test suite reaches through them for monkeypatching (e.g.
#: `monkeypatch.setattr(app_module.session_store, "owned_by", ...)`,
#: `app_module.websockets.connect`) — a deliberate, ordinary testing pattern
#: in this codebase (patch the name where it is looked up), not an accident.
__all__ = ["session_store", "user_store", "websockets"]

logger = logging.getLogger("michael.gate")

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

#: The sign-in page lives in `web/login.html`, beside the chat page, rather
#: than in a string here. It carries the Palm Vision brand — a solid green
#: cover block, the gold focus rule, the type scale — and that is markup and
#: CSS worth editing in a file a designer can open, not a Python literal.
#: `gate/Dockerfile` already copies the whole of `web/`, so it ships with the
#: image the same way `michael.html` and `michael.js` do.
LOGIN_PAGE_FILE = "login.html"

#: Small enough to keep beside the code it serves, unlike the chat and sign-in
#: pages. Palm Vision palette, flat — no blur here: this is an operator tool,
#: not a surface anyone is meant to enjoy looking at.
ADMIN_PAGE = """<!doctype html><html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Accounts · Michael</title>
<style>
:root{--g:#2E8B57;--gd:#1F6A40;--b:#006994;--gold:#F4C430;--d:#B23A28;
 --fg:#333;--fg2:#555;--fg3:#777;--rule:#E2E5E2;--paper:#FAFAF7}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--fg);
 font:16px/1.6 'Segoe UI','Open Sans',Helvetica,Arial,sans-serif}
header{background:#fff;border-bottom:2px solid var(--gold);padding:14px 20px}
.bar{max-width:60rem;margin:0 auto;display:flex;align-items:baseline;gap:12px}
h1{margin:0;font-size:15px;letter-spacing:.01em;color:var(--g)}
.eb{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--b)}
a{color:var(--b)}
main{max-width:60rem;margin:0 auto;padding:28px 20px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--rule)}
th{text-align:left;font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
 color:var(--fg3);padding:10px 14px;border-bottom:1px solid var(--rule)}
td{padding:11px 14px;border-bottom:1px solid #EFF1EF;font-size:14.5px}
tr:last-child td{border-bottom:0}
.email{font-weight:600;color:#1A1A1A}
.role{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
 padding:2px 7px;border-radius:2px}
.role-admin{background:#EAF3EE;color:var(--gd)}
.role-chat{background:#E5F0F5;color:#00547A}
.state{font-size:13px;font-weight:600}
.state.on{color:var(--gd)} .state.off{color:var(--d)}
.act{text-align:right}
button{font:inherit;font-size:13px;font-weight:700;color:#fff;background:var(--g);
 border:0;border-radius:4px;padding:7px 12px;min-height:36px;cursor:pointer}
button:hover:not(:disabled){background:var(--gd)}
button:disabled{background:#C7D9CE;cursor:default}
.none{color:var(--fg3)}
#msg{margin:0 0 16px;padding:10px 14px;font-size:14px;font-weight:600;display:none;
 border-left:2px solid var(--d);background:#FBF1EF;color:var(--d)}
#msg.on{display:block}
.note{margin-top:20px;font-size:13px;color:var(--fg3);line-height:1.55}
</style></head><body>
<header><div class="bar"><h1>Michael</h1><span class="eb">Accounts</span>
<span style="margin-left:auto"><a href="/">Back to chat</a></span></div></header>
<main>
<p id="msg" role="alert"></p>
<table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th>State</th><th></th></tr></thead>
<tbody><!--ROWS--></tbody></table>
<p class="note">Disabling an account refuses its next sign-in and its next
socket. An already-open socket is re-checked only when it reconnects, so a
signed-in reader may finish the turn in flight. Passwords are reset from the
command line: <code>michael user password &lt;email&gt;</code>.</p>
</main>
<script>
document.querySelector('table').addEventListener('click', async (ev) => {
  const btn = ev.target.closest('button[data-email]');
  if (!btn) return;
  const msg = document.getElementById('msg');
  msg.classList.remove('on');
  btn.disabled = true;
  try {
    const r = await fetch('/admin/users/enabled', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify({email: btn.dataset.email, enabled: btn.dataset.enable === '1'}),
    });
    if (r.ok) { location.reload(); return; }
    let detail = 'That did not work.';
    try { const b = await r.json(); if (b && b.detail) detail = b.detail; } catch {}
    msg.textContent = detail; msg.classList.add('on'); btn.disabled = false;
  } catch {
    msg.textContent = 'Could not reach the server.'; msg.classList.add('on');
    btn.disabled = false;
  }
});
</script></body></html>"""


def client_address(request: Request) -> str:
    """The address of whoever connected to Railway's edge, not the edge itself.

    Railway's edge proxy is the only hop between the public internet and this
    process. `request.client.host` is that edge's OWN address -- identical for
    every caller on earth -- so using it as a rate-limit key turns
    ``ADDRESS_LIMIT`` into one global counter (C3): twenty failed logins from
    anyone locks out every account for everyone.

    Railway's published request-header spec names ``X-Real-IP`` as the header
    "for identifying client's remote IP", and its edge sets it. That is the
    value to key off.

    ``X-Forwarded-For`` is NOT that value here, which the first version of
    this function got wrong. Railway does not document XFF at all, and the
    header that does arrive carries its own internal chain: the rightmost hop
    is an edge POP, measured in production as one of a small set of
    ``152.233.33.x`` addresses shared by every caller. Keying the limit off
    that restores the global-lockout DoS this function exists to prevent,
    spread across three or four buckets instead of one. A request from a
    machine whose real address was ``124.148.255.130`` was recorded as
    ``152.233.33.164``.

    This does NOT lean on uvicorn's ``--forwarded-allow-ips`` handling either.
    Trusting it via ``'*'`` makes uvicorn take the LEFTMOST XFF entry, which
    is precisely the value an attacker supplies by sending their own header.
    """
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


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


def _reported_session_ids(frame: object) -> tuple[str, tuple[str, ...]]:
    """Which session identifiers (if any) does this upstream reply report,
    and what title goes with them? Order matches the fixed key order below
    (``session_id`` before ``stored_session_id``), not that it is meaningful
    upstream — it just makes claim order deterministic for callers/tests.

    Pure and in-process — no I/O — so relay()'s upstream_to_client can call
    this inline, on the event loop, for EVERY frame the upstream sends
    (once per streamed answer token during a prompt.submit response), and
    reserve the threadpool hop for the rare frame that actually has
    something to claim, rather than one thread dispatch per token.
    """
    if not isinstance(frame, dict):
        return "", ()
    result = frame.get("result")
    if not isinstance(result, dict):
        return "", ()
    title = str(result.get("title", ""))
    ids = tuple(
        identifier
        for key in ("session_id", "stored_session_id")
        if isinstance(identifier := result.get(key), str) and identifier
    )
    return title, ids


def _claim_session_ids_from_reply(frame: object, user_id: int) -> frozenset[str]:
    """Record ownership of whatever session identifiers an upstream reply
    reports — for a ``session.create`` reply AND for a ``session.resume``
    reply alike. (Previously named ``_claim_new_session``, which describes
    only the first of those two call sites and not the second — the name,
    docstring and tests all implied this was create-only, when a resumed
    session's freshly issued live id is claimed through this exact same
    function too, and is not optional: without it, the very next
    ``prompt.submit`` naming that live id is refused by ``decide()`` as "not
    your session".)

    Returns the identifiers actually claimed rather than making the caller
    re-read ``session_store.owned_by()``: relay()'s upstream_to_client runs
    once per frame the upstream sends — once per streamed answer token during
    a prompt.submit response — and owned_by() opens a brand-new, unpooled
    Postgres connection (see ``michael.db.writable``). Re-reading it there
    would open one new connection per token, synchronously, on the event loop.

    ``session.create`` returns *two* identifiers for the same conversation: a
    live ``session_id`` and a durable ``stored_session_id``. ``session.resume``
    later sends the stored id under the parameter name ``session_id`` and gets
    back a *new* live ``session_id`` for the resumed conversation. Either way,
    every identifier found here must be claimed or the next request naming it
    is refused by ``decide()`` as "not your session" (see ``sessions.py``'s
    module docstring).

    This is the (comparatively expensive, Postgres-touching) half of the
    check; callers on a hot path should call `_reported_session_ids(frame)`
    first and only reach this function when it returns a non-empty set.
    """
    title, ids = _reported_session_ids(frame)
    for identifier in ids:
        session_store.claim(user_id, identifier, title)
    return frozenset(ids)


def _esc(value: object) -> str:
    """Escape for HTML text and double-quoted attributes alike.

    Email and display name are operator-supplied but arrive from a database,
    and the console that shows them is the one surface an administrator reads
    while signed in as an administrator. Rendering either raw would make
    `michael user add '<img onerror=...>' ...` a stored cross-site script
    aimed squarely at the only privileged session in the system.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_admin(accounts: list[User]) -> str:
    """The administrator console: the accounts, and the one write that matters."""
    rows = []
    for account in accounts:
        disabled = account.disabled_at is not None
        rows.append(
            "<tr>"
            f'<td class="email">{_esc(account.email)}</td>'
            f"<td>{_esc(account.display_name)}</td>"
            f'<td><span class="role role-{_esc(account.role)}">{_esc(account.role)}</span></td>'
            f'<td><span class="state {"off" if disabled else "on"}">'
            f'{"disabled" if disabled else "active"}</span></td>'
            f'<td class="act"><button type="button" data-email="{_esc(account.email)}" '
            f'data-enable="{"1" if disabled else "0"}">'
            f'{"Re-enable" if disabled else "Disable"}</button></td>'
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="5" class="none">No accounts yet.</td></tr>'
    return ADMIN_PAGE.replace("<!--ROWS-->", body)


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
        return FileResponse(web_root / LOGIN_PAGE_FILE)

    def _admin(request: Request) -> tuple[int, str] | None:
        """The session, but only when it carries the admin role.

        Returns None for a chat user as well as for an anonymous one, and the
        callers below answer both the same way. Telling a signed-in chat user
        that a route exists but is not theirs discloses the shape of the
        administrative surface to exactly the population the gate exists to
        keep away from it.
        """
        session = _session(request)
        if session is None or session[1] != "admin":
            return None
        return session

    async def admin_console(request: Request) -> Response:
        if _admin(request) is None:
            # 404, not 403: see _admin. An anonymous caller is redirected so a
            # bookmarked /admin still leads somewhere useful.
            if _session(request) is None:
                return RedirectResponse("/login", status_code=303)
            return PlainTextResponse("Not Found", status_code=404)
        accounts = await run_in_threadpool(user_store.list_users)
        return HTMLResponse(_render_admin(accounts))

    async def admin_set_enabled(request: Request) -> Response:
        """Disable or re-enable one account. The only privileged write."""
        session = _admin(request)
        if session is None:
            if _session(request) is None:
                return RedirectResponse("/login", status_code=303)
            return PlainTextResponse("Not Found", status_code=404)
        actor_id, _ = session

        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"detail": "expected a JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"detail": "expected a JSON object"}, status_code=400)

        email = str(body.get("email", "")).strip()
        enabled = body.get("enabled")
        if not email or not isinstance(enabled, bool):
            return JSONResponse(
                {"detail": "email and enabled are required"}, status_code=400
            )

        target = await run_in_threadpool(user_store.find_by_email, email)
        if target is None:
            return JSONResponse({"detail": "no such account"}, status_code=404)
        # An administrator who disables their own account has locked the last
        # door behind themselves, and re-opening it needs shell access to the
        # container. Refuse rather than let the console do that silently.
        if target.id == actor_id and not enabled:
            return JSONResponse(
                {"detail": "an administrator cannot disable their own account"},
                status_code=409,
            )

        action = user_store.enable_user if enabled else user_store.disable_user
        changed = await run_in_threadpool(action, email)
        logger.info(
            "admin %s account=%s by=%s changed=%s",
            "enabled" if enabled else "disabled",
            target.email,
            actor_id,
            changed,
        )
        return JSONResponse({"email": target.email, "enabled": enabled, "changed": changed})

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
        address = client_address(request)
        # _authenticate makes up to three unpooled Postgres round-trips plus
        # one argon2id verification (tens of milliseconds by design). Run it
        # off the event loop so one unauthenticated caller cannot block every
        # other request — including active WebSocket streams — while it waits.
        result = await run_in_threadpool(_authenticate, email, password, address)
        if result is None:
            # T9: after an incident on a tool holding named users' legal
            # questions, there must be a record of who tried to sign in and
            # from where. The account name, never the password, is logged.
            logger.warning("login failed account=%s address=%s", email, address)
            return JSONResponse({"detail": INVALID_CREDENTIALS}, status_code=401)
        user_id, role = result
        logger.info("login success user=%s role=%s address=%s", user_id, role, address)
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
        # Minor: matching login()'s set_cookie attributes. A delete_cookie
        # whose Set-Cookie attributes (secure/httponly/samesite) do not match
        # the cookie actually set can fail to clear it in some browsers —
        # mismatched attributes describe a different cookie, not an
        # overwrite of the existing one.
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict"
        )
        return response

    async def health(request: Request) -> Response:
        # Minor: unauthenticated by design (Railway's health check has no
        # session cookie) and deliberately says nothing about upstream
        # (Hermes) reachability — that is /api/ws's job, and probing it here
        # on every health check would hammer Hermes for no operational
        # benefit. This only answers "is the gate process itself up".
        return JSONResponse({"status": "ok"})

    async def relay(socket: WebSocket) -> None:
        token = socket.cookies.get(COOKIE_NAME)
        payload = verify(token, secret=secret, now=datetime.now(UTC)) if token else None
        if payload is None:
            await socket.close(code=4401)
            return
        # I5: the cookie alone asserts a user WAS enabled at login, up to 12h
        # ago. disable_user() is otherwise checked only at login, so a
        # disabled account keeps a valid cookie for the rest of its
        # lifetime. Re-checking live account state here closes the new-socket
        # half of that gap. It does NOT close the other half: a socket already
        # open at the moment an account is disabled is not re-checked again
        # for the rest of that connection (see docs/gate-cutover.md's residual
        # revocation lag, and I5 in the review this fixes).
        account = await run_in_threadpool(user_store.find_by_id, payload.user_id)
        if account is None or account.disabled_at is not None:
            await socket.close(code=4401)
            return
        await socket.accept(subprotocol=GATEWAY_PROTOCOL)
        logger.info("socket open user=%s", payload.user_id)

        try:
            try:
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    ticket = await fetch_ws_ticket(upstream, http_client)
            except Exception:
                # Hermes being unreachable is a routine Railway event, not a
                # bug in the gate. Fail closed with no detail rather than let
                # the exception escape into the ASGI server and log a full
                # traceback per attempt.
                logger.warning(
                    "could not obtain an upstream ticket for user=%s", payload.user_id
                )
                await socket.close(code=4503)
                return

            upstream_cm = websockets.connect(
                ws_url(upstream, ticket),
                subprotocols=[websockets.Subprotocol(GATEWAY_PROTOCOL)],
            )
            try:
                up = await upstream_cm.__aenter__()
            except Exception:
                logger.warning(
                    "could not reach the upstream gateway for user=%s", payload.user_id
                )
                await socket.close(code=4503)
                return

            try:
                # session_store.owned_by opens a brand-new, unpooled Postgres
                # connection (michael.db.writable). Calling it synchronously
                # here blocks the event loop for every other connection on
                # this process for the duration of that connect + query (C2
                # — the same defect as the login path above).
                owned = await run_in_threadpool(session_store.owned_by, payload.user_id)

                async def client_to_upstream() -> None:
                    nonlocal owned
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            raise WebSocketDisconnect(
                                message.get("code", 1000), message.get("reason")
                            )
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
                            logger.warning(
                                "relay refused user=%s reason=%s",
                                payload.user_id,
                                verdict.reason,
                            )
                            await socket.close(code=4403)
                            return
                        # I4: relay a re-serialization of the SAME object
                        # `decide()` inspected, not the raw text. Python's
                        # json.loads keeps the LAST of any duplicate key; if
                        # Hermes's parser keeps the FIRST instead, a frame like
                        # {"method":"admin.x","method":"session.create"} is
                        # decided against "session.create" here but could be
                        # dispatched as "admin.x" upstream. Re-dumping `frame`
                        # collapses any duplicate key to the one value
                        # decide() actually saw, so what Hermes parses can no
                        # longer disagree with what was allowed. Every value
                        # on this path (id/method/params.*) is a string, so
                        # round-tripping through json changes nothing
                        # Hermes-visible beyond that duplicate-key collapse.
                        await up.send(json.dumps(frame))

                async def upstream_to_client() -> None:
                    nonlocal owned
                    async for raw in up:
                        text = raw if isinstance(raw, str) else raw.decode()
                        try:
                            frame = json.loads(text)
                        except json.JSONDecodeError:
                            frame = None
                        # Minor: the cheap, in-process check runs inline for
                        # every frame; the threadpool (and Postgres) is only
                        # entered for the rare frame that actually reports a
                        # session id to claim — not once per streamed token.
                        _, candidate_ids = _reported_session_ids(frame)
                        if candidate_ids:
                            claimed = await run_in_threadpool(
                                _claim_session_ids_from_reply, frame, payload.user_id
                            )
                            if claimed:
                                # Merged in memory rather than re-read from
                                # session_store.owned_by(): a stale `owned` is
                                # safe ONLY because claims are monotonic
                                # (INSERT ... ON CONFLICT DO NOTHING, and
                                # sessions.py exposes no revocation), so
                                # staleness can only under-permit, never
                                # over-permit. If session un-claiming is ever
                                # added, this becomes fail-open and must be
                                # revisited.
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
                        # A client hanging up mid-conversation is routine, not
                        # an error: let it end the relay quietly rather than
                        # surface as an untraceable "Task exception was never
                        # retrieved".
                        pass
            finally:
                await upstream_cm.__aexit__(None, None, None)
        finally:
            logger.info("socket closed user=%s", payload.user_id)

    routes = [
        Route("/", chat_page),
        Route("/login", login_page),
        Route("/michael.js", chat_script),
        Route("/auth/login", login, methods=["POST"]),
        Route("/auth/logout", logout, methods=["POST"]),
        Route("/health", health),
        Route("/admin", admin_console),
        Route("/admin/users/enabled", admin_set_enabled, methods=["POST"]),
        WebSocketRoute("/api/ws", relay),
    ]
    app = Starlette(routes=routes)
    app.mount("/static", StaticFiles(directory=web_root), name="static")
    return app
