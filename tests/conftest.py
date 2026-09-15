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

os.environ.setdefault("MICHAEL_DATABASE_URL", "postgresql://michael:test@127.0.0.1:5433/michael")
os.environ.setdefault(
    "MICHAEL_RO_DATABASE_URL", "postgresql://michael_ro:test@127.0.0.1:5433/michael"
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
