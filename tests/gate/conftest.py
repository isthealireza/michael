"""Shared fixtures for the gate integration suite (pytest.mark.integration).

None of this runs under the default `uv run pytest` (see pyproject.toml's
addopts, `-m "not integration and not network"`); it only matters when
someone runs `uv run pytest -m integration` against a real Postgres carrying
the gate schema, per docs/gate-cutover.md's precondition.
"""

from __future__ import annotations

import pytest

from michael.db import writable
from michael.gate.schema import apply_gate_schema


@pytest.fixture
def gate_tables() -> None:
    """A clean, schema-applied gate database before a test runs.

    C4(a): test_sessions.py's `two_users` fixture created a fixed-email user
    afresh in EVERY test, and several tests in test_users.py did the same,
    with no truncation between them anywhere. `gate.users.email` is NOT NULL
    UNIQUE, so from the second test onward `create_user` raised
    UniqueViolation — on a clean database, five of six tests in
    test_sessions.py errored, and test_users.py was non-idempotent across
    repeated runs for the identical reason. Every test that touches the gate
    schema should depend on this fixture (directly, or transitively through
    a fixture like `two_users`) instead of assuming a database is either
    empty or already in some known state.
    """
    apply_gate_schema()
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE gate.login_attempts, gate.user_sessions, gate.users RESTART IDENTITY CASCADE"
        )
