"""Fixture provisions for offline tests.

These carry real citation metadata (Act name, section number, source URL) but
**no statutory text**. The text field is an explicit placeholder.

That is deliberate. Michael exists to stop invented law reaching an output, so
inventing statutory wording to make a test pass would defeat the thing being
tested. Tests here assert on citation structure, [MISSING] handling and the
closing blocks. The DB-backed acceptance test in test_acceptance.py asserts on
real retrieved text, and runs only against an ingested corpus.
"""

from __future__ import annotations

from datetime import date

from michael.retrieve import RetrievalResult, RetrievedProvision

PLACEHOLDER = "[FIXTURE - statutory text not reproduced; see source_url]"

FAIR_WORK_URL = "https://www.legislation.gov.au/C2009A00028/latest/text"
SNAPSHOT = date(2026, 7, 1)


def provision(
    *,
    section_number: str,
    heading: str,
    jurisdiction: str = "commonwealth",
    citation: str = "Fair Work Act 2009 (Cth)",
    score: float = 0.82,
) -> RetrievedProvision:
    return RetrievedProvision(
        provision_id=abs(hash((citation, section_number))) % 100_000,
        document_id=1,
        jurisdiction=jurisdiction,
        title=citation,
        citation=citation,
        source_url=FAIR_WORK_URL,
        snapshot_date=SNAPSHOT,
        sha256="0" * 64,
        doc_type="act",
        section_number=section_number,
        heading=heading,
        text=PLACEHOLDER,
        char_start=0,
        char_end=len(PLACEHOLDER),
        lexical_score=score,
        vector_score=score,
        score=score,
    )


#: Section numbers and headings as recorded at the snapshot date. Re-verify
#: against the source before relying on them.
FAIR_WORK_PROVISIONS: tuple[RetrievedProvision, ...] = (
    provision(section_number="15A", heading="Meaning of casual employee", score=0.88),
    provision(section_number="61", heading="The National Employment Standards", score=0.74),
    provision(section_number="125B", heading="Casual Employment Information Statement", score=0.69),
)


def covered_result(query: str, domain: str = "employment") -> RetrievalResult:
    return RetrievalResult(
        query=query,
        routing_domain=domain,
        domain_recognised=True,
        provisions=FAIR_WORK_PROVISIONS,
        threshold=0.35,
        best_score=0.88,
        filters={"jurisdictions": ("commonwealth",), "doc_types": ("act", "regulation", "award")},
    )


def empty_result(query: str, domain: str = "employment") -> RetrievalResult:
    return RetrievalResult(
        query=query,
        routing_domain=domain,
        domain_recognised=True,
        provisions=(),
        threshold=0.35,
        best_score=0.11,
        reason="best fused score 0.110 is below the threshold 0.350",
        filters={"jurisdictions": ("commonwealth",), "doc_types": ("act",)},
    )
