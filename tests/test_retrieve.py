"""Unit tests for the pure parsing helpers behind the direct section lookup.

The lookup itself needs a database connection and is verified against
production through `.orca/ro.sh` (see the round's report), not here. This
file covers the part that can fail silently in a regex: matching a section
marker without hijacking an ordinary content query that happens to contain a
number.
"""

from __future__ import annotations

from michael.retrieve import (
    RetrievedProvision,
    extract_act_phrase,
    extract_section_number,
    named_jurisdiction_mismatch,
)
from tests import fixtures


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


def test_every_unheld_abbreviation_is_recognised_in_running_text() -> None:
    for abbreviation in ("NSW", "Vic", "VIC", "Qld", "QLD", "SA", "Tas", "TAS", "NT", "ACT"):
        query = f"can a landlord in {abbreviation} terminate a periodic tenancy"
        assert named_jurisdiction_mismatch(query) == abbreviation


def test_every_unheld_abbreviation_is_recognised_as_a_citation_suffix() -> None:
    for abbreviation in ("NSW", "Vic", "Qld", "SA", "Tas", "NT", "ACT"):
        query = f"Residential Tenancies Act 2010 ({abbreviation}) s 26"
        assert named_jurisdiction_mismatch(query) == abbreviation


def test_wa_abbreviation_is_not_flagged() -> None:
    assert named_jurisdiction_mismatch("what does the Residential Tenancies Act (WA) say") is None


def test_lowercase_abbreviations_do_not_match() -> None:
    assert named_jurisdiction_mismatch("what is the usa position on this") is None
    assert named_jurisdiction_mismatch("isn't this covered already") is None
    assert named_jurisdiction_mismatch("nt sure this is right") is None


def test_the_ordinary_word_act_does_not_trigger_a_mismatch() -> None:
    assert named_jurisdiction_mismatch("What does the Fair Work Act say?") is None
    assert named_jurisdiction_mismatch("Act now to register the vehicle") is None
    assert named_jurisdiction_mismatch("What does the Land Tax Assessment Act 2002 say") is None
    assert named_jurisdiction_mismatch("must a cat be sterilised under the Cat Act 2011") is None


def test_act_the_territory_is_still_recognised_next_to_the_word_act() -> None:
    query = "does the Residential Tenancies Act 1997 (ACT) cover this"
    assert named_jurisdiction_mismatch(query) == "ACT"


def test_abbreviation_embedded_in_a_longer_word_does_not_match() -> None:
    assert named_jurisdiction_mismatch("Tasmania is not the same as Tas") == "Tasmania"
    assert named_jurisdiction_mismatch("the vicinity of the property") is None
    assert named_jurisdiction_mismatch("a contact for this matter") is None
    assert named_jurisdiction_mismatch("compact and exact obligations") is None


# --- pinpoint rendering ----------------------------------------------------
#
# The README's "Known limitations" recorded case law being cited as if it were
# legislation, with two observed rows from a real seed:
#
#     section_number | heading
#     2              | (1842) 5 Beav., at p. 303 [49 E.R., at p. 594].
#     6              | The Tang respondents are:
#
# Row 2 is a numbered FOOTNOTE of Muir v Open Brethren [1956] HCA 14; row 6 is
# paragraph 6 of Cromwell Corporation v ARA Real Estate [2020] FCA 1492. Both
# rendered as "s 2" and "s 6" - a pinpoint asserting that a passage of
# reasons, or a footnote, is a section of an Act. The splitter side of these
# two rows is pinned in tests/test_ingest.py; what is pinned here is that
# neither can ever be rendered with an "s".


def test_the_readme_row_6_renders_as_an_aglc_paragraph_pinpoint() -> None:
    """`Cromwell ... [2020] FCA 1492 s 6` becomes `... at [6]`."""
    row = fixtures.judgment_paragraph(number="6", heading="The Tang respondents are:")
    assert row.locator() == "at [6]"
    assert row.pinpoint() == f"{fixtures.CROMWELL} at [6] (snapshot {fixtures.SNAPSHOT})"
    assert " s 6 " not in row.pinpoint()


def test_the_readme_row_2_is_never_rendered_as_a_section() -> None:
    """Muir carries no paragraph numbering at all, so it is cited as the case
    and nothing is invented for it. The footnote that produced "s 2" is not a
    unit and has no pinpoint of its own."""
    row = fixtures.provision(
        section_number="(whole document)",
        heading="",
        citation=fixtures.MUIR,
        unit_type="document",
        doc_type="case",
    )
    assert row.locator() == "(whole document)"
    assert row.pinpoint() == f"{fixtures.MUIR} (whole document) (snapshot {fixtures.SNAPSHOT})"
    assert " s " not in row.pinpoint()


def test_a_judgment_paragraph_is_cited_per_the_aglc() -> None:
    """The form the task specifies: `Muir v Open Brethren [1956] HCA 14 at [2]`."""
    row = fixtures.judgment_paragraph(number="2", heading="", citation=fixtures.MUIR)
    assert row.pinpoint().startswith(f"{fixtures.MUIR} at [2] ")


def test_legislation_is_still_cited_by_section() -> None:
    """The other half: nothing about a statutory pinpoint changed."""
    row = fixtures.provision(section_number="15A", heading="Meaning of casual employee")
    assert row.locator() == "s 15A"
    assert row.pinpoint() == (f"Fair Work Act 2009 (Cth) s 15A (snapshot {fixtures.SNAPSHOT})")


def test_a_schedule_clause_is_still_cited_as_a_schedule_clause() -> None:
    row = fixtures.provision(section_number="Sch 1 cl 3", heading="Fixture clause")
    assert row.locator() == "Sch 1 cl 3"


def test_a_judgments_orders_are_not_given_an_invented_number() -> None:
    """The restarted-run decision, as a test. A judgment numbers its orders
    from 1 and then restarts its reasons at 1, and a report can carry more
    than one such block ("THE COURT DECLARES THAT: 1." then "THE COURT ORDERS
    THAT: 1."). AGLC has no pinpoint form for an order, so none is invented:
    the orders are cited as the block they are."""
    row = fixtures.provision(
        section_number="(orders)",
        heading="Orders",
        citation=fixtures.CROMWELL,
        unit_type="order",
        doc_type="case",
    )
    assert row.locator() == "(orders)"
    assert "at [" not in row.pinpoint()
    assert " s " not in row.pinpoint()


def test_orders_and_paragraph_one_of_the_same_judgment_do_not_share_a_pinpoint() -> None:
    """The collision, end to end. Under the section splitter both were stored
    as "1" of the same document and rendered the same pinpoint."""
    orders = fixtures.provision(
        section_number="(orders)",
        heading="Orders",
        citation=fixtures.CROMWELL,
        unit_type="order",
        doc_type="case",
    )
    first = fixtures.judgment_paragraph(number="1", heading="By originating application")
    assert orders.pinpoint() != first.pinpoint()


def test_an_unmigrated_row_still_renders_as_a_section() -> None:
    """`unit_type` defaults to "section", so a row read from a database that
    has not had migration 0001 applied behaves exactly as it did before."""
    row = RetrievedProvision(
        provision_id=1,
        document_id=1,
        jurisdiction="wa",
        title="t",
        citation="Fixture Act 2000 (WA)",
        source_url="u",
        snapshot_date=fixtures.SNAPSHOT,
        sha256="0" * 64,
        doc_type="act",
        section_number="4",
        heading="h",
        text="t",
        char_start=0,
        char_end=1,
        lexical_score=0.0,
        vector_score=0.0,
        score=0.0,
    )
    assert row.locator() == "s 4"
