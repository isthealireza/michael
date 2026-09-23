# michael-gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `michael-gate`, a public-facing service that authenticates named users, restricts them to the four JSON-RPC methods a conversation needs, and isolates each user's sessions — so `michael-hermes` can leave the public internet.

**Architecture:** A new Python service in `src/michael/gate/`. The security-critical logic (cookie signing, rate limiting, the JSON-RPC method allowlist, session-ownership checks) is written as **pure functions with no I/O**, so it is unit-tested in the default `pytest` run. Database access and the upstream WebSocket proxy are thin adapters around those pure cores, tested under the existing `integration` marker. The gate holds the single Hermes `basic_auth` credential; chat users never see it.

**Tech Stack:** Python 3.13, Starlette + Uvicorn (HTTP/WebSocket server), `websockets` (upstream client), `argon2-cffi` (password hashing), `psycopg` (already a dependency), pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-09-23-michael-chat-gate-design.md`

## Global Constraints

- Python `>=3.13,<3.14`. ruff `line-length = 100`, lint select `["B", "E", "F", "I", "UP"]`. mypy `strict = true`.
- Passwords hashed with **argon2id**. Not scrypt — that hash is operator-provisioned once, these are user-chosen and attacker-reachable.
- Session cookies: signed HMAC, `HttpOnly`, `Secure`, `SameSite=Strict`, **expire 12 hours after issue, not renewed on activity**.
- Login rate limits: **5 failures per account per 15 minutes**, **20 per address per 15 minutes**.
- The allowed JSON-RPC methods are exactly four: `session.create`, `session.resume`, `session.status`, `prompt.submit`. Any other method closes the socket and is logged.
- The `gate` schema must have **no grant to `michael_ro`**.
- Do not modify: `agent.disabled_toolsets`, the MCP `tools.include`/`exclude` allowlist, `michael_ro`, `MICHAEL.md`, the three closing blocks.
- Do not import any part of `hermes-webui`'s `api/` layer.
- Tests that need Postgres are marked `@pytest.mark.integration` — the default run excludes them.

**Out of scope for this plan:** the UI merge (theme, mobile layout, session sidebar, markdown, voice). Those are spec phases 2–4 and get their own plan. This plan serves today's `web/michael.html` unchanged.

## File Structure

```
src/michael/gate/__init__.py     package marker
src/michael/gate/schema.py       gate schema DDL; revokes from michael_ro
src/michael/gate/passwords.py    argon2id hash/verify                    PURE
src/michael/gate/cookies.py      signed session cookie mint/verify       PURE
src/michael/gate/ratelimit.py    login-attempt decision                  PURE
src/michael/gate/allowlist.py    JSON-RPC method + ownership decision    PURE
src/michael/gate/users.py        user store (Postgres adapter)
src/michael/gate/sessions.py     session-ownership store (Postgres adapter)
src/michael/gate/upstream.py     ws-ticket + upstream WebSocket client
src/michael/gate/app.py          Starlette routes, login, static, WS relay
src/michael/cli.py               MODIFY: add the `user` subcommand group
gate/Dockerfile                  Railway service image
tests/gate/test_*.py             one module per source module
```

---

### Task 1: Gate schema

**Files:**
- Create: `src/michael/gate/__init__.py`
- Create: `src/michael/gate/schema.py`
- Create: `tests/gate/__init__.py`
- Create: `tests/gate/test_schema.py`

**Interfaces:**
- Consumes: `michael.db.writable` (existing context manager yielding a `psycopg` connection).
- Produces: `gate_schema_sql() -> str`, `apply_gate_schema() -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_schema.py
from michael.gate.schema import gate_schema_sql


def test_creates_its_own_schema_not_public() -> None:
    sql = gate_schema_sql()
    assert "CREATE SCHEMA IF NOT EXISTS gate" in sql


def test_revokes_everything_from_the_readonly_role() -> None:
    """The answering path must not be able to read credentials, by grant and
    not by convention — the same reasoning that produced michael_ro."""
    sql = gate_schema_sql()
    assert "REVOKE ALL ON SCHEMA gate FROM michael_ro" in sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA gate FROM michael_ro" in sql
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA gate REVOKE ALL ON TABLES FROM michael_ro" in sql
    assert "GRANT" not in sql.replace("REVOKE ALL", "")


def test_is_idempotent() -> None:
    sql = gate_schema_sql()
    for table in ("users", "user_sessions", "login_attempts"):
        assert f"CREATE TABLE IF NOT EXISTS gate.{table}" in sql


def test_role_is_constrained_to_two_values() -> None:
    assert "CHECK (role IN ('admin', 'chat'))" in gate_schema_sql()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate'`

- [ ] **Step 3: Write minimal implementation**

Create `src/michael/gate/__init__.py` and `tests/gate/__init__.py` as empty files.

```python
# src/michael/gate/schema.py
"""Gate schema: accounts, session ownership, login attempts.

Its own schema rather than `public`, so a single REVOKE covers the lot. The
answering path connects as `michael_ro`; that role must not be able to read a
password hash even by mistake, which is a grant question and not a code-review
question.
"""

from __future__ import annotations

from michael.db import writable

GATE_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS gate;

CREATE TABLE IF NOT EXISTS gate.users (
    id            bigserial   PRIMARY KEY,
    email         text        NOT NULL UNIQUE,
    display_name  text        NOT NULL,
    password_hash text        NOT NULL,
    role          text        NOT NULL CHECK (role IN ('admin', 'chat')),
    disabled_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gate.user_sessions (
    user_id            bigint      NOT NULL REFERENCES gate.users (id) ON DELETE CASCADE,
    hermes_session_id  text        NOT NULL,
    title              text        NOT NULL DEFAULT '',
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hermes_session_id)
);

CREATE INDEX IF NOT EXISTS user_sessions_user_idx ON gate.user_sessions (user_id);

CREATE TABLE IF NOT EXISTS gate.login_attempts (
    id          bigserial   PRIMARY KEY,
    account_key text        NOT NULL,
    address     text        NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    outcome     text        NOT NULL CHECK (outcome IN ('ok', 'bad_password', 'no_such_user'))
);

CREATE INDEX IF NOT EXISTS login_attempts_account_idx ON gate.login_attempts (account_key, at);
CREATE INDEX IF NOT EXISTS login_attempts_address_idx ON gate.login_attempts (address, at);

REVOKE ALL ON SCHEMA gate FROM michael_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA gate FROM michael_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA gate REVOKE ALL ON TABLES FROM michael_ro;
"""


def gate_schema_sql() -> str:
    """Return the full gate DDL. Idempotent: safe to apply repeatedly."""
    return GATE_SCHEMA_SQL


def apply_gate_schema() -> dict[str, str]:
    """Create the gate schema. Idempotent."""
    with writable() as conn:
        with conn.cursor() as cur:
            cur.execute(gate_schema_sql())
    return {"status": "applied", "schema": "gate"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_schema.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/michael/gate tests/gate && uv run mypy src/michael/gate`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add src/michael/gate tests/gate
git commit -m "feat(gate): add the gate schema, revoked from michael_ro"
```

---

### Task 2: Password hashing

**Files:**
- Modify: `pyproject.toml` (add the `gate` optional-dependency group)
- Create: `src/michael/gate/passwords.py`
- Create: `tests/gate/test_passwords.py`

**Interfaces:**
- Produces: `hash_password(plain: str) -> str`, `verify_password(plain: str, stored: str) -> bool`.

- [ ] **Step 1: Add the dependency group**

In `pyproject.toml`, under `[project.optional-dependencies]`, after the `mcp` entry:

```toml
# Only needed to run michael-gate, the public front door.
gate = [
    "argon2-cffi>=23.1.0,<25",
    "starlette>=0.41.0,<0.48",
    "uvicorn>=0.32.0,<0.40",
    "websockets>=13.0,<16",
]
```

Run: `uv sync --extra gate`

- [ ] **Step 2: Write the failing test**

```python
# tests/gate/test_passwords.py
import pytest

from michael.gate.passwords import hash_password, verify_password


def test_round_trips() -> None:
    assert verify_password("correct horse battery staple",
                           hash_password("correct horse battery staple"))


def test_rejects_the_wrong_password() -> None:
    assert not verify_password("wrong", hash_password("right"))


def test_uses_argon2id_not_scrypt() -> None:
    """Operator-provisioned hashes may be scrypt; user-chosen ones may not."""
    assert hash_password("x").startswith("$argon2id$")


def test_salts_so_two_identical_passwords_differ() -> None:
    assert hash_password("same") != hash_password("same")


def test_a_malformed_stored_hash_is_false_not_an_exception() -> None:
    """A corrupted row must fail the login, not 500 the service."""
    assert not verify_password("anything", "not-a-hash")


@pytest.mark.parametrize("bad", ["", " ", "short"])
def test_refuses_to_hash_a_too_short_password(bad: str) -> None:
    with pytest.raises(ValueError, match="at least 12 characters"):
        hash_password(bad)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_passwords.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.passwords'`

- [ ] **Step 4: Write minimal implementation**

```python
# src/michael/gate/passwords.py
"""Password hashing for gate accounts.

argon2id, not the scrypt used for `dashboard.basic_auth`. That hash is
provisioned once by an operator; these are chosen by users and reachable by an
attacker, which is a different threat and deserves a memory-hard KDF.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

#: Short passwords are the single largest contributor to a guessable account,
#: and this gate is on the open internet. Enforced at hash time so no code path
#: can store one.
MINIMUM_LENGTH = 12

_HASHER = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a new password. Raises ValueError if it is too short."""
    if len(plain) < MINIMUM_LENGTH:
        raise ValueError(f"a password must be at least {MINIMUM_LENGTH} characters")
    return _HASHER.hash(plain)


def verify_password(plain: str, stored: str) -> bool:
    """True when ``plain`` matches ``stored``.

    A malformed stored hash is a failed login, not an exception: a corrupted
    row must not take the service down or leak its state through a 500.
    """
    try:
        return _HASHER.verify(stored, plain)
    except (Argon2Error, InvalidHashError):
        return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_passwords.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/michael/gate/passwords.py tests/gate/test_passwords.py
git commit -m "feat(gate): hash account passwords with argon2id"
```

---

### Task 3: Signed session cookies

**Files:**
- Create: `src/michael/gate/cookies.py`
- Create: `tests/gate/test_cookies.py`

**Interfaces:**
- Produces: `mint(user_id: int, role: str, *, secret: str, now: datetime) -> str`,
  `verify(token: str, *, secret: str, now: datetime) -> CookiePayload | None`,
  `CookiePayload` dataclass with `user_id: int`, `role: str`, `expires_at: datetime`,
  `COOKIE_NAME: str`, `LIFETIME: timedelta`.
- `now` is a parameter, never `datetime.now()` inside — expiry must be testable without sleeping.

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_cookies.py
from datetime import UTC, datetime, timedelta

from michael.gate.cookies import LIFETIME, CookiePayload, mint, verify

SECRET = "a" * 64
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_round_trips() -> None:
    payload = verify(mint(7, "chat", secret=SECRET, now=NOW), secret=SECRET, now=NOW)
    assert payload == CookiePayload(user_id=7, role="chat",
                                    expires_at=NOW + LIFETIME)


def test_lifetime_is_twelve_hours() -> None:
    assert LIFETIME == timedelta(hours=12)


def test_expired_token_is_rejected() -> None:
    token = mint(7, "chat", secret=SECRET, now=NOW)
    assert verify(token, secret=SECRET, now=NOW + LIFETIME + timedelta(seconds=1)) is None


def test_token_valid_one_second_before_expiry() -> None:
    token = mint(7, "chat", secret=SECRET, now=NOW)
    assert verify(token, secret=SECRET, now=NOW + LIFETIME - timedelta(seconds=1)) is not None


def test_a_different_secret_is_rejected() -> None:
    token = mint(7, "chat", secret=SECRET, now=NOW)
    assert verify(token, secret="b" * 64, now=NOW) is None


def test_tampering_with_the_role_is_rejected() -> None:
    """Privilege escalation by cookie edit is the attack this signature stops."""
    token = mint(7, "chat", secret=SECRET, now=NOW)
    body, _, signature = token.partition(".")
    forged = body.replace("chat", "admin") + "." + signature
    assert verify(forged, secret=SECRET, now=NOW) is None


def test_garbage_is_rejected_without_raising() -> None:
    for junk in ["", ".", "no-dot", "a.b.c", "!!!.???"]:
        assert verify(junk, secret=SECRET, now=NOW) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_cookies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.cookies'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/cookies.py
"""Signed session cookies.

The cookie carries the claim; the signature is the only thing that makes it
true. `now` is always a parameter so expiry is tested by arithmetic rather than
by sleeping.
"""

from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

COOKIE_NAME = "michael_session"

#: Not renewed on activity: a stolen cookie has a bounded life, and a reader
#: signing in once a day is an acceptable cost for that.
LIFETIME = timedelta(hours=12)


@dataclass(frozen=True)
class CookiePayload:
    user_id: int
    role: str
    expires_at: datetime


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64encode(hmac.new(secret.encode(), body.encode(), sha256).digest())


def mint(user_id: int, role: str, *, secret: str, now: datetime) -> str:
    """Return a signed token asserting this user and role until expiry."""
    expires_at = now + LIFETIME
    body = _b64encode(
        json.dumps(
            {"uid": user_id, "role": role, "exp": expires_at.timestamp()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return f"{body}.{_sign(body, secret)}"


def verify(token: str, *, secret: str, now: datetime) -> CookiePayload | None:
    """Return the payload when the token is authentic and unexpired, else None.

    Every failure mode returns None. A caller must not be able to tell a forged
    signature from a malformed body from an expired token.
    """
    body, separator, signature = token.partition(".")
    if not separator or not signature:
        return None
    if not hmac.compare_digest(signature, _sign(body, secret)):
        return None
    try:
        claims = json.loads(_b64decode(body))
        expires_at = datetime.fromtimestamp(float(claims["exp"]), tz=UTC)
        user_id = int(claims["uid"])
        role = str(claims["role"])
    except (ValueError, KeyError, TypeError):
        return None
    if now >= expires_at:
        return None
    return CookiePayload(user_id=user_id, role=role, expires_at=expires_at)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_cookies.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/michael/gate/cookies.py tests/gate/test_cookies.py
git commit -m "feat(gate): sign session cookies with a 12-hour lifetime"
```

---

### Task 4: Login rate limiting

**Files:**
- Create: `src/michael/gate/ratelimit.py`
- Create: `tests/gate/test_ratelimit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Attempt` dataclass (`at: datetime`, `outcome: str`),
  `is_locked_out(account_attempts, address_attempts, *, now) -> bool`,
  `ACCOUNT_LIMIT: int`, `ADDRESS_LIMIT: int`, `WINDOW: timedelta`.
- The decision is pure: the caller loads rows, this decides.

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_ratelimit.py
from datetime import UTC, datetime, timedelta

from michael.gate.ratelimit import (
    ACCOUNT_LIMIT,
    ADDRESS_LIMIT,
    WINDOW,
    Attempt,
    is_locked_out,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def failures(count: int, *, ago: timedelta = timedelta(seconds=1)) -> list[Attempt]:
    return [Attempt(at=NOW - ago, outcome="bad_password") for _ in range(count)]


def test_limits_match_the_spec() -> None:
    assert (ACCOUNT_LIMIT, ADDRESS_LIMIT, WINDOW) == (5, 20, timedelta(minutes=15))


def test_allows_below_the_account_limit() -> None:
    assert not is_locked_out(failures(4), [], now=NOW)


def test_locks_out_at_the_account_limit() -> None:
    assert is_locked_out(failures(5), [], now=NOW)


def test_locks_out_at_the_address_limit() -> None:
    assert is_locked_out([], failures(20), now=NOW)


def test_failures_outside_the_window_do_not_count() -> None:
    old = failures(10, ago=WINDOW + timedelta(seconds=1))
    assert not is_locked_out(old, old, now=NOW)


def test_successful_logins_do_not_count_toward_the_limit() -> None:
    """Otherwise an active reader locks themselves out by using the service."""
    ok = [Attempt(at=NOW, outcome="ok") for _ in range(50)]
    assert not is_locked_out(ok, ok, now=NOW)


def test_unknown_account_failures_still_count() -> None:
    """Enumerating addresses must cost the attacker the same as guessing."""
    probes = [Attempt(at=NOW, outcome="no_such_user") for _ in range(20)]
    assert is_locked_out([], probes, now=NOW)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.ratelimit'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/ratelimit.py
"""Login rate limiting.

A pure decision over already-loaded attempts, so the thresholds are tested by
arithmetic rather than against a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

ACCOUNT_LIMIT = 5
ADDRESS_LIMIT = 20
WINDOW = timedelta(minutes=15)

#: A success is not evidence of an attack. Counting it would let an active
#: reader lock themselves out by using the service normally.
FAILURE_OUTCOMES = frozenset({"bad_password", "no_such_user"})


@dataclass(frozen=True)
class Attempt:
    at: datetime
    outcome: str


def _recent_failures(attempts: Sequence[Attempt], now: datetime) -> int:
    cutoff = now - WINDOW
    return sum(1 for a in attempts if a.outcome in FAILURE_OUTCOMES and a.at > cutoff)


def is_locked_out(
    account_attempts: Sequence[Attempt],
    address_attempts: Sequence[Attempt],
    *,
    now: datetime,
) -> bool:
    """True when this login must be refused without checking the password."""
    return (
        _recent_failures(account_attempts, now) >= ACCOUNT_LIMIT
        or _recent_failures(address_attempts, now) >= ADDRESS_LIMIT
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_ratelimit.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/michael/gate/ratelimit.py tests/gate/test_ratelimit.py
git commit -m "feat(gate): rate-limit logins per account and per address"
```

---

### Task 5: The JSON-RPC method allowlist and ownership check

This is the security core of the whole design. It is pure, so every rule is a unit test.

**Files:**
- Create: `src/michael/gate/allowlist.py`
- Create: `tests/gate/test_allowlist.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ALLOWED_METHODS: frozenset[str]`, `Decision` dataclass
  (`allowed: bool`, `reason: str`, `session_id: str | None`),
  `decide(frame: object, *, owned_session_ids: frozenset[str]) -> Decision`.
- `frame` is whatever `json.loads` returned — it may be any type, including not a dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_allowlist.py
import pytest

from michael.gate.allowlist import ALLOWED_METHODS, decide

OWNED = frozenset({"sess-mine"})


def test_the_allowlist_is_exactly_the_four_methods() -> None:
    assert ALLOWED_METHODS == frozenset(
        {"session.create", "session.resume", "session.status", "prompt.submit"}
    )


def test_session_create_is_allowed_and_owns_nothing_yet() -> None:
    d = decide({"id": "c", "method": "session.create", "params": {}}, owned_session_ids=OWNED)
    assert d.allowed and d.session_id is None


@pytest.mark.parametrize("method", ["session.resume", "session.status", "prompt.submit"])
def test_owned_session_is_allowed(method: str) -> None:
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-mine"}}
    assert decide(frame, owned_session_ids=OWNED).allowed


@pytest.mark.parametrize("method", ["session.resume", "session.status", "prompt.submit"])
def test_another_users_session_is_refused(method: str) -> None:
    """Without this, per-user accounts are cosmetic: a session id would be the
    only thing between one reader and another reader's legal questions."""
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-theirs"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "not_your_session"


@pytest.mark.parametrize(
    "method",
    ["session.list", "session.delete", "tools.call", "file.read", "cron.create", "", "prompt"],
)
def test_every_other_method_is_refused(method: str) -> None:
    frame = {"id": "x", "method": method, "params": {"session_id": "sess-mine"}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "method_not_allowed"


def test_a_method_bearing_session_without_a_session_id_is_refused() -> None:
    d = decide({"id": "x", "method": "prompt.submit", "params": {}}, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "missing_session_id"


@pytest.mark.parametrize("frame", [None, [], "text", 7, {"params": {}}, {"method": 7}])
def test_a_frame_that_is_not_a_well_formed_call_is_refused(frame: object) -> None:
    """Refuse by default. An unparseable frame must never reach the gateway."""
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "malformed_frame"


def test_params_that_are_not_an_object_are_refused() -> None:
    d = decide({"method": "prompt.submit", "params": []}, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "malformed_frame"


def test_a_non_string_session_id_is_refused() -> None:
    frame = {"method": "prompt.submit", "params": {"session_id": {"$ne": None}}}
    d = decide(frame, owned_session_ids=OWNED)
    assert not d.allowed and d.reason == "missing_session_id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_allowlist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.allowlist'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/allowlist.py
"""What a chat user may send over /api/ws.

`web/michael.js` records that prompt.submit on this socket is "the single
server-side choke point every dashboard submit passes through". Allowing the
PATH therefore allows every method the dashboard uses, session enumeration
included. The gate reads each frame and permits four methods and no others.

Refusal is the default: anything not positively recognised is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

#: session.create opens a conversation. The other three act on one that already
#: exists, and so must be checked against ownership.
ALLOWED_METHODS = frozenset(
    {"session.create", "session.resume", "session.status", "prompt.submit"}
)

_NEEDS_OWNED_SESSION = frozenset({"session.resume", "session.status", "prompt.submit"})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    session_id: str | None = None


def decide(frame: object, *, owned_session_ids: frozenset[str]) -> Decision:
    """Decide whether one client frame may be relayed upstream."""
    if not isinstance(frame, dict):
        return Decision(False, "malformed_frame")

    method = frame.get("method")
    if not isinstance(method, str):
        return Decision(False, "malformed_frame")

    params = frame.get("params", {})
    if not isinstance(params, dict):
        return Decision(False, "malformed_frame")

    if method not in ALLOWED_METHODS:
        return Decision(False, "method_not_allowed")

    if method not in _NEEDS_OWNED_SESSION:
        return Decision(True, "ok")

    session_id = params.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return Decision(False, "missing_session_id")

    if session_id not in owned_session_ids:
        return Decision(False, "not_your_session")

    return Decision(True, "ok", session_id=session_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_allowlist.py -v`
Expected: PASS (24 tests, counting parametrised cases)

- [ ] **Step 5: Commit**

```bash
git add src/michael/gate/allowlist.py tests/gate/test_allowlist.py
git commit -m "feat(gate): allowlist four JSON-RPC methods and check session ownership"
```

---

### Task 6: User store and the `michael user` CLI

**Files:**
- Create: `src/michael/gate/users.py`
- Modify: `src/michael/cli.py` (add the `user` subcommand group; the parser block sits after the existing `ingest-file` parser, the dispatch arm after the existing `ingest` case)
- Create: `tests/gate/test_users.py`

**Interfaces:**
- Consumes: `michael.db.writable`, `michael.gate.passwords.hash_password`,
  `michael.gate.ratelimit.Attempt`.
- Produces: `User` dataclass (`id: int`, `email: str`, `display_name: str`,
  `password_hash: str`, `role: str`, `disabled_at: datetime | None`);
  `create_user(email, display_name, password, role) -> User`;
  `find_by_email(email) -> User | None`; `list_users() -> list[User]`;
  `disable_user(email) -> bool`; `record_attempt(account_key, address, outcome) -> None`;
  `recent_attempts(account_key, address) -> tuple[list[Attempt], list[Attempt]]`.

- [ ] **Step 1: Write the failing test**

Database-backed, so marked `integration` — the default run excludes it, matching this repo's existing convention.

```python
# tests/gate/test_users.py
import pytest

pytestmark = pytest.mark.integration

from michael.gate import users  # noqa: E402
from michael.gate.schema import apply_gate_schema  # noqa: E402


@pytest.fixture(autouse=True)
def gate_schema() -> None:
    apply_gate_schema()


def test_creates_and_finds_a_user() -> None:
    users.create_user("reader@example.com", "A Reader", "a-long-enough-password", "chat")
    found = users.find_by_email("reader@example.com")
    assert found is not None
    assert found.display_name == "A Reader"
    assert found.role == "chat"


def test_does_not_store_the_password_in_clear() -> None:
    users.create_user("clear@example.com", "X", "a-long-enough-password", "chat")
    found = users.find_by_email("clear@example.com")
    assert found is not None
    assert "a-long-enough-password" not in found.password_hash


def test_email_is_matched_case_insensitively() -> None:
    users.create_user("Mixed@Example.com", "X", "a-long-enough-password", "chat")
    assert users.find_by_email("mixed@example.com") is not None


def test_unknown_email_is_none_not_an_error() -> None:
    assert users.find_by_email("nobody@example.com") is None


def test_disable_marks_the_user_and_is_reported() -> None:
    users.create_user("gone@example.com", "X", "a-long-enough-password", "chat")
    assert users.disable_user("gone@example.com") is True
    found = users.find_by_email("gone@example.com")
    assert found is not None and found.disabled_at is not None


def test_disabling_an_unknown_user_returns_false() -> None:
    assert users.disable_user("nobody@example.com") is False


def test_rejects_an_unknown_role() -> None:
    with pytest.raises(ValueError, match="role must be"):
        users.create_user("bad@example.com", "X", "a-long-enough-password", "superuser")


def test_attempts_are_recorded_and_read_back_per_account_and_address() -> None:
    users.record_attempt("attempts@example.com", "203.0.113.5", "bad_password")
    account, address = users.recent_attempts("attempts@example.com", "203.0.113.5")
    assert len(account) == 1 and len(address) == 1
    assert account[0].outcome == "bad_password"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_users.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.users'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/users.py
"""The account store.

Thin adapter over Postgres. Every decision this module could make lives in
passwords.py or ratelimit.py instead, so the rules are unit-tested without a
database and this file stays readable as plain SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from michael.db import writable
from michael.gate.passwords import hash_password
from michael.gate.ratelimit import WINDOW, Attempt

ROLES = ("admin", "chat")


@dataclass(frozen=True)
class User:
    id: int
    email: str
    display_name: str
    password_hash: str
    role: str
    disabled_at: datetime | None


_COLUMNS = "id, email, display_name, password_hash, role, disabled_at"


def _row_to_user(row: dict[str, object]) -> User:
    return User(
        id=int(row["id"]),  # type: ignore[arg-type]
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        password_hash=str(row["password_hash"]),
        role=str(row["role"]),
        disabled_at=row["disabled_at"],  # type: ignore[arg-type]
    )


def create_user(email: str, display_name: str, password: str, role: str) -> User:
    """Create an account. Raises ValueError on an unknown role or short password."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, not {role!r}")
    password_hash = hash_password(password)
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO gate.users (email, display_name, password_hash, role) "
            f"VALUES (lower(%s), %s, %s, %s) RETURNING {_COLUMNS}",
            (email, display_name, password_hash, role),
        )
        row = cur.fetchone()
    assert row is not None
    return _row_to_user(row)


def find_by_email(email: str) -> User | None:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM gate.users WHERE email = lower(%s)", (email,))
        row = cur.fetchone()
    return _row_to_user(row) if row else None


def list_users() -> list[User]:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM gate.users ORDER BY email")
        return [_row_to_user(row) for row in cur.fetchall()]


def disable_user(email: str) -> bool:
    """Mark the account disabled. False when there was no such account."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE gate.users SET disabled_at = now() "
            "WHERE email = lower(%s) AND disabled_at IS NULL",
            (email,),
        )
        return cur.rowcount > 0


def record_attempt(account_key: str, address: str, outcome: str) -> None:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gate.login_attempts (account_key, address, outcome) "
            "VALUES (lower(%s), %s, %s)",
            (account_key, address, outcome),
        )


def recent_attempts(account_key: str, address: str) -> tuple[list[Attempt], list[Attempt]]:
    """Attempts inside the rate-limit window, as (for this account, for this address)."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT account_key, address, at, outcome FROM gate.login_attempts "
            "WHERE at > now() - %s AND (account_key = lower(%s) OR address = %s)",
            (WINDOW, account_key, address),
        )
        rows = cur.fetchall()
    account = [
        Attempt(at=r["at"], outcome=str(r["outcome"]))  # type: ignore[arg-type]
        for r in rows
        if str(r["account_key"]) == account_key.lower()
    ]
    by_address = [
        Attempt(at=r["at"], outcome=str(r["outcome"]))  # type: ignore[arg-type]
        for r in rows
        if str(r["address"]) == address
    ]
    return account, by_address
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_users.py -v -m integration`
Expected: PASS (8 tests). Requires the `michael-postgres` container.

- [ ] **Step 5: Add the CLI subcommands**

In `src/michael/cli.py`, in `build_parser()`, after the `ingest-file` parser block:

```python
    user = sub.add_parser("user", help="manage michael-gate accounts")
    user_sub = user.add_subparsers(dest="user_command", required=True)

    user_add = user_sub.add_parser("add", help="create an account")
    user_add.add_argument("email")
    user_add.add_argument("--name", required=True, help="display name")
    user_add.add_argument("--role", required=True, choices=["admin", "chat"])

    user_sub.add_parser("list", help="list accounts")

    user_disable = user_sub.add_parser("disable", help="disable an account")
    user_disable.add_argument("email")
```

In `_run()`, add a `case` arm alongside the existing ones:

```python
        case "user":
            from getpass import getpass

            from michael.gate import users as gate_users

            match args.user_command:
                case "add":
                    # Never taken as an argv argument: it would land in the
                    # operator's shell history and in the process table.
                    password = getpass("password: ")
                    if password != getpass("repeat: "):
                        print("passwords did not match", file=sys.stderr)
                        return 1
                    # Caught here rather than by adding ValueError to
                    # EXPECTED_FAILURES: that tuple names failures the design
                    # produces on purpose, and a bare ValueError there would
                    # swallow genuine bugs across every other subcommand.
                    try:
                        created = gate_users.create_user(
                            args.email, args.name, password, args.role
                        )
                    except ValueError as exc:
                        print(str(exc), file=sys.stderr)
                        return 1
                    _print({"email": created.email, "role": created.role})
                case "list":
                    _print(
                        [
                            {
                                "email": u.email,
                                "name": u.display_name,
                                "role": u.role,
                                "disabled": u.disabled_at is not None,
                            }
                            for u in gate_users.list_users()
                        ]
                    )
                case "disable":
                    if not gate_users.disable_user(args.email):
                        print(f"no such active account: {args.email}", file=sys.stderr)
                        return 1
                    _print({"disabled": args.email})
```

- [ ] **Step 6: Verify the CLI parses without a database**

Run: `uv run michael user --help && uv run michael user add --help`
Expected: both print usage and exit 0. Do **not** add `ValueError` to `EXPECTED_FAILURES`; the `case` arm above catches it locally.

- [ ] **Step 7: Commit**

```bash
git add src/michael/gate/users.py src/michael/cli.py tests/gate/test_users.py
git commit -m "feat(gate): add the account store and michael user add|list|disable"
```

---

### Task 7: Session ownership store

**Files:**
- Create: `src/michael/gate/sessions.py`
- Create: `tests/gate/test_sessions.py`

**Interfaces:**
- Consumes: `michael.db.writable`.
- Produces: `claim(user_id: int, hermes_session_id: str, title: str) -> None`;
  `owned_by(user_id: int) -> frozenset[str]`;
  `list_for_user(user_id: int) -> list[OwnedSession]`;
  `OwnedSession` dataclass (`hermes_session_id: str`, `title: str`, `created_at: datetime`).

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_sessions.py
import pytest

pytestmark = pytest.mark.integration

from michael.gate import sessions, users  # noqa: E402
from michael.gate.schema import apply_gate_schema  # noqa: E402


@pytest.fixture
def two_users() -> tuple[int, int]:
    apply_gate_schema()
    a = users.create_user("owner-a@example.com", "A", "a-long-enough-password", "chat")
    b = users.create_user("owner-b@example.com", "B", "a-long-enough-password", "chat")
    return a.id, b.id


def test_claimed_session_is_owned(two_users: tuple[int, int]) -> None:
    a, _ = two_users
    sessions.claim(a, "sess-1", "MICHAEL web — 23 Sep")
    assert "sess-1" in sessions.owned_by(a)


def test_one_users_session_is_not_owned_by_another(two_users: tuple[int, int]) -> None:
    a, b = two_users
    sessions.claim(a, "sess-1", "t")
    assert "sess-1" not in sessions.owned_by(b)


def test_owned_by_is_empty_for_a_user_with_no_sessions(two_users: tuple[int, int]) -> None:
    _, b = two_users
    assert sessions.owned_by(b) == frozenset()


def test_claiming_the_same_session_twice_is_idempotent(two_users: tuple[int, int]) -> None:
    a, _ = two_users
    sessions.claim(a, "sess-dup", "t")
    sessions.claim(a, "sess-dup", "t")
    assert len(sessions.list_for_user(a)) == 1


def test_a_session_cannot_be_stolen_by_a_second_claim(two_users: tuple[int, int]) -> None:
    """Ownership is first-writer-wins; a later claim must not reassign it."""
    a, b = two_users
    sessions.claim(a, "sess-contested", "t")
    sessions.claim(b, "sess-contested", "t")
    assert "sess-contested" in sessions.owned_by(a)
    assert "sess-contested" not in sessions.owned_by(b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_sessions.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.sessions'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/sessions.py
"""Which gate user owns which Hermes session.

Hermes session identifiers are the only handle a client has on a conversation.
Without an ownership record, one reader holding another's identifier can resume
their conversation, so this table is what makes per-user accounts mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from michael.db import writable


@dataclass(frozen=True)
class OwnedSession:
    hermes_session_id: str
    title: str
    created_at: datetime


def claim(user_id: int, hermes_session_id: str, title: str) -> None:
    """Record ownership. First writer wins; a later claim never reassigns."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gate.user_sessions (user_id, hermes_session_id, title) "
            "VALUES (%s, %s, %s) ON CONFLICT (hermes_session_id) DO NOTHING",
            (user_id, hermes_session_id, title),
        )


def owned_by(user_id: int) -> frozenset[str]:
    """Every session identifier this user may act on."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT hermes_session_id FROM gate.user_sessions WHERE user_id = %s", (user_id,)
        )
        return frozenset(str(row["hermes_session_id"]) for row in cur.fetchall())


def list_for_user(user_id: int) -> list[OwnedSession]:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT hermes_session_id, title, created_at FROM gate.user_sessions "
            "WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [
            OwnedSession(
                hermes_session_id=str(row["hermes_session_id"]),
                title=str(row["title"]),
                created_at=row["created_at"],  # type: ignore[arg-type]
            )
            for row in cur.fetchall()
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_sessions.py -v -m integration`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/michael/gate/sessions.py tests/gate/test_sessions.py
git commit -m "feat(gate): record which user owns which Hermes session"
```

---

### Task 8: Upstream client

**Files:**
- Create: `src/michael/gate/upstream.py`
- Create: `tests/gate/test_upstream.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `UpstreamConfig` dataclass (`base_url: str`, `username: str`, `password: str`);
  `basic_auth_header(config) -> str`; `ws_url(config, ticket) -> str`;
  `async fetch_ws_ticket(config, client) -> str`;
  `GATEWAY_PROTOCOL: str`.
- URL construction and header construction are pure and unit-tested; only the
  HTTP call itself is async.

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_upstream.py
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


def test_trailing_slash_on_base_url_does_not_double(  ) -> None:
    trailing = UpstreamConfig(base_url="http://host:1/", username="u", password="p")
    assert "//api/ws" not in ws_url(trailing, "t").removeprefix("ws://")


@pytest.mark.parametrize("bad", ["", "ftp://host", "host-without-scheme"])
def test_an_unusable_base_url_is_refused_at_construction(bad: str) -> None:
    with pytest.raises(ValueError, match="must start with http"):
        ws_url(UpstreamConfig(base_url=bad, username="u", password="p"), "t")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_upstream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.upstream'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/upstream.py
"""Talking to michael-hermes.

The gate holds the single Hermes basic_auth credential and presents it upstream.
A chat user never sees it, which is the point: the credential is all-or-nothing,
so it must not leave this process.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
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
    password: str


def _normalised_base(config: UpstreamConfig) -> str:
    if not config.base_url.startswith(("http://", "https://")):
        raise ValueError(f"upstream base_url must start with http:// or https://, got {config.base_url!r}")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_upstream.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/michael/gate/upstream.py tests/gate/test_upstream.py
git commit -m "feat(gate): build upstream ws-ticket and socket URLs"
```

---

### Task 9: The gate application

**Files:**
- Create: `src/michael/gate/app.py`
- Create: `tests/gate/test_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build_app(*, secret: str, upstream: UpstreamConfig, web_root: Path) -> Starlette`.
- Routes: `GET /` (the chat page, requires a session), `GET /login` (the form),
  `POST /auth/login`, `POST /auth/logout`, `GET /static/{path}`, `WS /api/ws`.
- `/api/auth/ws-ticket` is **not** exposed: the gate obtains the ticket itself.

- [ ] **Step 1: Write the failing test**

```python
# tests/gate/test_app.py
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from michael.gate.app import build_app
from michael.gate.upstream import UpstreamConfig

UPSTREAM = UpstreamConfig(base_url="http://upstream.invalid", username="u", password="p")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "michael.html").write_text("<html>michael</html>", encoding="utf-8")
    return TestClient(build_app(secret="s" * 64, upstream=UPSTREAM, web_root=tmp_path))


def test_the_chat_page_requires_a_session(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_login_page_is_public(client: TestClient) -> None:
    assert client.get("/login").status_code == 200


def test_the_websocket_refuses_an_unauthenticated_client(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws"):
            pass
    assert caught.value.code == 4401


def test_a_bad_login_does_not_reveal_whether_the_account_exists(client: TestClient) -> None:
    """Enumerating valid addresses must not be possible from the response."""
    absent = client.post("/auth/login", json={"email": "nobody@x.com", "password": "wrong-password"})
    assert absent.status_code == 401
    assert absent.json() == {"detail": "invalid email or password"}


def test_the_ws_ticket_endpoint_is_not_proxied(client: TestClient) -> None:
    """The gate fetches its own ticket. Exposing this would hand a client a
    credential-backed handle on the gateway."""
    assert client.post("/api/auth/ws-ticket", json={}).status_code == 404


@pytest.mark.parametrize("path", ["/settings", "/api/sessions", "/assets/index.js", "/health/../"])
def test_no_other_dashboard_route_is_reachable(client: TestClient, path: str) -> None:
    assert client.get(path, follow_redirects=False).status_code in (303, 404)


def test_session_cookie_is_hardened(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import michael.gate.app as app_module

    monkeypatch.setattr(app_module, "_authenticate", lambda email, password, address: (1, "chat"))
    response = client.post(
        "/auth/login", json={"email": "reader@x.com", "password": "a-long-enough-password"}
    )
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gate/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'michael.gate.app'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/michael/gate/app.py
"""The public front door.

The only service on the internet. It authenticates, then relays a strictly
filtered subset of /api/ws to michael-hermes, which is reachable only on the
private network.
"""

from __future__ import annotations

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
            subprotocols=[GATEWAY_PROTOCOL],
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

            import asyncio

            done, pending = await asyncio.wait(
                [asyncio.create_task(client_to_upstream()),
                 asyncio.create_task(upstream_to_client())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    routes = [
        Route("/", chat_page),
        Route("/login", login_page),
        Route("/auth/login", login, methods=["POST"]),
        Route("/auth/logout", logout, methods=["POST"]),
        WebSocketRoute("/api/ws", relay),
    ]
    app = Starlette(routes=routes)
    app.mount("/static", StaticFiles(directory=web_root), name="static")
    return app


def _claim_new_session(text: str, user_id: int) -> None:
    """Record ownership when the gateway reports a newly created session."""
    try:
        frame = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(frame, dict):
        return
    result = frame.get("result")
    if not isinstance(result, dict):
        return
    session_id = result.get("session_id")
    if isinstance(session_id, str) and session_id:
        session_store.claim(user_id, session_id, str(result.get("title", "")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/gate/test_app.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the whole default suite for regressions**

Run: `uv run pytest`
Expected: PASS, no previously-passing test broken.

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src tests && uv run mypy src/michael/gate`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add src/michael/gate/app.py tests/gate/test_app.py
git commit -m "feat(gate): serve the chat page and relay a filtered /api/ws"
```

---

### Task 10: Point michael.js at the gate

`web/michael.js` currently calls `/api/auth/ws-ticket` itself. The gate does not expose that endpoint, so the page must connect to `/api/ws` directly and let the gate handle ticketing.

**Files:**
- Modify: `web/michael.js` (the `connect()` function, around lines 52–58)
- Modify: `web/michael.test.js`
- Modify: `hermes/Dockerfile` (remove the asset-injection block)

**Interfaces:**
- Consumes: the `WS /api/ws` route from Task 9.
- Produces: no new exports.

- [ ] **Step 1: Export `connect` for the test harness**

`web/michael.test.js` requires the real functions from `michael.js` through the
Node-only guard at the end of that file. `connect` is not in it yet. Extend the
existing export line:

```javascript
  module.exports = { renderAnswer, headingMatch, NOT_COVERED, toolLabel, inline, esc, connect };
```

- [ ] **Step 2: Write the failing test**

Add to `web/michael.test.js`. The file already installs its DOM stub before
requiring `michael.js`; add `connect` to the destructured require on line 41,
then append:

```javascript
test("connect() does not request a ws ticket — the gate authenticates the socket", async () => {
  const fetched = [];
  const originalFetch = global.fetch;
  const originalWebSocket = global.WebSocket;
  global.fetch = async (path) => {
    fetched.push(String(path));
    return { ok: true, text: async () => '{"ticket":"t"}' };
  };
  // Never opens: the stub records the URL and stays silent, so connect()'s
  // open handler never fires and the promise never settles. Only the calls
  // made before that point are under test here.
  global.WebSocket = function (url) { this.url = url; this.addEventListener = () => {}; };
  try {
    await Promise.race([connect(), new Promise((r) => setTimeout(r, 50))]);
    assert.ok(!fetched.some((p) => p.includes("ws-ticket")),
      `expected no ws-ticket call, got ${JSON.stringify(fetched)}`);
  } finally {
    global.fetch = originalFetch;
    global.WebSocket = originalWebSocket;
  }
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `node --test web/`
Expected: FAIL — the assertion reports a `/api/auth/ws-ticket` call.

- [ ] **Step 4: Change `connect()`**

Replace the first three lines of `connect()` in `web/michael.js`:

```javascript
async function connect() {
  /* No ws-ticket call: michael-gate authenticates this socket from the session
   * cookie and obtains the upstream ticket itself, so a client never handles a
   * credential-backed handle on the gateway. */
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${scheme}//${location.host}/api/ws`, [GATEWAY_PROTOCOL]);
```

- [ ] **Step 5: Run test to verify it passes**

Run: `node --test web/`
Expected: PASS, including the file's pre-existing tests.

- [ ] **Step 6: Remove the asset-injection block from the Hermes image**

In `hermes/Dockerfile`, delete the `COPY web/michael.html web/michael.js /tmp/michael-web/` line, the `RUN set -eu ... rm -rf /tmp/michael-web` block that follows it, and the long comment above them. The page is served by the gate now, so the `/assets/` immutable-cache workaround it existed to defeat is no longer reachable.

Run: `docker build -f hermes/Dockerfile -t michael-hermes:gate-test .`
Expected: builds clean.

- [ ] **Step 7: Commit**

```bash
git add web/michael.js web/michael.test.js hermes/Dockerfile
git commit -m "feat(gate): serve the chat page from the gate, not the Hermes image"
```

---

### Task 11: Container, Railway service, and cutover

**Files:**
- Create: `gate/Dockerfile`
- Create: `gate/entrypoint.sh`
- Create: `docs/gate-cutover.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `michael.gate.app.build_app`.
- Produces: a runnable container; the `michael-gate` Railway service.

- [ ] **Step 1: Write the container**

```dockerfile
# gate/Dockerfile — michael-gate, the only service on the public internet.
FROM python:3.13-slim

WORKDIR /opt/michael
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY web ./web

RUN pip install --no-cache-dir uv \
    && uv pip install --system "/opt/michael[gate]"

COPY gate/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Never root. The gate parses attacker-controlled input by design.
RUN useradd --uid 10000 --no-create-home gate
USER 10000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

```sh
#!/bin/sh
# gate/entrypoint.sh
set -eu
: "${GATE_SECRET:?GATE_SECRET must be set}"
: "${HERMES_BASE_URL:?HERMES_BASE_URL must be set}"
: "${HERMES_USERNAME:?HERMES_USERNAME must be set}"
: "${HERMES_PASSWORD:?HERMES_PASSWORD must be set}"
exec uvicorn --factory michael.gate.wsgi:app \
     --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers
```

- [ ] **Step 2: Add the factory the entrypoint names**

```python
# src/michael/gate/wsgi.py
"""Uvicorn entry point. Reads the environment, builds the app."""

from __future__ import annotations

import os
from pathlib import Path

from starlette.applications import Starlette

from michael.gate.app import build_app
from michael.gate.upstream import UpstreamConfig


def app() -> Starlette:
    return build_app(
        secret=os.environ["GATE_SECRET"],
        upstream=UpstreamConfig(
            base_url=os.environ["HERMES_BASE_URL"],
            username=os.environ["HERMES_USERNAME"],
            password=os.environ["HERMES_PASSWORD"],
        ),
        web_root=Path(os.environ.get("GATE_WEB_ROOT", "/opt/michael/web")),
    )
```

- [ ] **Step 3: Document the required variables**

Append to `.env.example`:

```
# michael-gate. GATE_SECRET signs session cookies: 64 random hex characters,
# rotate it and every session is invalidated.
GATE_SECRET=
HERMES_BASE_URL=http://michael-hermes.railway.internal:9119
HERMES_USERNAME=michael
HERMES_PASSWORD=
```

- [ ] **Step 4: Verify the container locally against the running stack**

```bash
docker build -f gate/Dockerfile -t michael-gate:dev .
docker run --rm -p 8080:8080 --env-file .env \
  -e HERMES_BASE_URL=http://host.docker.internal:9119 michael-gate:dev
```

Expected: `GET http://localhost:8080/login` returns 200; `GET http://localhost:8080/` redirects to `/login`.

- [ ] **Step 5: Write the cutover runbook**

Create `docs/gate-cutover.md` recording, in order: create the `michael-gate` Railway service in project `michael` (`693389ce-128e-469f-ab3b-81901cdc4d8a`); set its four variables; `uv run michael user add <you> --role admin`; verify sign-in and one full answer end to end through the gate; **only then** remove the `michael-hermes` service domain `michael-hermes-production.up.railway.app`; verify the gate still answers and that the old domain is gone. Record the rollback: re-generate the `michael-hermes` domain.

The order is not advisory. The spec records that removing the domain before an `admin` account works leaves the dashboard unreachable.

- [ ] **Step 6: Commit**

```bash
git add gate/ src/michael/gate/wsgi.py .env.example docs/gate-cutover.md
git commit -m "feat(gate): containerise michael-gate and document the domain cutover"
```

- [ ] **Step 7: Deploy and cut over**

Follow `docs/gate-cutover.md` exactly. Do not remove the `michael-hermes` domain until an `admin` account has signed in through the gate and received a complete answer.

---

## Self-Review

**Spec coverage.** Identity → Tasks 2, 3, 6. Method allowlist → Task 5, wired in Task 9. Session ownership → Tasks 5, 7, wired in Task 9. Data model → Task 1. Provisioning CLI → Task 6. Gate serves the UI → Tasks 9, 10. Hermes leaves the public internet → Task 11. `gate` schema revoked from `michael_ro` → Task 1. Rate limits → Task 4. Cookie hardening → Tasks 3, 9. Testing requirements from the spec's Testing section → Tasks 5, 7, 9 (method rejection, cross-user refusal, forged cookie, rate limiting, no dashboard route reachable).

Spec phases 2–4 (theme, mobile, session sidebar, markdown, voice) are deliberately not covered here and need a second plan. `web/michael.test.js` coverage of `reasoning.delta` belongs with that plan's rendering work, not this one.

**Open items carried from the spec.** `session.status` stays in the allowlist for now; whether the sidebar needs it is decided in the UI plan. Administrator access to the Hermes dashboard after cutover is the Railway private network, recorded in `docs/gate-cutover.md`.

**Type consistency.** `UpstreamConfig`, `Decision`, `CookiePayload`, `Attempt`, `User`, `OwnedSession` are each defined once and used with the same field names throughout. `owned_session_ids` is `frozenset[str]` at every call site. `decide()` returns `Decision` in every branch.
