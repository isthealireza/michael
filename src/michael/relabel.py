"""Relabel Schedule clauses that were stored under a plain section number.

A one-off repair, not part of ingestion. It exists because the corpus holds
documents split before :data:`michael.ingest.SCHEDULE_HEADING` recognised an
unnumbered ("Schedule - The Commission") or ordinal ("First Schedule - Iron
Ore Agreement") heading. Those Schedules' clauses restart at 1 and were
stored with plain numeric ids, so they collide with the Act's own sections:
two different provisions both cited "1", indistinguishable to a reader.

Re-ingestion cannot repair them. The corpus was seeded from a snapshot, and
the ``source_url`` of an affected document no longer serves the text that was
ingested - measured across all 44: three documents are now materially shorter
at their own URL (one serves 17% of what was stored), twelve are longer.
Re-ingesting would not re-split the same law, it would acquire today's law
under yesterday's snapshot date.

So this repairs the label where it stands. Nothing is fetched, no text,
embedding or snapshot date is touched, and nothing is deleted - the only
column that changes is ``section_number``.

The planning is pure: :func:`plan_schedule_relabel` takes the stored
provisions and returns the changes it would make, so the decision can be
checked against every affected document before a single row is written.
"""

from __future__ import annotations

from dataclasses import dataclass

from michael.ingest import SCHEDULE_HEADING, _numbers_its_schedules, _schedule_number


@dataclass(frozen=True)
class Relabel:
    """One provision's section_number, as stored and as it should read."""

    provision_id: int
    old: str
    new: str


def _plain_number(section_number: str) -> bool:
    """Is this a bare section id, rather than one already carrying a label?

    Only a plain id can be a mislabelled Schedule clause. A provision already
    reading "Sch 1 cl 3" was labelled correctly when it was split and is left
    exactly as it is.
    """
    return bool(section_number) and section_number[0].isdigit()


def plan_schedule_relabel(provisions: list[dict[str, object]]) -> list[Relabel]:
    """The relabelling this document needs, in document order.

    ``provisions`` are the stored rows, each carrying at least ``id``,
    ``section_number``, ``text`` and ``char_start``. They are sorted here
    rather than trusted to arrive in order, because the order is what decides
    which Schedule a clause falls under.

    A Schedule heading is found inside the text of the provision that
    precedes the Schedule - the splitter that missed the heading swept it up
    as the tail of the last section before it - and it governs the provisions
    that follow that one, not the provision it sits in.
    """
    rows = sorted(provisions, key=lambda p: int(str(p["char_start"])))
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
