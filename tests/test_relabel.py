"""The Schedule-clause relabel, planned purely so it can be checked before it runs."""

from __future__ import annotations

from michael.relabel import Merge, Relabel, plan_note_merge, plan_schedule_relabel


def provision(pid: int, number: str, text: str, start: int) -> dict[str, object]:
    return {"id": pid, "section_number": number, "text": text, "char_start": start}


# The real shape, from Agricultural Produce Commission Act 1988 (WA): the
# splitter that could not see "Schedule -" swept the heading up as the tail of
# the last section before it, so the heading is INSIDE section 25's text.
AGRICULTURAL = [
    provision(1, "1", "1. Short title\nThis Act may be cited as the Act.", 0),
    provision(2, "2", "2. Commencement\nThis Act comes into operation on proclamation.", 100),
    provision(
        3,
        "25",
        "25. Regulations\nThe Governor may make regulations.\n"
        "[26, 27. Deleted: No. 11 of 2021 s. 31.]\n"
        "Schedule — The Commission and its proceedings\n[s. 5(6)]",
        200,
    ),
    provision(4, "1", "1. Term of office\nA member holds office for 3 years.", 400),
    provision(5, "2", "2. Remuneration\nA member is entitled to fees.", 500),
]


def test_a_schedules_clauses_are_relabelled_and_the_acts_sections_are_not() -> None:
    plan = plan_schedule_relabel(AGRICULTURAL)
    assert plan == [
        Relabel(provision_id=4, old="1", new="Sch 1 cl 1"),
        Relabel(provision_id=5, old="2", new="Sch 1 cl 2"),
    ]


def test_the_provision_carrying_the_heading_keeps_its_own_number() -> None:
    """The heading is the TAIL of section 25 - the section itself is the Act's,
    not the Schedule's, so it must not be swept into the Schedule with the
    clauses that follow it.
    """
    assert all(r.provision_id != 3 for r in plan_schedule_relabel(AGRICULTURAL))


def test_relabelling_resolves_the_collision_it_exists_for() -> None:
    plan = {r.provision_id: r.new for r in plan_schedule_relabel(AGRICULTURAL)}
    after = [plan.get(int(str(p["id"])), str(p["section_number"])) for p in AGRICULTURAL]
    assert len(after) == len(AGRICULTURAL), "a relabel never adds or drops a provision"
    assert len(after) == len(set(after)), after
    assert after.count("1") == 1


def test_a_provision_already_labelled_is_left_exactly_as_it_is() -> None:
    """Only a plain numeric id can be a mislabelled Schedule clause. A row
    reading "Sch 1 cl 3" was labelled correctly when it was split, and
    relabelling it would produce "Sch 1 cl Sch 1 cl 3".
    """
    rows = [
        provision(1, "5", "5. A section\nSchedule — Forms", 0),
        provision(2, "Sch 1 cl 3", "3. A clause already labelled", 100),
        provision(3, "4", "4. A clause not yet labelled", 200),
    ]
    assert plan_schedule_relabel(rows) == [Relabel(provision_id=3, old="4", new="Sch 1 cl 4")]


def test_an_ordinal_schedule_heading_relabels_to_its_number() -> None:
    rows = [
        provision(1, "2", "2. Ratification\nFirst Schedule — Iron Ore Agreement", 0),
        provision(2, "1", "1. Interpretation\nIn this Agreement terms have meanings.", 100),
    ]
    assert plan_schedule_relabel(rows) == [Relabel(provision_id=2, old="1", new="Sch 1 cl 1")]


def test_a_document_that_numbers_its_schedules_ignores_a_bare_schedule_line() -> None:
    """Same guard the splitter carries: a bare "Schedule" in a document that
    numbers its Schedules is a cross-reference, and claiming "Sch 1" for the
    clauses after it would assert a number the document does not use.
    """
    rows = [
        provision(1, "5", "5. A section mentioning the\nSchedule — see below", 0),
        provision(2, "6", "6. Another section\nSchedule 2 — Transitional", 100),
        provision(3, "1", "1. A transitional clause", 200),
    ]
    assert plan_schedule_relabel(rows) == [Relabel(provision_id=3, old="1", new="Sch 2 cl 1")]


def test_a_document_with_no_schedule_is_not_touched() -> None:
    rows = [
        provision(1, "1", "1. Short title\nThis Act may be cited as the Act.", 0),
        provision(2, "2", "2. Commencement\nIt comes into operation on proclamation.", 100),
    ]
    assert plan_schedule_relabel(rows) == []


def test_the_plan_reads_the_provisions_in_document_order_not_row_order() -> None:
    """Order decides which Schedule a clause falls under, so it is taken from
    char_start rather than trusted from the caller.
    """
    shuffled = [AGRICULTURAL[4], AGRICULTURAL[0], AGRICULTURAL[3], AGRICULTURAL[2], AGRICULTURAL[1]]
    assert plan_schedule_relabel(shuffled) == plan_schedule_relabel(AGRICULTURAL)


# The real shape, from Offshore Minerals Act 2003 (WA): the splitter that
# could not see "Notes for this subsection:" left it as the tail of the
# section it belongs to, and split the note's own items off as provisions
# numbered from 1.
NOTE_BLOCK = [
    provision(1, "1", "1. Short title\nThis Act may be cited as the Act.", 0),
    provision(
        2,
        "12",
        "12. Application of Act\nThis Act applies beyond the baseline.\nNotes for this subsection:",
        100,
    ),
    provision(3, "1", "1. So far as the agreement relates to petroleum.", 200),
    provision(4, "2", "2. The Seas and Submerged Lands Act 1973 declares.", 300),
    provision(5, "13", "13. Crown to be bound\nThis Act binds the Crown.", 400),
]


def test_note_items_are_folded_back_into_the_section_that_carries_the_note() -> None:
    plan = plan_note_merge(NOTE_BLOCK)
    assert plan == [
        Merge(provision_id=3, into_id=2, section_number="1"),
        Merge(provision_id=4, into_id=2, section_number="2"),
    ]


def test_the_run_stops_at_the_sections_own_text_resuming() -> None:
    """A real heading after a note block never continues the note's sequence,
    so it can never be absorbed however close it sits."""
    merged = {m.provision_id for m in plan_note_merge(NOTE_BLOCK)}
    assert 5 not in merged, "section 13 was swallowed by the note block"
    assert 1 not in merged


def test_a_provision_not_ending_in_a_note_opener_absorbs_nothing() -> None:
    rows = [
        provision(1, "12", "12. Application\nThis Act applies. Note that fees apply.", 0),
        provision(2, "1", "1. A real first clause of something else.", 100),
    ]
    assert plan_note_merge(rows) == []


def test_a_schedule_clauses_note_block_is_found_too() -> None:
    """Supreme Court (Fees) Regulations 2002 (WA) heads each fee row's notes
    "Notes for this item:", and its rows are already labelled Sch 1 cl N, so
    the run is read from the LOCAL number rather than the whole label.
    """
    rows = [
        provision(1, "Sch 1 cl 4", "4. Fee for a hearing\n$1 277.00\nNotes for this item:", 0),
        provision(2, "Sch 1 cl 1", "1. No fee is payable if interlocutory.", 100),
        provision(3, "Sch 1 cl 2", "2. The fee is paid per hearing day.", 200),
        provision(4, "Sch 1 cl 5", "5. Fee for taxation of costs\n$449.00", 300),
    ]
    assert plan_note_merge(rows) == [
        Merge(provision_id=2, into_id=1, section_number="Sch 1 cl 1"),
        Merge(provision_id=3, into_id=1, section_number="Sch 1 cl 2"),
    ]


def test_a_bare_notes_line_is_not_an_opener_here_either() -> None:
    """The Chattel Securities guard, carried across to the repair: a contents
    row reading "Notes" must not swallow the document that follows it."""
    rows = [
        provision(1, "3", "3. A section\nNotes", 0),
        provision(2, "1", "1. A real first clause.", 100),
        provision(3, "2", "2. A real second clause.", 200),
    ]
    assert plan_note_merge(rows) == []
