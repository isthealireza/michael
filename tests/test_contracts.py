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
