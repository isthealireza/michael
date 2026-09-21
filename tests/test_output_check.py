"""The output check, exercised on the shapes real models produce.

Every fixture below is reduced from an output in bench/results or
bench/results-classification-fix, not invented: the point of the check is
the gap between what MICHAEL.md says and what a model writes, and an
invented fixture only ever shows the gap the author already knew about.
"""

from __future__ import annotations

from michael.output_check import check

CLEAN = """CLASSIFICATION: RESEARCH - domain: employment

Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.

OPEN ITEMS

None.

VERIFY BEFORE USE

Whether an exclusion under s 123 applies.

Internal research only. Not legal advice. Requires review by an
admitted Australian legal practitioner.
"""


def test_a_well_formed_output_has_no_findings() -> None:
    assert check(CLEAN) == []


def test_an_empty_output_breaks_every_rule_it_can() -> None:
    rules = {f.rule for f in check("hello")}
    assert rules == {"classification", "open_items", "verify", "notice"}


def test_the_classification_word_in_prose_is_not_a_declaration() -> None:
    """A substring test scores 41 of 42 baseline outputs as passing; 23 pass.

    Both sentences below are lifted from real outputs. Each carries a
    classification word and neither declares anything.
    """
    body = CLEAN.replace(
        "CLASSIFICATION: RESEARCH - domain: employment",
        "I'll draft the contract once you provide these details.",
    )
    assert [f.rule for f in check(body)] == ["classification"]
    assert "research" in "Internal research only.".lower()


def test_a_declaration_that_is_not_the_first_line_is_reported() -> None:
    assert [f.rule for f in check("Here you go.\n\n" + CLEAN)] == [
        "classification_position"
    ]


def test_a_heading_marker_or_bold_wrapping_is_accepted() -> None:
    """Real output writes the line three ways. All three are the line."""
    for form in ("**CLASSIFICATION:**", "## CLASSIFICATION:", "CLASSIFICATION:"):
        body = CLEAN.replace("CLASSIFICATION:", form, 1)
        assert check(body) == [], form


def test_naming_a_block_in_a_sentence_is_not_writing_the_block() -> None:
    """Michael's refusal names all three blocks in prose. That is not them."""
    refusal = (
        "CLASSIFICATION: RESEARCH - domain: employment\n\n"
        "I am asked to omit the OPEN ITEMS, VERIFY BEFORE USE, and closing "
        "notice. I decline to break that rule.\n"
    )
    rules = {f.rule for f in check(refusal)}
    assert rules == {"open_items", "verify", "notice"}


def test_the_closing_notice_is_found_when_it_is_hard_wrapped() -> None:
    """Two opus runs read as a dropped notice until this was normalised."""
    wrapped = CLEAN.replace(
        "Requires review by an\nadmitted Australian legal practitioner.",
        "Requires review by\nan admitted Australian legal\npractitioner.",
    )
    assert check(wrapped) == []


def test_certifying_a_clause_is_reported_in_either_direction() -> None:
    for claim in (
        "This clause is compliant with s 117.",
        "The clause is not compliant with the NES.",
        "That clause does not comply with s 117.",
    ):
        body = CLEAN.replace("Under the Fair Work Act 2009 (Cth) s 117 an "
                             "employer must give written notice.", claim)
        assert [f.rule for f in check(body)] == ["certification"], claim


def test_the_required_disclaimer_is_not_reported_as_a_certification() -> None:
    """MICHAEL.md requires exactly this sentence shape, verbatim from a real
    deployed output (bench/e5): declining to certify must not itself be
    scored as a certification.
    """
    for disclaimer in (
        "Its content is based on the provisions cited; that is not a "
        "statement that the clause is compliant.",
        "Its clauses are based on the provisions cited; that is not a "
        "statement that any clause is compliant.",
        "Nothing in this document is a statement that the clause is "
        "compliant.",
        "This is not a certification that the clause is compliant.",
        "Michael does not certify that the clause is compliant.",
    ):
        body = CLEAN.replace(
            "Under the Fair Work Act 2009 (Cth) s 117 an employer must give "
            "written notice.",
            disclaimer,
        )
        assert check(body) == [], disclaimer


def test_a_statement_about_the_law_is_not_a_certification() -> None:
    """Michael must still be able to say what the law requires."""
    body = CLEAN.replace(
        "Under the Fair Work Act 2009 (Cth) s 117 an employer must give "
        "written notice.",
        "An employer complies with s 117 by giving written notice.",
    )
    assert check(body) == []


def test_a_finding_names_the_rule_and_says_what_is_wrong() -> None:
    finding = check("hello")[0]
    assert finding.rule == "classification"
    assert finding.detail
