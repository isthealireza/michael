"""Measure the clause-similarity threshold against a labelled set of real pairs.

Mirrors ``calibration/calibrate.py`` in shape, not in target. Retrieval's rule
-- the lowest threshold with zero false positives -- does not transfer here,
and the reason is about which failures are visible.

For retrieval, a false positive is silent: Michael answers from a corpus that
cannot answer the question, and nothing on the page says so. For clause
similarity, neither direction is silent:

* threshold too LOW  -> two unrelated clauses are paired and a nonsense diff
  is printed. Visible.
* threshold too HIGH -> a real pairing is missed and the clause is reported as
  ADDED plus REMOVED. Verbose, but visible, and true.

So this is a readability parameter, not a safety parameter. The target is:
maximise correct pairings across the labelled set, report wrong pairings and
missed pairings SEPARATELY at every threshold (never a single score that hides
which is which), and where two thresholds tie on total correct pairings,
prefer the higher one -- because a wrong pairing prints a diff between two
clauses that were never versions of each other and a reader can mistake that
for a real edit, while a missed pairing only prints ADDED and REMOVED:
verbose, but true.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import difflib  # noqa: E402

from michael.contracts import normalise  # noqa: E402

THRESHOLDS = [round(0.30 + 0.01 * i, 2) for i in range(66)]  # 0.30 .. 0.95


@dataclass(frozen=True, slots=True)
class SweepRow:
    threshold: float
    wrong_pairings: int  # different-clause pairs scoring >= threshold
    missed_pairings: int  # same-clause pairs scoring < threshold
    correct: int  # everything else: correctly paired or correctly kept apart


def sweep(same_ratios: list[float], different_ratios: list[float]) -> list[SweepRow]:
    rows = []
    total = len(same_ratios) + len(different_ratios)
    for t in THRESHOLDS:
        wrong = sum(1 for r in different_ratios if r >= t)
        missed = sum(1 for r in same_ratios if r < t)
        correct = total - wrong - missed
        rows.append(
            SweepRow(threshold=t, wrong_pairings=wrong, missed_pairings=missed, correct=correct)
        )
    return rows


def choose(rows: list[SweepRow]) -> SweepRow:
    """Maximise correct pairings; where thresholds tie, prefer the higher one.

    The tie-break is not arbitrary: at equal total error, a higher threshold
    trades wrong pairings for missed pairings, and a missed pairing is the
    failure this project prefers -- verbose (ADDED plus REMOVED) but true,
    against a wrong pairing that prints a diff between two clauses that were
    never versions of each other.
    """
    best_correct = max(r.correct for r in rows)
    candidates = [r for r in rows if r.correct == best_correct]
    return max(candidates, key=lambda r: r.threshold)


def main() -> int:
    data = json.loads(
        (ROOT / "calibration" / "labelled_clause_pairs.json").read_text(encoding="utf-8")
    )

    # autojunk=False, matching src/michael/contracts.py. Left at the default
    # True, SequenceMatcher marks any character recurring often enough in a
    # 200+-character sequence as "popular" and refuses to anchor a match on
    # it - which collapsed a real edited-clause pair (two numbers changed in
    # 266 characters of ordinary prose) from 0.9925 to 0.1692. A calibration
    # script measuring the wrong ratio would calibrate the wrong threshold.
    same_ratios = [
        difflib.SequenceMatcher(
            None, normalise(p["before"]), normalise(p["after"]), autojunk=False
        ).ratio()
        for p in data["same_clause"]
    ]
    different_ratios = [
        difflib.SequenceMatcher(None, normalise(p["a"]), normalise(p["b"]), autojunk=False).ratio()
        for p in data["different_clause"]
    ]

    print("=" * 92)
    print("SAME-CLAUSE PAIRS (should score HIGH -- a missed pairing here is a false ADDED+REMOVED)")
    print("=" * 92)
    for p, r in zip(data["same_clause"], same_ratios, strict=True):
        print(f"  {r:.4f}  {p['source'][:80]}")

    print()
    print("=" * 92)
    print("DIFFERENT-CLAUSE PAIRS (should score LOW -- a wrong pairing here is a false CHANGED)")
    print("=" * 92)
    for p, r in sorted(
        zip(data["different_clause"], different_ratios, strict=True), key=lambda x: -x[1]
    ):
        print(f"  {r:.4f}  {p['topic']}")

    print()
    print("=" * 92)
    print("SWEEP -- wrong and missed pairings reported SEPARATELY, never a single hiding score")
    print("=" * 92)
    print(f"{'thresh':>7} {'wrong':>7} {'missed':>8} {'correct':>9} {'/ total':>8}")
    rows = sweep(same_ratios, different_ratios)
    total = len(same_ratios) + len(different_ratios)
    for row in rows:
        print(
            f"{row.threshold:>7.2f} {row.wrong_pairings:>7} {row.missed_pairings:>8} "
            f"{row.correct:>9} {total:>8}"
        )

    chosen = choose(rows)
    print()
    print("=" * 92)
    print(f"CHOSEN: SIMILARITY_THRESHOLD = {chosen.threshold:.2f}")
    print(
        f"  {chosen.correct}/{total} correct "
        f"({chosen.wrong_pairings} wrong pairings, {chosen.missed_pairings} missed pairings)"
    )
    print("  maximised correct pairings; tied thresholds resolved toward the higher one,")
    print("  because a missed pairing (ADDED+REMOVED) is preferred over a wrong pairing")
    print("  (a false CHANGED diff between two clauses that were never the same clause).")
    print(f"  highest different-clause ratio: {max(different_ratios):.4f}")
    print(f"  lowest same-clause ratio: {min(same_ratios):.4f}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
