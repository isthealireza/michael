"""Test environment.

No test reaches the network or a database unless it is marked ``integration``
or ``network``. The settings below are placeholders so importing the package
does not require a configured .env.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# W4-5 (.orca/reports/2026-09-20-worker4-production-qa.md): all 5 integration
# tests skipped on a password mismatch between this file's hardcoded default
# and the local michael-postgres container. Fixing only the password would
# have been incomplete and dangerous: this default also names the wrong
# DATABASE. michael-postgres now doubles as the developer's real local
# corpus (database "michael", 205 real documents, 1536-dim embeddings) -
# not the empty schema this suite was written against. Pointing pytest at
# "michael" with the real credential would let the michael_db fixture in
# test_integration.py durably INSERT its two synthetic "(Cth-Test)"
# documents into that real corpus on every run, with no teardown to undo
# it. A separate, already-existing "michael_test" database on the same
# server (created previously for exactly this purpose, still holding
# those same two fixture documents from a run before the credential
# drifted) is the isolated target this suite actually needs.
#
# Only the PASSWORD needs to come from .env (the role is shared across
# databases on one Postgres server, so the credential is real even though
# the target database below never is); the rest of .env - in particular
# EMBEDDING_DIM=1536 for the real corpus - must NOT leak into the test
# environment, so this reads exactly one key rather than reusing
# michael.cli.load_dotenv()'s whole-file loader.
def _dotenv_value(key: str) -> str | None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


_rw_password = _dotenv_value("POSTGRES_PASSWORD") or "test"
_ro_password = _dotenv_value("MICHAEL_RO_PASSWORD") or "test"

os.environ.setdefault(
    "MICHAEL_DATABASE_URL",
    f"postgresql://michael:{_rw_password}@127.0.0.1:5433/michael_test",
)
os.environ.setdefault(
    "MICHAEL_RO_DATABASE_URL",
    f"postgresql://michael_ro:{_ro_password}@127.0.0.1:5433/michael_test",
)
os.environ.setdefault("EMBEDDING_API_KEY", "test-key-not-used-offline")
os.environ.setdefault("EMBEDDING_DIM", "8")


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point writable paths at tmp_path so no test touches sources/ or templates/."""
    from michael import config, domains

    monkeypatch.setenv("MICHAEL_SOURCES_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("MICHAEL_INGESTION_LOG", str(tmp_path / "sources" / "ingestion.jsonl"))
    monkeypatch.setenv("MICHAEL_DOMAINS_FILE", str(PROJECT_ROOT / "domains.yaml"))
    monkeypatch.setenv("MICHAEL_SYSTEM_PROMPT", str(PROJECT_ROOT / "MICHAEL.md"))
    config.settings.cache_clear()
    domains.load_domains.cache_clear()
    yield
    config.settings.cache_clear()
    domains.load_domains.cache_clear()


@pytest.fixture
def real_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the repository's templates/ directory, read-only."""
    from michael import config

    monkeypatch.setenv("MICHAEL_TEMPLATES_DIR", str(PROJECT_ROOT / "templates"))
    config.settings.cache_clear()
