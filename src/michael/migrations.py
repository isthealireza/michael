"""Schema migrations.

``schema.apply_schema()`` is ``CREATE TABLE IF NOT EXISTS`` throughout: it
builds a database that does not exist yet and, by design, never alters one
that does. That is the right behaviour for a bootstrap, and it is why it
cannot add a column to a table already holding 9,000 rows. This module is the
other half.

Three properties, each of which is a test rather than a promise:

* **Reversible.** Every migration is a pair of files, ``<id>_<name>.up.sql``
  and ``<id>_<name>.down.sql``, in the configured migrations directory. A migration with no
  ``.down.sql`` is not loadable, so an irreversible one cannot be added by
  forgetting to write the reverse.
* **Idempotent.** Applying is guarded twice: the ledger records what has run,
  and the SQL itself is written with ``IF NOT EXISTS`` / ``IF EXISTS`` so that
  re-running it against a database whose ledger was lost still succeeds.
* **Recorded.** :data:`LEDGER_TABLE` holds one row per applied migration, with
  the sha256 of the SQL that was applied. A file edited after it has been
  applied is reported as drifted rather than silently re-run or silently
  ignored.

Each migration runs inside one transaction, so a migration that fails halfway
leaves the database as it was.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from michael.config import PROJECT_ROOT, settings
from michael.db import readonly, writable

#: Where the migration files live, when the caller does not say.
#:
#: Configurable, and it has to be. PROJECT_ROOT is derived from this module's
#: own location, which is the repository root from a source checkout and
#: `.venv/lib/python3.13` from an installed package - so on the deployed
#: container the default resolved to
#: `/opt/michael/.venv/lib/python3.13/db/migrations` while the files sat at
#: `/opt/michael/db/migrations`. `michael migrate` found nothing, applied
#: nothing, and reported an applied migration as "(not on disk)" and drifted.
#: Measured there. MICHAEL_TEMPLATES_DIR, MICHAEL_SOURCES_DIR and
#: MICHAEL_DOMAINS_FILE already exist for exactly this reason.
DEFAULT_MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"


def migrations_dir() -> Path:
    """The configured migrations directory, or the source-tree default."""
    return settings().migrations_dir


LEDGER_TABLE = "schema_migrations"

LEDGER_SQL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    id         text        PRIMARY KEY,
    name       text        NOT NULL,
    sha256     char(64)    NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- The read-only role must be able to READ this table. "What has run here" is
-- exactly the question you want to ask from a connection that cannot change
-- the answer, and `status()` asks it over `readonly()`.
--
-- schema.py's GRANTS_SQL already sets ALTER DEFAULT PRIVILEGES, and both
-- production and the local database did in fact inherit SELECT that way, so
-- this grant is redundant today. It is stated anyway because the inheritance
-- is an ORDERING dependency: this table is created by the migration tool,
-- not by apply_schema(), so it only picks the default up when `michael
-- schema` ran first. A database migrated before its grants were applied
-- would read as "nothing has ever been applied" - the ledger invisible
-- rather than empty - which is the one wrong answer this command can give.
--
-- Guarded on the role existing: a scratch database may have no michael_ro.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'michael_ro') THEN
        GRANT SELECT ON {LEDGER_TABLE} TO michael_ro;
    END IF;
END
$$;
"""

#: ``0001_provision_unit_type.up.sql`` -> id ``0001``, name
#: ``provision_unit_type``. The id sorts lexically, so zero-padding is what
#: orders the migrations; there is no separate ordering column to disagree
#: with the filename.
FILENAME_RE = re.compile(r"^(?P<id>\d{4})_(?P<name>[a-z0-9_]+)\.up\.sql$")


class MigrationError(RuntimeError):
    """A migration could not be loaded or applied."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One reversible migration, loaded from its pair of files."""

    id: str
    name: str
    up: str
    down: str

    @property
    def sha256(self) -> str:
        """Hash of the *forward* SQL, so an edit to it is detectable."""
        return hashlib.sha256(self.up.encode("utf-8")).hexdigest()


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """Load every migration in ``directory``, in id order.

    Raises if an id repeats or a ``.down.sql`` is missing: both are mistakes
    that would otherwise only be discovered against a live database.
    """
    target = directory or migrations_dir()
    if not target.is_dir():
        return []
    migrations: list[Migration] = []
    seen: set[str] = set()
    for path in sorted(target.glob("*.up.sql")):
        match = FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(f"{path.name}: expected <0000>_<name>.up.sql")
        identifier = match.group("id")
        if identifier in seen:
            raise MigrationError(f"duplicate migration id {identifier}")
        seen.add(identifier)
        down = path.with_name(path.name[: -len(".up.sql")] + ".down.sql")
        if not down.is_file():
            raise MigrationError(
                f"{path.name} has no {down.name}. A migration without a reverse is not "
                "accepted: it cannot be rolled back and cannot be tested."
            )
        migrations.append(
            Migration(
                id=identifier,
                name=match.group("name"),
                up=path.read_text(encoding="utf-8"),
                down=down.read_text(encoding="utf-8"),
            )
        )
    return migrations


def _ensure_ledger() -> None:
    with writable() as conn, conn.cursor() as cur:
        cur.execute(LEDGER_SQL)
        conn.commit()


def applied() -> dict[str, str]:
    """Applied migration id -> the sha256 recorded when it was applied.

    Writes: creates the ledger if it is absent. For the read-only question -
    what has run, without changing anything - use :func:`recorded`.
    """
    _ensure_ledger()
    with writable() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id, sha256 FROM {LEDGER_TABLE}")
        return {str(row["id"]): str(row["sha256"]) for row in cur.fetchall()}


def recorded() -> dict[str, str]:
    """The same mapping, read over a READ-ONLY connection.

    `migrate --status` used to go through :func:`applied`, which creates the
    ledger before reading it. Against the read-only role - the one the
    answering path uses, and the one an operator's read-only harness is
    restricted to - that fails outright:

        psycopg.errors.ReadOnlySqlTransaction:
        cannot execute CREATE TABLE in a read-only transaction

    So the safest way to ask what has run was the one way you could not ask
    it. A missing ledger means nothing has been applied; that is an answer,
    not an error, and it does not need a table created to say so.
    """
    with readonly() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS present", (LEDGER_TABLE,))
        row = cur.fetchone()
        if row is None or row["present"] is None:
            return {}
        cur.execute(f"SELECT id, sha256 FROM {LEDGER_TABLE}")
        return {str(r["id"]): str(r["sha256"]) for r in cur.fetchall()}


def status(directory: Path | None = None) -> list[dict[str, object]]:
    """What is on disk, what has run, and whether any file has drifted."""
    on_disk = load_migrations(directory)
    ledger = recorded()
    rows: list[dict[str, object]] = [
        {
            "id": m.id,
            "name": m.name,
            "applied": m.id in ledger,
            "drifted": m.id in ledger and ledger[m.id] != m.sha256,
        }
        for m in on_disk
    ]
    known = {m.id for m in on_disk}
    rows += [
        {"id": identifier, "name": "(not on disk)", "applied": True, "drifted": True}
        for identifier in sorted(set(ledger) - known)
    ]
    return rows


def apply(directory: Path | None = None) -> list[dict[str, object]]:
    """Apply every migration not yet recorded, in order.

    Safe to run repeatedly: an already-recorded migration is skipped, and the
    SQL of each migration is itself written to be re-runnable, so a database
    whose ledger was lost converges rather than failing.

    A file whose sha256 no longer matches what was recorded is **not**
    re-applied. Re-running edited SQL would apply a migration nobody reviewed;
    it is reported as drifted and left alone.
    """
    _ensure_ledger()
    recorded = applied()
    outcomes: list[dict[str, object]] = []
    for migration in load_migrations(directory):
        if migration.id in recorded:
            drifted = recorded[migration.id] != migration.sha256
            outcomes.append(
                {
                    "id": migration.id,
                    "name": migration.name,
                    "outcome": "drifted" if drifted else "already applied",
                }
            )
            continue
        with writable() as conn, conn.cursor() as cur:
            cur.execute(migration.up)
            cur.execute(
                f"INSERT INTO {LEDGER_TABLE} (id, name, sha256) VALUES (%s, %s, %s)",
                (migration.id, migration.name, migration.sha256),
            )
            conn.commit()
        outcomes.append({"id": migration.id, "name": migration.name, "outcome": "applied"})
    return outcomes


def rollback(migration_id: str, directory: Path | None = None) -> dict[str, object]:
    """Roll one migration back and remove its ledger row.

    By id, never "the last one": rolling back whatever happens to be newest is
    how the wrong migration gets reversed on a machine whose state you did not
    check first.
    """
    by_id = {m.id: m for m in load_migrations(directory)}
    migration = by_id.get(migration_id)
    if migration is None:
        raise MigrationError(f"no migration {migration_id!r} in {migrations_dir()}")
    if migration_id not in applied():
        return {"id": migration_id, "name": migration.name, "outcome": "not applied"}
    with writable() as conn, conn.cursor() as cur:
        cur.execute(migration.down)
        cur.execute(f"DELETE FROM {LEDGER_TABLE} WHERE id = %s", (migration_id,))
        conn.commit()
    return {"id": migration_id, "name": migration.name, "outcome": "rolled back"}
