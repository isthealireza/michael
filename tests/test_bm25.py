"""BM25 scoring is a pure function, so it is tested without a database."""

from __future__ import annotations

import pytest

from michael.bm25 import CorpusStats, idf, normalise, raw_score

STATS = CorpusStats(provisions=10_000, avg_tokens=180.0)


def test_idf_is_never_negative_even_for_ubiquitous_terms() -> None:
    assert idf(document_frequency=9_999, total_documents=10_000) > 0.0
    assert idf(document_frequency=10_000, total_documents=10_000) > 0.0


def test_rare_terms_outweigh_common_ones() -> None:
    assert idf(5, 10_000) > idf(5_000, 10_000)


def test_term_frequency_saturates() -> None:
    """Doubling a term's count must not double the score."""
    single = raw_score(
        term_frequencies={"casual": 1},
        document_frequencies={"casual": 50},
        document_tokens=180,
        stats=STATS,
    )
    many = raw_score(
        term_frequencies={"casual": 20},
        document_frequencies={"casual": 50},
        document_tokens=180,
        stats=STATS,
    )
    assert single < many < single * 4


def test_longer_documents_are_penalised() -> None:
    short = raw_score(
        term_frequencies={"casual": 3},
        document_frequencies={"casual": 50},
        document_tokens=90,
        stats=STATS,
    )
    long = raw_score(
        term_frequencies={"casual": 3},
        document_frequencies={"casual": 50},
        document_tokens=900,
        stats=STATS,
    )
    assert short > long


def test_absent_terms_contribute_nothing() -> None:
    assert (
        raw_score(
            term_frequencies={},
            document_frequencies={"casual": 50},
            document_tokens=180,
            stats=STATS,
        )
        == 0.0
    )


def test_empty_document_scores_zero() -> None:
    assert (
        raw_score(
            term_frequencies={"casual": 3},
            document_frequencies={"casual": 50},
            document_tokens=0,
            stats=STATS,
        )
        == 0.0
    )


def test_normalise_maps_into_the_unit_interval_and_is_monotonic() -> None:
    assert normalise(0.0) == 0.0
    assert normalise(-1.0) == 0.0
    assert 0.0 < normalise(1.0) < normalise(50.0) < 1.0
    assert normalise(8.0) == pytest.approx(0.5)


def test_corpus_stats_rejects_an_impossible_average() -> None:
    with pytest.raises(ValueError):
        CorpusStats(provisions=10, avg_tokens=0.0)
