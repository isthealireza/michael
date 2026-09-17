from michael.contracts import PREAMBLE_ID, Clause, split_clauses


DECIMAL_NUMBERED = """SERVICES AGREEMENT
This agreement is made between the parties named below.

1. DEFINITIONS
1.1 In this agreement, "Services" means the services described in Schedule 1.
1.2 "Fees" means the amounts set out in clause 4.

2. TERM
2.1 This agreement commences on the Commencement Date.
"""

ALL_CAPS_UNNUMBERED = """CONSULTANCY AGREEMENT
Recitals appear here before any clause.

PAYMENT
The Client must pay within 30 days of invoice.

TERMINATION
Either party may terminate on 14 days notice.
"""

SCHEDULE_RESTARTS_NUMBERING = """1. PARTIES
The parties are named in Schedule 1.

2. TERM
The term is three years.

SCHEDULE 1
1. The Client is Acme Pty Ltd of 1 Example Street.
2. The Consultant is Beta Pty Ltd of 2 Sample Road.
"""


def test_text_before_the_first_clause_is_kept_as_preamble() -> None:
    clauses = split_clauses(DECIMAL_NUMBERED)
    assert clauses[0].clause_id == PREAMBLE_ID
    assert "made between the parties" in clauses[0].text


def test_the_deepest_numbered_unit_is_the_clause() -> None:
    ids = [c.clause_id for c in split_clauses(DECIMAL_NUMBERED)]
    assert "1.1" in ids and "1.2" in ids and "2.1" in ids


def test_a_clause_carries_its_heading_and_its_own_text() -> None:
    by_id = {c.clause_id: c for c in split_clauses(DECIMAL_NUMBERED)}
    assert "Services" in by_id["1.1"].text
    assert "Fees" in by_id["1.2"].text
    assert "Fees" not in by_id["1.1"].text


def test_unnumbered_all_caps_headings_split() -> None:
    ids = [c.clause_id for c in split_clauses(ALL_CAPS_UNNUMBERED)]
    assert "PAYMENT" in ids
    assert "TERMINATION" in ids


def test_numbering_that_restarts_in_a_schedule_is_not_dropped() -> None:
    """The monotonic-sequence assumption cost 1,199 provisions once. Never again."""
    clauses = split_clauses(SCHEDULE_RESTARTS_NUMBERING)
    text = " ".join(c.text for c in clauses)
    assert "Acme Pty Ltd" in text
    assert "Beta Pty Ltd" in text


def test_nothing_in_the_document_is_lost() -> None:
    """Every character of every document must survive into some clause."""
    for document in (DECIMAL_NUMBERED, ALL_CAPS_UNNUMBERED, SCHEDULE_RESTARTS_NUMBERING):
        clauses = split_clauses(document)
        joined = "".join(document[c.char_start:c.char_end] for c in clauses)
        assert joined.strip() == document.strip()


def test_a_document_with_no_clause_headings_returns_one_clause() -> None:
    clauses = split_clauses("Just some prose with no headings at all in it.")
    assert len(clauses) == 1
    assert clauses[0].clause_id == PREAMBLE_ID


import pathlib

import pytest

from michael.contracts import normalise


@pytest.mark.parametrize(
    "before, after",
    [
        ("The Employer must pay.", "The Employer may pay."),
        ("A fee of $5,000 applies.", "A fee of $5000 applies."),
        ("Notice of 30 days.", "Notice of 3 days."),
        ("Governed by WA law.", "Governed by NSW law."),
    ],
)
def test_a_material_edit_never_normalises_to_the_same_text(before: str, after: str) -> None:
    """The invariant: never report UNCHANGED for a clause that changed.

    A false CHANGED is noise a reader discards. A false UNCHANGED is a
    negotiation term that slipped through silently.
    """
    assert normalise(before) != normalise(after)


@pytest.mark.parametrize(
    "before, after",
    [
        ("The  Employer   must pay.", "The Employer must pay."),
        ("The Employer must pay.\n", "The Employer must pay."),
        ("The Employer\tmust pay.", "The Employer must pay."),
    ],
)
def test_whitespace_only_differences_normalise_away(before: str, after: str) -> None:
    assert normalise(before) == normalise(after)


from michael.contracts import ClauseChange, ComparisonReport, compare

V1 = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. PAYMENT
The Client must pay within 30 days of invoice.

3. TERMINATION
Either party may terminate on 14 days notice.
"""

V2_EDITED = V1.replace("within 30 days", "within 60 days")

V2_ADDED = V1 + "\n4. CONFIDENTIALITY\nEach party must keep information confidential.\n"

V2_RENUMBERED = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. TERMINATION
Either party may terminate on 14 days notice.

3. PAYMENT
The Client must pay within 30 days of invoice.
"""


def test_an_edited_clause_is_reported_changed_with_its_id() -> None:
    report = compare(V1, V2_EDITED)
    changed = [c for c in report.changes if c.status == "CHANGED"]
    assert len(changed) == 1
    assert changed[0].clause_id_before == "2"
    assert changed[0].clause_id_after == "2"


def test_an_edited_clause_shows_what_changed() -> None:
    report = compare(V1, V2_EDITED)
    rendered = " ".join(report.changes[0].diff)
    assert "30" in rendered and "60" in rendered


def test_an_added_clause_has_no_before_id() -> None:
    added = [c for c in compare(V1, V2_ADDED).changes if c.status == "ADDED"]
    assert len(added) == 1
    assert added[0].clause_id_before is None
    assert added[0].clause_id_after == "4"


def test_a_removed_clause_has_no_after_id() -> None:
    removed = [c for c in compare(V2_ADDED, V1).changes if c.status == "REMOVED"]
    assert len(removed) == 1
    assert removed[0].clause_id_after is None


def test_a_renumbered_but_identical_clause_is_moved_not_changed() -> None:
    statuses = {c.status for c in compare(V1, V2_RENUMBERED).changes}
    assert "MOVED" in statuses
    assert "CHANGED" not in statuses


def test_identical_documents_report_no_changes() -> None:
    report = compare(V1, V1)
    assert report.changes == ()
    assert report.unchanged > 0


def test_the_report_has_nowhere_to_put_a_verdict() -> None:
    """MICHAEL.md: Michael does not certify a clause in either direction."""
    fields = set(ClauseChange.__dataclass_fields__) | set(ComparisonReport.__dataclass_fields__)
    assert not fields & {"risk", "severity", "score", "recommendation", "verdict"}


# --- The conservation invariant, against a real document -------------------
#
# Every test above uses a fixture of a few lines with unique clause numbers,
# so no clause id ever repeats and the comparison's own bookkeeping is never
# exercised. A real contract repeats ids constantly: a numbered table of
# contents, an annexed deed that restarts at 1, a front-matter block also
# numbered 1. These tests are the real-document gate for the comparison layer,
# the way Task 3's evidence file is for the splitter.

REAL_CONTRACT = (
    pathlib.Path(__file__).parent / "fixtures" / "contracts"
    / "02_wa_gov_general_conditions_consultancy_agreement.docx"
)


def _real_text() -> str:
    from michael.contract_text import contract_text

    return contract_text(REAL_CONTRACT.read_bytes(), origin=REAL_CONTRACT.name)


def test_a_real_contract_repeats_clause_ids() -> None:
    """The premise of the two tests below. If this ever fails they prove nothing."""
    clauses = split_clauses(_real_text())
    ids = [c.clause_id for c in clauses]
    assert len(ids) > len(set(ids)), "fixture no longer repeats ids; these tests are void"


def test_comparing_a_real_document_with_itself_accounts_for_every_clause() -> None:
    """No clause may be lost between splitting and reporting.

    Keying the comparison on clause_id alone silently discarded every repeated
    id: 153 clauses became 137, and an edit inside one of the 16 discarded
    clauses was reported as no change at all.
    """
    text = _real_text()
    expected = len(split_clauses(text))
    report = compare(text, text)
    assert report.changes == ()
    assert report.unchanged == expected


def test_an_edit_inside_a_repeated_id_clause_is_never_silent() -> None:
    """The invariant, on the document that broke it.

    A false CHANGED is noise. A false UNCHANGED is a negotiated term that
    slipped through, and this is the case that produced one.
    """
    import collections

    text = _real_text()
    clauses = split_clauses(text)
    counts = collections.Counter(c.clause_id for c in clauses)

    for clause_id, count in counts.items():
        if count < 2:
            continue
        shadowed = next(c for c in clauses if c.clause_id == clause_id)
        head, _, body = shadowed.text.partition("\n")
        if len(body.split()) < 6:
            continue
        # Change one word deep in the body. No heading is touched.
        target = body.split()[4]
        edited_clause = head + "\n" + body.replace(target, "SUBSTITUTED", 1)
        edited = text[: shadowed.char_start] + edited_clause + text[shadowed.char_end :]

        report = compare(text, edited)
        assert report.changes, (
            f"silent miss: edited {target!r} inside the first clause with "
            f"repeated id {clause_id!r} and nothing was reported"
        )
        return

    raise AssertionError("no repeated-id clause with an editable body was found")


def test_no_clause_vanishes_between_splitting_and_reporting() -> None:
    """The general property, over a real document with a real edit.

    The splitter guarantees nothing is dropped. The comparison layer did not
    inherit that guarantee: keying on clause_id alone discarded 16 of 153
    clauses before comparing anything. Every clause on each side must be
    accounted for exactly once - as unchanged, or in some change.
    """
    text = _real_text()
    before_count = len(split_clauses(text))
    edited = text.replace(
        "The Consultant must take out and maintain insurance",
        "The Consultant must not take out or maintain insurance",
        1,
    )
    assert edited != text, "anchor for the edit is missing from the fixture"
    after_count = len(split_clauses(edited))

    report = compare(text, edited)
    accounted_before = report.unchanged + sum(
        1 for c in report.changes if c.clause_id_before is not None
    )
    accounted_after = report.unchanged + sum(
        1 for c in report.changes if c.clause_id_after is not None
    )
    assert accounted_before == before_count
    assert accounted_after == after_count


# --- autojunk: a 266-character real pair that scored 0.1692 while 99% ------
# identical ------------------------------------------------------------------
#
# From the WA Government consultancy agreement fixture's DISPUTE RESOLUTION
# clause (calibration/labelled_clause_pairs.json). Edited in exactly two
# numbers - "10 Business Days" to "15", "20" to "30" - nothing else. With
# difflib.SequenceMatcher's default autojunk=True, this pair scored 0.1692:
# below SIMILARITY_THRESHOLD, so a real two-number edit would have been
# reported ADDED plus REMOVED rather than CHANGED with a word diff. WORKER-2
# found this during Task 10 and escalated rather than fixing it in this file.

DISPUTE_CLAUSE_BEFORE = (
    "Within 10 Business Days after service of a notice of dispute, the parties "
    "must confer at least once to resolve the dispute. If the dispute has not "
    "been resolved within 20 Business Days of service of the notice of "
    "dispute, either party may commence legal proceedings."
)
DISPUTE_CLAUSE_AFTER = (
    "Within 15 Business Days after service of a notice of dispute, the parties "
    "must confer at least once to resolve the dispute. If the dispute has not "
    "been resolved within 30 Business Days of service of the notice of "
    "dispute, either party may commence legal proceedings."
)


def test_a_clause_edited_in_two_numbers_clears_the_similarity_threshold() -> None:
    """The autojunk regression, pinned to the exact pair that exposed it.

    A pair 99.25% identical by SequenceMatcher(autojunk=False).ratio() must
    not fall below SIMILARITY_THRESHOLD because autojunk marked the common
    letters of ordinary legal prose as too "popular" to anchor a match on
    once the clause passed 200 characters.
    """
    assert len(DISPUTE_CLAUSE_BEFORE) == 266
    report = compare(
        f"1. DISPUTE RESOLUTION\n{DISPUTE_CLAUSE_BEFORE}\n",
        f"1. DISPUTE RESOLUTION\n{DISPUTE_CLAUSE_AFTER}\n",
    )
    changed = [c for c in report.changes if c.status == "CHANGED"]
    assert len(changed) == 1, (
        "the edited clause was not paired as CHANGED - it fell below "
        "SIMILARITY_THRESHOLD and was reported ADDED plus REMOVED instead"
    )
    rendered = " ".join(changed[0].diff)
    assert "10" in rendered and "15" in rendered
    assert "20" in rendered and "30" in rendered
