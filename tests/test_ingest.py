"""Section splitting and record normalisation. No database involved.

The file-log tests below stub the database with a fake connection - they
never open a socket - to prove the audit trail without needing Postgres.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from michael import ingest, schema
from michael.config import settings
from michael.ingest import (
    IngestionError,
    Provision,
    _opening_schedule,
    _validate,
    certified_unit,
    detect_headings_only,
    extract_text,
    normalise_corpus_records,
    schedule_spans,
    snapshot_date_of,
    split_judgment,
    split_sections,
    unnumbered_note,
)
from michael.schema import JURISDICTIONS
from michael.sources import FetchedSource, SourceRefused

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
    """A trailing bare number is a page reference only when a neighbouring row
    - the real line before or after it - carries one too. These four lines and
    their real neighbours all come from ``CONTENTS_THEN_BODY`` above.
    """
    from michael.ingest import _is_contents_entry

    lines = CONTENTS_THEN_BODY.splitlines()

    def context(needle: str) -> tuple[str, str, str]:
        index = lines.index(needle)
        previous = lines[index - 1] if index > 0 else ""
        following = lines[index + 1] if index + 1 < len(lines) else ""
        return lines[index], previous, following

    line, previous, following = context("15A Meaning of casual employee 68")
    assert _is_contents_entry(line, previous=previous, following=following)

    line, previous, following = context("61 The National Employment Standards 164")
    assert _is_contents_entry(line, previous=previous, following=following)

    line, previous, following = context("15A Meaning of casual employee")
    assert not _is_contents_entry(line, previous=previous, following=following)

    line, previous, following = context("This Act may be cited as the Fair Work Act 2009.")
    assert not _is_contents_entry(line, previous=previous, following=following)


def test_a_cited_act_year_is_not_a_contents_entry() -> None:
    """The class of bug behind the Privacy Act 1988 (Cth) Part IIIC gap.

    A trailing bare number is not, by itself, evidence of a page reference: a
    cited Act's year ends a heading line exactly the same way a page number
    does (``... Act 2012`` and ``... employee 68`` are the same shape), and no
    digit-only rule - a length check, a plausible-year range - tells them
    apart, because a plausible year and a plausible page number overlap
    completely. What differs is the neighbouring rows: a table of provisions
    paginates every row, so they cluster; an operative heading's neighbours
    are prose or a Division line, and do not.

    Every string and its real previous/next line below is taken verbatim from
    the Privacy Act 1988 (Cth), Compilation No. 104 (in force 2026-06-04),
    downloaded from legislation.gov.au and run through Michael's own
    ``docx_to_text`` - not invented. The one exception is noted inline: two
    headings the ORCHESTRATOR sourced separately could not be located in this
    compilation (probably a different, older one - Commonwealth Acts get
    renumbered as they are amended), so they are tested with the default
    empty context rather than a fabricated neighbour.
    """
    from michael.ingest import _is_contents_entry

    # 26WD, in the table of provisions: both neighbours end in a page number.
    assert _is_contents_entry(
        "26WD Exception—notification under the My Health Records Act 2012 204",
        previous="26WC Deemed holding of information 203",
        following="Division 2—Eligible data breach 205",
    )

    # 26WD, the operative heading: neither neighbour ends in a bare number.
    # Both the em-dash form (as extracted) and a plain-hyphen form must agree.
    for dash in ("—", "-"):
        assert not _is_contents_entry(
            f"26WD Exception{dash}notification under the My Health Records Act 2012",
            previous="Note: See section 21NA.",
            following="If:",
        )

    # 26WC: operative heading, no page number, no cited year either.
    assert not _is_contents_entry(
        "26WC Deemed holding of information",
        previous="For the purposes of this Part, entity includes a person who is a "
        "file number recipient.",
        following="Overseas recipients",
    )

    # 26WL: operative heading; the previous line is a long subsection of prose.
    assert not _is_contents_entry(
        "26WL Entity must notify eligible data breach",
        previous=(
            "(4) If the entity has reasonable grounds to believe that the access, "
            "disclosure or loss that constituted the eligible data breach of the "
            "entity is an eligible data breach of one or more other entities, the "
            "statement referred to in subparagraph (2)(a)(i) may also set out the "
            "identity and contact details of those other entities."
        ),
        following="Scope",
    )

    # 26WK: operative heading, sitting directly under a Subdivision line.
    assert not _is_contents_entry(
        "26WK Statement about eligible data breach",
        previous="Subdivision B—General notification obligations",
        following="Scope",
    )

    # 6A: operative heading, previous line is the tail of a long definition.
    assert not _is_contents_entry(
        "6A Breach of an Australian Privacy Principle",
        previous=(
            "stepchild: without limiting who is a stepchild of an individual, "
            "someone is a stepchild of an individual if he or she would be the "
            "individual’s stepchild except that the individual is not legally "
            "married to the individual’s de facto partner."
        ),
        following=(
            "(1) For the purposes of this Act, an act or practice breaches an "
            "Australian Privacy Principle if, and only if, it is contrary to, or "
            "inconsistent with, that principle."
        ),
    )

    # 13G: operative heading, previous line is plain prose ending in "84." not "84".
    assert not _is_contents_entry(
        "13G Civil penalty provision for serious interference with privacy of an individual",
        previous="An act or practice that is not covered by section 13 is not an "
        "interference with the privacy of an individual.",
        following="Civil penalty provision",
    )

    # Same bug class, real section 34 (an operative heading citing the Freedom
    # of Information Act 1982), found independently of the two below.
    assert not _is_contents_entry(
        "34 Provisions relating to documents exempt under the Freedom of Information Act 1982",
        previous="Division 4—Miscellaneous",
        following=(
            "(1) The Commissioner shall not, in connection with the performance of "
            "the Commissioner’s functions, give to a person information as to "
            "the existence or nonexistence of a document..."
        ),
    )
    assert _is_contents_entry(
        "34 Provisions relating to documents exempt under the Freedom of Information Act 1982 254",
        previous="Division 4—Miscellaneous 254",
        following="35 Direction where refusal or failure to amend exempt document 254",
    )

    # Sourced by the ORCHESTRATOR from the real function in the project venv,
    # not reproducible in this session's copy of the Act (see docstring): no
    # real neighbouring lines available, so the default empty context stands
    # in for "no known contents cluster around this heading".
    assert not _is_contents_entry("80P Disclosure under the Freedom of Information Act 1982")
    assert not _is_contents_entry("7B Acts and practices of organisations 1988")


# The Ticket Scalping Act 2021 (WA), verbatim and complete (14,601 characters
# in the real document, only whitespace-adjacent here) - Compilation, sourced
# from the isaacus/open-australian-legal-corpus record whose text hashes to
# the sha256 already stored for this document in the michael database. Real,
# not excerpted: this is the entire Act, contents, body, and compilation
# tail. It is the fixture for the endnote/table-junk fix because it exercises
# every part of it in one real document - the contents-block boundary bug in
# find_body_start (its last row's neighbour is the bare word "Notes", not
# another paginated row), the required MUST-SURVIVE heading (section 14,
# "Application of Fair Trading Act 2010"), and an in-body numbered note
# ("Notes for this section: 1. ... 2. ...") that must not mint two spurious
# provisions the way the old predicate would have let it.
TICKET_SCALPING_ACT_2021 = """Western Australia
Ticket Scalping Act 2021
Western Australia
Ticket Scalping Act 2021
Contents
Part 1 — Preliminary
1. Short title 2
2. Commencement 2
3. Terms used 2
4. Act binds Crown 4
5. Resale restrictions 4
6. Application of Act 4
Part 2 — Resale, supply or advertising of tickets
7. Ticket scalping 5
8. Invalid resale restrictions 5
9. Supply of tickets not to be made contingent on other purchases 5
10. Prohibited advertisements 5
11. Ticket resale advertising 6
Part 3 — Online purchase of tickets
12. Prohibited conduct in relation to use of ticketing websites 7
Part 4 — Miscellaneous
13. Functions of Commissioner 8
14. Application of Fair Trading Act 2010 8
15. Infringement notices and Criminal Procedure Act 2004 10
16. Regulations 10
17. Review of Act 11
Part 5 — Transitional provision
18. Transitional provision 12
Notes
Compilation table 13
Defined terms
Western Australia
Ticket Scalping Act 2021
An Act to restrict the resale of event tickets and to prohibit the use of software designed to circumvent security measures on ticket selling websites, and for related purposes.

Part 1 — Preliminary

1. Short title
This is the Ticket Scalping Act 2021.

2. Commencement
This Act comes into operation as follows —
(a) Part 1 — on the day on which this Act receives the Royal Assent;
(b) the rest of the Act — on the day after that day.

3. Terms used
In this Act —
ticket scalping means selling a ticket for admission to an event for an amount which exceeds the original ticket price by more than 10%.

4. Act binds Crown
This Act binds the Crown in right of Western Australia and, so far as the legislative power of the Parliament permits, the Crown in all its other capacities.

5. Resale restrictions
(1) For the purposes of this Act, a resale restriction is a term or condition of a ticket for admission to an event that limits the circumstances in which the ticket may be resold.
(2) A term or condition that limits the circumstances in which a ticket may be resold includes a term or condition that provides for the ticket to be cancelled, surrendered or rendered invalid if the ticket is resold or if the ticket is resold in certain circumstances.

6. Application of Act
(1) This Act applies to tickets for admission to events in Western Australia that are subject to a resale restriction.
(2) Subject to subsection (1), this Act extends to conduct, and other acts, matters and things, occurring or existing outside or partly outside Western Australia (whether within or outside Australia).

Part 2 — Resale, supply or advertising of tickets

7. Ticket scalping
A person must not sell a ticket for admission to an event for an amount which exceeds the original ticket price by more than 10%.
Penalty: a fine of $20 000.

8. Invalid resale restrictions
A resale restriction is void to the extent that it provides for the ticket to be cancelled, surrendered or rendered invalid if the ticket is resold for an amount not exceeding 110% of the original ticket price.

9. Supply of tickets not to be made contingent on other purchases
(1) A person (the supplier) must not supply a ticket for admission to an event to any other person (the recipient) under an agreement that makes the liability of the supplier to supply the ticket to the recipient contingent on payment by the recipient to the supplier of an amount in consideration for the provision to the recipient of any other goods or services.
Penalty for this subsection: a fine of $20 000.
(2) Subsection (1) does not apply to the supply of a ticket under —
(a) an agreement that has been authorised by the event organiser for the relevant event; or
(b) any other agreement of a kind prescribed by the regulations.

10. Prohibited advertisements
(1) A ticket resale advertisement must not specify an amount for the sale of the ticket that is more than 110% of the original ticket price.
(2) A ticket resale advertisement must specify —
(a) the original ticket price; and
(b) details of the location from which the ticket holder is authorised to view the event (including, for example, any bay number, row number and seat number for the ticket).

11. Ticket resale advertising
(1) The owner of an advertising publication must ensure that no prohibited advertisement is published in the publication.
Penalty for this subsection: a fine of $20 000.
(2) It is a defence to a charge of an offence under subsection (1) to prove that —
(a) the advertisement was received by the person charged, or by a person acting on that person's behalf, in the ordinary course of carrying on the business or undertaking associated with the advertising publication; and
(b) the agreement relating to the publication of the advertisement between the person charged and the person placing the advertisement was subject to terms or conditions prohibiting the publication of prohibited advertisements; and
(c) the person charged, or a person responsible for managing the advertising publication on that person's behalf, as soon as practicable after becoming aware that the prohibited advertisement had been published in the publication, took reasonable steps to ensure that the advertisement was removed from the publication; and
(d) the person charged took such other steps as were reasonable in the circumstances to ensure that no prohibited advertisement was published in the publication.

Part 3 — Online purchase of tickets

12. Prohibited conduct in relation to use of ticketing websites
(1) In this section —
security measures, in relation to a website, include any measures of a kind prescribed by the regulations for the purposes of this definition.
(2) A person must not use any software to enable or assist the person to circumvent the security measures of a website to purchase tickets in contravention of the published terms of use of the website.
Penalty for this subsection: a fine of $100 000.
(3) For the purposes of subsection (2), terms of use of a website are published if they are published on the website.

Part 4 — Miscellaneous

13. Functions of Commissioner
(1) The functions of the Commissioner include the following —
(a) to promote the operation and effect of this Act;
(b) to conduct educational activities associated with promoting compliance with this Act;
(c) to receive complaints and information concerning potential breaches of this Act and, if the Commissioner considers it warranted, to investigate any matter and to take any action in respect of those complaints or that information considered to be appropriate by the Commissioner;
(d) to publish (in any form) statements identifying and giving warnings about conduct or practices that are in breach of this Act, including by identifying persons who engage or are likely to engage in such conduct or practices;
(e) to perform other functions associated with the operation or enforcement of this Act, or otherwise conferred on the Commissioner under, or for the purposes of, this Act.
(2) The Commissioner must not make or issue a statement under subsection (1)(d) that identifies a specific person unless satisfied that it is in the public interest to do so.

14. Application of Fair Trading Act 2010
(1) The following provisions of the Fair Trading Act 2010 apply, with any modifications that are necessary for the purposes of this Act, as if those provisions were a part of this Act —
(a) sections 60 and 61;
(b) Part 6, other than sections 64 and 65 and Division 4A;
(c) Part 7, other than sections 96, 97, 98, 100 and 108 and Division 4;
(d) Part 8, other than section 116.
(2) For the purposes of subsection (1), the Fair Trading Act 2010 is to be read as if —
(a) a reference to "this Act" or "this or any other Act" were a reference to this Act; and
(b) the words "or another Act", "or any other Act" (other than in section 60(1)) or "or another Act that confers functions on the Commissioner" were deleted.
(3) Subject to subsection (2), any definition contained in the Fair Trading Act 2010 of a term used in the provisions applied by subsection (1) also applies for the purposes of those provisions.
Notes for this section:
1. Subsection (1) incorporates into this Act certain provisions of the Fair Trading Act 2010 that provide for or in relation to the following —
(a) powers of the Commissioner;
(b) investigation and enforcement;
(c) criminal and civil proceedings;
(d) miscellaneous matters.
2. Subsection (2) makes certain modifications to those provisions in their application as part of this Act.

15. Infringement notices and Criminal Procedure Act 2004
(1) If this Act is a prescribed Act for the purposes of the Criminal Procedure Act 2004 Part 2, this section applies in relation to the service of an infringement notice under that Part by an authorised officer in relation to an alleged offence under this Act.
(2) The infringement notice must be served within —
(a) 21 days after the day on which the authorised officer forms the opinion that there is sufficient evidence to support the allegation of the offence; and
(b) 6 months after the day on which the alleged offence is believed to have been committed.
(3) The Criminal Procedure Act 2004 Part 2 is modified to the extent necessary to give effect to this section.

16. Regulations
(1) The Governor may make regulations prescribing matters —
(a) required or permitted by this Act to be prescribed; or
(b) necessary or convenient to be prescribed for giving effect to the purposes of this Act.
(2) The regulations may provide for offences against the regulations and prescribe penalties for those offences not exceeding a fine of $5 000.

17. Review of Act
(1) The Minister must review the operation and effectiveness of this Act, and prepare a report based on the review, as soon as practicable after the 5th anniversary of the day on which this section comes into operation.
(2) The review must address whether sections 7, 9 and 12 have been effective in reducing the practice of ticket scalping.
(3) The Minister must cause the report to be laid before each House of Parliament as soon as practicable after it is prepared, but not later than 12 months after the 5th anniversary.

Part 5 — Transitional provision

18. Transitional provision
This Act does not apply to a ticket purchased from an authorised ticket seller before the day on which Part 2 comes into operation.
Notes
This is a compilation of the Ticket Scalping Act 2021. For provisions that have come into operation see the compilation table.
Compilation table
Short title               Number and year  Assent      Commencement
Ticket Scalping Act 2021  17 of 2021       9 Sep 2021  Pt. 1: 9 Sep 2021 (see s. 2(a));
                                                       Act other than Pt. 1: 10 Sep 2021 (see s. 2(b))

Defined terms
[This is a list of terms defined and the provisions where they are defined. The list is not part of the law.]
Defined term Provision(s)
advertisement 3
advertising publication 3
authorised ticket seller 3
Commissioner 3
event 3
event organiser 3
original ticket price 3
owner 3
prohibited advertisement 3
recipient 9(1)
resale restriction 3, 5(1)
security measures 12(1)
sell 3
supplier 9(1)
supply 3
ticket resale advertisement 3
ticket scalping 3
"""  # noqa: E501 - verbatim quoted statutory/reprint text; do not reflow


def test_the_endnote_fix_recovers_every_real_section_of_a_full_real_act() -> None:
    """The find_body_start boundary bug this fix also needed.

    Before this fix, find_body_start mistook the LAST row of this Act's
    contents block for the start of the body: that row's own neighbours are
    "Part 5 ... (Transitional provision)" above it and the bare word "Notes"
    below it, and neither ends in a page number, so the accepted
    _is_contents_entry check (correctly) does not call it a contents row -
    but nothing else recognised "Notes", "Compilation table N" or "Defined
    terms" as structural either, so the prose lookahead concluded real body
    text started right there, at the tail of the contents block, cutting
    away the entire real Act.
    """
    provisions = {p.section_number: p for p in split_sections(TICKET_SCALPING_ACT_2021)}
    assert [p.section_number for p in split_sections(TICKET_SCALPING_ACT_2021)] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
    ]
    assert provisions["1"].heading == "Short title"
    assert provisions["1"].text.rstrip().endswith("This is the Ticket Scalping Act 2021.")


def test_must_survive_application_of_fair_trading_act_2010() -> None:
    """The owner's required MUST-SURVIVE case, verbatim.

    Section 14's own heading ends in a cited Act's year - the class of
    heading the contents fix (bac667e) exists to keep - and its body itself
    contains an in-line numbered note ("Notes for this section: 1. ... 2.
    ..."), the class of row this endnote fix exists to drop. Both must be
    true of the same real section at once: 14 survives, and the note's "1"
    and "2" mint no provisions of their own.
    """
    provisions = {p.section_number: p for p in split_sections(TICKET_SCALPING_ACT_2021)}
    assert "14" in provisions
    assert provisions["14"].heading == "Application of Fair Trading Act 2010"
    assert (
        "(1) The following provisions of the Fair Trading Act 2010 apply" in provisions["14"].text
    )
    # The in-body note's own "1." and "2." must not have become sections 1
    # and 2 again - those numbers are already used, by the real sections 1
    # and 2 much earlier in the Act.
    assert provisions["1"].heading == "Short title"
    assert provisions["2"].heading == "Commencement"


# Bank of Western Australia Act 1995 (WA), excerpted (not the full 56,810
# characters) but every line real and verbatim, sourced from the same
# corpus record, sha256-verified against the document already in the
# michael database. This is the owner's worked example: section "1" must
# resolve to exactly one provision, "Short title" - not the two schedule
# items that share its number purely by virtue of restarting their own
# local numbering.
BANK_OF_WA_SCHEDULE_COLLISION_EXCERPT = """Western Australia
Bank of Western Australia Act 1995
Contents
Part 1 — Preliminary
1. Short title 1
2. Commencement 1
3. Terms used 1
4. Bank not to be regarded as instrumentality or agent of Crown 5
Part 2 — Privatisation of Bank
43. Bank of Western Australia Act 1990, transitional provisions for 32
44. Other Acts, transitional provisions 32
Schedule 1 — Provisions relating to Bank of Western Australia Act 1990
Division 2 — Transitional provisions
11. Terms used 33
12. Auditor General may disclose information 33
13. Payments under repealed s. 31 up to day of privatisation 33
14. Agreements under s. 33(4a) 34
15. Securities taken as agent of Crown 34
Schedule 2 — Provisions relating to other Acts
Part B — Transitional provisions
1. Provision relating to Industry (Advances) Act 1947 35
2. Provisions relating to Superannuation and Family Benefits Act 1938 35
Notes
Compilation table 37
Uncommenced provisions table 38
Other notes 38
Defined terms
Western Australia
Bank of Western Australia Act 1995
An Act to provide for the full or partial privatisation of Bank of Western Australia Ltd, to amend the Bank of Western Australia Act 1990 and certain other Acts, and for related purposes.

Part 1 — Preliminary

1. Short title
This Act may be cited as the Bank of Western Australia Act 1995.

2. Commencement
(1) The long title, this Part, Part 2 (except section 11) and section 43(1) and (2) come into operation on the day on which this Act receives Royal Assent.

3. Terms used
In this Act, unless the contrary intention appears —
Bank means the public company registered under the Corporations Act 2001 (Commonwealth).

4. Bank not to be regarded as instrumentality or agent of Crown
The Bank is not, and does not represent, the Crown and is not an instrumentality or agency of the Crown.

44. Other Acts, transitional provisions
(2) Part B of Schedule 2 has effect to make transitional provisions.
Schedule 1 — Provisions relating to Bank of Western Australia Act 1990

Division 2 — Transitional provisions

11. Terms used
In this Schedule, unless the contrary intention appears — the 1990 Act means the Bank of Western Australia Act 1990.

12. Auditor General may disclose information
The Auditor General may disclose to the Treasurer information obtained under the 1990 Act.

Schedule 2 — Provisions relating to other Acts
[s. 44]
[Part A omitted under the Reprints Act 1984 s. 7(4)(e).]

Part B — Transitional provisions

1. Provision relating to Industry (Advances) Act 1947
(1) Any security for the repayment of advances taken under the Industry (Advances) Act 1947 and vested in the Bank immediately before the commencement of section 44 is vested in the Treasurer on that commencement.

2. Provisions relating to Superannuation and Family Benefits Act 1938
(1) Despite the amendment made by item 13 of Part A of this Schedule, the Bank is to be deemed to be a department under section 3 of the Superannuation and Family Benefits Act 1938.

Notes
Compilation table
Short title  Number and year  Assent  Commencement
Bank of Western Australia Act 1995  4 of 1995  1 Mar 1995  1 Jul 1995 (see s. 2 and Gazette 30 Jun 1995 p. 2837)

Other notes
1 The provisions in this Act amending the Bank of Western Australia Act 1990 and other Acts have been omitted under the Reprints Act 1984 s. 7(4)(e).
2 The Bank of Western Australia Act 1990 (originally enacted as the R&I Bank Act 1990), the short title of which was changed to the R & I Holdings Act 1990 by this Act Sch. 1 cl. 2, was repealed by the Financial Legislation Amendment Act 1996.
"""  # noqa: E501 - verbatim quoted statutory/reprint text; do not reflow


def test_schedule_local_numbering_does_not_duplicate_the_real_section() -> None:
    """The owner's worked example, verbatim: section 1 must resolve once.

    Real numbers: the real "1. Short title" is the Act's own section 1. Two
    more things are also headed "1." further into the document - a Schedule
    1 clause and a Schedule 2 Part B clause - because each Schedule restarts
    its own local numbering at 1, exactly the way Schedule 1's "11. Terms
    used" restarts at 11 rather than colliding with 1 either. Both must be
    excluded: keeping either would give this document's real section 1 two
    (or three) competing pinpoints for the same citation, which is the
    traceability failure this fix exists to prevent.
    """
    provisions = split_sections(BANK_OF_WA_SCHEDULE_COLLISION_EXCERPT)
    ones = [p for p in provisions if p.section_number == "1"]
    assert len(ones) == 1
    assert ones[0].heading == "Short title"
    assert ones[0].text.rstrip().endswith("Bank of Western Australia Act 1995.")

    # Schedule 1's own "11. Terms used" restarts at 11, well below the real
    # body's last accepted number (44, from "Other Acts, transitional
    # provisions") by the time the Schedule is reached - so it is excluded,
    # exactly as the real Act's genuine main-body section 11 (referenced
    # directly in section 2: "Part 2 (except section 11)") is not shadowed
    # by a same-numbered Schedule clause about something else entirely.
    assert not any(p.section_number == "11" and p.heading == "Terms used" for p in provisions)


def test_the_endnotes_own_amendment_history_is_not_a_provision() -> None:
    """The "Other notes" tail - the class of row Phase A first found."""
    provisions = split_sections(BANK_OF_WA_SCHEDULE_COLLISION_EXCERPT)
    assert not any("have been omitted under the Reprints Act 1984" in p.text for p in provisions)


# Small Business Development Corporation Act 1983 (WA), excerpted, real and
# verbatim - a genuine section numbered with a double-letter suffix inserted
# AFTER a single-letter one, "11" then "11AA" then "11A" in that real
# document order. Comparing suffixes alphabetically would put "11AA" ahead
# of "11A" and reject the real "11A" as if it went backwards; this fixture
# is the regression test for that.
DOUBLE_LETTER_SUFFIX_OUT_OF_LEXICAL_ORDER = """Western Australia
Small Business Development Corporation Act 1983
Contents
11. Functions of Corporation 10
11AA. Financial assistance, grants and operational funding in relation to small businesses 12
11A. Delegation by Corporation 13
Small Business Development Corporation Act 1983
An Act to establish the Small Business Development Corporation.

11. Functions of Corporation
(1) The functions of the Corporation are as set out in this Act.

11AA. Financial assistance, grants and operational funding in relation to small businesses
(1) The Corporation may provide financial assistance to small businesses.
[Section 11AA inserted: No. 4 of 2022 s. 6.]

11A. Delegation by Corporation
(1) The Corporation may, by instrument in writing, delegate the performance of any of its functions, except this power of delegation.
(2) A delegation under subsection (1) may be made to the Commissioner.
"""  # noqa: E501 - verbatim quoted statutory/reprint text; do not reflow


def test_a_later_double_letter_insertion_does_not_reject_an_earlier_single_letter_one() -> None:
    """Real amendment history: "11AA" was inserted after "11A" already existed.

    Only the base number gates monotonicity for exactly this reason - "AA"
    sorts after "A" alphabetically, but that is amendment history, not the
    document's real position order. Comparing suffixes would have rejected
    the genuine "11A. Delegation by Corporation" as if it came before "11AA"
    in error, when it is simply the next real section after it.
    """
    provisions = {
        p.section_number: p for p in split_sections(DOUBLE_LETTER_SUFFIX_OUT_OF_LEXICAL_ORDER)
    }
    assert set(provisions) == {"11", "11AA", "11A"}
    assert "Delegation by Corporation" in provisions["11A"].heading


# Fair Work Act 2009 (Cth), excerpted, real and verbatim - the commencement
# table every Commonwealth Act carries right after "2 Commencement", whose
# own table-item rows ("3 Sections 41 to 572") and bare dates ("26 May
# 2009") are heading-shaped and increase in number, so monotonicity alone
# would accept them and then reject the real sections 3, 4 and 5 that
# genuinely follow, because their numbers no longer exceed the table's.
FAIR_WORK_ACT_COMMENCEMENT_TABLE_EXCERPT = """1 Short title
This Act may be cited as the Fair Work Act 2009.
2 Commencement
(1) Each provision of this Act specified in column 1 of the table commences, or is taken to have commenced, in accordance with column 2 of the table. Any other statement in column 2 has effect according to its terms.

Commencement information
Column 1
Column 2
Column 3
Provision(s)
Commencement
Date/Details
1. Sections 1 and 2 and anything in this Act not elsewhere covered by this table
The day on which this Act receives the Royal Assent.
7 April 2009
2. Sections 3 to 40
A single day to be fixed by Proclamation.
26 May 2009
(see F2009L01818)
3. Sections 41 to 572
A day or days to be fixed by Proclamation.
A Proclamation must not specify a day that occurs before the day on which the Fair Work (Transitional Provisions and Consequential Amendments) Act 2009 receives the Royal Assent.
4. Sections 573 to 718
At the same time as the provision(s) covered by table item 2.
26 May 2009
5. Sections 719 to 800
A day or days to be fixed by Proclamation.
6. Schedule 1
At the same time as the provision(s) covered by table item 2.
26 May 2009
Note: This table relates only to the provisions of this Act as originally passed by both Houses of the Parliament and assented to.

3 Object of this Act
The object of this Act is to provide a balanced framework for cooperative and productive workplace relations.

4 Guide to this Act
Overview of this Act
(1) This Act is about workplace relations.

5 Terms and conditions of employment (Chapter 2)
(1) Chapter 2 provides for terms and conditions of employment of national system employees.
(2) Part 21 has the core provisions for the Chapter.
"""  # noqa: E501 - verbatim quoted statutory/reprint text; do not reflow


def test_a_commencement_table_does_not_swallow_the_real_sections_after_it() -> None:
    """The class of false accept monotonicity alone cannot catch.

    The table's own rows ("3 Sections 41 to 572", "26 May 2009") are
    heading-shaped and their numbers increase, so a monotonicity check
    with nothing else would accept them and set the sequence floor to 26 -
    above the real sections 3, 4 and 5 that immediately follow, rejecting
    every one of them. This is why the fix also asks whether a nearby line -
    not just the immediate neighbour - carries a bare number too: the
    table's cells wrap prose between the dated rows, but a real section
    does not sit inside that pattern.
    """
    provisions = {
        p.section_number: p for p in split_sections(FAIR_WORK_ACT_COMMENCEMENT_TABLE_EXCERPT)
    }
    assert set(provisions) >= {"1", "2", "3", "4", "5"}
    assert provisions["1"].heading == "Short title"
    assert provisions["2"].heading == "Commencement"
    assert provisions["3"].heading == "Object of this Act"
    assert provisions["4"].heading == "Guide to this Act"
    assert provisions["5"].heading == "Terms and conditions of employment (Chapter 2)"
    # None of the table's own rows became provisions.
    assert not any("Sections 41 to 572" == p.heading for p in provisions.values())
    assert not any(p.heading == "May 2009" for p in provisions.values())


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


# --- W1-2: a headings-only page must not be silently accepted -------------

#: The exact scenario from the QA campaign's Bug W1-2: a "guide" page whose
#: sections read as real headings with real-looking bodies (each clears
#: MIN_PROVISION_CHARS=40), but none carries any operative subsection
#: structure - one line summarising the Part, one defining a term in a single
#: sentence, one that is nothing but a pointer to "the compiled version".
REALISTIC_HEADINGS_ONLY = """Part IIIC—Notification of eligible data breaches

26WA Guide to this Part
This Part sets out a scheme for notification of eligible data breaches under this Act.

26WB Entity
For the purposes of this Part, entity includes a person who is a file number recipient.

26WC Deemed holding of information
See the compiled version of this Act for the full text of this provision.
"""


def test_the_realistic_headings_only_fixture_is_detected() -> None:
    provisions = split_sections(REALISTIC_HEADINGS_ONLY)
    assert len(provisions) == 3, "fixture should still split into 3 real-looking provisions"
    assert all("(1)" not in p.text and "(2)" not in p.text for p in provisions)
    reason = detect_headings_only(provisions)
    assert reason is not None, "a headings-only page with uniform short stubs must be flagged"


def test_ingest_document_refuses_a_headings_only_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_database(monkeypatch)
    with pytest.raises(IngestionError, match="headings-only"):
        ingest.ingest_document(
            jurisdiction="commonwealth",
            title="Privacy Act 1988",
            citation="Privacy Act 1988 (Cth) - fixture",
            source_url="https://www.legislation.gov.au/fixture-headings-only",
            snapshot_date=date(2026, 1, 1),
            sha256="b" * 64,
            doc_type="act",
            text=REALISTIC_HEADINGS_ONLY,
        )


def test_a_short_real_act_with_no_subsection_markers_is_not_flagged() -> None:
    """Some genuine provisions have no (1)/(2) at all - a short title, a single-
    sentence definition, a one-clause validation Act. The rule must judge the
    document's structure, not punish brevity: real sections still vary widely
    in length even when none of them uses subsection numbering, because a
    "Short title" clause sits next to a real substantive one. A synthetic
    stub set does not - see REALISTIC_HEADINGS_ONLY above."""
    real_short_act = """1. Short title
This is the Curriculum Council (Fees and Charges) Act 2006.

2. Commencement
This Act comes into operation on the day on which it receives the Royal Assent.

3. Definition
In this Act —
Curriculum Council means the Curriculum Council established under the
Curriculum Council Act 1997 section 5.

4. Validation of fees and charges
Any fee or charge imposed by and paid to the Curriculum Council before the
coming into operation of this Act is taken to be, and to always have been, as
validly and lawfully imposed and paid as it would have been if it had been
imposed and paid under regulations made under the Curriculum Council Act 1997.
"""
    provisions = split_sections(real_short_act)
    assert len(provisions) == 4
    assert detect_headings_only(provisions) is None, "a real short Act must not be flagged"


def test_a_document_with_subsection_markers_anywhere_is_never_flagged() -> None:
    """One provision with real subsection structure - a run of two or more
    markers in the SAME provision - is enough to clear the whole document."""
    provisions = [
        Provision(section_number="1", heading="A", text="1 A\nShort.", char_start=0, char_end=10),
        Provision(section_number="2", heading="B", text="2 B\nShort.", char_start=10, char_end=20),
        Provision(
            section_number="3",
            heading="C",
            text="3 C\n(1) Has real subsection structure. (2) And another.",
            char_start=20,
            char_end=30,
        ),
    ]
    assert detect_headings_only(provisions) is None


def test_fewer_than_three_provisions_is_never_flagged() -> None:
    """A document-wide proportion needs a document. Two provisions is not
    enough of a sample to call "uniform" a defect rather than coincidence."""
    provisions = [
        Provision(
            section_number="1", heading="A", text="1 A\n" + "x" * 40, char_start=0, char_end=10
        ),
        Provision(
            section_number="2", heading="B", text="2 B\n" + "x" * 41, char_start=10, char_end=20
        ),
    ]
    assert detect_headings_only(provisions) is None


# --- File log audit trail -------------------------------------------------
#
# The database `ingestion_log` row lives inside a transaction that a purge can
# cascade away (`TRUNCATE documents CASCADE`); `sources/ingestion.log.jsonl` is
# meant to survive that. Before this fix, only `ingest_url` reached it -
# `ingest_file` (and, through the same `_write`, `seed_from_corpus`) wrote the
# database row alone. These tests stub Postgres with a fake connection so they
# prove the file log without needing a running database.


class _FakeCursor:
    """Enough of a psycopg cursor for `_write` to run against, nothing more."""

    def __init__(self, conn: _FakeConnection | None = None) -> None:
        self._last_sql = ""
        self._conn = conn

    def execute(self, query: str, params: object = None) -> None:
        self._last_sql = query
        if self._conn is not None and "INSERT INTO documents" in query:
            # Index 2 is the citation, in the order _write passes them:
            # jurisdiction, title, citation, source_url, ... Recording it is
            # what lets a test ask whether a document row survived.
            self._conn.written.append(str(params[2]) if isinstance(params, tuple) else "?")

    def fetchone(self) -> dict[str, int] | None:
        if "INSERT INTO documents" in self._last_sql:
            return {"id": 1}
        return None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.written: list[str] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        pass

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """A SAVEPOINT, modelled rather than stubbed.

        psycopg issues `conn.transaction()` inside an open transaction as a
        SAVEPOINT: what the block wrote is undone if the block raises. A fake
        that merely yielded would let the savepoint test pass whether or not
        `seed_from_corpus` rolls anything back, which is a test that cannot
        fail on the thing it is for.
        """
        mark = len(self.written)
        try:
            yield
        except BaseException:
            del self.written[mark:]
            raise

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


@contextmanager
def _fake_writable(*, connect_timeout: int = 10) -> Iterator[_FakeConnection]:
    yield _FakeConnection()


def _stub_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """No socket opened: `_write` and `refresh_corpus_stats` run against a fake."""
    monkeypatch.setattr(ingest, "writable", _fake_writable)
    monkeypatch.setattr(schema, "writable", _fake_writable)
    monkeypatch.setattr(ingest, "embed", lambda texts, **kw: [[0.0] * 8 for _ in texts])


def _file_log_lines() -> list[dict[str, object]]:
    path = settings().ingestion_log
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_file_ingest_writes_the_file_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_database(monkeypatch)
    source_file = tmp_path / "act.txt"
    source_file.write_text(SAMPLE, encoding="utf-8")

    ingest.ingest_file(
        path=source_file,
        source_url="https://www.legislation.gov.au/fixture-file",
        jurisdiction="commonwealth",
        title="Fixture Act",
        citation="Fixture Employment Standards Act 2000 (Cth) - file",
        doc_type="act",
    )

    lines = _file_log_lines()
    assert any(
        line["outcome"] == "allowed"
        and line["url"] == "https://www.legislation.gov.au/fixture-file"
        for line in lines
    ), f"file ingest never reached the file log: {lines}"


def test_a_url_ingest_writes_the_file_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_database(monkeypatch)
    fake_source = FetchedSource(
        url="https://www.legislation.gov.au/fixture-url",
        host="www.legislation.gov.au",
        sha256="a" * 64,
        content_type="text/plain",
        fetched_at=datetime.now(UTC),
        path=tmp_path / "fixture-url.bin",
        body=SAMPLE.encode("utf-8"),
    )
    monkeypatch.setattr(ingest, "fetch", lambda url, **kw: fake_source)

    ingest.ingest_url(
        url="https://www.legislation.gov.au/fixture-url",
        jurisdiction="commonwealth",
        title="Fixture Act",
        citation="Fixture Employment Standards Act 2000 (Cth) - url",
        doc_type="act",
    )

    lines = _file_log_lines()
    assert any(
        line["outcome"] == "allowed" and line["url"] == "https://www.legislation.gov.au/fixture-url"
        for line in lines
    ), f"URL ingest never reached the file log: {lines}"


def test_a_refused_file_ingest_still_writes_the_file_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_database(monkeypatch)
    source_file = tmp_path / "act.txt"
    source_file.write_text(SAMPLE, encoding="utf-8")

    with pytest.raises(SourceRefused):
        ingest.ingest_file(
            path=source_file,
            source_url="https://example.com/not-allowlisted",
            jurisdiction="commonwealth",
            title="Fixture Act",
            citation="Fixture Employment Standards Act 2000 (Cth) - refused",
            doc_type="act",
        )

    lines = _file_log_lines()
    assert any(
        line["outcome"] == "refused" and line["url"] == "https://example.com/not-allowlisted"
        for line in lines
    ), f"a refused file ingest never reached the file log: {lines}"


# A definition line inside a Schedule, verbatim from Fair Work Act 2009 (Cth)
# compilation 73 volume 04. It opens with the words "Schedule 2" and is not a
# Schedule heading: matching it labelled the real s 47A as "Sch 2 cl 47A", a
# citation to a provision that does not exist.
SCHEDULE_DEFINITION_NOT_A_HEADING = """Schedule 1—Application, saving and transitional provisions

1 Definitions
In this Schedule:
Schedule 2 commencement day means the day on which Schedule 2 to the amending Act commences.
Schedule 1 commencement day means the day on which Schedule 1 to the amending Act commences.

47A Casual employees of small business employers
(1) This section applies to an employee of a small business employer.
(2) The employee may give the employer written notification.

Schedule 2—Amendments made by the Fair Work Amendment Act 2012

3 Transitional provision
(1) This item applies to a transfer of business.
"""


def test_a_definition_beginning_schedule_2_is_not_a_schedule_heading() -> None:
    """Only a heading separator makes "Schedule N" a heading, not a sentence."""
    headings = schedule_spans(SCHEDULE_DEFINITION_NOT_A_HEADING)
    assert [number for _, number in headings] == ["1", "2"]


def test_a_section_after_a_schedule_definition_keeps_its_own_number() -> None:
    """s 47A sits in Schedule 1, so it is Sch 1 cl 47A - never Sch 2 cl 47A."""
    provisions = {p.section_number: p for p in split_sections(SCHEDULE_DEFINITION_NOT_A_HEADING)}
    assert "Sch 2 cl 47A" not in provisions
    assert "Sch 1 cl 47A" in provisions
    assert provisions["Sch 1 cl 47A"].heading == "Casual employees of small business employers"


# Fair Work Act 2009 (Cth) volume 04, excerpted, real and verbatim -
# sourced from sources/Fair Work Act 2009/C2026C00355VOL04.docx. The real
# defect: this volume's own table of provisions runs long enough that
# find_body_start correctly cuts everything before the real body, but the
# real "Schedule 1" heading sits 220 characters INSIDE that cut - so
# schedule_spans, which only ever sees the text after the cut, found
# Schedules 2-5 and never 1. Every Schedule 1 clause fell through with a
# plain numeric id, colliding with the Act's own section of the same
# number: real s 47 ("When a modern award applies to an employer...", in
# volume 01) and Sch 1 cl 47 ("Transitioning casual employees", in this
# volume) were BOTH stored as section_number "47".
#
# Two Schedule 1 headings appear below, both real, both verbatim: the
# contents-block row (trailing page number "1", the shape that must NOT
# seed the opener) and the real body heading further down (no trailing
# number, preceded by a blank line, followed by "Note: See section
# 795A." - the shape that must).
FAIR_WORK_VOLUME_04_SCHEDULE_1_OPENING = """Contents
Schedule 1—Application, saving and transitional provisions relating to amendments of this Act 1
Part 1—Amendments made by the Fair Work Amendment (Textile, Clothing and Footwear Industry) Act 2012 1
1 Definitions 1
2 Section 789BB of amended Act applies to contracts entered into after commencement 1
3 Effect on TCF contract outworker’s entitlements 2
4 Fair work instruments etc. made before commencement 2
5 Application of Division 3 of Part 64A of amended Act 3
6 Application of subsection 203(2A) of amended Act 3
7 Regulations dealing with various matters 3
Part 2—Amendments made by the Superannuation Legislation Amendment (Further MySuper and Transparency Measures) Act 2012 5
8 Definitions 5
9 Application of sections 149A and 155A of amended Act 5
10 FWC to vary certain modern awards 5
11 FWC to update text of certain modern awards 6
12 Application of paragraph 194(h) of amended Act 6

Schedule 1—Application, saving and transitional provisions relating to amendments of this Act
Note: See section 795A.
Part 1—Amendments made by the Fair Work Amendment (Textile, Clothing and Footwear Industry) Act 2012

1 Definitions
In this Part:
amended Act means this Act as amended by the amending Act.
amending Act means the Fair Work Amendment (Textile, Clothing and Footwear Industry) Act 2012.
commencement means the commencement of this Part.

47 Transitioning casual employees
(1) This clause applies if, before the commencement, a person was a regular casual employee.
"""  # noqa: E501 - verbatim quoted statutory/reprint text; do not reflow


def test_a_volume_that_opens_mid_schedule_labels_its_clauses_accordingly() -> None:
    """The owner's measured defect, reproduced from the real document.

    section_number "47" must not exist here at all - both real occurrences
    (the Schedule's own clause 1's near-namesake, and clause 47) are inside
    Schedule 1, because this whole excerpt's operative body opens inside it.
    A lookup for "Fair Work Act 2009 (Cth) s 47" against the real corpus
    would otherwise silently resolve to this Schedule clause instead of the
    real section 47 in volume 01, with no way for the reader to tell.
    """
    provisions = {
        p.section_number: p for p in split_sections(FAIR_WORK_VOLUME_04_SCHEDULE_1_OPENING)
    }
    assert "47" not in provisions
    assert "Sch 1 cl 47" in provisions
    assert provisions["Sch 1 cl 47"].heading == "Transitioning casual employees"
    assert "Sch 1 cl 1" in provisions
    assert provisions["Sch 1 cl 1"].heading == "Definitions"


def test_the_schedule_1_seed_ignores_the_contents_rows_own_heading() -> None:
    """Both Schedule 1 headings in the fixture carry a dash; only one seeds.

    SCHEDULE_HEADING alone cannot tell "Schedule 1—Application, saving and
    transitional provisions relating to amendments of this Act 1" (a
    contents row, trailing page number "1") from the real heading with no
    trailing number - both carry the dash the pattern requires. This is
    exactly the reasoning _is_contents_entry already applies to section
    headings, reused here rather than re-derived.
    """
    body_start = FAIR_WORK_VOLUME_04_SCHEDULE_1_OPENING.index("\n\nSchedule 1—") + 2
    assert _opening_schedule(FAIR_WORK_VOLUME_04_SCHEDULE_1_OPENING, body_start) == "1"


# Noise Abatement (Noise Labelling of Equipment) Regulations (No. 2) 1985
# (WA), real and verbatim, in full - only 9 section-like lines precede its
# real body, one short of CONTENTS_MIN_ENTRIES, so split_sections never cuts
# this document and _opening_schedule is never reached through it today.
# Tested directly anyway: found during a sweep of the same 200-document
# corpus that caught the Bank of Western Australia regression above, and it
# is exactly the same shape of risk - a document whose Schedules are listed
# bare, three in a row, each with its title wrapped onto its own line
# ("Schedule 2\nImplementation dates\nSchedule 3\n..."), which without
# skipping every one of them made the lookahead see real prose one line too
# early. A larger contents block - a future amendment, a different
# compilation - would reach this same path for real.
NOISE_ABATEMENT_REGULATIONS_1985 = """Western Australia
Environmental Protection Act 1986 2
Noise Abatement (Noise Labelling of Equipment) Regulations (No. 2) 1985
Western Australia
Noise Abatement (Noise Labelling of Equipment) Regulations (No. 2) 1985
Contents
1. Citation 1
2. Interpretation 1
3. Equipment to be labelled 1
4. Label to be correct 2
5. Equipment not to be altered 2
6. Inspection of equipment 2
Schedule 1
Equipment
Schedule 2
Implementation dates
Schedule 3
Acoustic output descriptors and labels
1. Mobile Air Compressor — 5
2. Pavement Breaker — 6
3. Air‑conditioner — 7
Notes
Compilation table 8
Defined terms
Western Australia
Environmental Protection Act 1986 2
Noise Abatement (Noise Labelling of Equipment) Regulations (No. 2) 1985

1. Citation
These regulations may be cited as the Noise Abatement (Noise Labelling of Equipment) Regulations (No. 2) 1985 1.

2. Interpretation
In these regulations unless the contrary intention appears —
acoustic output descriptor means the quantity obtained when the test procedure specified in paragraph (b) of the appropriate item in Schedule 3 is used.
"""  # noqa: E501 - verbatim quoted statutory/reprint text; do not reflow


def test_a_run_of_bare_schedule_headings_each_with_a_wrapped_title_is_not_a_seed() -> None:
    """Three Schedules listed bare, back to back, each title on its own line.

    Skipping only the title of the Schedule heading being tested is not
    enough: Schedule 2's own lookahead runs straight into Schedule 3's bare
    heading and then ITS wrapped title, "Acoustic output descriptors and
    labels", which is not itself a Schedule heading and was read as the real
    prose that makes a heading genuine. None of the three is real - this
    whole excerpt is still inside the contents block - so none should seed.
    """
    body_start = NOISE_ABATEMENT_REGULATIONS_1985.index("\n\n1. Citation") + 2
    assert _opening_schedule(NOISE_ABATEMENT_REGULATIONS_1985, body_start) is None


TICKET_SCALPING_HAS_NO_SCHEDULE_HEADING = "Schedule" not in TICKET_SCALPING_ACT_2021


def test_a_document_with_no_schedule_before_the_body_keeps_plain_ids() -> None:
    """The regression this fix must not cause: no Schedule, no relabelling.

    Defaulting to a Schedule context whenever the contents block simply
    happens to be long enough would relabel every ordinary section of a
    single-volume Act as a clause - the same shape of error as the
    monotonic floor, wrong in the direction that corrupts citations. The
    Ticket Scalping Act 2021 (WA) fixture above has no Schedule at all: its
    18 real sections must all keep plain numeric ids.
    """
    assert TICKET_SCALPING_HAS_NO_SCHEDULE_HEADING
    body_start_of_ticket_scalping = TICKET_SCALPING_ACT_2021.index("\n\nPart 1")
    assert _opening_schedule(TICKET_SCALPING_ACT_2021, body_start_of_ticket_scalping) is None

    provisions = split_sections(TICKET_SCALPING_ACT_2021)
    assert len(provisions) == 18
    assert all(not p.section_number.startswith("Sch") for p in provisions)
    assert [p.section_number for p in provisions] == [str(n) for n in range(1, 19)]


def test_a_note_block_after_a_section_is_not_a_second_section_one() -> None:
    """Reduced from Offshore Minerals Act 2003 (WA) around char 47,564.

    The Act carries 31 "Note:" blocks. Each numbers its items from 1, and each
    produced a provision citing s 1 - 32 provisions under one pinpoint - so a
    search for section 1 could return "For 'petroleum' see section 5" cited as
    the Short title section.
    """
    text = (
        "1. Short title\n"
        "This Act may be cited as the Offshore Minerals Act 2003.\n"
        "\n"
        "35. Act does not apply to exploration for or recovery of petroleum\n"
        "This Act does not apply to the exploration for or recovery of petroleum.\n"
        "Note:\n"
        '1. For "petroleum" see section 5.\n'
        "2. Offshore petroleum exploration and mining are regulated by the "
        "Petroleum (Submerged Lands) Act 1967 of the Commonwealth.\n"
        "\n"
        "36. Section number not used\n"
        "See note 2 to section 3(1).\n"
    )
    numbers = [p.section_number for p in ingest.split_sections(text)]
    assert numbers.count("1") == 1, numbers
    assert "35" in numbers and "36" in numbers


def test_a_bare_notes_line_does_not_suppress_the_document() -> None:
    """Reduced from Chattel Securities Regulations 1988 (WA).

    Its contents list ends with a bare "Notes" line immediately before the
    body. Treating that as a note opener suppressed every provision in the
    document - all eight of them. The colon is what separates the two, so
    this is the case that keeps the pattern honest.
    """
    text = (
        "Notes\n"
        "Compilation table 9\n"
        "\n"
        "1. Citation\n"
        "These regulations may be cited as the Chattel Securities "
        "Regulations 1988.\n"
        "\n"
        "2. Commencement\n"
        "These regulations come into operation on the day on which the Act "
        "comes into operation.\n"
    )
    numbers = [p.section_number for p in ingest.split_sections(text)]
    assert numbers == ["1", "2"], numbers


# --- Round 2: W1-S1 through W1-S5 -----------------------------------------


def test_a_leading_utf8_bom_does_not_delete_the_first_section() -> None:
    """A BOM (EF BB BF) at byte 0, decoded naively, sits in front of the
    document's first character as U+FEFF. SECTION_RE's "^" anchor does not
    match through it, so the first heading fails to match at all and
    find_body_start treats the SECOND heading as if it were the first,
    cutting the real section 1 - heading and all - away as front matter.
    Every Windows-authored source can carry a BOM this way. (W1-S1.)
    """
    body = b"\xef\xbb\xbf" + SAMPLE.encode("utf-8")
    text = extract_text(body)
    numbers = [p.section_number for p in split_sections(text)]
    assert numbers == ["1", "15A", "23AB"], numbers


def test_a_heading_longer_than_the_old_150_char_cap_is_still_split() -> None:
    """A heading whose own text runs past 150 characters used to fail
    SECTION_RE outright - the pattern required the WHOLE line to match, so a
    longer heading was not truncated, it simply never matched, and the
    section it belonged to was silently absorbed into the PREVIOUS
    provision's body. (W1-S2.)
    """
    long_heading = "A" + "b" * 160  # 161 chars, past the old 151-char cap
    text = (
        "1 Short title\n"
        "This Act may be cited as the Long Heading Act 2000.\n"
        f"2 {long_heading}\n"
        "This section has a very long heading indeed.\n"
    )
    provisions = {p.section_number: p for p in split_sections(text)}
    assert "2" in provisions, "the long heading was absorbed into the previous section"
    assert provisions["2"].heading == long_heading
    assert "Long Heading Act 2000" not in provisions["2"].text


def test_a_single_decorative_marker_does_not_defeat_headings_only_detection() -> None:
    """One "(1)" anywhere used to satisfy the marker test and disable the
    detector for the WHOLE document - exactly what a footnote marker or a
    list label looks like, not evidence of real subsection structure.
    (W1-S5.)
    """
    text = (
        "26WA Guide to this Part\n"
        "This Part sets out a scheme for notification of eligible data "
        "breaches under this Act. (1)\n"
        "\n"
        "26WB Entity\n"
        "For the purposes of this Part, entity includes a person who is a "
        "file number recipient.\n"
        "\n"
        "26WC Deemed holding of information\n"
        "See the compiled version of this Act for the full text of this "
        "provision.\n"
    )
    provisions = split_sections(text)
    assert len(provisions) == 3
    markers = sum(1 for p in provisions if "(1)" in p.text)
    assert markers == 1, "fixture must carry exactly one marker"
    reason = detect_headings_only(provisions)
    assert reason is not None, "a single decorative marker must not exempt a headings-only page"


def test_a_single_long_stub_does_not_defeat_headings_only_detection() -> None:
    """One disproportionately long stub used to push max/min past the
    uniformity ratio and pass the whole document - the uniformity check
    needs MORE THAN ONE provision to break the pattern, not just one.
    (W1-S4.)
    """
    text = (
        "1 Guide to Part A\n"
        "This Part sets out preliminary matters relevant to the scheme in general terms.\n"
        "\n"
        "2 Guide to Part B\n"
        "This Part sets out notification matters relevant to the scheme in general terms.\n"
        "\n"
        "3 Guide to Part C\n"
        "This Part sets out enforcement matters relevant to the scheme in general terms.\n"
        "\n"
        "4 Guide to Part D\n"
        "This Part sets out review matters relevant to the scheme in general terms overall.\n"
        "\n"
        "5 Guide to Part E\n"
        "This Part sets out, at considerable and repetitive length so as to be "
        "disproportionately long compared with every other guide provision in this "
        "document, a general summary of the miscellaneous matters that the scheme "
        "addresses without actually stating any operative rule at all, merely "
        "restating in different words that this Part exists and that its heading "
        "describes its general subject matter in the broadest possible terms.\n"
    )
    provisions = split_sections(text)
    assert len(provisions) == 5
    assert all("(1)" not in p.text for p in provisions)
    lengths = [len(p.text) for p in provisions]
    assert max(lengths) / min(lengths) >= ingest.HEADINGS_ONLY_MAX_LENGTH_RATIO, (
        "fixture must clear the old, single-pair ratio on its own"
    )
    reason = detect_headings_only(provisions)
    assert reason is not None, "a single padded-out stub must not exempt a headings-only page"


def test_a_genuine_three_provision_act_is_not_flagged_by_the_tightened_length_check() -> None:
    """The false positive the W1-S4 fix would have caused without the size gate.

    Corporations (Taxing) Act 1990 (WA), verbatim and complete, is real law:
    a short title, a commencement clause, and ONE substantive section - which
    is exactly the shape of the vast majority of three-provision Acts, and
    has exactly one long provision by that shape alone. Measured against the
    full 205-document production corpus (read-only), applying the "two long
    provisions" tightening from W1-S4 at the 3-provision floor flagged this
    real Act and one other (a Town Planning by-law) as false positives - so
    the tightened check only applies from
    HEADINGS_ONLY_PROPORTIONAL_MIN_PROVISIONS provisions up; below that, the
    original max/min ratio - which this Act clears at 2.72 - still governs.
    """
    text = (
        "1. Short title\n"
        "This Act may be cited as the Corporations (Taxing) Act 1990.\n"
        "\n"
        "2. Commencement\n"
        "This Act shall come into operation on the day on which it receives the Royal Assent.\n"
        "\n"
        "3. Imposition of tax\n"
        "To the extent that any fee, contribution, or levy referred to in Part 7 of the "
        "Corporations (Western Australia) Act 1990 may be a tax, this Act imposes the fee, "
        "contribution, or levy.\n"
    )
    provisions = split_sections(text)
    assert len(provisions) == 3
    assert all("(1)" not in p.text for p in provisions)
    assert detect_headings_only(provisions) is None, "a real short Act must not be flagged"


def test_seed_from_corpus_does_not_lose_other_documents_when_one_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single headings-only refusal, mid-stream, must not roll back the batch.

    Before this fix, every document in a `seed_from_corpus` run shared one
    transaction, and an `IngestionError` (a headings-only refusal, or no text
    to ingest) was left to propagate out of the loop: the
    `with writable() as conn:` block then exited on the exception without
    ever reaching `conn.commit()`, discarding every document already ingested
    earlier in that same run - however many there were. (W1-S3.)
    """
    _stub_database(monkeypatch)

    good_act = "1. Short title\nThis Act may be cited as the {name}.\n"

    class _FakeStream:
        def __iter__(self) -> Iterator[dict[str, object]]:
            return iter(
                [
                    {
                        "jurisdiction": "wa",
                        "type": "primary_legislation",
                        "text": good_act.format(name="Alpha Act 2000"),
                        "citation": "Alpha Act 2000 (WA)",
                        "url": "https://legislation.wa.gov.au/alpha",
                        "date": "2020-01-01",
                    },
                    {
                        "jurisdiction": "wa",
                        "type": "primary_legislation",
                        "text": REALISTIC_HEADINGS_ONLY,
                        "citation": "Headings Only Act 2000 (WA)",
                        "url": "https://legislation.wa.gov.au/headings-only",
                        "date": "2020-01-01",
                    },
                    {
                        "jurisdiction": "commonwealth",
                        "type": "primary_legislation",
                        "text": good_act.format(name="Charlie Act 2000"),
                        "citation": "Charlie Act 2000 (Cth)",
                        "url": "https://legislation.gov.au/charlie",
                        "date": "2020-01-01",
                    },
                ]
            )

    fake_datasets_module = SimpleNamespace(load_dataset=lambda *a, **kw: _FakeStream())
    monkeypatch.setitem(sys.modules, "datasets", fake_datasets_module)

    results = ingest.seed_from_corpus(jurisdictions=("wa", "commonwealth"))

    citations = [r.citation for r in results]
    assert citations == ["Alpha Act 2000 (WA)", "Charlie Act 2000 (Cth)"], (
        f"the refusal in the middle of the batch must not lose the documents around it: {citations}"
    )


def test_a_refusal_after_the_document_row_is_written_leaves_no_half_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W1-S3, the part the `continue` alone does not cover.

    Catching `IngestionError` and carrying on is only safe while every such
    error is raised before the document's first write. That is true today, but
    it is a property of `ingest_document`'s control flow, not of this loop, and
    nothing pinned it. Add one refusal after the document row is written - a
    dimension check on `embed()` is the obvious future one - and `continue`
    steps over a half-written document that the enclosing `with writable()`
    then commits: a row with some of its provisions, no error, no log.

    So this test raises exactly that error, from `embed`, after the document
    INSERT has run. The savepoint is what makes the row disappear again.
    """
    conn = _FakeConnection()

    @contextmanager
    def _shared_writable(*, connect_timeout: int = 10) -> Iterator[_FakeConnection]:
        yield conn

    monkeypatch.setattr(ingest, "writable", _shared_writable)
    monkeypatch.setattr(schema, "writable", _shared_writable)

    def _embed_failing_on_bravo(texts: list[str], **kw: object) -> list[list[float]]:
        if any("Bravo" in text for text in texts):
            raise IngestionError("embedding returned the wrong dimension")
        return [[0.0] * 8 for _ in texts]

    monkeypatch.setattr(ingest, "embed", _embed_failing_on_bravo)

    act = "1. Short title\nThis Act may be cited as the {name}.\n"
    records = [
        ("wa", "Alpha Act 2000 (WA)", "alpha", "Alpha Act 2000"),
        ("wa", "Bravo Act 2000 (WA)", "bravo", "Bravo Act 2000"),
        ("commonwealth", "Charlie Act 2000 (Cth)", "charlie", "Charlie Act 2000"),
    ]

    class _FakeStream:
        def __iter__(self) -> Iterator[dict[str, object]]:
            return iter(
                [
                    {
                        "jurisdiction": where,
                        "type": "primary_legislation",
                        "text": act.format(name=name),
                        "citation": citation,
                        "url": f"https://legislation.wa.gov.au/{slug}",
                        "date": "2020-01-01",
                    }
                    for where, citation, slug, name in records
                ]
            )

    monkeypatch.setitem(
        sys.modules, "datasets", SimpleNamespace(load_dataset=lambda *a, **kw: _FakeStream())
    )

    results = ingest.seed_from_corpus(jurisdictions=("wa", "commonwealth"))

    assert [r.citation for r in results] == ["Alpha Act 2000 (WA)", "Charlie Act 2000 (Cth)"], (
        "a failure mid-batch must not lose the documents around it"
    )
    assert "Bravo Act 2000 (WA)" not in conn.written, (
        "the failed document's row was written and never rolled back - the batch "
        f"would commit a document with no provisions: {conn.written}"
    )
    assert conn.written == ["Alpha Act 2000 (WA)", "Charlie Act 2000 (Cth)"], conn.written


# Agricultural Produce Commission Act 1988 (WA), excerpted and verbatim from
# the production corpus. The Act has exactly ONE Schedule, so Western
# Australian drafting heads it "Schedule" with no number at all. Its clauses
# restart at 1 and collided with the Act's own sections 1-9: nine duplicate
# pinpoint groups in one document, every one of them silent, because both
# provisions were stored under the same plain number.
UNNUMBERED_SCHEDULE_COLLISION = """1. Short title
This Act may be cited as the Agricultural Produce Commission Act 1988.
2. Commencement
This Act shall come into operation on a day to be fixed by proclamation.
3. Terms used
In this Act unless the context otherwise requires — agricultural industry
means a horticultural industry or any other industry.
25. Regulations
The Governor may make regulations prescribing all matters that are required.
[Section 25 amended: No. 29 of 1993 s. 7; No. 11 of 2021 s. 30.]
[26, 27. Deleted: No. 11 of 2021 s. 31.]
Schedule — The Commission and its proceedings
[s. 5(6)]
[Heading inserted: No. 19 of 2010 s. 7.]
1. Term of office of member of Commission
Subject to this Schedule, a member of the Commission holds office for 3 years.
2. Remuneration of member of Commission
A member of the Commission other than an ex officio member is entitled to fees.
3. Casual vacancies
Where an office of member of the Commission becomes vacant, the Minister acts.
"""

# Iron Ore (Mount Goldsworthy) Agreement Act 1964 (WA) and its relatives set
# their Schedule out under an ORDINAL heading. "First" is the number, written
# as a word.
ORDINAL_SCHEDULE_COLLISION = """1. Short title
This Act may be cited as the Iron Ore (Mount Goldsworthy) Agreement Act 1964.
2. Ratification
The Agreement set out in the First Schedule is ratified.
First Schedule — Iron Ore Agreement
1. Interpretation
In this Agreement subject to the context the following terms have meanings.
2. Term
This Agreement continues for a period of 21 years from the commencement day.
"""


def test_an_unnumbered_schedule_heading_is_recognised() -> None:
    """A sole Schedule carries no number, and was invisible to the splitter.

    87 of production's 151 duplicate pinpoint groups are this shape: the
    Schedule's clauses fall through with plain numeric ids and collide with
    the Act's own sections, so two different provisions are both cited "1"
    and a reader cannot tell them apart.
    """
    assert schedule_spans("Schedule — The Commission and its proceedings") == [(0, "1")]
    assert schedule_spans("Schedule - Forms") == [(0, "1")]
    assert schedule_spans("The Schedule — Matters to be prescribed") == [(0, "1")]


def test_an_ordinal_schedule_heading_is_recognised() -> None:
    """ "First Schedule" is Schedule 1 with the number written as a word."""
    assert schedule_spans("First Schedule — Iron Ore Agreement") == [(0, "1")]
    assert schedule_spans("Second Schedule - Forms") == [(0, "2")]
    assert schedule_spans("Third Schedule — Transitional provisions") == [(0, "3")]


def test_a_bare_schedule_word_is_still_not_a_heading() -> None:
    """The separator remains what distinguishes a heading from a sentence.

    Widening the pattern to an unnumbered Schedule makes this the only thing
    standing between the rule and ordinary prose, so it is asserted directly.
    """
    for line in (
        "Schedule means the schedule to this Act as amended from time to time.",
        "Schedule 2 commencement day means the day on which Schedule 2 commences.",
        "The Schedule referred to in section 5 sets out the procedure.",
    ):
        assert schedule_spans(line) == [], line


def test_an_unnumbered_schedule_is_ignored_when_the_document_numbers_its_schedules() -> None:
    """Only a document whose sole Schedule is unnumbered may claim "Sch 1".

    If the document numbers its Schedules, a bare "Schedule" line is a
    cross-reference or a contents row, and labelling its neighbours "Sch 1"
    would assert a number the document does not use.
    """
    text = "Schedule — Forms\n1. A form\nSchedule 2 — Transitional\n1. A transitional clause\n"
    assert schedule_spans(text) == [(len("Schedule — Forms\n1. A form\n"), "2")]


def test_an_unnumbered_schedules_clauses_do_not_collide_with_the_acts_sections() -> None:
    """End to end, on the real document: section 1 must resolve once."""
    provisions = split_sections(UNNUMBERED_SCHEDULE_COLLISION)
    numbers = [p.section_number for p in provisions]
    assert len(numbers) == len(set(numbers)), f"duplicate pinpoints remain: {numbers}"

    ones = [p for p in provisions if p.section_number == "1"]
    assert len(ones) == 1 and ones[0].heading == "Short title"
    assert any(p.section_number == "Sch 1 cl 1" for p in provisions), numbers
    assert any(
        p.section_number == "Sch 1 cl 3" and p.heading == "Casual vacancies" for p in provisions
    ), numbers


def test_an_ordinal_schedules_clauses_do_not_collide_either() -> None:
    provisions = split_sections(ORDINAL_SCHEDULE_COLLISION)
    numbers = [p.section_number for p in provisions]
    assert len(numbers) == len(set(numbers)), f"duplicate pinpoints remain: {numbers}"
    assert any(p.section_number == "Sch 1 cl 1" for p in provisions), numbers


def test_the_relabelled_clauses_keep_the_shape_retrieval_looks_for() -> None:
    """retrieve.py matches a Schedule sibling with `^Sch \S+ cl <n>$`, and the
    page's citation chip with `Sch\s+\w+\s+cl`. A label without a number -
    "Sch cl 3" - satisfies neither, so relabelling would have made these
    provisions unreachable by the very lookup that exists to find them.
    """
    import re

    for text in (UNNUMBERED_SCHEDULE_COLLISION, ORDINAL_SCHEDULE_COLLISION):
        for p in split_sections(text):
            if p.section_number.startswith("Sch"):
                assert re.fullmatch(r"Sch \S+ cl \S+", p.section_number), p.section_number


def test_a_note_block_headed_for_this_subsection_is_not_a_second_section_one() -> None:
    """Reduced from Offshore Minerals Act 2003 (WA), verbatim shape.

    The opener that already existed hardcoded the noun - "Notes for this
    SECTION:" - and the Act heads most of its note blocks "Notes for this
    subsection:". Those went unrecognised, so their items were split off as
    sections numbered from 1: 65 excess pinpoints in this document alone, the
    largest remaining group in the corpus, and 32 provisions all citing s 1
    where only the first is the Short title.
    """
    text = (
        "1. Short title\n"
        "This Act may be cited as the Offshore Minerals Act 2003.\n"
        "\n"
        "12. Application of Act\n"
        "This Act applies to offshore mining beyond the baseline of "
        "Australia's territorial sea.\n"
        "Notes for this subsection:\n"
        "1. So far as the agreement relates to petroleum, it is reflected in "
        "the Petroleum (Submerged Lands) Act 1982.\n"
        "2. The Seas and Submerged Lands Act 1973 declares the sovereignty.\n"
        "\n"
        "13. Crown to be bound\n"
        "This Act binds the Crown.\n"
    )
    numbers = [p.section_number for p in ingest.split_sections(text)]
    assert numbers.count("1") == 1, numbers
    assert "12" in numbers and "13" in numbers


def test_a_note_block_headed_for_this_item_is_not_a_second_clause_one() -> None:
    """Reduced from Supreme Court (Fees) Regulations 2002 (WA).

    A fee table's rows each carry "Notes for this item:" - the same mechanism,
    a third noun - and each note run restarted at 1 inside Schedule 1, so six
    provisions ended up cited "Sch 1 cl 1".
    """
    text = (
        "1. Citation\n"
        "These regulations are the Supreme Court (Fees) Regulations 2002.\n"
        "\n"
        "4. Fees payable\n"
        "The fees set out in Schedule 1 are payable.\n"
        "Notes for this item:\n"
        "1. No fee is payable if the proceedings are of an interlocutory "
        "nature.\n"
        "2. The fee is to be paid in respect of the number of hearing days.\n"
        "\n"
        "5. Waiver of fees\n"
        "The registrar may waive a fee.\n"
    )
    numbers = [p.section_number for p in ingest.split_sections(text)]
    assert numbers.count("1") == 1, numbers
    assert "4" in numbers and "5" in numbers


def test_the_note_opener_still_needs_its_colon_after_widening_the_noun() -> None:
    """The Chattel Securities guard, restated against the wider pattern.

    Widening "for this section:" to any noun makes the colon the only thing
    left separating a note opener from a contents row, so it is asserted
    directly rather than left to the end-to-end tests.
    """
    from michael.ingest import APPARATUS_OPENERS

    for opener in (
        "Notes for this section:",
        "Notes for this subsection:",
        "Notes for this item:",
        "Notes for this clause:",
        "Note:",
        "Notes:",
    ):
        assert APPARATUS_OPENERS.match(opener), opener
    for not_an_opener in (
        "Notes",
        "Notes for this section",
        "Noted for the record",
        "Note that the fee is payable",
    ):
        assert not APPARATUS_OPENERS.match(not_an_opener), not_an_opener


def test_a_suppressed_row_stays_in_its_section_rather_than_falling_out_of_the_corpus() -> None:
    """apparatus_rows says an in-body row "is part of a real section and stays
    in that section's text". It did not.

    A provision ended at the next heading CANDIDATE, so a row rejected as
    apparatus terminated the provision before it and was then skipped - and
    its text belonged to no provision at all. Measured on Supreme Court (Fees)
    Regulations 2002 (WA), whose Schedule 1 is one long run of fee items and
    their note blocks: 32,362 characters, the entire fee schedule, sat past
    the last provision, unreachable by any search. A provision now runs to the
    next KEPT heading.
    """
    text = (
        "1. Citation\n"
        "These regulations are the Example Regulations 2002.\n"
        "\n"
        "4. Fees payable\n"
        "The fees set out in Schedule 1 are payable to the registrar.\n"
        "Notes for this item:\n"
        "1. No fee is payable if the proceedings are of an interlocutory nature.\n"
        "2. The fee is to be paid in respect of the number of hearing days.\n"
        "\n"
        "5. Waiver of fees\n"
        "The registrar may waive a fee in a case of hardship.\n"
    )
    provisions = ingest.split_sections(text)
    numbers = [p.section_number for p in provisions]
    assert numbers == ["1", "4", "5"], numbers

    fees = next(p for p in provisions if p.section_number == "4")
    assert "No fee is payable" in fees.text, "the note fell out of the corpus"
    assert "number of hearing days" in fees.text

    for earlier, later in zip(provisions, provisions[1:], strict=False):
        assert later.char_start == earlier.char_end, (
            f"text between {earlier.section_number} and {later.section_number} "
            f"belongs to no provision"
        )


# --- judgments -------------------------------------------------------------
#
# The fixtures below reproduce, in synthetic text, every shape measured on the
# real corpus judgments seeded locally (see .handoff/phase4-readme.md for the
# stored rows). No real judgment text is copied in: what is under test is the
# structure, and a fixture that states its own expected answer is a better
# test than a 98,000-character report nobody can read in a diff.

FIXTURE_JUDGMENT = """Federal Court of Australia

Fixture Pty Ltd v Example Corporation [2099] FCA 1

Catchwords:                 PRACTICE AND PROCEDURE - fixture matter

Number of paragraphs:       4

ORDERS

THE COURT ORDERS THAT:

1. The application is dismissed.
2. The applicant pay the respondent's costs of the application.
Note: Entry of orders is dealt with in Rule 39.32 of the Federal Court Rules 2011.

REASONS FOR JUDGMENT

FIXTURE J
1 The applicant seeks an order under the fixture rule.
2 The respondent relies on Another Party v Third Party [2098] FCA 9, where the
Court said at [10] to [11]:
      10. The fixture rule is not engaged where the applicant already holds the
      information it seeks.
      11. That is so whether or not a cause of action has been pleaded.
3 Section 5 of the Fixture Act 2098 (Cth) provides:
      5 Fixture orders
      The Court may make a fixture order if satisfied of the matters set out in
      subsection (2).
4 For those reasons the application is dismissed.
I certify that the preceding four (4) numbered paragraphs are a true copy of the
Reasons for Judgment of the Honourable Justice Fixture.

Associate:
Dated: 1 January 2099
"""

FIXTURE_JUDGMENT_UNNUMBERED = """Federal Court of Australia

Fixture Union v Fixture Board [2099] FCA 2

REASONS FOR JUDGMENT

THE COURT
This is an appeal against a decision given on 2 December 2098. The proceeding
concerns the construction of one word in the fixture award.

We agree with the primary judge that the normal meaning of the word is an area
of land delineated for a purpose.

In these circumstances the appeal should be dismissed.

I certify that this and the preceding three (3) pages are a true copy of the
reasons for judgment of the Court.

Associate:
Dated: 1 January 2099
"""

FIXTURE_JUDGMENT_FOOTNOTES = """High Court of Australia

Fixture v Another Fixture [2099] HCA 3

    This appeal concerns the residuary bequest contained in the last will of
    the testator.

    It is not seriously contended that if the earlier part of the provision
    stood alone it would not be valid as a charitable trust.

     1. (1899) 5 Fixture 300 [49 F.R. 593].
     2. (1899) 5 Fixture, at p. 303 [49 F.R., at p. 594].

    The real question is whether the words confine that part of the bequest.
"""


def _numbers(provisions: list[Provision], unit_type: str) -> list[str]:
    return [p.section_number for p in provisions if p.unit_type == unit_type]


def test_a_judgment_paragraph_is_stored_as_a_paragraph_not_a_section() -> None:
    """The defect this phase exists to fix. README "Known limitations" recorded
    judgment paragraphs landing in `section_number` as if they were sections of
    an Act; `pinpoint()` then rendered "s 2" for a passage of reasons."""
    provisions = split_judgment(FIXTURE_JUDGMENT)
    assert _numbers(provisions, "paragraph") == ["1", "2", "3", "4"]
    assert not _numbers(provisions, "section")


def test_orders_and_reasons_both_numbered_from_one_do_not_collide() -> None:
    """Measured on the seeded corpus: Cromwell Corporation v ARA Real Estate
    [2020] FCA 1492 runs orders 1, 2 and then reasons 1..145, and AGV20 v
    Minister [2023] FCA 1430 runs orders 1-4 and then reasons 1-8. Under the
    section splitter each produced two rows for "1" in the same document."""
    provisions = split_judgment(FIXTURE_JUDGMENT)
    numbers = [p.section_number for p in provisions]
    assert len(numbers) == len(set(numbers)), numbers
    assert _numbers(provisions, "order") == ["(orders)"]
    orders = next(p for p in provisions if p.unit_type == "order")
    # The orders keep their own numbering inside their text; what they do not
    # get is a pinpoint that competes with a paragraph of the reasons.
    assert "1. The application is dismissed." in orders.text
    assert "2. The applicant pay the respondent's costs" in orders.text


def test_a_judgment_with_no_numbered_paragraphs_is_reported_not_numbered() -> None:
    """Muir v Open Brethren [1956] HCA 14 - the case the README names - has no
    numbered paragraphs at all. Nothing is invented for it, and the fact is
    reported rather than left to be inferred from a provision count of 1."""
    provisions = split_judgment(FIXTURE_JUDGMENT_UNNUMBERED)
    assert _numbers(provisions, "paragraph") == []
    assert _numbers(provisions, "document") == ["(whole document)"]
    assert "no numbered paragraphs" in unnumbered_note(provisions)


def test_a_split_judgment_with_paragraphs_reports_no_unnumbered_note() -> None:
    assert unnumbered_note(split_judgment(FIXTURE_JUDGMENT)) == ""


def test_a_quoted_judgment_paragraph_is_not_a_paragraph_of_this_judgment() -> None:
    """Measured: the section splitter stored paragraphs 10-14 of St Barbara
    Mines, quoted inside Cromwell's paragraph 40, as paragraphs of Cromwell."""
    provisions = split_judgment(FIXTURE_JUDGMENT)
    assert "10" not in _numbers(provisions, "paragraph")
    assert "11" not in _numbers(provisions, "paragraph")
    quoting = next(p for p in provisions if p.section_number == "2")
    # Not dropped: the quotation stays inside the paragraph that quotes it.
    assert "The fixture rule is not engaged" in quoting.text


def test_a_quoted_statutory_section_is_not_a_paragraph_of_this_judgment() -> None:
    """Measured: ss 659AA and 659B of the Corporations Act, reproduced inside
    Cromwell's reasons, were stored as provisions of the judgment."""
    provisions = split_judgment(FIXTURE_JUDGMENT)
    assert "5" not in _numbers(provisions, "paragraph")
    quoting = next(p for p in provisions if p.section_number == "3")
    assert "The Court may make a fixture order" in quoting.text


def test_a_numbered_footnote_never_becomes_a_citable_unit() -> None:
    """The README's observed rows, as a regression.

    README.md recorded a judgment stored with `section_number = 2` and
    `heading = "(1842) 5 Beav., at p. 303 [49 E.R., at p. 594]."` - a footnote
    of Muir v Open Brethren, cited as if it were section 2 of an Act. A
    footnote is not a unit of the judgment and must not be given a pinpoint.
    """
    provisions = split_judgment(FIXTURE_JUDGMENT_FOOTNOTES)
    assert [p.section_number for p in provisions] == ["(whole document)"]
    assert provisions[0].unit_type == "document"
    assert "(1899) 5 Fixture, at p. 303" in provisions[0].text


def test_the_certificate_is_not_part_of_the_last_paragraph() -> None:
    """The associate's certificate is apparatus. Absorbed into the last
    paragraph it reads as the court's own words under that paragraph's
    pinpoint."""
    last = split_judgment(FIXTURE_JUDGMENT)[-1]
    assert last.section_number == "4"
    assert "I certify that" not in last.text


def test_the_cover_sheet_is_not_stored_as_a_unit() -> None:
    """Catchwords and counsel are apparatus with no pinpoint to cite them by."""
    provisions = split_judgment(FIXTURE_JUDGMENT)
    assert not any("Catchwords" in p.text for p in provisions)
    assert not any("Number of paragraphs" in p.text for p in provisions)


def test_a_report_certified_in_pages_is_not_given_paragraph_numbers() -> None:
    """Measured on Commissioner of Taxation v Northumberland Development Co
    [1995] FCA 588: certified in pages, with a numbered list of three
    propositions inside the reasons that otherwise reads as paragraphs 1-3."""
    text = FIXTURE_JUDGMENT_UNNUMBERED.replace(
        "In these circumstances the appeal should be dismissed.",
        "Three matters arise:\n1. The first matter.\n2. The second matter.\n3. The third matter.",
    )
    assert certified_unit(text) == "page"
    provisions = split_judgment(text)
    assert _numbers(provisions, "paragraph") == []
    assert _numbers(provisions, "document") == ["(whole document)"]


def test_certified_unit_reads_the_reports_own_words() -> None:
    assert certified_unit(FIXTURE_JUDGMENT) == "paragraph"
    assert certified_unit(FIXTURE_JUDGMENT_UNNUMBERED) == "page"
    assert certified_unit(FIXTURE_JUDGMENT_FOOTNOTES) is None


def test_a_qualified_reasons_heading_is_still_a_reasons_heading() -> None:
    """Measured on Adlam v Bauer [1999] FCA 634, headed "EX TEMPORE REASONS
    FOR DECISION". Missing the heading moved the start of the reasons to the
    top of the report, where the date line "10 MAY 1999" anchored the
    numbering and 13 of 14 paragraphs were lost."""
    text = FIXTURE_JUDGMENT.replace("REASONS FOR JUDGMENT", "EX TEMPORE REASONS FOR DECISION")
    provisions = split_judgment(text)
    assert _numbers(provisions, "paragraph") == ["1", "2", "3", "4"]
    # The numbers alone are not enough: with the heading missed, the orders
    # block swallows the whole report and the next-in-run rule re-derives the
    # same four numbers out of it. What must hold is that the orders stop
    # where the reasons start.
    orders = next(p for p in provisions if p.unit_type == "order")
    assert "The applicant seeks an order" not in orders.text


def test_a_reasons_heading_with_a_parenthetical_is_still_found() -> None:
    """Measured on ASIC v Southcorp Ltd [2003] FCA 804: "REASONS FOR JUDGMENT
    (No 1)"."""
    text = FIXTURE_JUDGMENT.replace("REASONS FOR JUDGMENT", "REASONS FOR JUDGMENT (No 1)")
    provisions = split_judgment(text)
    assert _numbers(provisions, "paragraph") == ["1", "2", "3", "4"]
    orders = next(p for p in provisions if p.unit_type == "order")
    assert "The applicant seeks an order" not in orders.text


def test_a_sentence_mentioning_reasons_for_judgment_is_not_a_heading() -> None:
    """The heading rule is case-sensitive and requires the line to be in
    capitals, because a lower-case mention in prose would move the start of
    the reasons into the middle of a paragraph."""
    text = FIXTURE_JUDGMENT.replace(
        "1 The applicant seeks an order under the fixture rule.",
        "1 The applicant relies on the reasons for judgment of the primary judge.",
    )
    assert _numbers(split_judgment(text), "paragraph") == ["1", "2", "3", "4"]


def test_paragraph_numbers_are_strictly_increasing() -> None:
    """The guarantee the pinpoint rests on: one number, one paragraph."""
    numbers = [int(n) for n in _numbers(split_judgment(FIXTURE_JUDGMENT), "paragraph")]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))


def test_a_paragraph_whose_indent_was_lost_is_still_the_next_paragraph() -> None:
    """Measured on Flashback Holdings v Showtime DVD (No 6) [2010] FCA 694,
    where paragraph 10 alone of 47 sits at column 0 while the rest sit at
    column 4. Without the next-in-run rule its text is absorbed into
    paragraph 9 and would be quoted under [9]."""
    text = FIXTURE_JUDGMENT.replace(
        "\n1 The applicant seeks", "\n    1 The applicant seeks"
    ).replace("\n2 The respondent relies", "\n    2 The respondent relies")
    assert _numbers(split_judgment(text), "paragraph") == ["1", "2", "3", "4"]


def test_no_text_is_lost_between_consecutive_paragraphs() -> None:
    """A rejected marker must never mean dropped text: the enclosing paragraph
    runs through it to the next real one."""
    provisions = [p for p in split_judgment(FIXTURE_JUDGMENT) if p.unit_type == "paragraph"]
    for earlier, later in zip(provisions, provisions[1:], strict=False):
        assert earlier.char_end == later.char_start
    assert "The fixture rule is not engaged" in provisions[1].text
    assert "The Court may make a fixture order" in provisions[2].text


def test_numbered_paragraphs_reports_what_it_rejected() -> None:
    _markers, rejected = ingest.numbered_paragraphs(FIXTURE_JUDGMENT)
    assert rejected > 0


def test_case_law_is_split_by_the_judgment_splitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dispatch. `ingest_document` chooses the splitter from the document type,
    so nothing downstream has to know which was used."""
    written: dict[str, object] = {}

    def fake_write(conn: object, **kwargs: object) -> ingest.IngestResult:
        written.update(kwargs)
        return ingest.IngestResult(
            document_id=1, citation="c", sha256="0" * 64, provisions=0, created=True
        )

    monkeypatch.setattr(ingest, "_write", fake_write)
    ingest.ingest_document(
        jurisdiction="commonwealth",
        title="Fixture Pty Ltd v Example Corporation [2099] FCA 1",
        citation="Fixture Pty Ltd v Example Corporation [2099] FCA 1",
        source_url="(corpus record)",
        snapshot_date=date(2099, 1, 1),
        sha256="a" * 64,
        doc_type="case",
        text=FIXTURE_JUDGMENT,
        conn=SimpleNamespace(),  # type: ignore[arg-type]
    )
    provisions = written["provisions"]
    assert isinstance(provisions, list)
    assert {p.unit_type for p in provisions} == {"order", "paragraph"}


def test_legislation_is_still_split_by_the_section_splitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the dispatch: nothing about legislation changed."""
    written: dict[str, object] = {}

    def fake_write(conn: object, **kwargs: object) -> ingest.IngestResult:
        written.update(kwargs)
        return ingest.IngestResult(
            document_id=1, citation="c", sha256="0" * 64, provisions=0, created=True
        )

    monkeypatch.setattr(ingest, "_write", fake_write)
    ingest.ingest_document(
        jurisdiction="wa",
        title="Fixture Employment Standards Act 2000",
        citation="Fixture Employment Standards Act 2000 (WA)",
        source_url="(corpus record)",
        snapshot_date=date(2000, 1, 1),
        sha256="a" * 64,
        doc_type="act",
        text=SAMPLE,
        conn=SimpleNamespace(),  # type: ignore[arg-type]
    )
    provisions = written["provisions"]
    assert isinstance(provisions, list)
    assert [p.section_number for p in provisions] == ["1", "15A", "23AB"]
    assert {p.unit_type for p in provisions} == {"section"}


def test_a_whole_document_row_is_not_typed_as_a_section() -> None:
    """`(whole document)` was never a section; migration 0001 backfills the
    existing rows and the splitter now says so at the source."""
    provisions = split_sections("Prose with no section headings at all, at some length.")
    assert [p.unit_type for p in provisions] == ["document"]


def test_numbering_ignores_a_numbered_line_before_paragraph_one() -> None:
    """Measured on Adlam v Bauer [1999] FCA 634: the date line "10 MAY 1999"
    reads as paragraph 10, after which every real paragraph from 1 to 9 is
    below the running maximum and is rejected."""
    text = (
        "10 MAY 2099\n\n"
        "1 The first paragraph of the reasons, at some length.\n"
        "2 The second paragraph of the reasons, at some length.\n"
    )
    markers, _rejected = ingest.numbered_paragraphs(text)
    assert [number for _position, number in markers] == [1, 2]


FIXTURE_JUDGMENT_TWO_SETS = """Federal Court of Australia

Fixture Pty Ltd v Example Corporation (No 2) [2099] FCAFC 1

REASONS FOR JUDGMENT

FIRST J
1 I have had the benefit of reading the reasons of Second J in draft form,
and I agree with them.
I certify that the preceding one (1) numbered paragraph is a true copy of the
Reasons for Judgment of the Honourable Justice First.

Associate:

REASONS FOR JUDGMENT

SECOND J
2 The applicant seeks an order under the fixture rule.
3 For the reasons that follow the application is dismissed.
I certify that the preceding two (2) numbered paragraphs are a true copy of the
Reasons for Judgment of the Honourable Justice Second.

Associate:
"""


def test_a_judgment_with_one_certificate_per_judge_keeps_every_paragraph() -> None:
    """Measured on Lu v Minister for Immigration & Multicultural Affairs
    [2000] FCA 178: Kiefel J's paragraph 1 is certified before the other
    members' reasons begin at paragraph 2. Cutting at the first certificate
    found ends the judgment there and stores 1 paragraph of 19."""
    provisions = split_judgment(FIXTURE_JUDGMENT_TWO_SETS)
    assert _numbers(provisions, "paragraph") == ["1", "2", "3"]
    assert not any("I certify that" in p.text for p in provisions)


FIXTURE_JUDGMENT_UNIFORM = """Federal Court of Australia

Fixture Pty Ltd v Example Corporation (No 3) [2099] FCA 4

REASONS FOR JUDGMENT

FIXTURE J
1 The applicant applied for an order and the respondent opposed the order.
2 The applicant applied for an order and the respondent opposed the order.
3 The applicant applied for an order and the respondent opposed the order.
4 The applicant applied for an order and the respondent opposed the order.
"""


def test_the_headings_only_guard_is_not_applied_to_a_judgment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detect_headings_only asks whether provisions carry subsection markers
    and vary in length. A judgment's paragraphs carry neither property by
    nature, so running it over a judgment refuses real judgments for being
    judgments."""
    provisions = split_judgment(FIXTURE_JUDGMENT_UNIFORM)
    # The guard WOULD flag this document - that is what makes the test able to
    # fail - and ingestion must take it anyway.
    assert detect_headings_only(provisions) is not None

    written: dict[str, object] = {}

    def fake_write(conn: object, **kwargs: object) -> ingest.IngestResult:
        written.update(kwargs)
        return ingest.IngestResult(
            document_id=1, citation="c", sha256="0" * 64, provisions=0, created=True
        )

    monkeypatch.setattr(ingest, "_write", fake_write)
    ingest.ingest_document(
        jurisdiction="commonwealth",
        title="Fixture Pty Ltd v Example Corporation (No 3) [2099] FCA 4",
        citation="Fixture Pty Ltd v Example Corporation (No 3) [2099] FCA 4",
        source_url="(corpus record)",
        snapshot_date=date(2099, 1, 1),
        sha256="a" * 64,
        doc_type="case",
        text=FIXTURE_JUDGMENT_UNIFORM,
        conn=SimpleNamespace(),  # type: ignore[arg-type]
    )
    stored = written["provisions"]
    assert isinstance(stored, list)
    assert [p.section_number for p in stored] == ["1", "2", "3", "4"]


def test_a_headings_only_page_of_legislation_is_still_refused() -> None:
    """The other half: the guard is not weakened, only scoped."""
    stub = "\n\n".join(
        f"{n} Fixture heading {n}\n    This section is about fixture heading {n}."
        for n in range(1, 5)
    )
    with pytest.raises(IngestionError, match="headings-only"):
        ingest.ingest_document(
            jurisdiction="wa",
            title="Fixture Guide Act 2000",
            citation="Fixture Guide Act 2000 (WA)",
            source_url="(corpus record)",
            snapshot_date=date(2000, 1, 1),
            sha256="a" * 64,
            doc_type="act",
            text=stub,
            conn=SimpleNamespace(),  # type: ignore[arg-type]
        )


# --- Part-numbered regulations (Migration Regulations 1994 shape) ----------
#
# The structure is real - regulation, Schedule, Division and clause numbering
# as the Migration Regulations lay them out, and the multi-volume cover
# listing. The operative words are placeholders, for the reason given in
# tests/fixtures.py: no statutory text is invented to make a test pass.

PART_NUMBERED_REGULATIONS = """FIXTURE MIGRATION REGULATIONS 1994
Compilation No. 1
This compilation is in 2 volumes
Volume 1:
Parts 1 to 2A
regulations 1.01 to 2.60
Schedule 1
Volume 2:
Schedule 2, Subclasses 010 to 020
Schedule 13
Each volume has its own contents
About this compilation
This compilation is a fixture and carries no law of its own.
Contents
Part 1—Preliminary 1
1.01 Name of Regulations 1
1.03 Definitions 1
Part 2A—Sponsorship 2
2.57 Interpretation 2
2.59 Criteria for approval 2
Part 1—Preliminary
1.01 Name of Regulations
These Regulations are the fixture regulations, placeholder text only.
1.03 Definitions
(1) Placeholder definition text, in which a term is defined for testing.
(2) Placeholder definition text, continued for testing purposes only.
Part 2A—Sponsorship
2.57 Interpretation
(1) Placeholder interpretation text for Part 2A of the fixture.
(2) Further placeholder interpretation text for Part 2A of the fixture.
2.59 Criteria for approval
(1) Placeholder criteria text for the approval of a sponsor.
(2) Further placeholder criteria text for the approval of a sponsor.
Schedule 2—Provisions with respect to visas
Subclass 010—Bridging A
010.2—Primary criteria
010.21—Criteria to be satisfied at the time of application
010.211
(1) Placeholder criterion text that an applicant must satisfy.
(2) Further placeholder criterion text that an applicant must satisfy.
010.6—Conditions
010.611
Conditions 8101 and 8102.
Schedule 13—Transitional arrangements
9903 Operation of Schedule 3
(1) Placeholder transitional text directing that a Division be read as if
replaced with the following:
010.311
The quoted replacement clause, which belongs to 9903 and to nothing else.
(2) Placeholder transitional text, continued after the quotation.
"""


def _regulation_split() -> dict[str, ingest.Provision]:
    provisions = split_sections(
        PART_NUMBERED_REGULATIONS, pattern=ingest.section_pattern("regulation")
    )
    return {p.section_number: p for p in provisions}


def test_part_numbered_regulations_are_split_by_their_own_numbers() -> None:
    provisions = _regulation_split()
    assert {"1.01", "1.03", "2.57", "2.59"} <= set(provisions)
    assert provisions["2.57"].heading == "Interpretation"
    assert "Part 2A of the fixture" in provisions["2.57"].text


def test_the_cover_pages_volume_listing_is_not_a_schedule_heading() -> None:
    # "Schedule 1" in the cover's volume listing once made every regulation of
    # Parts 1-5 a "Sch 1 cl" - and every section of the Migration Act too.
    provisions = _regulation_split()
    assert not any(n.startswith("Sch 1 cl") for n in provisions)


def test_an_act_whose_cover_lists_an_unnumbered_schedule_keeps_plain_section_ids() -> None:
    act = (
        "FIXTURE MIGRATION ACT 1958\n"
        "This compilation is in 2 volumes\n"
        "Volume 1:\n"
        "sections 1-261K\n"
        "Volume 2:\n"
        "sections 262-507\n"
        "Schedule\n"
        "Endnotes\n"
        "Each volume has its own contents\n"
        "About this compilation\n"
        "This compilation is a fixture and carries no law of its own.\n"
        "1 Short title\n"
        "This Act may be cited as the Fixture Act, placeholder text only.\n"
        "2 Commencement\n"
        "Placeholder commencement text that is long enough to be a provision.\n"
    )
    assert [p.section_number for p in split_sections(act)] == ["1", "2"]


def test_a_bare_schedule_2_clause_number_is_a_clause_with_no_heading() -> None:
    provisions = _regulation_split()
    clause = provisions["Sch 2 cl 010.211"]
    assert clause.heading == ""
    assert "an applicant must satisfy" in clause.text


def test_a_schedule_2_division_heading_is_not_cited_as_a_clause() -> None:
    provisions = _regulation_split()
    assert "Sch 2 cl 010.21" not in provisions
    assert "Sch 2 cl 010.2" not in provisions
    assert "Sch 2 cl 010.6" not in provisions


def test_a_schedule_2_clause_shorter_than_the_floor_is_kept() -> None:
    provisions = _regulation_split()
    assert "Conditions 8101 and 8102." in provisions["Sch 2 cl 010.611"].text


def test_a_schedule_2_clause_quoted_in_another_schedule_stays_where_it_is_quoted() -> None:
    provisions = _regulation_split()
    assert "Sch 13 cl 010.311" not in provisions
    assert "Sch 2 cl 010.311" not in provisions
    assert "belongs to 9903" in provisions["Sch 13 cl 9903"].text


def test_part_numbering_is_for_regulations_only() -> None:
    # An award numbers its subclauses "10.1"; under the regulation pattern
    # every subclause would become a provision of its own.
    assert ingest.section_pattern("regulation") is ingest.DOTTED_SECTION_RE
    for doc_type in ("act", "award", "case", "guidance"):
        assert ingest.section_pattern(doc_type) is ingest.SECTION_RE
    award = (
        "10. Types of employment\n"
        "10.1 A full-time employee is engaged for placeholder hours of work.\n"
        "10.2 A part-time employee is engaged for fewer placeholder hours.\n"
    )
    assert [p.section_number for p in split_sections(award)] == ["10"]


# --- departmental guidance --------------------------------------------------

HOME_AFFAIRS_URL = "https://immigration.homeaffairs.gov.au/visas/getting-a-visa/fixture"


def test_guidance_is_stored_whole_and_its_steps_are_never_sections() -> None:
    page = (
        "Fixture visa guidance\n"
        "1. Check you are eligible\n"
        "Placeholder guidance text about eligibility for this fixture.\n"
        "2. Gather your documents\n"
        "Placeholder guidance text about documents for this fixture.\n"
    )
    (only,) = ingest.split_guidance(page)
    assert only.section_number == "(whole document)"
    assert only.unit_type == "document"
    assert "Gather your documents" in only.text


@pytest.mark.parametrize(
    ("url", "doc_type"),
    [
        (HOME_AFFAIRS_URL, "act"),
        (HOME_AFFAIRS_URL, "regulation"),
        ("https://www.legislation.gov.au/F1996B03551/latest/text", "guidance"),
    ],
)
def test_ingest_document_refuses_a_doc_type_the_source_host_contradicts(
    url: str, doc_type: str
) -> None:
    with pytest.raises(IngestionError, match="refused"):
        ingest.ingest_document(
            jurisdiction="commonwealth",
            title="Fixture",
            citation="Fixture",
            source_url=url,
            snapshot_date=date(2026, 9, 25),
            sha256="a" * 64,
            doc_type=doc_type,
            text="1 Placeholder\nPlaceholder text that is long enough to be a provision.",
        )


def test_ingest_url_refuses_the_mismatch_before_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_fetch(url: str) -> None:
        raise AssertionError("fetched a source whose doc_type was already refused")

    monkeypatch.setattr(ingest, "fetch", no_fetch)
    monkeypatch.setattr(ingest, "_log_refusal", lambda **_: None)
    with pytest.raises(SourceRefused, match="departmental guidance"):
        ingest.ingest_url(
            url=HOME_AFFAIRS_URL,
            jurisdiction="commonwealth",
            title="Fixture",
            citation="Fixture",
            doc_type="act",
        )
