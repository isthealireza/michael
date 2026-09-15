"""Database-backed tests.

Excluded from the default run. They need the container up:

    docker compose up -d && uv run michael schema
    uv run pytest -m integration

Embeddings are stubbed with a deterministic bag-of-words hash so the tests do
not need an embeddings API key. Everything else - the schema, pgvector, the
BM25 term statistics, the read-only role - is real.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest

pytestmark = pytest.mark.integration

DIM = 64

FIXTURE_TEXT = """FIXTURE EMPLOYMENT STANDARDS ACT 2000

An Act to record fixture employment standards for testing.

15A Meaning of casual worker
    A person is a casual worker if the engagement is made on the basis that
    there is no firm advance commitment to continuing and indefinite work
    according to an agreed pattern of work.

61 Minimum standards
    The minimum standards in this Part apply to a casual worker in the same way
    as they apply to any other worker, except as expressly provided.

125B Casual worker information statement
    An employer must give each casual worker the casual worker information
    statement before, or as soon as practicable after, the worker starts work.
"""

UNRELATED_TEXT = """FIXTURE MARINE NAVIGATION ACT 2000

7 Lighthouse keeping
    A lighthouse keeper must maintain the light during the hours of darkness
    and must record the hours during which the light was shown.
"""


def fake_embedding(text: str) -> list[float]:
    """Deterministic bag-of-words hash, L2-normalised.

    Similar wording gives similar vectors, which is all the vector arm needs to
    be exercised end to end.
    """
    buckets = [0.0] * DIM
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        buckets[digest[0] % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in buckets)) or 1.0
    return [v / norm for v in buckets]


@pytest.fixture
def michael_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from michael import config, embeddings, ingest, schema

    monkeypatch.setenv("EMBEDDING_DIM", str(DIM))
    monkeypatch.setenv("EMBEDDING_API_KEY", "stubbed-for-integration-tests")
    config.settings.cache_clear()

    try:
        with psycopg.connect(config.settings().database_url, connect_timeout=3):
            pass
    except psycopg.Error as exc:
        pytest.skip(f"michael-postgres is not reachable: {exc}")

    monkeypatch.setattr(embeddings, "embed", lambda texts, **kw: [fake_embedding(t) for t in texts])
    monkeypatch.setattr(ingest, "embed", lambda texts, **kw: [fake_embedding(t) for t in texts])
    monkeypatch.setattr("michael.retrieve.embed_one", lambda text, **kw: fake_embedding(text))

    schema.apply_schema()
    for citation, text in (
        ("Fixture Employment Standards Act 2000 (Cth-Test)", FIXTURE_TEXT),
        ("Fixture Marine Navigation Act 2000 (Cth-Test)", UNRELATED_TEXT),
    ):
        ingest.ingest_document(
            jurisdiction="commonwealth",
            title=citation,
            citation=citation,
            source_url="https://www.legislation.gov.au/fixture",
            snapshot_date=date(2026, 7, 1),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            doc_type="act",
            text=text,
        )
    schema.refresh_corpus_stats()
    yield


def test_a_matching_query_returns_provisions_with_full_metadata(michael_db: None) -> None:
    from michael import retrieve

    result = retrieve.search("casual worker no firm advance commitment", min_score=0.1)
    assert result.covered
    top = result.provisions[0]
    assert top.section_number == "15A"
    assert "firm advance commitment" in top.text
    assert top.snapshot_date == date(2026, 7, 1)
    assert len(top.sha256) == 64
    assert top.pinpoint().endswith("(snapshot 2026-07-01)")


def test_both_arms_contribute(michael_db: None) -> None:
    from michael import retrieve

    result = retrieve.search("casual worker information statement", min_score=0.1)
    assert result.covered
    assert result.provisions[0].lexical_score > 0.0
    assert result.provisions[0].vector_score > 0.0


def test_an_off_topic_query_returns_empty_not_the_nearest_guess(michael_db: None) -> None:
    from michael import retrieve

    result = retrieve.search(
        "stamp duty on the transfer of a racehorse syndicate share", min_score=0.9
    )
    assert result.covered is False
    assert result.provisions == ()
    assert "below the threshold" in result.reason
    assert result.not_covered_message("racehorse syndicates").startswith("NOT COVERED")


def test_a_provision_cannot_exist_without_its_document(michael_db: None) -> None:
    from michael.db import writable

    orphaned = psycopg.errors.ForeignKeyViolation
    with writable() as conn, conn.cursor() as cur, pytest.raises(orphaned):
        cur.execute(
            "INSERT INTO provisions (document_id, section_number, text, char_start, char_end) "
            "VALUES (%s, %s, %s, %s, %s)",
            (9_999_999, "1", "orphan", 0, 6),
        )


def test_the_answering_connection_cannot_write(michael_db: None) -> None:
    from michael.config import settings
    from michael.db import readonly

    if settings().readonly_database_url == settings().database_url:
        pytest.skip("MICHAEL_RO_DATABASE_URL is not configured separately")

    with readonly() as conn, conn.cursor() as cur, pytest.raises(psycopg.Error):
        cur.execute("INSERT INTO documents (jurisdiction) VALUES ('wa')")
