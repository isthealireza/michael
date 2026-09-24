"""Database-backed tests.

Excluded from the default run. They need the container up:

    docker compose up -d && uv run michael schema && uv run michael migrate
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
    from michael import config, embeddings, ingest, migrations, schema

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
    # apply_schema() only CREATEs; a test database created before a
    # migration existed still needs the migration run against it, the
    # same way the real one does.
    migrations.apply()
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
    from michael import config, embeddings, ingest, migrations, schema

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
    # apply_schema() only CREATEs; a test database created before a
    # migration existed still needs the migration run against it, the
    # same way the real one does.
    migrations.apply()
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


# --- case law --------------------------------------------------------------

FIXTURE_JUDGMENT_TEXT = """Federal Court of Australia

Fixture Judgment Pty Ltd v Example Corporation [2099] FCA 7

Number of paragraphs:       3

ORDERS

THE COURT ORDERS THAT:

1. The application for a lighthouse keeping order is dismissed.
2. The applicant pay the respondent's costs of the application.

REASONS FOR JUDGMENT

FIXTURE J
1 The applicant seeks a lighthouse keeping order under the fixture rule.
2 A lighthouse keeper must maintain the light, but that duty does not create
the order the applicant seeks in this proceeding.
3 For those reasons the application is dismissed.
I certify that the preceding three (3) numbered paragraphs are a true copy of
the Reasons for Judgment of the Honourable Justice Fixture.
"""


CASE_CITATION = "Fixture Judgment Pty Ltd v Example Corporation [2099] FCA 7"


@pytest.fixture
def michael_db_with_case_law(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The legislation corpus plus one judgment, split by the case-law
    splitter and stored with its unit types."""
    from michael import config, embeddings, ingest, migrations, schema

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
    migrations.apply()
    # Re-ingested from scratch every run. The same citation and sha256 is a
    # no-op by design (ingestion is idempotent), so a document left behind by
    # an earlier run would be read back instead of written - and a test about
    # what the INSERT writes would be passing on last week's rows.
    from michael.db import writable

    with writable() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE citation = %s", (CASE_CITATION,))
        conn.commit()
    for citation, text, doc_type in (
        ("Fixture Marine Navigation Act 2000 (Cth-Test)", UNRELATED_TEXT, "act"),
        (
            "Fixture Judgment Pty Ltd v Example Corporation [2099] FCA 7",
            FIXTURE_JUDGMENT_TEXT,
            "case",
        ),
    ):
        ingest.ingest_document(
            jurisdiction="commonwealth",
            title=citation,
            citation=citation,
            source_url="https://www.legislation.gov.au/fixture",
            snapshot_date=date(2026, 7, 1),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            doc_type=doc_type,
            text=text,
        )
    schema.refresh_corpus_stats()
    yield


def _stored_units() -> list[tuple[str, str]]:
    from michael.db import writable

    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.unit_type, p.section_number
              FROM provisions p JOIN documents d ON d.id = p.document_id
             WHERE d.citation = %s
             ORDER BY p.char_start
            """,
            (CASE_CITATION,),
        )
        return [(str(r["unit_type"]), str(r["section_number"])) for r in cur.fetchall()]


def test_a_judgments_unit_types_are_stored_not_defaulted(
    michael_db_with_case_law: None,
) -> None:
    """The column has to carry what the splitter decided. Defaulted to
    'section' in the INSERT, every paragraph is cited "s 1" again and no test
    above this layer can tell."""
    assert _stored_units() == [
        ("order", "(orders)"),
        ("paragraph", "1"),
        ("paragraph", "2"),
        ("paragraph", "3"),
    ]


def test_a_stored_judgment_paragraph_renders_an_aglc_pinpoint(
    michael_db_with_case_law: None,
) -> None:
    """End to end, through real retrieval: the pinpoint a draft would carry."""
    from michael import retrieve

    # "contracts" here simply because it is the domain this fixture's lighthouse
    # scenario routes to. It used to be a workaround: employment filtered to
    # [act, regulation, award] and could not return case law at all. That is
    # fixed, and test_an_employment_question_can_reach_a_judgment below is the
    # one that guards it.
    from michael.domains import Routing, load_domains

    contracts = next(d for d in load_domains() if d.name == "contracts")
    result = retrieve.search(
        "lighthouse keeper must maintain the light",
        routing=Routing(domain=contracts, matched_keywords=("contract",), recognised=True),
        min_score=0.0,
    )
    paragraphs = [p for p in result.provisions if p.doc_type == "case"]
    assert paragraphs, result.reason
    assert paragraphs[0].unit_type == "paragraph"
    assert paragraphs[0].pinpoint().startswith(f"{CASE_CITATION} at [")
    assert " s " not in paragraphs[0].pinpoint()


def test_a_section_lookup_does_not_return_a_judgment_paragraph(
    michael_db_with_case_law: None,
) -> None:
    """ "section 2" asks for legislation. Once case law is in the corpus a bare
    number matches judgment paragraphs too, and the identifier lookup reports
    itself as an exact match rather than a ranked guess - so an unrelated
    judgment's paragraph 2 would be returned as section 2."""
    from michael.domains import route
    from michael.retrieve import _section_lookup

    # Asserted on the identifier lookup itself. Asserting on search() would
    # pass either way: with no section 1 in the corpus the lookup returns None
    # and the hybrid arm answers, and the hybrid arm is allowed to rank case
    # law. What must not happen is the lookup reporting paragraph 1 of a
    # judgment as an exact identifier match for "section 1".
    query = "what does section 1 say"
    assert _section_lookup(query, routing=route(query)) is None

    # The positive control, so this cannot pass by the lookup being broken:
    # the Act's own section 7 is still found.
    found = _section_lookup("what does section 7 say", routing=route("what does section 7 say"))
    assert found is not None
    assert [p.section_number for p in found.provisions] == ["7"]


def test_a_judgment_and_an_act_can_share_a_number_without_sharing_a_pinpoint(
    michael_db_with_case_law: None,
) -> None:
    from michael.db import writable

    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n FROM (
                SELECT d.citation, p.unit_type, p.section_number
                  FROM provisions p JOIN documents d ON d.id = p.document_id
                 WHERE d.doc_type = 'case'
                 GROUP BY 1, 2, 3 HAVING count(*) > 1
            ) duplicates
            """
        )
        row = cur.fetchone()
        assert row is not None and row["n"] == 0


@pytest.mark.integration
def test_an_employment_question_can_reach_a_judgment(
    michael_db_with_case_law: None,
) -> None:
    """The domain filter, end to end, against real retrieval.

    domains.yaml filtered `employment` to [act, regulation, award], so an
    employment question could not retrieve a judgment however well it matched.
    While the corpus held no case law that cost nothing; once judgments were
    ingested it was a silent ceiling over the domain Michael is most used for.

    Asserted through retrieval rather than by reading the YAML, because the
    YAML says what is configured and this says what a reader actually gets.
    """
    from michael import retrieve
    from michael.domains import Routing, load_domains

    employment = next(d for d in load_domains() if d.name == "employment")
    result = retrieve.search(
        "employee dismissed by the police force discrimination",
        routing=Routing(domain=employment, matched_keywords=("employee",), recognised=True),
        min_score=0.0,
    )
    judgments = [p for p in result.provisions if p.doc_type == "case"]
    assert judgments, f"employment retrieval returned no case law: {result.reason}"
    assert any(p.unit_type in ("paragraph", "order", "document") for p in judgments)
