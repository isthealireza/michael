"""Section splitting and record normalisation. No database involved."""

from __future__ import annotations

from datetime import date

import pytest

from michael.ingest import (
    IngestionError,
    _validate,
    normalise_corpus_records,
    snapshot_date_of,
    split_sections,
)
from michael.schema import JURISDICTIONS

SAMPLE = """FIXTURE EMPLOYMENT STANDARDS ACT 2000

An Act about fixture employment standards.

Table of provisions

1. Short title
   This Act may be cited as the Fixture Employment Standards Act 2000.

15A Meaning of casual worker
   A person is a casual worker if the engagement is made on the basis that
   there is no firm advance commitment to continuing work.

23AB Minimum engagement
   A casual worker engaged for a period must be paid for at least the minimum
   engagement period set out in the applicable instrument.
"""


def test_sections_are_split_by_heading_not_token_count() -> None:
    provisions = split_sections(SAMPLE)
    numbers = [p.section_number for p in provisions]
    assert numbers == ["1", "15A", "23AB"]


def test_letter_suffixed_section_numbers_survive() -> None:
    provisions = {p.section_number: p for p in split_sections(SAMPLE)}
    assert provisions["15A"].heading == "Meaning of casual worker"
    assert "no firm advance commitment" in provisions["15A"].text


def test_char_ranges_are_contiguous_and_point_back_into_the_document() -> None:
    provisions = split_sections(SAMPLE)
    for provision in provisions:
        assert provision.char_end > provision.char_start
        assert provision.text.strip() in SAMPLE[provision.char_start : provision.char_end]
    for earlier, later in zip(provisions, provisions[1:], strict=False):
        assert earlier.char_end == later.char_start


def test_front_matter_before_the_first_section_is_not_cited_as_a_provision() -> None:
    provisions = split_sections(SAMPLE)
    assert all("An Act about fixture" not in p.text for p in provisions)


def test_a_document_with_no_sections_is_kept_whole_not_dropped() -> None:
    provisions = split_sections("A short note with no numbered sections at all.")
    assert len(provisions) == 1
    assert provisions[0].section_number == "(whole document)"


def test_empty_text_yields_nothing() -> None:
    assert split_sections("   \n  ") == []


def test_validation_rejects_a_foreign_jurisdiction() -> None:
    with pytest.raises(IngestionError, match="jurisdiction"):
        _validate("nsw", "act", "a" * 64)


def test_validation_rejects_an_unknown_doc_type() -> None:
    with pytest.raises(IngestionError, match="doc_type"):
        _validate("wa", "practice_note", "a" * 64)


def test_validation_rejects_a_malformed_hash() -> None:
    with pytest.raises(IngestionError, match="sha256"):
        _validate("wa", "act", "not-a-hash")


def test_corpus_records_outside_wa_and_commonwealth_are_dropped() -> None:
    stream: list[dict[str, object]] = [
        {"jurisdiction": "nsw", "type": "primary_legislation", "text": "x", "citation": "A"},
        {"jurisdiction": "wa", "type": "primary_legislation", "text": "x", "citation": "B"},
    ]
    kept = list(normalise_corpus_records(stream, {"wa", "commonwealth"}))
    assert [r["citation"] for r in kept] == ["B"]


def test_corpus_records_without_a_citation_are_dropped_not_guessed() -> None:
    stream: list[dict[str, object]] = [
        {"jurisdiction": "wa", "type": "primary_legislation", "text": "x", "citation": ""}
    ]
    assert list(normalise_corpus_records(stream, {"wa"})) == []


def test_unmappable_document_types_are_dropped() -> None:
    stream: list[dict[str, object]] = [
        {"jurisdiction": "wa", "type": "newsletter", "text": "x", "citation": "C"}
    ]
    assert list(normalise_corpus_records(stream, {"wa"})) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-07-01", date(2023, 7, 1)),
        ("01/07/2023", date(2023, 7, 1)),
        ("2023", date(2023, 1, 1)),
    ],
)
def test_snapshot_dates_are_parsed(raw: str, expected: date) -> None:
    assert snapshot_date_of(raw) == expected


def test_an_unparseable_date_falls_back_rather_than_being_invented() -> None:
    from datetime import UTC, datetime

    assert snapshot_date_of("sometime last winter") == datetime.now(UTC).date()
    assert snapshot_date_of(None) == datetime.now(UTC).date()


def test_western_australia_is_mapped_to_the_schema_code() -> None:
    """The corpus spells WA out in full; the schema uses 'wa'.

    This was a silent data-loss bug: an unmapped label is indistinguishable
    from an out-of-scope jurisdiction, so every WA document was dropped.
    """
    stream: list[dict[str, object]] = [
        {
            "jurisdiction": "western_australia",
            "type": "primary_legislation",
            "text": "1. Short title\n   This Act may be cited as the Fixture Act.",
            "citation": "Fixture Act 2000 (WA)",
        }
    ]
    kept = list(normalise_corpus_records(stream, {"wa", "commonwealth"}))
    assert len(kept) == 1
    assert kept[0]["jurisdiction"] == "wa"


def test_other_states_are_still_excluded() -> None:
    stream: list[dict[str, object]] = [
        {"jurisdiction": j, "type": "primary_legislation", "text": "x" * 60, "citation": j}
        for j in ("new_south_wales", "queensland", "south_australia", "tasmania", "victoria")
    ]
    assert list(normalise_corpus_records(stream, {"wa", "commonwealth"})) == []


def test_every_mapped_jurisdiction_is_a_valid_schema_value() -> None:
    from michael.ingest import CORPUS_JURISDICTION_MAP

    assert set(CORPUS_JURISDICTION_MAP.values()) <= set(JURISDICTIONS)


def test_doc_type_filter_keeps_legislation_and_drops_cases() -> None:
    stream: list[dict[str, object]] = [
        {"jurisdiction": "commonwealth", "type": "decision", "text": "x" * 60, "citation": "A"},
        {
            "jurisdiction": "western_australia",
            "type": "primary_legislation",
            "text": "x" * 60,
            "citation": "B",
        },
        {
            "jurisdiction": "commonwealth",
            "type": "secondary_legislation",
            "text": "x" * 60,
            "citation": "C",
        },
    ]
    kept = list(normalise_corpus_records(stream, {"wa", "commonwealth"}, {"act", "regulation"}))
    assert [r["citation"] for r in kept] == ["B", "C"]
    assert all(r["doc_type"] != "case" for r in kept)


def test_no_doc_type_filter_keeps_everything_mappable() -> None:
    stream: list[dict[str, object]] = [
        {"jurisdiction": "commonwealth", "type": "decision", "text": "x" * 60, "citation": "A"},
    ]
    assert len(list(normalise_corpus_records(stream, {"commonwealth"}, None))) == 1


CONTENTS_THEN_BODY = """Fair Work Act 2009
Compilation No. 73

Contents
Chapter 1—Introduction
Part 1-1—Introduction
1 Short title 1
2 Commencement 1
15A Meaning of casual employee 68
61 The National Employment Standards 164
125B Casual Employment Information Statement 210
216A Variation of supported bargaining agreement to add employer and employees 400
Chapter 2—Terms and conditions
Part 2-1—Core provisions 100
47A Casual employees of small business employers 29
66AAA Object of this Division 176
600 Determining matters in the absence of a person 151
536QK FWC must consider whether to vary or revoke an order 43

An Act relating to workplace relations, and for related purposes
Chapter 1—Introduction
1 Short title
This Act may be cited as the Fair Work Act 2009.
15A Meaning of casual employee
General rule
(1) An employee is a casual employee of an employer only if:
(a) the employment relationship is characterised by an absence of a firm advance
commitment to continuing and indefinite work; and
(b) the employee would be entitled to a casual loading.
61 The National Employment Standards are minimum standards
(1) This Part sets minimum standards that apply to the employment of employees.
"""


def test_the_table_of_provisions_is_not_stored_as_provisions() -> None:
    """Contents entries duplicate every citation and, being short, outrank the real law."""
    provisions = split_sections(CONTENTS_THEN_BODY)
    numbers = [p.section_number for p in provisions]

    assert len(numbers) == len(set(numbers)), f"duplicate sections stored: {numbers}"
    # Sections that exist only in the contents table are not invented as provisions.
    assert "216A" not in numbers
    assert "600" not in numbers


def test_the_operative_section_is_the_one_kept() -> None:
    provisions = {p.section_number: p for p in split_sections(CONTENTS_THEN_BODY)}
    assert "15A" in provisions
    body = provisions["15A"].text
    assert "(1)" in body
    # Normalised: the source wraps mid-phrase.
    assert "firm advance commitment" in " ".join(body.split())
    assert not body.rstrip().endswith("68"), "kept the contents entry instead of the section"


def test_a_contents_entry_is_recognised_by_its_page_number() -> None:
    from michael.ingest import _is_contents_entry

    assert _is_contents_entry("15A Meaning of casual employee 68")
    assert _is_contents_entry("61 The National Employment Standards 164")
    assert not _is_contents_entry("15A Meaning of casual employee")
    assert not _is_contents_entry("This Act may be cited as the Fair Work Act 2009.")


def test_penalty_table_rows_are_not_mistaken_for_sections() -> None:
    """Civil-remedy tables are full of lines like '60 penalty units'."""
    table = """13 Grounds for review
The court may review a decision on the following grounds.
60 penalty units 5AAA 66MA(8) (a) an employee; (b) an employee organisation
30 penalty units 18 462(1) (a) an employee; (b) an employer
600 penalty units 5A 179(1) (a) a bargaining representative
"""
    numbers = [p.section_number for p in split_sections(table)]
    assert numbers == ["13"], f"penalty rows captured as sections: {numbers}"


def test_char_offsets_still_point_into_the_original_document() -> None:
    """Cutting the contents block must not break traceability of a quote."""
    for provision in split_sections(CONTENTS_THEN_BODY):
        excerpt = CONTENTS_THEN_BODY[provision.char_start : provision.char_end]
        assert provision.text.strip() in excerpt


def test_a_document_without_a_contents_table_is_untouched() -> None:
    plain = """1 Short title
This Act may be cited as the Example Act.
2 Commencement
(1) This Act commences on Royal Assent.
"""
    numbers = [p.section_number for p in split_sections(plain)]
    assert numbers == ["1", "2"]
