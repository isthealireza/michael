import pathlib
from pathlib import Path

import pytest

from michael.contracts import split_clauses
from michael.elements import Element, ElementConfigError, elements_for, load_elements


def test_the_shipped_file_loads() -> None:
    elements = load_elements()
    assert elements
    assert all(isinstance(e, Element) for e in elements)


def test_every_element_id_is_unique() -> None:
    ids = [e.id for e in load_elements()]
    assert len(ids) == len(set(ids))


def test_every_element_has_synonyms() -> None:
    assert all(e.synonyms for e in load_elements())


def test_employment_adds_to_the_universal_list_rather_than_replacing_it() -> None:
    universal = {e.id for e in elements_for()}
    employment = {e.id for e in elements_for("employment")}
    assert universal < employment
    assert "modern_award" in employment
    assert "governing_law" in employment


def test_an_unknown_domain_falls_back_to_universal() -> None:
    assert {e.id for e in elements_for("nonsense")} == {e.id for e in elements_for()}


def test_a_duplicate_id_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "elements.yaml"
    bad.write_text(
        "universal:\n"
        "  - {id: x, label: X, placeholder: X, synonyms: [x], basis: drafting convention}\n"
        "  - {id: x, label: Y, placeholder: Y, synonyms: [y], basis: drafting convention}\n",
        encoding="utf-8",
    )
    with pytest.raises(ElementConfigError, match="duplicate"):
        load_elements(bad)


def test_an_element_with_no_synonyms_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "elements.yaml"
    bad.write_text(
        "universal:\n"
        "  - {id: x, label: X, placeholder: X, synonyms: [], basis: drafting convention}\n",
        encoding="utf-8",
    )
    with pytest.raises(ElementConfigError, match="synonyms"):
        load_elements(bad)


from michael.elements import CompletenessReport, ElementFinding, audit

WITH_GOVERNING_LAW = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. GOVERNING LAW
This agreement is governed by the laws of Western Australia.
"""

WITHOUT_GOVERNING_LAW = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. PAYMENT
The Client must pay within 30 days.
"""

MENTIONED_ONLY_IN_BODY = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd, and the governing law of any
dispute is a matter the parties will address elsewhere.
"""


def _state(report: CompletenessReport, element_id: str) -> str:
    return next(f.state for f in report.findings if f.element_id == element_id)


def test_an_element_with_its_own_clause_heading_is_present() -> None:
    report = audit(WITH_GOVERNING_LAW)
    assert _state(report, "governing_law") == "PRESENT"


def test_an_element_that_appears_nowhere_is_absent() -> None:
    report = audit(WITHOUT_GOVERNING_LAW)
    assert _state(report, "governing_law") == "ABSENT"


def test_an_element_mentioned_only_in_body_text_is_uncertain() -> None:
    """Three states, not two: a false ABSENT costs the report its credibility."""
    report = audit(MENTIONED_ONLY_IN_BODY)
    assert _state(report, "governing_law") == "UNCERTAIN"


def test_a_present_finding_names_the_clause_it_was_found_in() -> None:
    finding = next(f for f in audit(WITH_GOVERNING_LAW).findings if f.element_id == "governing_law")
    assert finding.clause_id == "2"


def test_the_domain_adds_its_own_elements_to_the_report() -> None:
    ids = {f.element_id for f in audit(WITH_GOVERNING_LAW, domain="employment").findings}
    assert "modern_award" in ids


def test_a_finding_has_nowhere_to_put_a_verdict() -> None:
    fields = set(ElementFinding.__dataclass_fields__) | set(CompletenessReport.__dataclass_fields__)
    assert not fields & {"risk", "severity", "score", "recommendation", "verdict"}


# --- The real-document gate, at this layer too ------------------------------
#
# Every test above uses a small synthetic document, written to exercise one
# state in isolation. That is exactly the shape that let the compare() defect
# through, and before it the monotonic floor and the splitter's title bug: "A
# test that cannot fail on the documents the feature is for is not evidence"
# (.orca/WORKER.md). A gate at one layer does not protect the layer above it -
# split_clauses() and compare() each have their own real-document gate now;
# audit() had none until this file.
#
# Two real defects were found running audit() over the three committed
# fixtures and reading the output by eye, before any assertion below was
# written:
#
# 1. Clause.text/heading for a one-line numbered clause (e.g. "11.1 This
#    document records the whole agreement between the parties...") puts the
#    clause's entire first sentence into Clause.heading, by Task 2's design.
#    Matching synonyms against that sentence, not a title, gave the `parties`
#    element a second spurious "heading hit" on the real casual-employment
#    fixture - clause "1" (PARTIES) was the genuine one, and PRESENT became
#    UNCERTAIN. Fixed with a length-and-punctuation heuristic in
#    `_heading_text` (see its docstring): a genuine heading is a label, not a
#    sentence, and does not end in a full stop.
# 2. Naive substring matching let the `term` element's synonym "term" match
#    inside "termination" - a different element - so any document with a
#    TERMINATION heading falsely produced a second heading hit for `term`.
#    Fixed with `_hits`, a whole-phrase boundary check mirroring
#    `michael.domains._hits`, which solved the identical problem for keyword
#    routing.
#
# A third gap - not a code defect, a synonym-coverage gap - surfaced on the
# real WA Form 1AA fixture: its "IF A DISPUTE CANNOT BE RESOLVED" heading did
# not match the `dispute_resolution` element, because none of its synonyms
# were the bare word "dispute". `elements.yaml` was widened accordingly.

REAL_FIXTURES = tuple(
    sorted((pathlib.Path(__file__).parent / "fixtures" / "contracts").iterdir())
)
ELEMENTS_YAML = pathlib.Path(__file__).parent.parent / "elements.yaml"


def _fixture_text(path: Path) -> str:
    from michael.contract_text import contract_text

    return contract_text(path.read_bytes(), origin=path.name)


def test_every_finding_resolves_to_exactly_one_of_three_states() -> None:
    """The shape guarantee the Literal type promises, checked on real output."""
    for fixture in REAL_FIXTURES:
        text = _fixture_text(fixture)
        for domain in (None, "employment"):
            report = audit(text, domain=domain, path=ELEMENTS_YAML)
            for finding in report.findings:
                assert finding.state in ("PRESENT", "ABSENT", "UNCERTAIN"), (
                    f"{fixture.name} domain={domain}: {finding.element_id} "
                    f"has an invalid state {finding.state!r}"
                )


def test_no_present_finding_names_a_clause_id_that_does_not_exist() -> None:
    """A PRESENT finding's clause_id must be a real clause in that document."""
    for fixture in REAL_FIXTURES:
        text = _fixture_text(fixture)
        real_ids = {c.clause_id for c in split_clauses(text)}
        for domain in (None, "employment"):
            report = audit(text, domain=domain, path=ELEMENTS_YAML)
            for finding in report.findings:
                if finding.state == "PRESENT":
                    assert finding.clause_id in real_ids, (
                        f"{fixture.name} domain={domain}: {finding.element_id} "
                        f"is PRESENT at clause_id={finding.clause_id!r}, which "
                        f"is not one of this document's own clause ids"
                    )


def test_an_element_whose_synonym_appears_in_the_text_is_never_reported_absent() -> None:
    """The general property behind the ORCHESTRATOR's named check.

    Verified by eye first: the WA Form 1AA fixture contains none of the
    governing_law synonyms anywhere - ABSENT is the correct report for it,
    confirmed directly against the raw text, not assumed. This test does not
    encode that one fact; it encodes the property that would have caught the
    two defects above and would catch a regression of either: if some synonym
    of an element is present anywhere in the document's raw text, the element
    must never be reported ABSENT, on any of the three real fixtures.
    """
    import re

    for fixture in REAL_FIXTURES:
        text = _fixture_text(fixture)
        lowered = text.lower()
        for domain in (None, "employment"):
            report = audit(text, domain=domain, path=ELEMENTS_YAML)
            elements = {e.id: e for e in elements_for(domain, path=ELEMENTS_YAML)}
            for finding in report.findings:
                element = elements[finding.element_id]
                appears = any(
                    re.search(rf"(?<![a-z0-9]){re.escape(s)}(?![a-z0-9])", lowered)
                    for s in element.synonyms
                )
                if appears:
                    assert finding.state != "ABSENT", (
                        f"{fixture.name} domain={domain}: {finding.element_id} "
                        f"is ABSENT but a synonym of it appears in the raw text"
                    )


def test_dispute_resolution_is_present_on_the_form_1aa_tenancy_fixture() -> None:
    """The specific case named in the dispatch, pinned down.

    Form 1AA has a clause literally headed "IF A DISPUTE CANNOT BE RESOLVED".
    Before elements.yaml added the bare "dispute" synonym, this reported
    UNCERTAIN at the wrong clause (NOTICES, the first body-only match found)
    rather than PRESENT at its own heading.
    """
    fixture = next(f for f in REAL_FIXTURES if "form1aa" in f.name)
    report = audit(_fixture_text(fixture), path=ELEMENTS_YAML)
    finding = next(f for f in report.findings if f.element_id == "dispute_resolution")
    assert finding.state == "PRESENT"
    assert finding.clause_id == "IF A DISPUTE CANNOT BE RESOLVED"


def test_the_casual_employment_fixture_does_not_regress_to_uncertain() -> None:
    """The document that exposed defect 1 and defect 2, pinned down.

    Before the `_heading_text`/`_hits` fixes, `parties`, `payment`,
    `termination` and `modern_award` each read UNCERTAIN on this real
    document, not because the document is ambiguous about them but because a
    coincidental word inside another clause's one-line sentence-as-heading,
    or a substring collision between synonyms, produced a spurious second
    heading hit.
    """
    fixture = next(f for f in REAL_FIXTURES if f.name.startswith("01_"))
    report = audit(_fixture_text(fixture), domain="employment", path=ELEMENTS_YAML)
    by_id = {f.element_id: f for f in report.findings}
    assert by_id["parties"].state == "PRESENT"
    assert by_id["payment"].state == "PRESENT"
    assert by_id["termination"].state == "PRESENT"
    assert by_id["modern_award"].state == "PRESENT"
