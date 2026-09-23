import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

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
    # The sign-in page is served from web/ too, so the fake web root needs one
    # or `/login` 500s on a missing file rather than returning the page.
    (tmp_path / "login.html").write_text("<html>sign in</html>", encoding="utf-8")
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
    session.create reply to actually flow through `_claim_session_ids_from_reply`
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


class _UpstreamLike(Protocol):
    """Structural shape shared by `_FakeUpstreamConnection` and
    `_ScriptedUpstreamConnection`, so `_FakeConnect` can wrap either without
    the two unrelated fakes needing a common base class."""

    sent: list[str]

    async def send(self, raw: str) -> None: ...
    def __aiter__(self) -> "_UpstreamLike": ...
    async def __anext__(self) -> str: ...


class _FakeConnect:
    """Stands in for `websockets.connect(...)`. relay() enters/exits it
    manually (not via `async with`), so only `__aenter__`/`__aexit__` and
    being callable are needed."""

    def __init__(self, upstream: _UpstreamLike) -> None:
        self._upstream = upstream

    def __call__(self, *args: object, **kwargs: object) -> "_FakeConnect":
        return self

    async def __aenter__(self) -> _UpstreamLike:
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


def _enabled_user(user_id: int = 1) -> User:
    return User(
        id=user_id,
        email="reader@x.com",
        display_name="Reader",
        password_hash="irrelevant",
        role="chat",
        disabled_at=None,
    )


def _patch_relay_success(
    monkeypatch: pytest.MonkeyPatch, owned: frozenset[str]
) -> _FakeUpstreamConnection:
    import michael.gate.app as app_module

    fake_upstream = _FakeUpstreamConnection()
    monkeypatch.setattr(app_module.session_store, "owned_by", lambda user_id: owned)
    monkeypatch.setattr(app_module, "fetch_ws_ticket", _fake_fetch_ws_ticket)
    monkeypatch.setattr(app_module.websockets, "connect", _FakeConnect(fake_upstream))
    # I5: relay() re-checks live account state (disabled_at) at accept time,
    # which reaches Postgres via user_store.find_by_id. Stubbed here for the
    # same reason _authenticate is stubbed elsewhere in this file: this suite
    # runs with no database available.
    monkeypatch.setattr(app_module.user_store, "find_by_id", lambda user_id: _enabled_user(user_id))
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


def test_the_real_chat_page_has_no_dead_password_login_card(tmp_path: Path) -> None:
    """C1: web/michael.html used to carry a second, pre-gate login card wired
    to a route the gate never serves (/auth/password-login), and #thread /
    #askArea were un-hidden ONLY by that dead handler's success branch — so a
    signed-in user saw a password box that could never succeed and never saw
    the question box. This reads the REAL repository file (not a tmp_path
    stand-in) so a reintroduced login card is caught here."""
    repo_web_root = Path(__file__).resolve().parents[2] / "web"
    client = TestClient(build_app(secret=SECRET, upstream=UPSTREAM, web_root=repo_web_root))

    response = client.get("/", cookies={COOKIE_NAME: _token()})

    assert response.status_code == 200
    assert "password-login" not in response.text
    assert "loginCard" not in response.text
    # The thread and composer must be visible on load, not un-hidden only by
    # a login handler that no longer exists.
    assert 'id="thread" class="hidden"' not in response.text
    assert 'id="askArea"' in response.text
    assert 'class="wrap hidden" id="askArea"' not in response.text


def test_the_real_login_page_posts_to_the_route_the_gate_actually_serves() -> None:
    """The chat page once carried a form wired to `/auth/password-login`, a
    route that does not exist, and nothing caught it until a whole-branch
    review read the markup. The sign-in page is the same shape of risk, so
    this reads the REAL web/login.html rather than a tmp_path stand-in."""
    repo_web_root = Path(__file__).resolve().parents[2] / "web"
    client = TestClient(build_app(secret=SECRET, upstream=UPSTREAM, web_root=repo_web_root))

    page = client.get("/login").text

    assert '"/auth/login"' in page
    assert "password-login" not in page
    # The two controls the POST body is built from must exist, with the
    # autocomplete hints a password manager needs to offer the right entry.
    assert 'id="email"' in page and 'autocomplete="username"' in page
    assert 'id="password"' in page and 'autocomplete="current-password"' in page


def test_the_real_login_page_keeps_the_palm_vision_brand_rules() -> None:
    """Palm Vision's design system names these as "do not violate". They are
    the kind of rule a later edit breaks silently — a gradient or a blur reads
    as an improvement to whoever adds it — so they are asserted rather than
    left to memory. Source: the design system's SKILL.md."""
    page = (Path(__file__).resolve().parents[2] / "web" / "login.html").read_text(encoding="utf-8")

    # "No gradients, no glassmorphism, no backdrop blur, no neon."
    for banned in ("linear-gradient", "radial-gradient", "backdrop-filter", "blur("):
        assert banned not in page, f"{banned} violates a Palm Vision do-not-violate rule"

    # "Gold is a highlighter, never a fill." The primary action is green; a
    # gold button is the most likely well-meant breach of this one.
    submit_rule = page.split(".submit{", 1)[1].split("}", 1)[0]
    assert "--pv-green" in submit_rule, "the primary button must be green"
    assert "--pv-gold" not in submit_rule, "gold must not fill the primary button"

    # "Black is for the company NAME only." Body copy is #333.
    assert "color:#000" not in page.replace(" ", "")

    # Inputs must stay at 16px or iOS zooms the viewport on focus, which on a
    # split-screen layout throws the reader into a half-scrolled page.
    field_rule = page.split(".field input{", 1)[1].split("}", 1)[0]
    assert "font-size:16px" in field_rule.replace(" ", "")


def test_health_is_public_and_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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


def test_two_different_forwarded_addresses_yield_two_different_addresses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3: request.client.host is Railway's edge proxy — identical for every
    caller on the internet — so the rate limit must key off the rightmost
    (edge-appended) X-Forwarded-For hop instead. This proves two distinct
    callers, each behind that same edge, are told apart."""
    import michael.gate.app as app_module

    seen: list[str] = []

    def fake_authenticate(email: str, password: str, address: str) -> None:
        seen.append(address)
        return None

    monkeypatch.setattr(app_module, "_authenticate", fake_authenticate)

    client.post(
        "/auth/login",
        json={"email": "a@x.com", "password": "whatever-12345"},
        headers={"X-Forwarded-For": "203.0.113.5"},
    )
    client.post(
        "/auth/login",
        json={"email": "a@x.com", "password": "whatever-12345"},
        headers={"X-Forwarded-For": "198.51.100.9"},
    )

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert seen == ["203.0.113.5", "198.51.100.9"]


def test_client_address_takes_the_rightmost_forwarded_hop_not_the_leftmost(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The TRAP the reviewer named explicitly: trusting the leftmost XFF entry
    (what '--forwarded-allow-ips=*' would give uvicorn) lets an attacker
    supply their own fabricated leading hop and impersonate any address. The
    rightmost entry is the one the trusted edge itself appended."""
    import michael.gate.app as app_module

    seen: list[str] = []

    def _record(email: str, password: str, address: str) -> None:
        seen.append(address)

    monkeypatch.setattr(app_module, "_authenticate", _record)

    client.post(
        "/auth/login",
        json={"email": "a@x.com", "password": "whatever-12345"},
        headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.5"},
    )

    assert seen == ["203.0.113.5"]


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


def test_login_success_is_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T9: the only prior record of who signed in was nothing at all —
    refusal used print(), and success wasn't logged anywhere."""
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: (7, "chat"))
    with caplog.at_level("INFO", logger="michael.gate"):
        response = client.post(
            "/auth/login", json={"email": "reader@x.com", "password": "a-long-enough-password"}
        )
    assert response.status_code == 204
    assert any(
        "login success" in r.message and "user=7" in r.message for r in caplog.records
    )


def test_login_failure_is_logged_with_account_and_address(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: None)
    with caplog.at_level("WARNING", logger="michael.gate"):
        response = client.post(
            "/auth/login",
            json={"email": "attacker@x.com", "password": "wrong-password"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
    assert response.status_code == 401
    assert any(
        "login failed" in r.message
        and "attacker@x.com" in r.message
        and "203.0.113.9" in r.message
        for r in caplog.records
    )


def test_relay_refusal_is_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_relay_success(monkeypatch, frozenset({"owned-1"}))
    with caplog.at_level("WARNING", logger="michael.gate"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
                ws.send_text(json.dumps({"method": "session.list", "params": {}}))
                ws.receive_text()
    assert any("relay refused" in r.message for r in caplog.records)


def test_socket_open_and_close_are_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_relay_success(monkeypatch, frozenset({"owned-1"}))
    frame = {"method": "prompt.submit", "params": {"session_id": "owned-1", "text": "hi"}}
    with caplog.at_level("INFO", logger="michael.gate"):
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text(json.dumps(frame))
            ws.receive_text()
    messages = [r.message for r in caplog.records]
    assert any("socket open" in m for m in messages)
    assert any("socket closed" in m for m in messages)


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


def test_logout_clears_the_cookie_with_matching_attributes(client: TestClient) -> None:
    """Minor: a delete_cookie whose Set-Cookie attributes don't match the
    original cookie's (secure/httponly/samesite) can fail to clear it in some
    browsers -- it describes a different cookie, not an overwrite."""
    response = client.post("/auth/logout", cookies={COOKIE_NAME: _token()})
    assert response.status_code == 204
    lowered = response.headers["set-cookie"].lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
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


def test_a_disabled_accounts_cookie_cannot_open_a_new_socket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I5: disable_user() was checked only at login, so a disabled account
    kept a valid cookie for up to 12h and could still open a fresh /api/ws
    connection with it. relay() now re-checks live account state at accept
    time."""
    import michael.gate.app as app_module

    disabled = User(
        id=1,
        email="gone@x.com",
        display_name="Gone",
        password_hash="irrelevant",
        role="chat",
        disabled_at=datetime.now(UTC),
    )
    monkeypatch.setattr(app_module.user_store, "find_by_id", lambda user_id: disabled)

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}):
            pass

    assert caught.value.code == 4401


def test_an_unknown_account_id_cannot_open_a_socket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt-and-braces: a cookie naming a user_id that no longer exists at all
    (e.g. deleted) must fail the same way as a disabled one."""
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module.user_store, "find_by_id", lambda user_id: None)

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}):
            pass

    assert caught.value.code == 4401


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


def test_a_duplicate_top_level_key_forwards_the_decided_value_not_the_raw_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I4: the exact scenario the reviewer named.
    {"method":"admin.x","method":"session.create"} is syntactically valid
    JSON with a duplicate key; Python's json.loads keeps the LAST one, so
    decide() evaluates this as session.create and allows it. If the gate
    then relayed the ORIGINAL raw text (as it used to), Hermes would receive
    the literal substring "admin.x" too — and a parser that resolves
    duplicate keys differently (keeps the FIRST) could dispatch as admin.x
    instead of the thing that was actually approved. Relaying
    json.dumps(frame) instead means what is forwarded can only ever be the
    one value decide() actually saw."""
    fake_upstream = _patch_relay_success(monkeypatch, frozenset())
    raw = '{"id":"x","method":"admin.x","method":"session.create","params":{"title":"t"}}'

    with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
        ws.send_text(raw)
        ws.receive_text()

    assert len(fake_upstream.sent) == 1
    forwarded = fake_upstream.sent[0]
    assert "admin.x" not in forwarded
    assert json.loads(forwarded) == {
        "id": "x",
        "method": "session.create",
        "params": {"title": "t"},
    }


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
    monkeypatch.setattr(app_module.user_store, "find_by_id", lambda user_id: _enabled_user(user_id))

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
    monkeypatch.setattr(app_module.user_store, "find_by_id", lambda user_id: _enabled_user(user_id))

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.receive_text()

    assert caught.value.code == 4503


# --------------------------------------------------------------------------
# _claim_session_ids_from_reply (Defect 2 / C3; renamed from
# _claim_new_session — Minor: that name, its old docstring, and these tests
# all implied "create only", when the function is equally load-bearing for
# session.resume). Claims both identifiers, and returns them rather than the
# caller re-reading owned_by() from Postgres.
# --------------------------------------------------------------------------


def test_claim_session_ids_from_reply_claims_both_session_identifiers(
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

    result = app_module._claim_session_ids_from_reply(frame, 42)

    assert claimed == [(42, "live-1", "t"), (42, "stored-1", "t")]
    assert result == frozenset({"live-1", "stored-1"})


def test_claim_session_ids_from_reply_ignores_empty_or_missing_identifiers(
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

    result = app_module._claim_session_ids_from_reply(frame, 42)

    assert claimed == []
    assert result == frozenset()


def test_a_resumed_sessions_new_live_id_becomes_usable_immediately(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume-case test the Minor asked for: _claim_session_ids_from_reply
    is exactly as load-bearing for session.resume as for session.create.
    session.resume returns a NEW live session_id for the resumed conversation
    (web/michael.js: `state.live = again.session_id`), and the very next
    prompt.submit names that new id. If this function only claimed
    session.create replies, that prompt.submit would be refused as
    "not_your_session" even though the resume itself was for an owned
    session."""
    import michael.gate.app as app_module

    monkeypatch.setattr(
        app_module.session_store, "owned_by", lambda user_id: frozenset({"stored-1"})
    )
    monkeypatch.setattr(app_module, "fetch_ws_ticket", _fake_fetch_ws_ticket)
    monkeypatch.setattr(
        app_module.user_store, "find_by_id", lambda user_id: _enabled_user(user_id)
    )
    claimed_ids: list[str] = []
    monkeypatch.setattr(
        app_module.session_store,
        "claim",
        lambda user_id, hermes_session_id, title: claimed_ids.append(hermes_session_id),
    )

    resume_reply = json.dumps({"result": {"session_id": "resumed-live-1", "title": "t"}})
    fake_upstream = _ScriptedUpstreamConnection([resume_reply])
    monkeypatch.setattr(app_module.websockets, "connect", _FakeConnect(fake_upstream))

    resume_frame = json.dumps({"method": "session.resume", "params": {"session_id": "stored-1"}})
    submit_frame = json.dumps(
        {"method": "prompt.submit", "params": {"session_id": "resumed-live-1", "text": "hi"}}
    )

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws", cookies={COOKIE_NAME: _token()}) as ws:
            ws.send_text(resume_frame)
            ws.receive_text()  # the session.resume reply, relayed back
            ws.send_text(submit_frame)
            # Forces a deterministic server-initiated close for teardown —
            # see _FakeUpstreamConnection's docstring.
            ws.send_text(json.dumps({"method": "session.list", "params": {}}))
            ws.receive_text()

    assert caught.value.code == 4403
    # The important assertion: prompt.submit naming the freshly resumed live
    # id was actually FORWARDED, not refused.
    assert fake_upstream.sent == [resume_frame, submit_frame]
    assert claimed_ids == ["resumed-live-1"]


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
    monkeypatch.setattr(app_module.user_store, "find_by_id", lambda user_id: _enabled_user(user_id))

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
