"""The acceptance test.

Input:  "I need a casual employment contract between Company X and Mr Y,
         with conditions A and B."

Expected: classified BOTH; the relevant Fair Work provisions retrieved with
citations; the contract produced from the template with every unknown marked
[MISSING]; ending with OPEN ITEMS, VERIFY BEFORE USE and the closing notice.

This runs offline against fixture provisions that carry real citation metadata
and no statutory text (see tests/fixtures.py for why). The variant marked
``integration`` runs the same flow against a real ingested corpus and asserts
on retrieved statutory text.
"""

from __future__ import annotations

import pytest

from michael import retrieve, tools
from michael.draft import CLOSING_NOTICE
from tests.fixtures import covered_result

REQUEST = "I need a casual employment contract between Company X and Mr Y, with conditions A and B."

#: Only what the user actually gave. Nothing else may be filled in.
FACTS = {
    "EMPLOYER_NAME": "Company X",
    "EMPLOYEE_NAME": "Mr Y",
    "CONDITION_A": "Condition A",
    "CONDITION_B": "Condition B",
}


@pytest.fixture
def michael(monkeypatch: pytest.MonkeyPatch, real_templates: None) -> None:
    monkeypatch.setattr(retrieve, "search", lambda q, **kw: covered_result(q))


def test_the_request_is_classified_both(michael: None) -> None:
    result = tools.classify_request(REQUEST)
    assert result["classification"] == "BOTH"
    assert result["domain"] == "employment"
    assert result["jurisdiction_filter"] == ["commonwealth"]


def test_fair_work_provisions_are_retrieved_with_citations(michael: None) -> None:
    payload = tools.search_provisions(REQUEST)
    assert payload["covered"] is True
    assert payload["provisions"]
    for provision in payload["provisions"]:
        assert provision["citation"] == "Fair Work Act 2009 (Cth)"
        assert provision["section_number"]
        assert provision["snapshot_date"]
        assert provision["pinpoint"].startswith("Fair Work Act 2009 (Cth) s ")
        assert "snapshot" in provision["pinpoint"]


def test_the_contract_comes_from_the_template(michael: None) -> None:
    result = tools.draft_document(REQUEST, facts=FACTS)
    assert result["no_template"] is False
    assert result["template"].endswith("casual_employment_contract.md")
    document = result["document"]
    assert "CASUAL EMPLOYMENT CONTRACT" in document
    assert "Company X" in document
    assert "Mr Y" in document
    assert "Condition A" in document
    assert "Condition B" in document
    assert "{{" not in document


@pytest.mark.parametrize(
    "item",
    [
        "employer abn",
        "employer address",
        "employee address",
        "commencement date",
        "position title",
        "modern award name",
        "classification level",
        "base hourly rate",
        "casual loading percentage",
        "superannuation fund",
    ],
)
def test_every_unknown_is_marked_missing_and_never_invented(michael: None, item: str) -> None:
    result = tools.draft_document(REQUEST, facts=FACTS)
    assert f"[MISSING: {item}]" in result["document"]
    assert item in result["open_items"]


def test_the_draft_cites_what_it_is_based_on(michael: None) -> None:
    result = tools.draft_document(REQUEST, facts=FACTS)
    assert result["citations"]
    assert "## BASED ON" in result["document"]
    assert "Fair Work Act 2009 (Cth) s 15A" in result["document"]


def test_no_wa_legislation_is_cited_for_national_system_employment(michael: None) -> None:
    result = tools.draft_document(REQUEST, facts=FACTS)
    assert all("(WA)" not in citation for citation in result["citations"])
    assert "Fair Work Act 2009 (Cth)" in result["document"]


def test_the_output_ends_with_the_three_mandatory_blocks(michael: None) -> None:
    document = tools.draft_document(REQUEST, facts=FACTS)["document"]

    open_items = document.index("## OPEN ITEMS")
    verify = document.index("## VERIFY BEFORE USE")
    notice = document.index(CLOSING_NOTICE)
    assert open_items < verify < notice
    assert document.rstrip().endswith(CLOSING_NOTICE)


def test_open_items_are_numbered_and_complete(michael: None) -> None:
    from michael.draft import unresolved

    result = tools.draft_document(REQUEST, facts=FACTS)
    document = result["document"]
    body = document[: document.index("## OPEN ITEMS")]

    # Every [MISSING] item in the contract body appears in the numbered list.
    for index, item in enumerate(result["open_items"], start=1):
        assert f"{index}. [MISSING: {item}]" in document
    assert set(unresolved(body)) == set(result["open_items"])


def test_no_clause_is_asserted_to_be_compliant(michael: None) -> None:
    """Compliance may be disclaimed, never asserted.

    Checked per sentence rather than per phrase: the template legitimately says
    "nothing in this document is a statement that any clause is compliant",
    which contains the words but makes the opposite claim.
    """
    import re

    document = tools.draft_document(REQUEST, facts=FACTS)["document"]
    # Unwrapped first: the template is hard-wrapped, so a disclaimer and the
    # words it disclaims can sit on different lines.
    unwrapped = " ".join(document.split())
    negations = ("not ", "nothing", "no ", "never", "n't")
    for sentence in re.split(r"(?<=[.!?])\s+", unwrapped):
        lowered = sentence.lower()
        if "compliant" in lowered or "complies" in lowered:
            assert any(word in lowered for word in negations), (
                f"compliance asserted without qualification: {sentence!r}"
            )
