"""In-place repairs for provisions already stored under a wrong pinpoint.

One-off repairs, not part of ingestion. They exist because the corpus holds
documents split before the splitter learned two things:

* an unnumbered ("Schedule - The Commission") or ordinal ("First Schedule -
  Iron Ore Agreement") Schedule heading, so those Schedules' clauses restart
  at 1 and were stored under plain numeric ids colliding with the Act's own
  sections - :func:`plan_schedule_relabel`;
* a note block headed "Note:", "Notes for this subsection:" or "Notes for
  this item:", whose items number from 1 and were stored as provisions of
  their own - :func:`plan_note_merge`.

Re-ingestion cannot repair either. The corpus was seeded from a snapshot, and
an affected document's ``source_url`` no longer serves the text that was
ingested - measured across all 44: three documents are now materially shorter
at their own URL (one serves 17% of what was stored), twelve are longer.
Re-ingesting would not re-split the same law, it would acquire today's law
under yesterday's snapshot date.

So these repair what is stored. Nothing is fetched, and no snapshot date is
touched. The planning is pure - both functions take the stored provisions and
return the changes they would make - so every decision can be checked against
every affected document before a single row is written.
"""

from __future__ import annotations

from dataclasses import dataclass

from michael.ingest import (
    APPARATUS_OPENERS,
    SCHEDULE_HEADING,
    _numbers_its_schedules,
    _schedule_number,
)


@dataclass(frozen=True)
class Relabel:
    """One provision's section_number, as stored and as it should read."""

    provision_id: int
    old: str
    new: str


@dataclass(frozen=True)
class Merge:
    """One provision that is not a provision: an item of the note block
    belonging to ``into_id``, to be folded back into it."""

    provision_id: int
    into_id: int
    section_number: str


def _plain_number(section_number: str) -> bool:
    """Is this a bare section id, rather than one already carrying a label?

    Only a plain id can be a mislabelled Schedule clause. A provision already
    reading "Sch 1 cl 3" was labelled correctly when it was split and is left
    exactly as it is.
    """
    return bool(section_number) and section_number[0].isdigit()


def _ordered(provisions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Document order, taken from the text rather than trusted from the caller."""
    return sorted(provisions, key=lambda p: int(str(p["char_start"])))


def _local_number(section_number: str) -> str:
    """The number a provision carries locally: "3" from either "3" or
    "Sch 1 cl 3"."""
    return section_number.rsplit(" ", 1)[-1]


def plan_schedule_relabel(provisions: list[dict[str, object]]) -> list[Relabel]:
    """The relabelling this document needs, in document order.

    ``provisions`` are the stored rows, each carrying at least ``id``,
    ``section_number``, ``text`` and ``char_start``.

    A Schedule heading is found inside the text of the provision that
    precedes the Schedule - the splitter that missed the heading swept it up
    as the tail of the last section before it - and it governs the provisions
    that follow that one, not the provision it sits in.
    """
    rows = _ordered(provisions)
    whole = "\n".join(str(p["text"]) for p in rows)
    numbered = _numbers_its_schedules(whole)

    plan: list[Relabel] = []
    current: str | None = None
    for row in rows:
        section = str(row["section_number"])
        if current is not None and _plain_number(section):
            plan.append(
                Relabel(
                    provision_id=int(str(row["id"])),
                    old=section,
                    new=f"Sch {current} cl {section}",
                )
            )
        # Headings inside THIS provision govern the ones after it. The last
        # one wins: a provision's tail can carry two Schedule headings when a
        # Schedule's own first clause was also swept up.
        for match in SCHEDULE_HEADING.finditer(str(row["text"])):
            number = _schedule_number(match)
            if number is None:
                if numbered:
                    continue
                number = "1"
            current = number
    return plan


def plan_note_merge(provisions: list[dict[str, object]]) -> list[Merge]:
    """The note items stored as provisions, and the provision each belongs to.

    This mirrors :func:`michael.ingest.apparatus_rows`, but over stored rows
    rather than over a live document: a provision whose text ENDS with a note
    opener is followed by that note's items, numbered consecutively from 1,
    and each of those was stored as a provision of its own. The run ends at
    the first row that does not continue the sequence, which is the section's
    own text resuming - a real heading after a note block never continues from
    1, so it can never be absorbed however close it sits.

    A merge conserves text by construction: the item's text is appended to the
    provision that carries the note, which is where it always belonged.
    """
    rows = _ordered(provisions)
    plan: list[Merge] = []
    index = 0
    while index < len(rows):
        tail = str(rows[index]["text"]).rstrip()
        last_line = tail.rsplit("\n", 1)[-1].strip()
        if not APPARATUS_OPENERS.match(last_line):
            index += 1
            continue
        parent = int(str(rows[index]["id"]))
        expected = 1
        cursor = index + 1
        while cursor < len(rows):
            number = _local_number(str(rows[cursor]["section_number"]))
            if not number.isdigit() or int(number) != expected:
                break
            plan.append(
                Merge(
                    provision_id=int(str(rows[cursor]["id"])),
                    into_id=parent,
                    section_number=str(rows[cursor]["section_number"]),
                )
            )
            expected += 1
            cursor += 1
        index = max(cursor, index + 1)
    return plan
