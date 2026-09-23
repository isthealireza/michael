"""The output check, exercised on the shapes real models produce.

Every fixture below is reduced from an output in bench/results or
bench/results-classification-fix, not invented: the point of the check is
the gap between what MICHAEL.md says and what a model writes, and an
invented fixture only ever shows the gap the author already knew about.
"""

from __future__ import annotations

from michael.output_check import check, citation_fidelity

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
    assert [f.rule for f in check("Here you go.\n\n" + CLEAN)] == ["classification_position"]


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
        body = CLEAN.replace(
            "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.", claim
        )
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
        "Nothing in this document is a statement that the clause is compliant.",
        "This is not a certification that the clause is compliant.",
        "Michael does not certify that the clause is compliant.",
    ):
        body = CLEAN.replace(
            "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.",
            disclaimer,
        )
        assert check(body) == [], disclaimer


def test_an_unrelated_negation_in_a_previous_sentence_does_not_hide_a_certification() -> None:
    """W3-S1 regression: the disclaimer guard must not fire on any negation
    it finds nearby - only on the specific frame MICHAEL.md requires,
    directly wrapped around the certified words. A negation about something
    else entirely, one sentence earlier, is not that frame.
    """
    body = CLEAN.replace(
        "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.",
        "There is no finding that supports the alternative view. This clause complies with s 117.",
    )
    assert [f.rule for f in check(body)] == ["certification"]


def test_an_unrelated_negation_in_the_same_sentence_does_not_hide_a_certification() -> None:
    """W3-S1 regression, same-sentence form: the disclaiming words must flow
    straight into the certified clause, not merely share a sentence with it.
    """
    body = CLEAN.replace(
        "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.",
        "Nothing in the earlier email is a claim that pricing was fixed; "
        "this clause complies with s 117.",
    )
    assert [f.rule for f in check(body)] == ["certification"]


def test_a_negation_in_a_previous_sentence_does_not_cross_the_sentence_boundary() -> None:
    """W3-S2: the lookback is bounded to the sentence, not a character
    budget. Even a disclaiming-shaped phrase in the previous sentence must
    not suppress a certification in this one.
    """
    body = CLEAN.replace(
        "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.",
        "This is not a statement that the earlier clause was drafted badly. "
        "This clause complies with s 117.",
    )
    assert [f.rule for f in check(body)] == ["certification"]


def test_a_numbered_clause_is_a_clause() -> None:
    """W3-S4 regression. The subject used to be the bare bigram
    "this|the|that clause", so a drafting answer that named the clause it had
    just written - the normal way to name one - sailed past the guard.

    Reported as a negation in the previous sentence suppressing the
    certification. It was not: CERTIFIES never matched these strings at all.
    """
    for claim in (
        "Clause 3 is not unusual. Clause 7 is compliant with s 62.",
        "Clause 12.3 does not comply with s 62.",
        "cl 4A is compliant with the NES.",
    ):
        body = CLEAN.replace(
            "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.", claim
        )
        assert [f.rule for f in check(body)] == ["certification"], claim


def test_a_modified_clause_is_still_a_clause() -> None:
    """The other half of W3-S4: a determiner separated from the noun by what
    the clause is about. "The termination clause is compliant" is the same
    assertion as "the clause is compliant" and was not caught.
    """
    for claim in (
        "Nothing here is a compliance opinion. The termination clause is compliant.",
        "That indemnity clause does not comply.",
        "Your termination clause is compliant.",
    ):
        body = CLEAN.replace(
            "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.", claim
        )
        assert [f.rule for f in check(body)] == ["certification"], claim


def test_widening_the_subject_did_not_swallow_the_disclaimer_s_own_that() -> None:
    """Caught while widening the subject, and the reason a modifier may not
    be a determiner.

    In "not a statement THAT THE CLAUSE is compliant", the first "that" is a
    conjunction. Allow it to open the clause subject and the match begins one
    word too early, eats the word DISCLAIMS anchors on, and every correct
    output - which must carry this sentence - gets flagged as a
    certification. The match has to start at "the clause".
    """
    for disclaimer in (
        "Its content is based on the provisions cited; that is not a "
        "statement that the clause is compliant.",
        "Its clauses are based on the provisions cited; that is not a "
        "statement that any clause is compliant.",
        "Michael does not certify that the clause is compliant.",
    ):
        body = CLEAN.replace(
            "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.",
            disclaimer,
        )
        assert check(body) == [], disclaimer


def test_a_statement_about_the_law_is_not_a_certification() -> None:
    """Michael must still be able to say what the law requires."""
    body = CLEAN.replace(
        "Under the Fair Work Act 2009 (Cth) s 117 an employer must give written notice.",
        "An employer complies with s 117 by giving written notice.",
    )
    assert check(body) == []


def test_a_finding_names_the_rule_and_says_what_is_wrong() -> None:
    finding = check("hello")[0]
    assert finding.rule == "classification"
    assert finding.detail


def test_quoted_operative_words_must_exist_in_retrieved_provisions() -> None:
    provisions = [{"text": "An employer must give written notice of termination."}]
    clean = 'The Act says "An employer must give written notice of termination."'
    assert citation_fidelity(clean, provisions) == []

    invented = 'The Act says "An employer must give four weeks of written notice."'
    findings = citation_fidelity(invented, provisions)
    assert [finding.rule for finding in findings] == ["citation_fidelity"]


def test_short_quoted_labels_are_not_treated_as_legal_quotes() -> None:
    assert citation_fidelity('See "s 117".', []) == []


# The s 117(3) notice-period table, as it sits in provisions.text (row 73073).
# Reduced from the corpus read recorded in
# .orca/reports/2026-09-22-worker3-citation-integrity-investigation.md.
S117_TABLE = (
    "Employee's period of continuous service with the employer at the end "
    "of the day the notice is given Period 1 Not more than 1 year 1 week "
    "2 More than 1 year but not more than 3 years 2 weeks "
    "3 More than 3 years but not more than 5 years 3 weeks "
    "4 More than 5 years 4 weeks"
)


def test_a_misquoted_table_value_is_caught_even_though_the_quote_is_short() -> None:
    """The live defect: a correct citation wrapped around a wrong number.

    One of three isolated calls quoting s 117(3) returned "4 weeks" for the
    tier the table gives as "3 weeks". The quote is seven characters, so a
    minimum-length rule aimed at skipping labels skipped the one thing in
    the answer a reader cannot check for themselves.
    """
    answer = (
        'The relevant table row states: "More than 3 years but not more than 5 years" - "4 weeks"'
    )
    findings = citation_fidelity(answer, [{"text": S117_TABLE}])
    assert [finding.rule for finding in findings] == ["citation_fidelity"]


def test_a_correctly_quoted_table_value_is_not_flagged() -> None:
    answer = (
        'The relevant table row states: "More than 3 years but not more than 5 years" - "3 weeks"'
    )
    assert citation_fidelity(answer, [{"text": S117_TABLE}]) == []


def test_a_value_quoted_from_the_wrong_row_of_the_same_table_is_caught() -> None:
    """The whole point of checking the pair rather than the value alone.

    "4 weeks" is genuinely in the s 117(3) table - it is the figure for more
    than 5 years - so a substring test over the provision text passes a quote
    that attaches it to the wrong tier. The tier and its value have to be
    adjacent in the source, not merely both present somewhere in it.
    """
    answer = '"More than 1 year but not more than 3 years" - "4 weeks"'
    findings = citation_fidelity(answer, [{"text": S117_TABLE}])
    assert [finding.rule for finding in findings] == ["citation_fidelity"]


def test_a_misquoted_value_inside_a_blockquote_is_caught() -> None:
    """Three of five live runs quoted the table as a blockquote, not in
    quotation marks. A check that only reads quotation marks sees nothing."""
    answer = (
        "The relevant table cell reads:\n\n"
        "> More than 3 years but not more than 5 years - 6 weeks\n"
    )
    findings = citation_fidelity(answer, [{"text": S117_TABLE}])
    assert [finding.rule for finding in findings] == ["citation_fidelity"]


def test_a_blockquoted_row_joined_by_a_dash_the_model_added_is_not_flagged() -> None:
    """The source table has no dash between the tier and its value; the model
    inserts one to render the row. Checking the whole line as a literal
    substring would fail every correctly quoted row."""
    answer = (
        "The relevant table cell reads:\n\n"
        "> More than 3 years but not more than 5 years - 3 weeks\n"
    )
    assert citation_fidelity(answer, [{"text": S117_TABLE}]) == []


def test_a_blockquoted_row_joined_by_an_em_dash_is_not_flagged() -> None:
    """Run 3 of the five live reproduction calls wrote exactly this line.

    A model renders the cell boundary as an em dash far more often than as a
    hyphen, so a separator pattern that only knows the hyphen splits nothing
    and condemns a faithfully quoted row.
    """
    answer = (
        "The relevant table cell (Period 3) reads:\n\n"
        "> More than 3 years but not more than 5 years — 3 weeks\n"
    )
    assert citation_fidelity(answer, [{"text": S117_TABLE}]) == []


def test_a_quote_differing_only_in_dashes_and_smart_quotes_is_not_flagged() -> None:
    provisions = [{"text": "a period of 12 months - or, where the employee agrees, 6 months"}]
    answer = "The Act allows “a period of 12 months — or, where the employee agrees”."
    assert citation_fidelity(answer, provisions) == []
