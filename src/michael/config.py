"""Configuration, read from the environment only.

Secrets live in .env, which is git-ignored. .env.example documents every key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _path(name: str, default: str) -> Path:
    raw = os.environ.get(name, "").strip() or default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings. Construct through :func:`settings`."""

    # Database. Two URLs on purpose: ingestion writes, answering only reads.
    database_url: str
    readonly_database_url: str

    # Model (OpenRouter) — used by the orchestrator, not by these tools.
    openrouter_api_key: str
    openrouter_base_url: str
    model: str

    # Embeddings. Any OpenAI-compatible endpoint; OpenRouter does not serve
    # embeddings, so this is configured separately.
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_dim: int
    embedding_max_chars: int

    # Retrieval.
    retrieval_min_score: float
    retrieval_candidates: int
    retrieval_top_k: int

    # Paths.
    sources_dir: Path
    templates_dir: Path
    ingestion_log: Path
    domains_file: Path
    system_prompt_file: Path

    def require_embeddings(self) -> None:
        if not self.embedding_api_key:
            raise ConfigError(
                "EMBEDDING_API_KEY is not set. Retrieval fails closed rather than "
                "silently answering from the lexical arm alone."
            )


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Resolve settings once per process."""
    return Settings(
        database_url=_require("MICHAEL_DATABASE_URL"),
        readonly_database_url=os.environ.get("MICHAEL_RO_DATABASE_URL", "").strip()
        or _require("MICHAEL_DATABASE_URL"),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "").strip()
        or "https://openrouter.ai/api/v1",
        model=os.environ.get("MICHAEL_MODEL", "").strip() or "anthropic/claude-sonnet-5",
        embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", "").strip()
        or "https://api.openai.com/v1",
        embedding_api_key=os.environ.get("EMBEDDING_API_KEY", "").strip(),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "").strip() or "text-embedding-3-small",
        embedding_dim=_int("EMBEDDING_DIM", 1536),
        embedding_max_chars=_int("EMBEDDING_MAX_CHARS", 16_000),
        retrieval_min_score=_float("RETRIEVAL_MIN_SCORE", 0.02),
        retrieval_candidates=_int("RETRIEVAL_CANDIDATES", 200),
        retrieval_top_k=_int("RETRIEVAL_TOP_K", 12),
        sources_dir=_path("MICHAEL_SOURCES_DIR", "sources"),
        templates_dir=_path("MICHAEL_TEMPLATES_DIR", "templates"),
        ingestion_log=_path("MICHAEL_INGESTION_LOG", "sources/ingestion.log.jsonl"),
        domains_file=_path("MICHAEL_DOMAINS_FILE", "domains.yaml"),
        system_prompt_file=_path("MICHAEL_SYSTEM_PROMPT", "MICHAEL.md"),
    )
