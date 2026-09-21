"""Unit tests for the pure parsing helpers behind the direct section lookup.

The lookup itself needs a database connection and is verified against
production through `.orca/ro.sh` (see the round's report), not here. This
file covers the part that can fail silently in a regex: matching a section
marker without hijacking an ordinary content query that happens to contain a
number.
"""

from __future__ import annotations

from michael.retrieve import (
    extract_act_phrase,
    extract_section_number,
    named_jurisdiction_mismatch,
)


def test_the_word_section_is_recognised() -> None:
    assert extract_section_number("What does section 47 of the Fair Work Act say?") == "47"


def test_the_bare_s_abbreviation_is_recognised() -> None:
    assert extract_section_number("section 47 Fair Work Act") == "47"
    assert extract_section_number("s 47 Fair Work Act") == "47"
    assert extract_section_number("s47 Fair Work Act") == "47"
    assert extract_section_number("s. 47 Fair Work Act") == "47"


def test_a_letter_suffix_is_kept_and_uppercased() -> None:
    assert extract_section_number("what does section 15a say") == "15A"


def test_a_bare_number_with_no_marker_is_not_a_section_reference() -> None:
    # The exact failure mode this must not cause: a content query that merely
    # contains a number must not be hijacked into an identifier lookup.
    assert extract_section_number("47 hours per week") is None
    assert extract_section_number("must an employee work more than 47 hours") is None


def test_s_embedded_in_an_ordinary_word_does_not_match() -> None:
    # "is 47", "As 47": the "s" here is not a standalone token, it is the
    # tail of "is" / "As", so it must not be read as the section abbreviation.
    assert extract_section_number("is 47 the right number of hours") is None
    assert extract_section_number("As 47 provides, hours are limited") is None


def test_a_paraphrase_with_no_section_marker_is_unaffected() -> None:
    assert extract_section_number("when a modern award applies to an employer") is None


def test_the_named_act_is_extracted() -> None:
    assert extract_act_phrase("What does section 47 of the Fair Work Act say?") == "Fair Work Act"
    assert extract_act_phrase("section 47 Fair Work Act") == "Fair Work Act"
    assert extract_act_phrase("Land Tax Assessment Act s 26B") == "Land Tax Assessment Act"


def test_no_act_named_is_none() -> None:
    assert extract_act_phrase("what does section 47 say") is None


# --- W2-1: the "Sch N cl M" pinpoint format ---------------------------------


def test_schedule_clause_pinpoint_is_recognised() -> None:
    # This is the exact citation the direct lookup previously could not
    # recognise, even though it is the format Michael's own pinpoint() renders
    # for a Schedule clause (see ingest.py: `f"Sch {enclosing[-1]} cl {number}"`).
    assert extract_section_number("Sch 1 cl 47A") == "Sch 1 cl 47A"


def test_schedule_clause_with_full_words_is_recognised() -> None:
    assert extract_section_number("Schedule 2 clause 11") == "Sch 2 cl 11"


def test_schedule_clause_is_recognised_inside_a_sentence() -> None:
    assert (
        extract_section_number("what does Sch 1 cl 47A of the Fair Work Act say?") == "Sch 1 cl 47A"
    )


def test_schedule_clause_takes_priority_over_the_plain_section_pattern() -> None:
    # "cl 47A" must not be read as "s 47A" via SECTION_REFERENCE's "s" arm
    # matching the "s" in "cl"'s neighbourhood, and the Schedule number must
    # not be dropped: the whole "Sch N cl M" identifier is returned, not just
    # the trailing clause number.
    result = extract_section_number("Sch 1 cl 47A")
    assert result is not None
    assert result.lower() == "sch 1 cl 47a"


def test_a_bare_schedule_number_with_no_clause_is_not_a_schedule_reference() -> None:
    # "Schedule 1" alone names a whole Schedule, not one clause of it - it has
    # no single section_number to look up, so this must not match.
    assert extract_section_number("what is in Schedule 1") is None


# --- W2-5: jurisdiction-mismatch signal -------------------------------------


def test_a_query_naming_an_unheld_jurisdiction_is_flagged() -> None:
    query = "can a landlord in New South Wales terminate a periodic tenancy"
    assert named_jurisdiction_mismatch(query) == "New South Wales"


def test_a_query_naming_wa_is_not_flagged() -> None:
    # WA is a jurisdiction the corpus holds - not a mismatch.
    query = "can a landlord in Western Australia terminate a tenancy"
    assert named_jurisdiction_mismatch(query) is None


def test_a_query_naming_no_jurisdiction_is_not_flagged() -> None:
    assert named_jurisdiction_mismatch("can a landlord terminate a periodic tenancy") is None


def test_every_unheld_state_or_territory_is_recognised() -> None:
    for name in (
        "Victoria",
        "Queensland",
        "South Australia",
        "Tasmania",
        "Northern Territory",
        "Australian Capital Territory",
    ):
        assert named_jurisdiction_mismatch(f"tenancy law in {name}") == name
