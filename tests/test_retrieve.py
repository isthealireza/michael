"""Unit tests for the pure parsing helpers behind the direct section lookup.

The lookup itself needs a database connection and is verified against
production through `.orca/ro.sh` (see the round's report), not here. This
file covers the part that can fail silently in a regex: matching a section
marker without hijacking an ordinary content query that happens to contain a
number.
"""

from __future__ import annotations

from michael.retrieve import extract_act_phrase, extract_section_number


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
