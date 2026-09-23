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
    (1) The minimum standards in this Part apply to a casual worker in the same
    way as they apply to any other worker, except as expressly provided.
    (2) Subsection (1) does not limit any other entitlement a casual worker has
    under this Act or under a fair work instrument.

125B Casual worker information statement
    An employer must give each casual worker the casual worker information
    statement before, or as soon as practicable after, the worker starts work.
"""

UNRELATED_TEXT = """FIXTURE MARINE NAVIGATION ACT 2000

7 Lighthouse keeping
    A lighthouse keeper must maintain the light during the hours of darkness
    and must record the hours during which the light was shown.
"""

# W2-S1/W2-S2: a Schedule clause sharing its bare number with the Act's own
# section 200 above (clause 200 - the shape that hid a real second provision
# behind a false total_matches:1, W2-S2), plus one with a number the Act uses
# nowhere else (clause 82 - the shape a direct lookup narrowed to this Act's
# name could not find at all, because the Schedule clause is stored as
# "Sch 1 cl 82", not "82", W2-S1). A dedicated citation, not
# FIXTURE_TEXT's, because the persistent local test database carries
# leftover documents from earlier sessions under that citation and this test
# needs an exact, known count.
SCHEDULE_COLLISION_TEXT = """FIXTURE SCHEDULE COLLISION ACT 2026

200 Minimum standards
    (1) The minimum standards in this Part apply to a casual worker in the
    same way as they apply to any other worker, except as expressly provided.

Schedule 1—Transitional provisions

200 Application of amendments
    This clause shares its bare number with section 200 above and is a real,
    distinct provision, not a duplicate.

82 Savings
    This clause's number is not used anywhere else in this Act.
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


@pytest.fixture
def michael_db_with_schedule_collision(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Same corpus as :func:`michael_db`, plus a Schedule clause colliding with
    a plain section number - see :data:`SCHEDULE_COLLISION_TEXT`."""
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
    ingest.ingest_document(
        jurisdiction="commonwealth",
        title="Fixture Schedule Collision Act 2026 (Cth-Test)",
        citation="Fixture Schedule Collision Act 2026 (Cth-Test)",
        source_url="https://www.legislation.gov.au/fixture",
        snapshot_date=date(2026, 7, 1),
        sha256=hashlib.sha256(SCHEDULE_COLLISION_TEXT.encode("utf-8")).hexdigest(),
        doc_type="act",
        text=SCHEDULE_COLLISION_TEXT,
    )
    schema.refresh_corpus_stats()
    yield


def test_a_bare_number_lookup_also_finds_its_schedule_clause_sibling(
    michael_db_with_schedule_collision: None,
) -> None:
    """W2-S2: a bare-number identifier lookup must not report a false
    total_matches:1 when a Schedule clause of the same document shares that
    number - both are real, distinct provisions."""
    from michael import tools

    payload = tools.search_provisions("s 200 of the Fixture Schedule Collision Act")
    assert payload["identifier_lookup"] is True
    assert payload["total_matches"] == 2
    assert payload["ambiguous_pinpoint"] is True
    section_numbers = {p["section_number"] for p in payload["provisions"]}
    assert section_numbers == {"200", "Sch 1 cl 200"}


def test_an_act_qualified_bare_number_resolves_to_its_schedule_clause(
    michael_db_with_schedule_collision: None,
) -> None:
    """W2-S1: "s 82 of <Act>" must resolve even though this Act has no plain
    section 82 - only a Schedule clause carrying that number. Previously the
    exact-string lookup, narrowed by the Act name, matched nothing and fell
    through to an unreliable hybrid-search guess."""
    from michael import retrieve

    result = retrieve.search("s 82 of the Fixture Schedule Collision Act")
    assert result.covered
    assert result.identifier_lookup
    assert result.total_matches == 1
    assert result.provisions[0].section_number == "Sch 1 cl 82"


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
