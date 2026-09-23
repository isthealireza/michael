"""Gate schema: accounts, session ownership, login attempts.

Its own schema rather than `public`, so a single REVOKE covers the lot. The
answering path connects as `michael_ro`; that role must not be able to read a
password hash even by mistake, which is a grant question and not a code-review
question.
"""

from __future__ import annotations

from michael.db import writable

_TABLES_SQL = """
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
"""

#: I6: gated on `michael_ro` existing, mirroring michael/schema.py's
#: apply_schema(). Without the guard, applying this DDL on a database that
#: has no `michael_ro` role (e.g. a fresh local/dev Postgres) fails on the
#: first REVOKE and rolls back the whole statement — including the CREATE
#: TABLEs above it — so runbook Step 3 creates nothing at all.
_REVOKES_SQL = """
REVOKE ALL ON SCHEMA gate FROM michael_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA gate FROM michael_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA gate REVOKE ALL ON TABLES FROM michael_ro;
"""

#: The full DDL, unconditional REVOKEs included. Used by tests and by anyone
#: wanting to see the whole picture in one string; `apply_gate_schema()` itself
#: applies the two halves separately so the REVOKEs can be skipped when
#: `michael_ro` does not exist yet (see `_REVOKES_SQL`'s docstring above).
GATE_SCHEMA_SQL = _TABLES_SQL + _REVOKES_SQL


def gate_schema_sql() -> str:
    """Return the full gate DDL. Idempotent: safe to apply repeatedly."""
    return GATE_SCHEMA_SQL


def apply_gate_schema() -> dict[str, str]:
    """Create the gate schema. Idempotent.

    Revokes `michael_ro`'s access only when that role exists, so this does
    not hard-fail (and roll back the table creation with it) on a database
    where it has not been provisioned yet.
    """
    with writable() as conn:
        with conn.cursor() as cur:
            cur.execute(_TABLES_SQL)
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'michael_ro'")
            if cur.fetchone() is not None:
                cur.execute(_REVOKES_SQL)
    return {"status": "applied", "schema": "gate"}
