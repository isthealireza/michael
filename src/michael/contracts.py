"""Clause-level comparison of two contracts. Pure functions, no I/O.

Deliberately separate from the legislation splitter in `michael.ingest`.
Contracts number nothing like Acts - "1.1", "3.2(a)", "SCHEDULE A", bare
capitalised headings - and a splitter tuned for one document shape meeting
another is how this project lost 1,199 provisions in a single afternoon.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Literal

#: The clause id given to text that precedes the first heading.
PREAMBLE_ID = "(preamble)"

#: A numbered clause heading: "1.", "1.1", "1.1.1", "3.2(a)", optionally
#: followed by a heading on the same line. Anchored at the line start.
NUMBERED_CLAUSE = re.compile(
    r"^[ \t]*(?P<number>\d{1,3}(?:\.\d{1,3})*(?:\([a-z]{1,2}\))?)[.)]?[ \t]+(?P<heading>\S[^\n]*)?$",
    re.MULTILINE,
)

#: An unnumbered heading in capitals on a line of its own - "PAYMENT",
#: "SCHEDULE A". Requires at least three characters so an initial or a stray
#: "A" cannot split a document.
#:
#: KNOWN OVER-MATCH (documented, not fixed here): this pattern will also match
#: an all-capitals line that sits *inside* a clause body - a defined term
#: written in capitals, or a heading inside a schedule that is not meant to
#: start a new top-level clause. That produces a spurious clause, never a
#: dropped one, so it fails on the safe side of rule 1 (nothing is dropped).
#: Tightening the pattern to eliminate it risks dropping text instead, which is
#: the wrong side to fail on. See Task 3's evidence for where this fires on
#: real documents.
CAPS_CLAUSE = re.compile(r"^[ \t]*(?P<number>[A-Z][A-Z0-9 &'()-]{2,60})[ \t]*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Clause:
    """One clause of a contract, with its offsets into the source text."""

    clause_id: str
    heading: str
    text: str
    char_start: int
    char_end: int


def _headings(text: str) -> list[tuple[int, str, str]]:
    """Every clause heading as (offset, clause_id, heading), in document order.

    Both patterns are applied and the results merged by offset. A line matched
    by both - a numbered heading in capitals - is taken once, numbered form
    first, because the number is the more stable identifier.
    """
    found: dict[int, tuple[str, str]] = {}
    for match in CAPS_CLAUSE.finditer(text):
        if match.start() == 0:
            # A caps-style line with nothing before it has no boundary to mark:
            # it is the document's title, not a clause heading, and is left to
            # fold into the preamble rather than manufacture a spurious clause
            # out of it. A numbered "1." at offset 0 is unambiguous and is not
            # touched by this exception.
            continue
        found[match.start()] = (match.group("number").strip(), "")
    for match in NUMBERED_CLAUSE.finditer(text):
        found[match.start()] = (
            match.group("number").strip(),
            (match.group("heading") or "").strip(),
        )
    return [(offset, *found[offset]) for offset in sorted(found)]


def split_clauses(text: str) -> tuple[Clause, ...]:
    """Split a contract into clauses. Never drops text.

    Everything before the first heading becomes the ``(preamble)`` clause, and
    a document with no headings at all becomes a single ``(preamble)`` clause,
    so every character of the input survives into exactly one clause.

    No assumption is made that numbering rises. A schedule restarting at 1 is
    ordinary drafting, not evidence that the text stopped being a contract.
    """
    headings = _headings(text)
    if not headings:
        return (
            Clause(
                clause_id=PREAMBLE_ID,
                heading="",
                text=text.strip(),
                char_start=0,
                char_end=len(text),
            ),
        )

    clauses: list[Clause] = []
    first_offset = headings[0][0]
    if text[:first_offset].strip():
        clauses.append(
            Clause(
                clause_id=PREAMBLE_ID,
                heading="",
                text=text[:first_offset].strip(),
                char_start=0,
                char_end=first_offset,
            )
        )

    for index, (offset, clause_id, heading) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        clauses.append(
            Clause(
                clause_id=clause_id,
                heading=heading,
                text=text[offset:end].strip(),
                char_start=offset,
                char_end=end,
            )
        )
    return tuple(clauses)


def normalise(text: str) -> str:
    """Collapse whitespace, and nothing else.

    Deliberately timid. Lowercasing would make "Employer" and "employer" the
    same word, and stripping punctuation would make "$5,000" and "$5000" the
    same amount - both are edits a reader is negotiating over. The cost of
    timidity is a few reported changes that are cosmetic; the cost of
    aggression is a changed term reported as unchanged.
    """
    return " ".join(text.split())


#: Above this ratio, two clauses with different ids are taken to be the same
#: clause, edited. MEASURED 2026-09-17 against calibration/labelled_clause_pairs.json
#: (18 same-clause, 11 different-clause real pairs from tests/fixtures/contracts/):
#: 0.74 is the highest threshold that maximises correct pairings (28/29, 0 wrong
#: pairings, 1 missed pairing), tied with every value from 0.55 through 0.74 and
#: resolved toward the higher one, per calibration/calibrate_similarity.py. This
#: is a readability parameter, not a safety parameter: unlike RETRIEVAL_MIN_SCORE,
#: neither a threshold set too low (a wrong pairing, printed as a false CHANGED)
#: nor too high (a missed pairing, printed as true but verbose ADDED+REMOVED) is
#: silent, so the target is maximising correct pairings, not zero false positives.
SIMILARITY_THRESHOLD = 0.74

#: Above this many unpaired clauses on either side, stage 3 is skipped and the
#: remainder reported as ADDED/REMOVED. Bounds an O(u^2) scan.
MAX_UNPAIRED_FOR_SIMILARITY = 400


@dataclass(frozen=True, slots=True)
class ClauseChange:
    """One difference between two drafts. Carries no judgement of it."""

    clause_id_before: str | None
    clause_id_after: str | None
    status: Literal["CHANGED", "ADDED", "REMOVED", "MOVED"]
    heading: str
    diff: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """What differs between two drafts, and how much did not."""

    unchanged: int
    changes: tuple[ClauseChange, ...]


def _word_diff(before: str, after: str) -> tuple[str, ...]:
    """A compact word-level rendering of what changed. Facts, not opinions."""
    old = normalise(before).split()
    new = normalise(after).split()
    lines: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new).get_opcodes():
        if tag == "equal":
            continue
        if old[i1:i2]:
            lines.append("- " + " ".join(old[i1:i2]))
        if new[j1:j2]:
            lines.append("+ " + " ".join(new[j1:j2]))
    return tuple(lines)


def _sans_own_id(clause: Clause) -> str:
    """``clause.text`` with its own leading id token stripped, for comparison.

    `Clause.text` runs from the clause's own heading to the next one, so it
    always includes its own number or caps heading verbatim. That is correct
    for display, but it means a clause identical in every substantive respect
    compares as different text purely because its id changed - "2. PAYMENT"
    is never textually identical to "3. PAYMENT" even though nothing but the
    number moved. Stripping the id (only when the text actually starts with
    it, followed by a real separator or nothing) restores "same content,
    different id" as detectable equality, which is what stage 2 exists to
    find.
    """
    text = clause.text
    prefix = clause.clause_id
    if prefix == PREAMBLE_ID or not text.startswith(prefix):
        return text
    rest = text[len(prefix) :]
    if rest and rest[0] not in ".) \t\n":
        return text
    return rest.lstrip(".) \t\n")


#: A clause's identity inside one document: its id, and which occurrence of
#: that id it is. A bare clause id is NOT an identity - see :func:`_occurrences`.
ClauseKey = tuple[str, int]


def _occurrences(clauses: tuple[Clause, ...]) -> dict[ClauseKey, Clause]:
    """Key every clause uniquely, by its id and its occurrence number.

    Keying on ``clause_id`` alone loses clauses, because a real contract
    repeats ids constantly: a numbered table of contents, an annexed deed that
    restarts at 1, a front-matter block also numbered 1. On the WA Government
    consultancy agreement fixture that collapsed 153 clauses to 137 unique
    ids, and an edit inside one of the 16 discarded clauses was reported as no
    change at all - a negotiated term vanishing from the report rather than
    being mis-described, which is the one failure this module must not have.

    Pairing the nth occurrence of an id with the nth occurrence of the same id
    in the other document is the right default: a repeated id repeats in
    document order in both drafts, and nothing is discarded to find out.
    """
    counts: dict[str, int] = {}
    keyed: dict[ClauseKey, Clause] = {}
    for clause in clauses:
        nth = counts.get(clause.clause_id, 0)
        keyed[(clause.clause_id, nth)] = clause
        counts[clause.clause_id] = nth + 1
    return keyed


def compare(text_a: str, text_b: str) -> ComparisonReport:
    """Compare two drafts clause by clause.

    Three stages, cheapest first: exact id AND text match (UNCHANGED), then
    exact text match alone among what is left (MOVED), then bounded
    similarity (CHANGED, possibly also MOVED). A clause is reported UNCHANGED
    only when its normalised text is identical - see :func:`normalise`. A
    same-id pair whose text differs is never resolved directly in stage 1;
    see the comment there for why.
    """
    before = _occurrences(split_clauses(text_a))
    after = _occurrences(split_clauses(text_b))

    changes: list[ClauseChange] = []
    unchanged = 0

    left = dict(before)
    right = dict(after)

    # Stage 1 - same id, same text. The only pairing with no ambiguity at
    # all, so it is the only thing resolved here.
    #
    # A same id with DIFFERENT text is deliberately NOT resolved in this
    # stage: it is left in `left`/`right` for stage 2 to consider first. Two
    # clauses can trade ids in an ordinary renumbering - a document with
    # clauses "2. PAYMENT" and "3. TERMINATION" renumbered to
    # "2. TERMINATION" and "3. PAYMENT" is not edited at all, just reordered.
    # Comparing text at the same id directly, as this stage's plan-specified
    # reference implementation did, reports each clause CHANGED against the
    # OTHER clause's unrelated text instead of MOVED, which is the wrong
    # answer to give a reader: it says something was edited that was not.
    # Deferring this decision to stage 2 (exact text match, wherever it now
    # is) lets a genuine renumbering resolve to MOVED, and a same-id clause
    # that really was edited in place still resolves correctly, either by
    # stage 2 finding no match for it and falling through to stage 3's
    # similarity ranking, which reports it CHANGED at its own id when no
    # better pairing exists.
    for key in before.keys() & after.keys():
        a, b = before[key], after[key]
        if normalise(a.text) == normalise(b.text):
            unchanged += 1
            del left[key]
            del right[key]

    # Stage 2 - identical text, different id.
    #
    # Compared with the clause's own leading id stripped off, not with the
    # raw `.text`. `Clause.text` includes its own heading line, number and
    # all, by Task 2's design - correct for display and for the no-drop
    # guarantee - but it means a clause renumbered from "2." to "3." with
    # nothing else changed would never compare textually identical to
    # itself, because its own new number is baked into the string. Stripping
    # it restores "identical content, different id", which is what this
    # stage exists to catch. See `_sans_own_id`.
    by_text = {normalise(_sans_own_id(v)): k for k, v in right.items()}
    for key, clause in list(left.items()):
        match_key = by_text.get(normalise(_sans_own_id(clause)))
        if match_key is None or match_key not in right:
            continue
        changes.append(
            ClauseChange(
                clause_id_before=key[0],
                clause_id_after=match_key[0],
                status="MOVED",
                heading=clause.heading,
                diff=(),
            )
        )
        del left[key]
        del right[match_key]

    # Stage 3 - similarity, bounded.
    if len(left) <= MAX_UNPAIRED_FOR_SIMILARITY and len(right) <= MAX_UNPAIRED_FOR_SIMILARITY:
        scored = sorted(
            (
                difflib.SequenceMatcher(None, normalise(a.text), normalise(b.text)).ratio(),
                a_key,
                b_key,
            )
            for a_key, a in left.items()
            for b_key, b in right.items()
        )
        for ratio, a_key, b_key in reversed(scored):
            if ratio < SIMILARITY_THRESHOLD:
                break
            if a_key not in left or b_key not in right:
                continue
            a, b = left[a_key], right[b_key]
            changes.append(
                ClauseChange(
                    clause_id_before=a_key[0],
                    clause_id_after=b_key[0],
                    status="CHANGED",
                    heading=b.heading or a.heading,
                    diff=_word_diff(a.text, b.text),
                )
            )
            del left[a_key]
            del right[b_key]

    changes += [
        ClauseChange(clause_id_before=key[0], clause_id_after=None, status="REMOVED",
                     heading=clause.heading, diff=())
        for key, clause in left.items()
    ]
    changes += [
        ClauseChange(clause_id_before=None, clause_id_after=key[0], status="ADDED",
                     heading=clause.heading, diff=())
        for key, clause in right.items()
    ]
    return ComparisonReport(unchanged=unchanged, changes=tuple(changes))
