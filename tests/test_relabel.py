"""The Schedule-clause relabel, planned purely so it can be checked before it runs."""

from __future__ import annotations

from michael.relabel import Relabel, plan_schedule_relabel


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
