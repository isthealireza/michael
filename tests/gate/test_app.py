import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from michael.gate.app import build_app
from michael.gate.cookies import COOKIE_NAME, mint
from michael.gate.upstream import UpstreamConfig
from michael.gate.users import User

UPSTREAM = UpstreamConfig(base_url="http://upstream.invalid", username="u", password="p")
SECRET = "s" * 64


def _token(user_id: int = 1, role: str = "chat") -> str:
    return mint(user_id, role, secret=SECRET, now=datetime.now(UTC))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "michael.html").write_text("<html>michael</html>", encoding="utf-8")
    (tmp_path / "michael.js").write_text("// chat client script\n", encoding="utf-8")
    return TestClient(build_app(secret=SECRET, upstream=UPSTREAM, web_root=tmp_path))


# --------------------------------------------------------------------------
# Fakes for the relay's upstream side (C2): a fake duplex connection and fake
# websockets.connect/fetch_ws_ticket, so authenticated relay behaviour can be
# tested without a real Hermes and without a database. `owned_by` is stubbed
# per-test with a fixed frozenset for the same reason `_authenticate` is
# stubbed elsewhere in this file.
# --------------------------------------------------------------------------


_END_OF_STREAM = object()


class _FakeUpstreamConnection:
    """Stands in for the real Hermes websocket connection.

    `send` echoes the frame back (prefixed) through the same queue that
    `__anext__` drains, then immediately signals end-of-stream. This gives
    tests a synchronisation point with no wall-clock waiting: receiving the
    echo on the client guarantees `.sent` already holds the forwarded frame,
    since the append happens strictly before the echo is queued.

    Ending the iteration right after the echo (rather than blocking forever)
    is deliberate: it lets `upstream_to_client` finish and relay() return on
    its own, driven entirely by internal scheduling with no further input
    needed from the client. The alternative — leaving the client to end the
    connection and relying on Starlette's TestClient to tear it down —
    races: TestClient's __exit__ sends a client-initiated disconnect and then
    unconditionally cancels the server-side task shortly after, with no
    synchronisation between the two. If that cancellation lands while
    relay()'s own asyncio.gather cleanup is still in flight, it raises a
    CancelledError that escapes as the test's own failure — flaky roughly a
    third of the time in practice, which is exactly the kind of test this
    review round has been trying to eliminate, not add.

    When `send` is never called (the refusal tests below), `__anext__` blocks
    forever, as before: those tests still end via the *server* actively
    closing the connection (a deliberate, observed 4400/4403/4503), which
    every test here confirms with an explicit read, not by relying on
    TestClient's teardown timing at all.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._queue: asyncio.Queue[object] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(raw)
        await self._queue.put(f"echo:{raw}")
        await self._queue.put(_END_OF_STREAM)

    def __aiter__(self) -> "_FakeUpstreamConnection":
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is _END_OF_STREAM:
            raise StopAsyncIteration
        assert isinstance(item, str)
        return item


class _ScriptedUpstreamConnection:
    """Like `_FakeUpstreamConnection`, but replies to each `send` with one
    canned frame from a fixed script (rather than an automatic echo), and
    does NOT end the stream once the script runs out — later `send`s are
    still recorded, just with no reply. Used for the one test that needs a
    session.create reply to actually flow through `_claim_new_session`
    mid-conversation, so a *later* frame naming that session can be checked
    against the in-memory `owned` set alone."""

    def __init__(self, replies: list[str]) -> None:
        self.sent: list[str] = []
        self._replies = list(replies)
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(raw)
        if self._replies:
            await self._queue.put(self._replies.pop(0))

    def __aiter__(self) -> "_ScriptedUpstreamConnection":
        return self

    async def __anext__(self) -> str:
        return await self._queue.get()


class _FakeConnect:
    """Stands in for `websockets.connect(...)`. relay() enters/exits it
    manually (not via `async with`), so only `__aenter__`/`__aexit__` and
    being callable are needed."""

    def __init__(self, upstream: _FakeUpstreamConnection) -> None:
        self._upstream = upstream

    def __call__(self, *args: object, **kwargs: object) -> "_FakeConnect":
        return self

    async def __aenter__(self) -> _FakeUpstreamConnection:
        return self._upstream

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FailingConnect:
    """Stands in for a `websockets.connect(...)` whose handshake fails —
    Hermes unreachable, refused, wrong subprotocol, etc."""

    def __call__(self, *args: object, **kwargs: object) -> "_FailingConnect":
        return self

    async def __aenter__(self) -> _FakeUpstreamConnection:
        raise OSError("connection refused")

    async def __aexit__(self, *exc_info: object) -> None:
        return None


async def _fake_fetch_ws_ticket(config: object, http_client: object) -> str:
    return "test-ticket"


def _patch_relay_success(
    monkeypatch: pytest.MonkeyPatch, owned: frozenset[str]
) -> _FakeUpstreamConnection:
    import michael.gate.app as app_module

    fake_upstream = _FakeUpstreamConnection()
    monkeypatch.setattr(app_module.session_store, "owned_by", lambda user_id: owned)
    monkeypatch.setattr(app_module, "fetch_ws_ticket", _fake_fetch_ws_ticket)
    monkeypatch.setattr(app_module.websockets, "connect", _FakeConnect(fake_upstream))
    return fake_upstream


# --------------------------------------------------------------------------
# Route shape
# --------------------------------------------------------------------------


def test_the_chat_page_requires_a_session(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_chat_page_serves_the_page_for_a_signed_in_user(client: TestClient) -> None:
    """The success branch: I5 noted that mutating chat_page to redirect
    unconditionally still passed all prior tests, since nothing asserted a 200."""
    response = client.get("/", cookies={COOKIE_NAME: _token()})
    assert response.status_code == 200
    assert response.text == "<html>michael</html>"


def test_the_login_page_is_public(client: TestClient) -> None:
    assert client.get("/login").status_code == 200


def test_michael_js_is_deliberately_public(client: TestClient) -> None:
    """chat_script performs no session check by design — it serves static
    client code, not user data, and the real authentication boundary is
    /api/ws — so this only needs an unauthenticated request."""
    assert client.get("/michael.js").status_code == 200


def test_unauthenticated_root_still_redirects_once_michael_js_is_reachable(
    tmp_path: Path,
) -> None:
    """Making /michael.js reachable must not accidentally make '/' public too
    (e.g. by placing a catch-all static mount ahead of the guarded route)."""
    (tmp_path / "michael.html").write_text("<html>michael</html>", encoding="utf-8")
    (tmp_path / "michael.js").write_text("// chat client script\n", encoding="utf-8")
    anon = TestClient(build_app(secret=SECRET, upstream=UPSTREAM, web_root=tmp_path))

    assert anon.get("/michael.js").status_code == 200

    redirect = anon.get("/", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/login"


def test_the_ws_ticket_endpoint_is_not_proxied(client: TestClient) -> None:
    """The gate fetches its own ticket. Exposing this would hand a client a
    credential-backed handle on the gateway."""
    assert client.post("/api/auth/ws-ticket", json={}).status_code == 404


@pytest.mark.parametrize("path", ["/settings", "/api/sessions", "/assets/index.js", "/health/../"])
def test_no_other_dashboard_route_is_reachable(client: TestClient, path: str) -> None:
    assert client.get(path, follow_redirects=False).status_code in (303, 404)


def test_static_mount_serves_files_by_path(client: TestClient) -> None:
    assert client.get("/static/michael.html").status_code == 200


# --------------------------------------------------------------------------
# Login: shape, hardening, and the C1 timing oracle
# --------------------------------------------------------------------------


def test_a_bad_login_does_not_reveal_whether_the_account_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enumerating valid addresses must not be possible from the response.

    _authenticate is monkeypatched to None here rather than exercised for
    real: the real function reaches Postgres via user_store.recent_attempts,
    and this suite (unlike Task 6's integration tests) must run with no
    database available. This test only proves the route's response shape does
    not distinguish failure causes; the timing side of the same property is
    covered by test_bad_login_pays_the_same_password_check_cost_regardless_of_cause
    below, and the real credential path by Task 6's integration tests.
    """
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: None)
    absent = client.post(
        "/auth/login", json={"email": "nobody@x.com", "password": "wrong-password"}
    )
    assert absent.status_code == 401
    assert absent.json() == {"detail": "invalid email or password"}


def test_bad_login_pays_the_same_password_check_cost_regardless_of_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1: _authenticate must call verify_password on every failure branch,
    including "no such account", so argon2id's cost — tens of milliseconds,
    orders of magnitude above network jitter — cannot be used to distinguish
    "no such account" from "wrong password" from the open internet, even
    though the response body is identical either way.

    Tested deterministically by call count, not by timing: a timing-based
    test would be flaky, and a flaky security test is worse than none.
    """
    import michael.gate.app as app_module

    checked: list[str] = []

    def fake_verify_password(plain: str, stored: str) -> bool:
        checked.append(stored)
        return False

    monkeypatch.setattr(app_module.user_store, "recent_attempts", lambda *a: ([], []))
    monkeypatch.setattr(app_module.user_store, "record_attempt", lambda *a: None)
    monkeypatch.setattr(app_module, "verify_password", fake_verify_password)

    monkeypatch.setattr(app_module.user_store, "find_by_email", lambda email: None)
    assert app_module._authenticate("nobody@x.com", "whatever-12345", "1.2.3.4") is None
    assert checked == [app_module._DUMMY_PASSWORD_HASH]

    checked.clear()
    known_user = User(
        id=1,
        email="reader@x.com",
        display_name="Reader",
        password_hash="real-stored-hash",
        role="chat",
        disabled_at=None,
    )
    monkeypatch.setattr(app_module.user_store, "find_by_email", lambda email: known_user)
    assert app_module._authenticate("reader@x.com", "wrong-password", "1.2.3.4") is None
    assert checked == ["real-stored-hash"]


def test_bad_login_pays_the_password_check_cost_for_a_disabled_account_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extending C1: `user.disabled_at is not None or not verify_password(...)`
    would short-circuit and skip verify_password entirely for a disabled
    account, making "disabled" a fourth distinguishable timing class even
    after the dummy-hash fix closes the first three. _authenticate computes
    the password check unconditionally instead, specifically so this case
    costs the same as a wrong password against an enabled account."""
    import michael.gate.app as app_module

    checked: list[str] = []

    def fake_verify_password(plain: str, stored: str) -> bool:
        checked.append(stored)
        return True  # even a *correct* password must not help a disabled account

    monkeypatch.setattr(app_module.user_store, "recent_attempts", lambda *a: ([], []))
    monkeypatch.setattr(app_module.user_store, "record_attempt", lambda *a: None)
    monkeypatch.setattr(app_module, "verify_password", fake_verify_password)

    disabled_user = User(
        id=2,
        email="gone@x.com",
        display_name="Gone",
        password_hash="disabled-users-hash",
        role="chat",
        disabled_at=datetime.now(UTC),
    )
    monkeypatch.setattr(app_module.user_store, "find_by_email", lambda email: disabled_user)

    assert app_module._authenticate("gone@x.com", "correct-password", "1.2.3.4") is None
    assert checked == ["disabled-users-hash"]


def test_login_with_a_non_json_body_returns_400(client: TestClient) -> None:
    """I4: a non-JSON body must not 500 an unauthenticated caller."""
    response = client.post(
        "/auth/login", content=b"not json at all", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


def test_login_with_a_json_array_body_returns_400(client: TestClient) -> None:
    """I4: valid JSON that isn't an object must also 400, not 500 on `.get`."""
    response = client.post(
        "/auth/login", content=b"[1, 2, 3]", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


def test_session_cookie_is_hardened(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: (1, "chat"))
    response = client.post(
        "/auth/login", json={"email": "reader@x.com", "password": "a-long-enough-password"}
    )
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    # Compared case-insensitively throughout: Starlette's exact attribute
    # casing (e.g. "HttpOnly" vs "httponly") is not contractual, so pinning the
    # assertion to one particular casing would be depending on an
    # implementation detail rather than the constraint itself.
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert "max-age=43200" in lowered
    assert "path=/" in lowered


def test_a_forged_cookie_is_refused_by_http_and_websocket(client: TestClient) -> None:
    valid = _token()
    forged = valid[:-1] + ("A" if not valid.endswith("A") else "B")

    redirect = client.get("/", cookies={COOKIE_NAME: forged}, follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/login"

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: forged}):
            pass
    assert caught.value.code == 4401


def test_an_expired_cookie_is_refused_by_http_and_websocket(client: TestClient) -> None:
    stale = mint(1, "chat", secret=SECRET, now=datetime.now(UTC) - timedelta(hours=13))

    redirect = client.get("/", cookies={COOKIE_NAME: stale}, follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/login"

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: stale}):
            pass
    assert caught.value.code == 4401


# --------------------------------------------------------------------------
# The relay itself (C2): authenticated behaviour, not just the unauthenticated
# refusal. Each test stubs owned_by/fetch_ws_ticket/websockets.connect rather
# than touching Postgres or a real Hermes.
# --------------------------------------------------------------------------


def test_the_websocket_refuses_an_unauthenticated_client(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws"):
            pass
    assert caught.value.code == 4401


def test_an_allowed_frame_for_an_owned_session_reaches_the_fake_upstream_verbatim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_upstream = _patch_relay_success(monkeypatch, frozenset({"owned-1"}))
    frame = {"method": "prompt.submit", "params": {"session_id": "owned-1", "text": "hi"}}
    raw = json.dumps(frame)

    with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
        ws.send_text(raw)
        echo = ws.receive_text()

    assert echo == f"echo:{raw}"
    assert fake_upstream.sent == [raw]


def test_a_prompt_submit_naming_an_unowned_session_is_refused_and_forwards_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The important half is that the fake upstream received NOTHING."""
    fake_upstream = _patch_relay_success(monkeypatch, frozenset({"owned-1"}))
    frame = {"method": "prompt.submit", "params": {"session_id": "unowned-1", "text": "hi"}}

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text(json.dumps(frame))
            ws.receive_text()

    assert caught.value.code == 4403
    assert fake_upstream.sent == []


def test_a_disallowed_method_is_refused_and_forwards_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_upstream = _patch_relay_success(monkeypatch, frozenset({"owned-1"}))

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text(json.dumps({"method": "session.list", "params": {}}))
            ws.receive_text()

    assert caught.value.code == 4403
    assert fake_upstream.sent == []


def test_a_non_object_json_frame_is_refused_and_forwards_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_upstream = _patch_relay_success(monkeypatch, frozenset())

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text(json.dumps([1, 2, 3]))
            ws.receive_text()

    assert caught.value.code == 4403
    assert fake_upstream.sent == []


def test_malformed_json_text_closes_4400_and_forwards_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_upstream = _patch_relay_success(monkeypatch, frozenset())

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text("not valid json{")
            ws.receive_text()

    assert caught.value.code == 4400
    assert fake_upstream.sent == []


def test_a_binary_frame_closes_4400_without_crashing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3: receive_text() would raise an unhandled KeyError on a binary
    frame (message["text"] with no "text" key). It fails closed either way,
    but this proves it does so via the deliberate 4400 path, not by accident."""
    fake_upstream = _patch_relay_success(monkeypatch, frozenset())

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_bytes(b"\x00\x01\x02")
            ws.receive_text()

    assert caught.value.code == 4400
    assert fake_upstream.sent == []


def test_upstream_ticket_fetch_failure_closes_4503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: Hermes being unreachable is routine, not a bug in the gate."""
    import michael.gate.app as app_module

    async def _boom(config: object, http_client: object) -> str:
        raise RuntimeError("hermes unreachable")

    monkeypatch.setattr(app_module, "fetch_ws_ticket", _boom)

    # socket.accept() has already happened by this point (unlike the 4401
    # case), so the close is only observable by actually trying to read —
    # entering/exiting the context manager without reading would not surface
    # it, since the client already completed its handshake successfully.
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.receive_text()

    assert caught.value.code == 4503


def test_upstream_connect_failure_closes_4503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: the upstream handshake itself can fail even after a ticket was
    obtained (wrong subprotocol, refused connection, ...)."""
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "fetch_ws_ticket", _fake_fetch_ws_ticket)
    monkeypatch.setattr(app_module.websockets, "connect", _FailingConnect())

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.receive_text()

    assert caught.value.code == 4503


# --------------------------------------------------------------------------
# _claim_new_session (Defect 2 / C3): claims both identifiers, and now
# returns them rather than the caller re-reading owned_by() from Postgres.
# --------------------------------------------------------------------------


def test_claim_new_session_claims_both_session_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session.create returns a live session_id AND a stored_session_id, and
    session.resume later sends the *stored* id under the parameter name
    session_id (web/michael.js lines ~95-120). Claiming only the live id
    would make decide() refuse every resume as "not_your_session"."""
    import michael.gate.app as app_module

    claimed: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        app_module.session_store,
        "claim",
        lambda user_id, hermes_session_id, title: claimed.append(
            (user_id, hermes_session_id, title)
        ),
    )
    frame = {"result": {"session_id": "live-1", "stored_session_id": "stored-1", "title": "t"}}

    result = app_module._claim_new_session(frame, 42)

    assert claimed == [(42, "live-1", "t"), (42, "stored-1", "t")]
    assert result == frozenset({"live-1", "stored-1"})


def test_claim_new_session_ignores_empty_or_missing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import michael.gate.app as app_module

    claimed: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        app_module.session_store,
        "claim",
        lambda user_id, hermes_session_id, title: claimed.append(
            (user_id, hermes_session_id, title)
        ),
    )
    frame = {"result": {"session_id": "", "stored_session_id": None, "title": "t"}}

    result = app_module._claim_new_session(frame, 42)

    assert claimed == []
    assert result == frozenset()


def test_owned_by_is_read_once_per_connection_not_once_per_frame(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3: session_store.owned_by opens a brand-new, unpooled Postgres
    connection (michael.db.writable). Re-reading it once per upstream frame —
    as the pre-fix code did, inside upstream_to_client's `async for` loop —
    means one new Postgres connection per streamed answer token, synchronously
    on the event loop: enough concurrent users to exhaust max_connections
    takes the whole gate down, since it is the only public service.

    This proves two things at once: owned_by is called exactly once for the
    whole connection (not once per frame), AND a session claimed mid-stream
    from a session.create reply is usable immediately afterward through the
    in-memory merge alone — the fix does not just delete the read, it
    preserves the claim-before-forward correctness the read used to provide.
    """
    import michael.gate.app as app_module

    owned_by_calls = 0

    def counting_owned_by(user_id: int) -> frozenset[str]:
        nonlocal owned_by_calls
        owned_by_calls += 1
        return frozenset()

    claimed_ids: list[str] = []
    monkeypatch.setattr(app_module.session_store, "owned_by", counting_owned_by)
    monkeypatch.setattr(
        app_module.session_store,
        "claim",
        lambda user_id, hermes_session_id, title: claimed_ids.append(hermes_session_id),
    )
    monkeypatch.setattr(app_module, "fetch_ws_ticket", _fake_fetch_ws_ticket)

    create_reply = json.dumps(
        {"result": {"session_id": "new-1", "stored_session_id": "stored-1", "title": "t"}}
    )
    fake_upstream = _ScriptedUpstreamConnection([create_reply])
    monkeypatch.setattr(app_module.websockets, "connect", _FakeConnect(fake_upstream))

    create_frame = json.dumps({"method": "session.create", "params": {"title": "t"}})
    submit_frame = json.dumps(
        {"method": "prompt.submit", "params": {"session_id": "new-1", "text": "hi"}}
    )

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text(create_frame)
            ws.receive_text()  # the session.create reply, relayed back
            ws.send_text(submit_frame)
            # A disallowed frame, purely to force a deterministic,
            # server-initiated close for teardown — see
            # _FakeUpstreamConnection's docstring on why this suite avoids
            # ending a "success" test via a client-initiated disconnect.
            ws.send_text(json.dumps({"method": "session.list", "params": {}}))
            ws.receive_text()

    assert caught.value.code == 4403
    assert fake_upstream.sent == [create_frame, submit_frame]
    assert claimed_ids == ["new-1", "stored-1"]
    assert owned_by_calls == 1
