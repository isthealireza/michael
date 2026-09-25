"""Schema migrations: loading them, and applying them to a real database.

``schema.apply_schema()`` is ``CREATE TABLE IF NOT EXISTS`` throughout and by
design never alters an existing table, so adding ``provisions.unit_type`` to a
database already holding the corpus needs a migration. The three properties a
migration has to have are all testable, and all tested here rather than
asserted in a docstring:

* it applies;
* it rolls back;
* applying it twice is safe.

The database-backed half is marked ``integration``: it needs the container up
and runs against ``michael_test`` (see tests/conftest.py), never against the
developer's real local corpus and never against production.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from michael import migrations
from michael.db import readonly
from michael.migrations import MigrationError, load_migrations

# --- loading (no database) -------------------------------------------------


def test_the_repository_migrations_load() -> None:
    loaded = load_migrations()
    assert [m.id for m in loaded] == sorted(m.id for m in loaded)
    assert "0001" in {m.id for m in loaded}


def test_every_migration_on_disk_has_a_reverse() -> None:
    """Loading is what enforces reversibility. A migration whose ``.down.sql``
    was forgotten is not loadable, so an irreversible one cannot reach the
    database by omission."""
    for migration in load_migrations():
        assert migration.down.strip()


def test_a_migration_without_a_reverse_is_refused(tmp_path: Path) -> None:
    (tmp_path / "0001_no_way_back.up.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="no 0001_no_way_back.down.sql"):
        load_migrations(tmp_path)


def test_a_misnamed_migration_is_refused(tmp_path: Path) -> None:
    (tmp_path / "add_a_column.up.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "add_a_column.down.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="expected"):
        load_migrations(tmp_path)


def test_a_duplicate_migration_id_is_refused(tmp_path: Path) -> None:
    for name in ("0001_first", "0001_second"):
        (tmp_path / f"{name}.up.sql").write_text("SELECT 1;", encoding="utf-8")
        (tmp_path / f"{name}.down.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate migration id"):
        load_migrations(tmp_path)


def test_the_recorded_hash_is_of_the_forward_sql() -> None:
    """The ledger stores this, so an edit to an applied migration is reported
    as drift rather than silently re-run or silently ignored."""
    import hashlib

    migration = next(m for m in load_migrations() if m.id == "0001")
    assert migration.sha256 == hashlib.sha256(migration.up.encode("utf-8")).hexdigest()


def test_0001_declares_the_same_unit_types_as_the_schema() -> None:
    """A fresh database is built by ``schema.apply_schema()`` and an existing
    one by this migration. If the two disagree, a migrated database and a
    newly created one are not the same database."""
    from michael.schema import UNIT_TYPES, schema_sql

    migration = next(m for m in load_migrations() if m.id == "0001")
    for unit_type in UNIT_TYPES:
        assert f"'{unit_type}'" in migration.up
        assert f"'{unit_type}'" in schema_sql(8)


# --- applying (needs the container) ----------------------------------------


@pytest.fixture
def migrated() -> Iterator[None]:
    """Leave the test database migrated, whatever the test did to it.

    Fully applied is the test database's steady state - every other
    integration test in the suite reads ``provisions.unit_type`` - so the
    teardown restores that rather than whatever this test happened to find.
    """
    yield
    migrations.apply()


def _has_column() -> bool:
    from michael.db import writable

    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n FROM information_schema.columns
             WHERE table_name = 'provisions' AND column_name = 'unit_type'
            """
        )
        row = cur.fetchone()
        return bool(row and row["n"])


@pytest.mark.integration
def test_the_migration_applies(migrated: None) -> None:
    from michael.schema import apply_schema

    apply_schema(grant_readonly=False)
    if "0001" in migrations.applied():
        migrations.rollback("0001")
    assert not _has_column()

    outcomes = migrations.apply()
    assert {"id": "0001", "name": "provision_unit_type", "outcome": "applied"} in outcomes
    assert _has_column()
    assert "0001" in migrations.applied()


@pytest.mark.integration
def test_applying_twice_is_safe(migrated: None) -> None:
    """Guarded twice on purpose: the ledger records what has run, and the SQL
    is written with IF NOT EXISTS so a database whose ledger was lost
    converges instead of failing."""
    migrations.apply()
    again = migrations.apply()
    assert [o["outcome"] for o in again if o["id"] == "0001"] == ["already applied"]
    assert _has_column()

    # And with the ledger row removed, so the SQL itself has to be re-runnable.
    from michael.db import writable

    with writable() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {migrations.LEDGER_TABLE} WHERE id = '0001'")
        conn.commit()
    assert [o["outcome"] for o in migrations.apply() if o["id"] == "0001"] == ["applied"]
    assert _has_column()


@pytest.mark.integration
def test_the_migration_rolls_back(migrated: None) -> None:
    migrations.apply()
    assert _has_column()
    assert migrations.rollback("0001")["outcome"] == "rolled back"
    assert not _has_column()
    assert "0001" not in migrations.applied()


@pytest.mark.integration
def test_rolling_back_twice_is_safe(migrated: None) -> None:
    migrations.apply()
    migrations.rollback("0001")
    assert migrations.rollback("0001")["outcome"] == "not applied"
    assert not _has_column()


@pytest.mark.integration
def test_rolling_back_an_unknown_id_is_refused(migrated: None) -> None:
    """By id, never "the last one": reversing whatever happens to be newest is
    how the wrong migration gets undone on a machine nobody checked first."""
    with pytest.raises(MigrationError, match="no migration '9999'"):
        migrations.rollback("9999")


@pytest.mark.integration
def test_the_column_rejects_a_unit_type_outside_the_check(migrated: None) -> None:
    """The constraint is the guarantee: a pinpoint cannot be rendered for a
    unit type nothing knows how to render."""
    import psycopg

    from michael.db import writable
    from michael.schema import apply_schema

    apply_schema(grant_readonly=False)
    migrations.apply()
    with writable() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents
                    (jurisdiction, title, citation, source_url, snapshot_date,
                     sha256, doc_type)
                VALUES ('wa', 't', 'Fixture Unit Type Act 2000 (WA)', 'u',
                        '2000-01-01', %s, 'act')
                ON CONFLICT (citation, sha256) DO UPDATE SET title = excluded.title
                RETURNING id
                """,
                ("c" * 64,),
            )
            row = cur.fetchone()
            assert row is not None
            document_id = int(row["id"])
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO provisions
                    (document_id, section_number, unit_type, heading, text,
                     char_start, char_end)
                VALUES (%s, '1', 'not-a-unit-type', '', 'text', 0, 4)
                """,
                (document_id,),
            )
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        conn.commit()


@pytest.mark.integration
def test_the_backfill_corrects_the_whole_document_sentinel(migrated: None) -> None:
    """`(whole document)` was never a section. It is the one existing row the
    migration can correct without guessing, and it is corrected."""
    from michael.db import writable
    from michael.schema import apply_schema

    apply_schema(grant_readonly=False)
    if "0001" in migrations.applied():
        migrations.rollback("0001")
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents
                (jurisdiction, title, citation, source_url, snapshot_date, sha256, doc_type)
            VALUES ('wa', 't', 'Fixture Backfill Act 2000 (WA)', 'u', '2000-01-01', %s, 'act')
            ON CONFLICT (citation, sha256) DO UPDATE SET title = excluded.title
            RETURNING id
            """,
            ("d" * 64,),
        )
        row = cur.fetchone()
        assert row is not None
        document_id = int(row["id"])
        cur.execute(
            """
            INSERT INTO provisions
                (document_id, section_number, heading, text, char_start, char_end)
            VALUES (%s, '(whole document)', '', 'prose', 0, 5)
            ON CONFLICT (document_id, section_number, char_start) DO NOTHING
            """,
            (document_id,),
        )
        conn.commit()

    migrations.apply()
    with writable() as conn, conn.cursor() as cur:
        cur.execute("SELECT unit_type FROM provisions WHERE document_id = %s", (document_id,))
        assert [r["unit_type"] for r in cur.fetchall()] == ["document"]
        cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        conn.commit()


@pytest.mark.integration
def test_status_answers_over_a_read_only_connection(migrated: None) -> None:
    """`migrate --status` must not write, because the role it is most useful to
    cannot.

    It used to read the ledger through `applied()`, which creates the ledger
    first. Against `michael_ro` - the role the answering path uses and the one
    an operator's read-only harness is restricted to - that failed outright:

        psycopg.errors.ReadOnlySqlTransaction:
        cannot execute CREATE TABLE in a read-only transaction

    So the safest way to ask what had run was the one way you could not ask it.
    Found on the deployed container, not in a test, which is why there is one
    now.
    """
    from michael import migrations

    rows = migrations.status()
    assert isinstance(rows, list)
    assert any(r["id"] == "0001" for r in rows), rows

    # And the read path itself, unmediated: no write, over the read-only role.
    with readonly() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user AS who")
        row = cur.fetchone()
        assert row is not None
        assert str(row["who"]).endswith("_ro")
    assert migrations.recorded(), "the ledger read back empty over readonly()"


def test_a_missing_ledger_reads_as_nothing_applied_not_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent ledger is an answer, and giving it needs no table created.

    This is what lets `status` stay read-only on a database that has never
    been migrated. Asserted on the SQL actually issued, not just the return
    value: returning {} while quietly writing would pass a weaker test.
    """
    statements: list[str] = []

    class _Cursor:
        def execute(self, sql: str, params: object = None) -> None:
            statements.append(sql)

        def fetchone(self) -> dict[str, object]:
            return {"present": None}

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    @contextmanager
    def _fake_readonly() -> Iterator[_Conn]:
        yield _Conn()

    monkeypatch.setattr(migrations, "readonly", _fake_readonly)

    assert migrations.recorded() == {}
    assert statements, "recorded() asked the database nothing at all"
    assert not any("CREATE" in sql.upper() for sql in statements), statements


@pytest.mark.integration
def test_status_never_reaches_for_a_writable_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guarantee, pinned where it can fail.

    `status` reading through `applied()` is not a visible bug on a developer's
    machine: the writable role is right there and the ledger gets created
    silently. It only surfaces where it matters - against `michael_ro`, on the
    deployed container - as

        psycopg.errors.ReadOnlySqlTransaction:
        cannot execute CREATE TABLE in a read-only transaction

    So this makes any use of the writable connection an outright failure,
    which is the only way this test can fail if the read path is put back.
    """

    def _forbidden() -> object:
        raise AssertionError("status() asked for a writable connection")

    monkeypatch.setattr(migrations, "writable", _forbidden)
    rows = migrations.status()
    assert isinstance(rows, list)


def test_the_image_ships_the_migrations_it_would_have_to_apply() -> None:
    """A migration that cannot be applied where the database lives is not one.

    The deployed image carried `michael migrate` but not `db/migrations`, so
    the command could only ever report - and it reported the applied migration
    as `(not on disk)` and drifted, because the ledger had a row and the
    directory had no files. Measured on the container:

        [{"id": "0001", "name": "(not on disk)", "applied": true,
          "drifted": true}]

    Asserted against the Dockerfile rather than a running image because the
    build is not available in the test environment, and the line being absent
    is the whole defect.
    """
    dockerfile = Path(__file__).resolve().parents[1] / "hermes" / "Dockerfile"
    # Instruction lines only. A substring test passes on `# COPY db ./db`,
    # which is how the first version of this test passed with the line
    # commented out - the exact defect it exists to catch, in the test.
    instructions = [
        line.strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert any(line.startswith("COPY db") for line in instructions), (
        "hermes/Dockerfile does not ship db/, so `michael migrate` has nothing "
        "to apply on the container and reports every migration as drifted"
    )
    # And the image must point the loader at where it put them: PROJECT_ROOT
    # resolves inside the venv for an installed package, so shipping the files
    # without declaring the path leaves `migrate` still finding nothing.
    assert any(
        line.startswith("ENV MICHAEL_MIGRATIONS_DIR=/opt/michael/db/migrations")
        for line in instructions
    ), "hermes/Dockerfile ships db/ but does not tell michael where it went"
    assert (Path(__file__).resolve().parents[1] / "db" / "migrations").is_dir()


def test_the_migrations_directory_is_configurable_not_derived_from_the_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PROJECT_ROOT is this module's own location, which is the repository root
    from a checkout and `.venv/lib/python3.13` from an installed package.

    On the deployed container the derived default resolved to
    `/opt/michael/.venv/lib/python3.13/db/migrations` while the files shipped
    at `/opt/michael/db/migrations`, so `michael migrate` loaded nothing,
    could apply nothing, and reported an applied migration as "(not on disk)"
    and drifted. Measured there, after the image was fixed to ship the files
    at all - the second defect hiding behind the first.
    """
    from michael import config

    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "0009_example.up.sql").write_text("SELECT 1;", encoding="utf-8")
    (target / "0009_example.down.sql").write_text("SELECT 1;", encoding="utf-8")

    monkeypatch.setenv("MICHAEL_MIGRATIONS_DIR", str(target))
    config.settings.cache_clear()
    try:
        assert [m.id for m in migrations.load_migrations()] == ["0009"], (
            "load_migrations ignored MICHAEL_MIGRATIONS_DIR and used the package-derived default"
        )
    finally:
        config.settings.cache_clear()
