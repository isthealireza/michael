"""Calibrate RETRIEVAL_MIN_SCORE against the seeded corpus.

Run with the container up and the corpus seeded::

    uv run python calibration/calibrate.py

Retrieval runs **unfiltered** (no domain filter) on purpose. A jurisdiction
filter would reject most of the known-absent queries before scoring, which
would flatter the threshold. Unfiltered, the threshold alone has to do the
work, so the chosen value is conservative.

A false positive is the failure that matters: returning any provision for a
question the corpus cannot answer is exactly the "nearest guess" the design
forbids. So the chosen threshold is the lowest one with zero false positives.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from michael.cli import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from michael.domains import Domain, Routing  # noqa: E402
from michael.retrieve import search  # noqa: E402

THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
TOP_K = 10
UNFILTERED = Routing(domain=None, matched_keywords=(), recognised=False)


@dataclass(frozen=True, slots=True)
class Probe:
    """One query's measured outcome, independent of any threshold."""

    query: str
    best_score: float
    target_score: float | None  # score of the labelled provision, if it ranked
    target_rank: int | None


def probe(
    query: str,
    target: tuple[str, str, str] | None,
    *,
    routing: Routing = UNFILTERED,
) -> Probe:
    """Score a query once, with the threshold effectively disabled.

    ``target`` is (citation, number, unit_type). The unit type is part of the
    identity of a target, not decoration: once case law is in the corpus,
    "2" names both section 2 of an Act and paragraph 2 of a judgment, and a
    match on the number alone would score the wrong row as a hit.
    """
    result = search(query, routing=routing, top_k=TOP_K, min_score=0.0)
    best = result.provisions[0].score if result.provisions else 0.0

    target_score: float | None = None
    target_rank: int | None = None
    if target is not None:
        citation, section, unit = target
        for rank, provision in enumerate(result.provisions, start=1):
            if (
                provision.citation == citation
                and provision.section_number == section
                and provision.unit_type == unit
            ):
                target_score = provision.score
                target_rank = rank
                break
    return Probe(query=query, best_score=best, target_score=target_score, target_rank=target_rank)


#: Retrieval restricted to legislation, for measuring the corpus as it was
#: before case law was in it without deleting anything from it. Everything
#: else about the run is unchanged, so the two sweeps are comparable.
LEGISLATION_ONLY = Routing(
    domain=Domain(
        name="(legislation only)",
        keywords=(),
        jurisdictions=(),
        doc_types=("act", "regulation", "award"),
        templates="",
    ),
    matched_keywords=(),
    recognised=True,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=["all", "legislation", "case"],
        default="all",
        help="which labelled queries to measure. Default: all of them.",
    )
    parser.add_argument(
        "--corpus",
        choices=["all", "legislation"],
        default="all",
        help=(
            "which documents retrieval may return. 'legislation' reproduces the "
            "corpus as it was before case law was ingested, without removing it."
        ),
    )
    args = parser.parse_args(argv)
    routing = LEGISLATION_ONLY if args.corpus == "legislation" else UNFILTERED

    data = json.loads((ROOT / "calibration" / "labelled_queries.json").read_text(encoding="utf-8"))

    def wanted(case: dict[str, str]) -> bool:
        return args.kind == "all" or case.get("kind", "legislation") == args.kind

    data["known_good"] = [c for c in data["known_good"] if wanted(c)]
    data["known_absent"] = [c for c in data["known_absent"] if wanted(c)]
    print(
        f"queries: kind={args.kind} corpus={args.corpus} "
        f"known_good={len(data['known_good'])} known_absent={len(data['known_absent'])}"
    )

    print("Probing known-good queries...")
    good = [
        probe(
            case["query"],
            (case["citation"], case["section"], case.get("unit", "section")),
            routing=routing,
        )
        for case in data["known_good"]
    ]
    print("Probing known-absent queries...")
    absent = [probe(case["query"], None, routing=routing) for case in data["known_absent"]]

    print("\n" + "=" * 96)
    print("KNOWN-GOOD: does the labelled provision come back, and at what score?")
    print("=" * 96)
    for case, p in zip(data["known_good"], good, strict=True):
        found = (
            f"rank {p.target_rank}, score {p.target_score:.3f}"
            if p.target_rank
            else "NOT IN TOP 10"
        )
        unit = case.get("unit", "section")
        locator = f"at [{case['section']}]" if unit == "paragraph" else f"s {case['section']}"
        where = f"{case['citation'][:38]} {locator}"
        print(f"  {p.best_score:.3f} best | {found:<28} | {where}")
        print(f"        {case['query'][:86]}")

    print("\n" + "=" * 96)
    print("KNOWN-ABSENT: the top score is what the threshold must reject")
    print("=" * 96)
    for p in sorted(absent, key=lambda x: -x.best_score):
        print(f"  {p.best_score:.3f}  {p.query[:82]}")

    print("\n" + "=" * 96)
    print("SWEEP")
    print("=" * 96)
    print(f"{'thresh':>7} {'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4} {'precision':>10} {'recall':>8}")
    rows = []
    for t in THRESHOLDS:
        tp = sum(1 for p in good if p.target_score is not None and p.target_score >= t)
        fn = len(good) - tp
        fp = sum(1 for p in absent if p.best_score >= t)
        tn = len(absent) - fp
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / len(good) if good else 0.0
        rows.append((t, tp, fn, fp, tn, precision, recall))
        print(f"{t:>7.2f} {tp:>4} {fn:>4} {fp:>4} {tn:>4} {precision:>10.3f} {recall:>8.3f}")

    clean = [r for r in rows if r[3] == 0]
    print("\n" + "=" * 96)
    if clean:
        chosen = min(clean, key=lambda r: r[0])
        print(f"CHOSEN: RETRIEVAL_MIN_SCORE = {chosen[0]:.2f}")
        print("  lowest threshold with zero false positives")
        print(
            f"  precision {chosen[5]:.3f}, recall {chosen[6]:.3f} "
            f"({chosen[1]}/{len(good)} known-good retrieved)"
        )
        worst_absent = max(p.best_score for p in absent)
        print(f"  highest known-absent score was {worst_absent:.3f}")
    else:
        print("NO THRESHOLD IN THE SWEEP ELIMINATES FALSE POSITIVES.")
        print(f"  highest known-absent score: {max(p.best_score for p in absent):.3f}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
