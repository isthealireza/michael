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
