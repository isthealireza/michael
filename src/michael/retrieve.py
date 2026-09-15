"""Retrieval. Hybrid BM25 + vector, over a read-only connection.

Two rules shape this module:

* If the best fused score is below the threshold the result is **empty**. The
  nearest guess is never returned.
* An empty result is a value, not silence: :class:`RetrievalResult` carries
  ``covered = False`` and a reason, so the caller reports "NOT COVERED" rather
  than answering from nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from michael.bm25 import CorpusStats, normalise, raw_score
from michael.config import settings
from michael.db import readonly, vector_literal
from michael.domains import Routing, route
from michael.embeddings import embed_one

#: Weights for the two arms. They sum to 1 so the fused score stays in [0, 1)
#: and the threshold can be read as "how relevant, absolutely".
LEXICAL_WEIGHT = 0.5
VECTOR_WEIGHT = 0.5


@dataclass(frozen=True, slots=True)
class RetrievedProvision:
    """A provision with the full metadata needed to cite it."""

    provision_id: int
    document_id: int
    jurisdiction: str
    title: str
    citation: str
    source_url: str
    snapshot_date: date
    sha256: str
    doc_type: str
    section_number: str
    heading: str
    text: str
    char_start: int
    char_end: int
    lexical_score: float
    vector_score: float
    score: float

    def pinpoint(self) -> str:
        """The citation a statement based on this provision must carry."""
        section = (
            f"s {self.section_number}" if self.section_number[:1].isdigit() else self.section_number
        )
        return f"{self.citation} {section} (snapshot {self.snapshot_date.isoformat()})"


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The outcome of one retrieval. Empty is a first-class outcome."""

    query: str
    routing_domain: str
    domain_recognised: bool
    provisions: tuple[RetrievedProvision, ...] = ()
    threshold: float = 0.0
    best_score: float = 0.0
    reason: str = ""
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def covered(self) -> bool:
        return bool(self.provisions)

    def not_covered_message(self, topic: str) -> str:
        """The exact line MICHAEL.md requires when retrieval is empty."""
        return f"NOT COVERED - run ingestion for {topic}"


LEXICAL_CANDIDATES_SQL = """
SELECT p.id
  FROM provisions p
  JOIN documents d ON d.id = p.document_id
 WHERE p.search_vector @@ websearch_to_tsquery('english', %(query)s)
   AND (%(jurisdictions)s::text[] IS NULL OR d.jurisdiction = ANY(%(jurisdictions)s))
   AND (%(doc_types)s::text[] IS NULL OR d.doc_type = ANY(%(doc_types)s))
 ORDER BY ts_rank_cd(p.search_vector, websearch_to_tsquery('english', %(query)s)) DESC
 LIMIT %(limit)s
"""

VECTOR_CANDIDATES_SQL = """
SELECT p.id, 1.0 - (p.embedding <=> %(embedding)s::vector) AS similarity
  FROM provisions p
  JOIN documents d ON d.id = p.document_id
 WHERE p.embedding IS NOT NULL
   AND (%(jurisdictions)s::text[] IS NULL OR d.jurisdiction = ANY(%(jurisdictions)s))
   AND (%(doc_types)s::text[] IS NULL OR d.doc_type = ANY(%(doc_types)s))
 ORDER BY p.embedding <=> %(embedding)s::vector
 LIMIT %(limit)s
"""

QUERY_LEXEMES_SQL = "SELECT lexeme FROM unnest(to_tsvector('english', %(query)s))"

#: Document frequency is taken over the whole corpus, not the filtered subset,
#: so a term's rarity does not change with the domain filter. 'simple' is used
#: because the lexemes are already stemmed.
DOCUMENT_FREQUENCY_SQL = """
SELECT lex AS lexeme,
       (SELECT count(*) FROM provisions p
         WHERE p.search_vector @@ to_tsquery('simple', quote_literal(lex))) AS df
  FROM unnest(%(lexemes)s::text[]) AS lex
"""

TERM_FREQUENCY_SQL = """
SELECT p.id, t.lexeme, cardinality(t.positions) AS tf
  FROM provisions p, LATERAL unnest(p.search_vector) AS t
 WHERE p.id = ANY(%(ids)s::bigint[])
   AND t.lexeme = ANY(%(lexemes)s::text[])
"""

PROVISION_SQL = """
SELECT p.id AS provision_id, p.document_id, p.section_number, p.heading, p.text,
       p.char_start, p.char_end, p.token_count,
       d.jurisdiction, d.title, d.citation, d.source_url, d.snapshot_date,
       d.sha256, d.doc_type
  FROM provisions p
  JOIN documents d ON d.id = p.document_id
 WHERE p.id = ANY(%(ids)s::bigint[])
"""

CORPUS_STATS_SQL = "SELECT provisions, avg_tokens FROM corpus_stats WHERE id = true"


def _array(values: tuple[str, ...]) -> list[str] | None:
    """An empty filter means 'no filter', which SQL reads as NULL."""
    return list(values) or None


def search(
    query: str,
    *,
    routing: Routing | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> RetrievalResult:
    """Run the hybrid search and return provisions with full metadata.

    Below-threshold results are discarded, not downgraded. If everything is
    below threshold the result is empty and ``covered`` is False.
    """
    config = settings()
    routing = routing if routing is not None else route(query)
    top_k = top_k if top_k is not None else config.retrieval_top_k
    threshold = min_score if min_score is not None else config.retrieval_min_score
    filters = {
        "jurisdictions": routing.jurisdictions,
        "doc_types": routing.doc_types,
    }

    def empty(reason: str, best: float = 0.0) -> RetrievalResult:
        return RetrievalResult(
            query=query,
            routing_domain=routing.name,
            domain_recognised=routing.recognised,
            threshold=threshold,
            best_score=best,
            reason=reason,
            filters=filters,
        )

    if not query.strip():
        return empty("empty query")

    # Fails closed: no embeddings means no vector arm, and a lexical-only
    # answer would be a different, weaker guarantee than the one advertised.
    embedding = vector_literal(embed_one(query))

    params = {
        "query": query,
        "jurisdictions": _array(routing.jurisdictions),
        "doc_types": _array(routing.doc_types),
        "limit": config.retrieval_candidates,
        "embedding": embedding,
    }

    with readonly() as conn, conn.cursor() as cur:
        cur.execute(CORPUS_STATS_SQL)
        stats_row = cur.fetchone()
        if stats_row is None or not stats_row["provisions"]:
            return empty("corpus is empty or statistics have never been refreshed")
        stats = CorpusStats(
            provisions=int(stats_row["provisions"]),
            avg_tokens=float(stats_row["avg_tokens"]),
        )

        cur.execute(QUERY_LEXEMES_SQL, {"query": query})
        lexemes = [str(row["lexeme"]) for row in cur.fetchall()]

        lexical_ids: list[int] = []
        if lexemes:
            cur.execute(LEXICAL_CANDIDATES_SQL, params)
            lexical_ids = [int(row["id"]) for row in cur.fetchall()]

        cur.execute(VECTOR_CANDIDATES_SQL, params)
        vector_rows = cur.fetchall()
        similarity = {int(row["id"]): float(row["similarity"]) for row in vector_rows}

        candidate_ids = sorted(set(lexical_ids) | set(similarity))
        if not candidate_ids:
            return empty("no provision matched the query in either arm")

        document_frequency: dict[str, int] = {}
        term_frequency: dict[int, dict[str, int]] = {}
        if lexemes:
            cur.execute(DOCUMENT_FREQUENCY_SQL, {"lexemes": lexemes})
            document_frequency = {str(r["lexeme"]): int(r["df"]) for r in cur.fetchall()}
            cur.execute(TERM_FREQUENCY_SQL, {"ids": candidate_ids, "lexemes": lexemes})
            for row in cur.fetchall():
                term_frequency.setdefault(int(row["id"]), {})[str(row["lexeme"])] = int(row["tf"])

        cur.execute(PROVISION_SQL, {"ids": candidate_ids})
        rows = cur.fetchall()

    scored: list[RetrievedProvision] = []
    for row in rows:
        provision_id = int(row["provision_id"])
        lexical = normalise(
            raw_score(
                term_frequencies=term_frequency.get(provision_id, {}),
                document_frequencies=document_frequency,
                document_tokens=int(row["token_count"]),
                stats=stats,
            )
        )
        vector = max(0.0, min(1.0, similarity.get(provision_id, 0.0)))
        scored.append(
            RetrievedProvision(
                provision_id=provision_id,
                document_id=int(row["document_id"]),
                jurisdiction=str(row["jurisdiction"]),
                title=str(row["title"]),
                citation=str(row["citation"]),
                source_url=str(row["source_url"]),
                snapshot_date=row["snapshot_date"],
                sha256=str(row["sha256"]),
                doc_type=str(row["doc_type"]),
                section_number=str(row["section_number"]),
                heading=str(row["heading"]),
                text=str(row["text"]),
                char_start=int(row["char_start"]),
                char_end=int(row["char_end"]),
                lexical_score=lexical,
                vector_score=vector,
                score=LEXICAL_WEIGHT * lexical + VECTOR_WEIGHT * vector,
            )
        )

    scored.sort(key=lambda p: (-p.score, p.citation, p.section_number))
    best = scored[0].score if scored else 0.0
    keep = tuple(p for p in scored if p.score >= threshold)[:top_k]

    if not keep:
        return empty(
            f"best fused score {best:.3f} is below the threshold {threshold:.3f}; "
            "returning nothing rather than the nearest guess",
            best=best,
        )

    return RetrievalResult(
        query=query,
        routing_domain=routing.name,
        domain_recognised=routing.recognised,
        provisions=keep,
        threshold=threshold,
        best_score=best,
        reason="",
        filters=filters,
    )
