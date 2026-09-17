"""Clause-level comparison of two contracts. Pure functions, no I/O.

Deliberately separate from the legislation splitter in `michael.ingest`.
Contracts number nothing like Acts - "1.1", "3.2(a)", "SCHEDULE A", bare
capitalised headings - and a splitter tuned for one document shape meeting
another is how this project lost 1,199 provisions in a single afternoon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
