from contextlib import contextmanager
from typing import Any

import pytest

import michael.gate.schema as schema_module
from michael.gate.schema import gate_schema_sql


class _FakeCursor:
    """Records every statement handed to execute(); fetchone() answers the
    single ``SELECT 1 FROM pg_roles ...`` probe apply_gate_schema makes."""

    def __init__(self, *, role_exists: bool) -> None:
        self.executed: list[str] = []
        self._role_exists = role_exists

    def execute(self, sql: str, *args: Any) -> None:
        self.executed.append(sql)

    def fetchone(self) -> tuple[int] | None:
        return (1,) if self._role_exists else None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    @contextmanager
    def cursor(self):  # type: ignore[no-untyped-def]
        yield self._cursor


def _patch_writable(monkeypatch: pytest.MonkeyPatch, cursor: _FakeCursor) -> None:
    @contextmanager
    def _fake_writable():  # type: ignore[no-untyped-def]
        yield _FakeConn(cursor)

    monkeypatch.setattr(schema_module, "writable", _fake_writable)


def test_apply_gate_schema_skips_the_revokes_when_michael_ro_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I6: gate/schema.py used to issue the REVOKEs unconditionally, unlike
    its sibling michael/schema.py's apply_schema(), which guards them on
    michael_ro existing. On a database with no michael_ro role (e.g. a fresh
    local Postgres), the unconditional REVOKE fails and rolls back the whole
    statement -- including the CREATE TABLEs -- so runbook Step 3 would
    create nothing at all."""
    cur = _FakeCursor(role_exists=False)
    _patch_writable(monkeypatch, cur)

    schema_module.apply_gate_schema()

    assert any("CREATE TABLE IF NOT EXISTS gate.users" in stmt for stmt in cur.executed)
    assert not any("REVOKE" in stmt for stmt in cur.executed)


def test_apply_gate_schema_applies_the_revokes_when_michael_ro_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur = _FakeCursor(role_exists=True)
    _patch_writable(monkeypatch, cur)

    schema_module.apply_gate_schema()

    assert any(
        "REVOKE ALL ON SCHEMA gate FROM michael_ro" in stmt for stmt in cur.executed
    )


def test_creates_its_own_schema_not_public() -> None:
    sql = gate_schema_sql()
    assert "CREATE SCHEMA IF NOT EXISTS gate" in sql


def test_revokes_everything_from_the_readonly_role() -> None:
    """The answering path must not be able to read credentials, by grant and
    not by convention — the same reasoning that produced michael_ro."""
    sql = gate_schema_sql()
    assert "REVOKE ALL ON SCHEMA gate FROM michael_ro" in sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA gate FROM michael_ro" in sql
    assert (
        "ALTER DEFAULT PRIVILEGES IN SCHEMA gate REVOKE ALL ON TABLES FROM michael_ro"
        in sql
    )
    assert "GRANT" not in sql.replace("REVOKE ALL", "")


def test_is_idempotent() -> None:
    sql = gate_schema_sql()
    for table in ("users", "user_sessions", "login_attempts"):
        assert f"CREATE TABLE IF NOT EXISTS gate.{table}" in sql


def test_role_is_constrained_to_two_values() -> None:
    assert "CHECK (role IN ('admin', 'chat'))" in gate_schema_sql()
