"""BM25 scoring.

Term statistics come from Postgres' ``tsvector`` so that the lexical arm uses
one tokeniser and one stemmer end to end: the lexemes counted here are exactly
the lexemes Postgres indexed. The scoring itself is a pure function so it can
be tested without a database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Standard BM25 parameters. k1 controls term-frequency saturation, b controls
#: length normalisation.
K1 = 1.2
B = 0.75

#: BM25 is unbounded, but the retrieval threshold has to mean something on an
#: absolute scale, so scores are squashed to [0, 1) by ``s / (s + SATURATION)``.
#: A raw BM25 of SATURATION maps to 0.5.
SATURATION = 8.0


@dataclass(frozen=True, slots=True)
class CorpusStats:
    """Corpus-wide statistics, refreshed at the end of each ingestion run."""

    provisions: int
    avg_tokens: float

    def __post_init__(self) -> None:
        if self.provisions < 0:
            raise ValueError("provisions must not be negative")
        if self.avg_tokens <= 0:
            raise ValueError("avg_tokens must be positive")


def idf(document_frequency: int, total_documents: int) -> float:
    """Inverse document frequency, in the always-positive Lucene form.

    The classic Robertson/Sparck-Jones form goes negative for terms present in
    more than half the corpus, which would let a common word subtract from a
    score. This form cannot.
    """
    if total_documents <= 0:
        return 0.0
    document_frequency = max(0, min(document_frequency, total_documents))
    return math.log(1.0 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5))


def raw_score(
    *,
    term_frequencies: dict[str, int],
    document_frequencies: dict[str, int],
    document_tokens: int,
    stats: CorpusStats,
    k1: float = K1,
    b: float = B,
) -> float:
    """Raw, unbounded BM25 score for one provision against one query.

    ``term_frequencies`` holds only the query lexemes present in the provision;
    absent lexemes contribute nothing, so they need not appear.
    """
    if document_tokens <= 0:
        return 0.0

    length_norm = k1 * (1.0 - b + b * (document_tokens / stats.avg_tokens))
    total = 0.0
    for lexeme, frequency in term_frequencies.items():
        if frequency <= 0:
            continue
        weight = idf(document_frequencies.get(lexeme, 0), stats.provisions)
        total += weight * (frequency * (k1 + 1.0)) / (frequency + length_norm)
    return total


def normalise(score: float, *, saturation: float = SATURATION) -> float:
    """Squash a raw BM25 score into [0, 1) so a threshold can be absolute."""
    if score <= 0.0:
        return 0.0
    return score / (score + saturation)
