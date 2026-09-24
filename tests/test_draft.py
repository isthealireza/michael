"""The [MISSING] rule and the mandatory closing blocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from michael.draft import (
    CLOSING_NOTICE,
    NO_TEMPLATE_LABEL,
    citations_of,
    closing_blocks,
    draft_from_template,
    fill,
    find_template,
    outline_without_template,
    unresolved,
    wrong_jurisdiction_warnings,
)
from tests.fixtures import (
    CROMWELL,
    FAIR_WORK_PROVISIONS,
    judgment_paragraph,
    provision,
)

TEMPLATE = """# Contract

Employer: {{EMPLOYER_NAME}} (ABN {{EMPLOYER_ABN}})
Employee: {{EMPLOYEE_NAME}}
Rate: {{BASE_HOURLY_RATE}}
Award: {{MODERN_AWARD_NAME}}
"""


def test_supplied_facts_are_used_and_the_rest_are_marked_missing() -> None:
    body, missing = fill(TEMPLATE, {"EMPLOYER_NAME": "Palm Vision Pty Ltd"})
    assert "Palm Vision Pty Ltd" in body
    assert "[MISSING: employer abn]" in body
    assert "[MISSING: base hourly rate]" in body
    assert "employer name" not in missing


def test_nothing_is_invented_when_no_facts_are_supplied() -> None:
    body, missing = fill(TEMPLATE, None)
    assert "{{" not in body
    assert set(missing) == {
        "employer name",
        "employer abn",
        "employee name",
        "base hourly rate",
        "modern award name",
    }


def test_a_blank_value_counts_as_missing() -> None:
    """An empty string in a contract reads as an answer, and it is not one."""
    body, missing = fill(TEMPLATE, {"EMPLOYER_ABN": "   "})
    assert "[MISSING: employer abn]" in body
    assert "employer abn" in missing


def test_missing_items_are_de_duplicated_in_order() -> None:
    body, missing = fill("{{A_B}} {{C_D}} {{A_B}}", None)
    assert missing == ("a b", "c d")
    assert body.count("[MISSING: a b]") == 2


def test_unresolved_finds_every_missing_item_in_rendered_text() -> None:
    body, _ = fill(TEMPLATE, None)
    assert "employer abn" in unresolved(body)


def test_closing_blocks_are_always_present_even_with_nothing_outstanding() -> None:
    rendered = closing_blocks(open_items=(), verify_before_use=())
    assert "## OPEN ITEMS" in rendered
    assert "## VERIFY BEFORE USE" in rendered
    assert CLOSING_NOTICE in rendered


def test_open_items_are_numbered() -> None:
    rendered = closing_blocks(open_items=("employer abn", "base hourly rate"), verify_before_use=())
    assert "1. [MISSING: employer abn]" in rendered
    assert "2. [MISSING: base hourly rate]" in rendered


def test_wa_legislation_retrieved_for_employment_is_flagged_not_cited() -> None:
    wa = provision(
        section_number="8",
        heading="Minimum conditions",
        jurisdiction="wa",
        citation="Minimum Conditions of Employment Act 1993 (WA)",
    )
    warnings = wrong_jurisdiction_warnings(domain="employment", provisions=(wa,))
    assert warnings
    assert "Fair Work Act 2009 (Cth)" in warnings[0]
    assert "not relied on" in warnings[0]


def test_wa_legislation_is_not_flagged_outside_employment() -> None:
    wa = provision(
        section_number="8",
        heading="Caveats",
        jurisdiction="wa",
        citation="Transfer of Land Act 1893 (WA)",
    )
    assert wrong_jurisdiction_warnings(domain="property", provisions=(wa,)) == ()


def test_pinpoint_carries_act_section_and_snapshot_date() -> None:
    pinpoint = FAIR_WORK_PROVISIONS[0].pinpoint()
    assert "Fair Work Act 2009 (Cth)" in pinpoint
    assert "s 15A" in pinpoint
    assert "snapshot 2026-07-01" in pinpoint


def test_no_template_produces_a_grounded_outline_rather_than_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from michael import config

    monkeypatch.setenv("MICHAEL_TEMPLATES_DIR", str(tmp_path / "templates"))
    config.settings.cache_clear()

    result = outline_without_template(
        request="I need a shareholders deed for two founders",
        provisions=FAIR_WORK_PROVISIONS,
        domain="corporate",
    )
    assert NO_TEMPLATE_LABEL in result.body
    assert result.no_template is True
    assert "[MISSING: party a name]" in result.body
    assert result.citations
    assert result.written_to is not None
    written = result.written_to.read_text(encoding="utf-8")
    assert "{{PARTY_A_NAME}}" in written
    assert "DRAFT template for review" in written


def test_no_template_outline_never_frames_a_provision_as_grounding_a_clause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """W3-S4: a NO-TEMPLATE outline can only ever retrieve the nearest match,
    which may be real but topically unrelated to the clause it sits under
    (an NDA outline citing WA settlement-agent conduct rules, say). The
    per-clause line must not read as "Based on" - a reader who only skims
    the clause body, not the VERIFY BEFORE USE footnote, must still see that
    the provision is unconfirmed.
    """
    from michael import config

    monkeypatch.setenv("MICHAEL_TEMPLATES_DIR", str(tmp_path / "templates"))
    config.settings.cache_clear()

    result = outline_without_template(
        request="I need a shareholders deed for two founders",
        provisions=FAIR_WORK_PROVISIONS,
        domain="corporate",
    )
    assert "- Based on:" not in result.body
    assert "Nearest retrieved provision (relevance not confirmed):" in result.body
    assert any("confirm each" in item.lower() for item in result.verify_before_use)


def test_the_real_casual_contract_template_is_found_and_states_what_it_is_based_on(
    real_templates: None,
) -> None:
    path = find_template(
        "I need a casual employment contract between Company X and Mr Y", domain="employment"
    )
    assert path is not None
    assert path.name == "casual_employment_contract.md"

    result = draft_from_template(
        template_path=path,
        facts=None,
        provisions=FAIR_WORK_PROVISIONS,
        domain="employment",
    )
    rendered = result.render()
    assert "## BASED ON" in rendered
    assert "Fair Work Act 2009 (Cth) s 15A" in rendered
    assert CLOSING_NOTICE in rendered
    assert "that is not a statement" in rendered.lower()


# --- both unit types -------------------------------------------------------


def test_citations_of_renders_each_unit_in_its_own_form() -> None:
    """`citations_of` is unit-agnostic: the form is `pinpoint()`'s business.
    What it must not do is flatten a judgment paragraph into a section."""
    citations = citations_of(
        (
            provision(section_number="15A", heading="Meaning of casual employee"),
            judgment_paragraph(number="6", heading="The Tang respondents are:"),
        )
    )
    assert citations[0].startswith("Fair Work Act 2009 (Cth) s 15A ")
    assert citations[1].startswith(f"{CROMWELL} at [6] ")


def test_citations_of_still_de_duplicates_across_unit_types() -> None:
    para = judgment_paragraph(number="6", heading="The Tang respondents are:")
    assert len(citations_of((para, para))) == 1


def test_an_outline_names_a_judgment_paragraph_as_a_paragraph() -> None:
    """An outline is read by a practitioner. "Nearest retrieved provision"
    against a judgment paragraph misdescribes the authority in the one line
    the reader relies on to tell legislation from case law."""
    draft = outline_without_template(
        request="fixture request about a judgment",
        provisions=(judgment_paragraph(number="6", heading="The Tang respondents are:"),),
        domain="employment",
        write_template=False,
    )
    assert "Nearest retrieved paragraph (relevance not confirmed):" in draft.body
    assert "Nearest retrieved provision" not in draft.body
    assert f"{CROMWELL} at [6] " in draft.body


def test_an_outline_still_names_legislation_a_provision() -> None:
    draft = outline_without_template(
        request="fixture request about an Act",
        provisions=(provision(section_number="15A", heading="Meaning of casual employee"),),
        domain="employment",
        write_template=False,
    )
    assert "Nearest retrieved provision (relevance not confirmed):" in draft.body


def test_an_outline_labels_an_unheaded_judgment_paragraph_by_its_paragraph_number() -> None:
    """A judgment paragraph has no heading of its own, so the fallback label
    must not read "Section 6"."""
    draft = outline_without_template(
        request="fixture request",
        provisions=(judgment_paragraph(number="6", heading=""),),
        domain="employment",
        write_template=False,
    )
    assert "### 1. Paragraph [6]" in draft.body
    assert "Section 6" not in draft.body
