"""Schema definition and application.

Two tables. A provision may not exist without its parent document: the foreign
key is ``NOT NULL`` and ``ON DELETE CASCADE``, so the invariant is enforced by
Postgres.
"""

from __future__ import annotations

from michael.config import settings
from michael.db import writable

#: ``guidance`` is departmental guidance - how an agency says it applies the
#: law - and is never cited or judged as legislation. Added by
#: db/migrations/0002_guidance_doc_type; see sources.GUIDANCE_HOSTS.
DOC_TYPES = ("act", "regulation", "award", "case", "guidance")
JURISDICTIONS = ("wa", "commonwealth")

#: What KIND of unit one provision row is, so a pinpoint can be rendered in
#: the form its authority actually uses. Legislation is cited by section;
#: a judgment's reasons are cited by paragraph, under the Australian Guide to
#: Legal Citation, as ``at [12]``. Recorded per row rather than derived from
#: ``documents.doc_type``, because a single judgment carries more than one
#: kind: numbered paragraphs of reasons, a block of orders, and - when the
#: report has no paragraph numbering at all - one whole-document row.
#:
#: Added by db/migrations/0001_provision_unit_type. It is declared here as
#: well so a database created from scratch matches one that was migrated;
#: the migration is what moves an existing database, and it is written to be
#: a no-op when this DDL already created the column.
UNIT_TYPES = ("section", "paragraph", "order", "document")


def schema_sql(embedding_dim: int) -> str:
    """Return the full DDL. Idempotent: safe to apply repeatedly."""
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS documents (
    id            bigserial PRIMARY KEY,
    jurisdiction  text        NOT NULL CHECK (jurisdiction IN ('wa', 'commonwealth')),
    title         text        NOT NULL,
    citation      text        NOT NULL,
    source_url    text        NOT NULL,
    snapshot_date date        NOT NULL,
    sha256        char(64)    NOT NULL,
    doc_type      text        NOT NULL
                  CHECK (doc_type IN ('act', 'regulation', 'award', 'case', 'guidance')),
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    -- The same bytes under the same citation are the same snapshot. Re-running
    -- ingestion is therefore idempotent rather than duplicating the corpus.
    CONSTRAINT documents_citation_sha_key UNIQUE (citation, sha256)
);

CREATE INDEX IF NOT EXISTS documents_jurisdiction_idx ON documents (jurisdiction);
CREATE INDEX IF NOT EXISTS documents_doc_type_idx ON documents (doc_type);

CREATE TABLE IF NOT EXISTS provisions (
    id             bigserial PRIMARY KEY,
    document_id    bigint  NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    section_number text    NOT NULL,
    unit_type      text    NOT NULL DEFAULT 'section'
                   CHECK (unit_type IN ('section', 'paragraph', 'order', 'document')),
    heading        text    NOT NULL DEFAULT '',
    text           text    NOT NULL,
    embedding      vector({embedding_dim}),
    -- Character offsets into the parent document's extracted text, so every
    -- quote can be traced back to its position in the original.
    char_start     integer NOT NULL,
    char_end       integer NOT NULL,
    -- Token count, maintained at ingest, so BM25 can read avgdl cheaply.
    token_count    integer NOT NULL DEFAULT 0,
    search_vector  tsvector GENERATED ALWAYS AS (
                       setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
                       setweight(to_tsvector('english', coalesce(text, '')), 'B')
                   ) STORED,
    CONSTRAINT provisions_char_range_check CHECK (char_end >= char_start),
    CONSTRAINT provisions_document_section_key UNIQUE (document_id, section_number, char_start)
);

CREATE INDEX IF NOT EXISTS provisions_document_id_idx ON provisions (document_id);
CREATE INDEX IF NOT EXISTS provisions_unit_type_idx ON provisions (unit_type);
CREATE INDEX IF NOT EXISTS provisions_search_idx ON provisions USING gin (search_vector);
CREATE INDEX IF NOT EXISTS provisions_embedding_idx
    ON provisions USING hnsw (embedding vector_cosine_ops);

-- Ingestion audit log. Every fetch attempt is recorded, including refusals.
CREATE TABLE IF NOT EXISTS ingestion_log (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    url         text        NOT NULL,
    host        text        NOT NULL,
    outcome     text        NOT NULL CHECK (outcome IN ('allowed', 'refused', 'failed')),
    reason      text        NOT NULL DEFAULT '',
    sha256      char(64),
    document_id bigint      REFERENCES documents (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ingestion_log_at_idx ON ingestion_log (at DESC);

-- Corpus statistics for BM25. Refreshed at the end of each ingestion run so
-- the answering path never has to compute an aggregate over the whole table.
CREATE TABLE IF NOT EXISTS corpus_stats (
    id            boolean PRIMARY KEY DEFAULT true CHECK (id),
    provisions    bigint  NOT NULL,
    avg_tokens    double precision NOT NULL,
    refreshed_at  timestamptz NOT NULL DEFAULT now()
);
"""


GRANTS_SQL = """
GRANT SELECT ON documents, provisions, ingestion_log, corpus_stats TO michael_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO michael_ro;
"""


def apply_schema(*, grant_readonly: bool = True) -> None:
    """Create the schema if absent, then grant SELECT to the read-only role.

    Never drops or alters existing objects.
    """
    with writable() as conn, conn.cursor() as cur:
        cur.execute(schema_sql(settings().embedding_dim))
        if grant_readonly:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'michael_ro'")
            if cur.fetchone() is not None:
                cur.execute(GRANTS_SQL)
        conn.commit()


def refresh_corpus_stats() -> None:
    """Recompute BM25 corpus statistics. Called at the end of ingestion."""
    with writable() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO corpus_stats (id, provisions, avg_tokens, refreshed_at)
            SELECT true, count(*), coalesce(avg(nullif(token_count, 0)), 1.0), now()
              FROM provisions
            ON CONFLICT (id) DO UPDATE
               SET provisions = excluded.provisions,
                   avg_tokens = excluded.avg_tokens,
                   refreshed_at = excluded.refreshed_at
            """
        )
        conn.commit()
