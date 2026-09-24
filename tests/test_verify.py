"""Verification: claim splitting, JSON validation, and every fail-closed path.

The judge is mocked throughout. The mock is written as the *contract* rather
than as a rubber stamp: :func:`judge_saying` answers from a script keyed by
claim id and refuses to answer a claim the script does not mention, so a test
about the id-not-in-the-retrieved-set rule cannot pass because the mock
happened to return something agreeable. A mock that says SUPPORTED to any
input proves nothing about any of this.

One test at the bottom uses the real judge. It is marked ``integration`` so
the default run deselects it.
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Sequence

import pytest

from michael import draft as drafting
from michael import verify
from michael.verify import (
    Claim,
    JudgeCall,
    JudgeError,
    annotate,
    annotate_draft,
    build_prompt,
    check_judge_model,
    split_claims,
    validate_verdicts,
    verify_draft,
)
from tests.fixtures import FAIR_WORK_PROVISIONS, provision

PROVISION_IDS = [str(p.provision_id) for p in FAIR_WORK_PROVISIONS]


def judge_saying(script: dict[str, dict[str, object]]) -> verify.JudgeFn:
    """A judge that answers exactly what the script says, and nothing else.

    It reads the claim ids out of the prompt it is given and returns a verdict
    only for the ones the script names. A claim in the prompt but not in the
    script comes back with no entry at all, which is the shape a real judge
    dropping a claim produces - and which the validator must turn into
    UNSUPPORTED rather than into silence.
    """

    def judge(prompt: str) -> tuple[str, JudgeCall]:
        verdicts = [
            {"claim_id": claim_id, **answer}
            for claim_id, answer in script.items()
            if f"\n{claim_id} (" in prompt
        ]
        return json.dumps({"verdicts": verdicts}), JudgeCall(model="test/judge", seconds=0.01)

    return judge


def exploding_judge(prompt: str) -> tuple[str, JudgeCall]:
    raise JudgeError("provider timed out after 90s")


def claims_of(text: str) -> list[str]:
    produced, _ = split_claims(text)
    return [c.text for c in produced]


# --- step 1: splitting -----------------------------------------------------


def test_a_hard_wrapped_sentence_is_one_claim_not_three() -> None:
    """Templates wrap at 80 columns; a claim split on line breaks is a fragment."""
    text = (
        "## CLAUSE\n\n"
        "2.2 There is no firm advance commitment to continuing and indefinite\n"
        "work according to an agreed pattern of work between these parties.\n"
    )
    assert claims_of(text) == [
        "2.2 There is no firm advance commitment to continuing and indefinite work "
        "according to an agreed pattern of work between these parties."
    ]


def test_two_sentences_in_one_paragraph_are_two_claims() -> None:
    text = (
        "The employee is entitled to four weeks of paid annual leave each year. "
        "Leave accrues progressively and accumulates from year to year.\n"
    )
    assert claims_of(text) == [
        "The employee is entitled to four weeks of paid annual leave each year.",
        "Leave accrues progressively and accumulates from year to year.",
    ]


def test_headings_rules_and_label_lines_are_not_claims() -> None:
    text = (
        "# CASUAL EMPLOYMENT CONTRACT\n\n"
        "---\n\n"
        "**Employer:** Palm Vision Pty Ltd of 1 Example Street Perth\n\n"
        "Signed for and on behalf of the Employer:\n\n"
        "The employer must give the employee written notice of the termination day.\n"
    )
    assert claims_of(text) == [
        "The employer must give the employee written notice of the termination day."
    ]


def test_a_sentence_with_an_unsupplied_value_is_skipped_and_counted() -> None:
    """It is already in front of the reader under OPEN ITEMS; judging it adds noise.

    The count is asserted too: a skip nobody can see is a silent one, and the
    VERIFICATION section reports this number precisely so it is not.
    """
    text = (
        "The employment is covered by [MISSING: modern award name] at all times.\n\n"
        "The employee may refuse unreasonable additional hours beyond 38 in a week.\n"
    )
    produced, skipped = split_claims(text)
    assert [c.text for c in produced] == [
        "The employee may refuse unreasonable additional hours beyond 38 in a week."
    ]
    assert skipped == 1


def test_the_closing_blocks_and_citation_list_are_not_claims() -> None:
    """Everything draft.py appends after the body is generated, not drafted."""
    text = (
        "The employer must give the employee written notice of the termination day.\n\n"
        "## BASED ON\n- Fair Work Act 2009 (Cth) s 117 (snapshot 2026-07-01)\n\n"
        "## OPEN ITEMS\n1. [MISSING: employer name]\n\n"
        "## VERIFY BEFORE USE\n- Whether this draft suits these parties and these facts.\n\n"
        + drafting.CLOSING_NOTICE
    )
    assert claims_of(text) == [
        "The employer must give the employee written notice of the termination day."
    ]


def test_a_sentence_about_the_document_is_not_a_claim_about_the_law() -> None:
    text = (
        "Nothing in this document is a statement that any clause is compliant.\n\n"
        "This document records the whole agreement between the parties about it.\n"
    )
    assert claims_of(text) == []


def test_the_generated_outline_scaffolding_is_not_claims() -> None:
    """The lines draft.outline_without_template() writes itself.

    Every one of these was a claim in the first cut of this module, and the
    "Nearest retrieved provision" lines survived the self-referential rule
    because they name an Act - the statutory-reference guard kept them. They
    are matched by their literal text instead, and excluded unconditionally:
    code wrote them, so code knows they assert nothing. The pinpoint on that
    line is already guaranteed by draft.citations_of, and the quote beside it
    by output_check.citation_fidelity.
    """
    text = (
        "# DRAFT - NO TEMPLATE\n\n"
        "Request: a deed of release for a former employee\n"
        "Domain: employment\n\n"
        "No template in templates/ matched this request. The outline below is a "
        "clause-level skeleton grounded in the provisions retrieved for it.\n\n"
        "## CLAUSES\n\n"
        "### 1. Requirement for notice of termination\n\n"
        "- Nearest retrieved provision (relevance not confirmed): Fair Work Act 2009 "
        "(Cth) s 117 (snapshot 2026-07-01)\n"
        '- Operative words: "An employer must not terminate an employee\'s employment."\n'
        "- Source: https://www.legislation.gov.au/C2009A00028/latest/downloads\n"
        "- Clause to be drafted from the above. Terms not supplied: [MISSING: clause 1 terms]\n"
    )
    assert claims_of(text) == []


def test_blanking_a_generated_line_does_not_move_a_later_claims_offset() -> None:
    """Scaffolding is blanked, never deleted, or every marker after it lands wrong."""
    text = (
        "- Source: https://www.legislation.gov.au/C2009A00028/latest/downloads\n\n"
        "The employer must give the employee written notice of the termination day.\n"
    )
    produced, _ = split_claims(text)
    assert len(produced) == 1
    assert text[produced[0].start : produced[0].end] == produced[0].text


def test_a_sentence_about_the_document_that_names_a_statute_is_still_checked() -> None:
    """The exclusion must not swallow a claim about the law.

    Both sentences below open with the document as their subject, so both
    match the self-referential pattern. Only the second asserts anything a
    provision could carry - and it is the casual contract template's own
    preamble, a real claim about which Act governs the employment. The
    statutory-reference guard is the only thing keeping it in front of the
    judge.

    An earlier version of this test used "Nothing in this clause limits ...",
    which never matched the self-referential pattern at all ("clause" is not
    in it). It passed whether or not the guard existed, which is to say it
    tested nothing.
    """
    excluded = "This agreement is recorded in writing and signed by both of the parties.\n"
    kept = (
        "This contract is drafted on the basis that the Fair Work Act 2009 (Cth) and the "
        "National Employment Standards govern the employment.\n"
    )
    assert claims_of(excluded) == []
    assert claims_of(kept) == [
        "This contract is drafted on the basis that the Fair Work Act 2009 (Cth) and the "
        "National Employment Standards govern the employment."
    ]


def test_claim_ids_are_sequential_and_spans_point_at_the_original_text() -> None:
    text = "The first claim says something about the law here.\n\nThe second claim says another.\n"
    produced, _ = split_claims(text)
    assert [c.claim_id for c in produced] == ["c1", "c2"]
    for claim in produced:
        assert text[claim.start : claim.end] == claim.text


def test_a_later_unit_type_can_be_supplied_without_changing_the_splitter() -> None:
    """The seam Phase 4's paragraph unit uses. Asserted, not merely intended."""

    class WholeText:
        name = "paragraph"

        def split(self, text: str) -> list[tuple[int, int]]:
            return [(0, len(text.rstrip()))]

    text = "One sentence here. And a second sentence here.\n"
    produced, _ = split_claims(text, unit=WholeText())
    assert len(produced) == 1
    assert produced[0].unit == "paragraph"
    assert produced[0].text == "One sentence here. And a second sentence here."


# --- step 3: validating the judge's answer ---------------------------------


def one_claim(claim_id: str = "c1") -> list[Claim]:
    return [Claim(claim_id=claim_id, text="A claim about the law.", start=0, end=22)]


def test_a_well_formed_verdict_is_kept() -> None:
    raw = json.dumps(
        {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "verdict": "SUPPORTED",
                    "provision_ids": [PROVISION_IDS[0]],
                    "reason": "s 15A says so",
                }
            ]
        }
    )
    (ruling,) = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert ruling.verdict == "SUPPORTED"
    assert ruling.provision_ids == (PROVISION_IDS[0],)


def test_a_provision_id_outside_the_retrieved_set_makes_the_claim_unsupported() -> None:
    """The rule that stops a judge reasoning from something it was not given.

    The whole claim falls, rather than the unknown id being dropped from the
    list: a judge citing a provision retrieval never returned is working from
    its own recollection, and nothing it said about that claim is evidence.
    """
    raw = json.dumps(
        {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "verdict": "SUPPORTED",
                    "provision_ids": [PROVISION_IDS[0], "999999"],
                    "reason": "s 15A and s 123 together say so",
                }
            ]
        }
    )
    (ruling,) = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert ruling.verdict == "UNSUPPORTED"
    assert "999999" in ruling.reason
    assert ruling.provision_ids == ()


def test_an_id_the_judge_invented_alone_is_caught_too() -> None:
    raw = json.dumps(
        {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "verdict": "PARTIAL",
                    "provision_ids": ["4242"],
                    "reason": "close enough",
                }
            ]
        }
    )
    (ruling,) = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert ruling.verdict == "UNSUPPORTED"


def test_a_missing_claim_is_unsupported_not_absent() -> None:
    raw = json.dumps({"verdicts": [{"claim_id": "c2", "verdict": "SUPPORTED"}]})
    claims = one_claim("c1")
    (ruling,) = validate_verdicts(raw, claims=claims, retrieved_ids=PROVISION_IDS)
    assert ruling.claim_id == "c1"
    assert ruling.verdict == "UNSUPPORTED"
    assert "no verdict" in ruling.reason


@pytest.mark.parametrize(
    "verdict",
    ["PARTIALLY", "supported", "YES", "", "SUPPORTED ish", "UNKNOWN"],
)
def test_a_word_that_is_not_one_of_the_three_verdicts_is_unsupported(verdict: str) -> None:
    raw = json.dumps(
        {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "verdict": verdict,
                    "provision_ids": [PROVISION_IDS[0]],
                    "reason": "r",
                }
            ]
        }
    )
    (ruling,) = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert ruling.verdict == "UNSUPPORTED"


def test_supported_without_a_provision_id_is_unsupported() -> None:
    """A verdict nobody can check against a provision is not a verdict."""
    raw = json.dumps(
        {
            "verdicts": [
                {"claim_id": "c1", "verdict": "SUPPORTED", "provision_ids": [], "reason": "r"}
            ]
        }
    )
    (ruling,) = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert ruling.verdict == "UNSUPPORTED"
    assert "without naming a provision" in ruling.reason


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json at all",
        "{",
        json.dumps({"result": "everything looks fine"}),
        json.dumps({"verdicts": "SUPPORTED"}),
    ],
)
def test_a_body_that_is_not_a_verdict_list_fails_closed_and_says_why(raw: str) -> None:
    """Resolving to UNSUPPORTED is only half the job.

    A reader told "the judge returned no verdict for it" goes looking for a
    claim the judge disliked. When the whole response was junk, nothing in the
    draft was checked at all, and the reason has to say so - otherwise a
    provider outage is indistinguishable from a judge doing its job.
    """
    rulings = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert [r.verdict for r in rulings] == ["UNSUPPORTED"]
    assert "did not return JSON" in rulings[0].reason or "'verdicts' list" in rulings[0].reason


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps([{"claim_id": "c1"}]),
        json.dumps({"verdicts": [{"claim_id": "c1", "provision_ids": ["x"]}]}),
        json.dumps({"verdicts": [{"verdict": "SUPPORTED", "provision_ids": []}]}),
    ],
)
def test_a_verdict_list_missing_the_parts_that_matter_fails_closed(raw: str) -> None:
    """A well-formed list of objects that carry no usable verdict is still a failure."""
    rulings = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert [r.verdict for r in rulings] == ["UNSUPPORTED"]


def test_a_fenced_json_object_is_still_read() -> None:
    """Models wrap JSON in a code fence. That is not a reason to fail a draft."""
    body = json.dumps(
        {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "verdict": "SUPPORTED",
                    "provision_ids": [PROVISION_IDS[0]],
                    "reason": "r",
                }
            ]
        }
    )
    (ruling,) = validate_verdicts(
        f"```json\n{body}\n```", claims=one_claim(), retrieved_ids=PROVISION_IDS
    )
    assert ruling.verdict == "SUPPORTED"


def test_an_integer_provision_id_is_compared_as_a_string() -> None:
    """Retrieval ids are database integers; a judge returns whichever it likes."""
    raw = json.dumps(
        {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "verdict": "SUPPORTED",
                    "provision_ids": [int(PROVISION_IDS[0])],
                    "reason": "r",
                }
            ]
        }
    )
    (ruling,) = validate_verdicts(raw, claims=one_claim(), retrieved_ids=PROVISION_IDS)
    assert ruling.verdict == "SUPPORTED"


# --- the judge model contract ---------------------------------------------


def test_a_judge_from_the_drafting_models_own_family_is_refused() -> None:
    """A model does not reliably audit itself, so the check is in code."""
    with pytest.raises(JudgeError, match="same family"):
        check_judge_model("anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-5")


def test_a_judge_from_another_family_is_accepted() -> None:
    check_judge_model("openai/gpt-oss-120b", "anthropic/claude-sonnet-5")
    check_judge_model("openai/gpt-oss-120b", "deepseek/deepseek-v4-pro")


def test_an_unset_judge_model_is_refused() -> None:
    with pytest.raises(JudgeError, match="not set"):
        check_judge_model("  ", "deepseek/deepseek-v4-pro")


def test_a_same_family_judge_fails_the_draft_closed_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from michael import config

    monkeypatch.setenv("MICHAEL_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setenv("VERIFY_JUDGE_MODEL", "anthropic/claude-haiku-4.5")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    config.settings.cache_clear()

    result = verify_draft(
        "The employer must give the employee written notice of the termination day.\n",
        provisions=FAIR_WORK_PROVISIONS,
    )
    assert [r.verdict for r in result.rulings] == ["UNSUPPORTED"]
    assert "same family" in result.rulings[0].reason


# --- verify_draft end to end ----------------------------------------------

DRAFT = (
    "# ADVICE\n\n"
    "The employer must give the employee written notice of the termination day.\n\n"
    "An employee with four years of service is entitled to six weeks of notice.\n"
)


def test_a_judge_that_times_out_marks_every_claim_unsupported() -> None:
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=exploding_judge)
    assert len(result.rulings) == 2
    assert {r.verdict for r in result.rulings} == {"UNSUPPORTED"}
    assert all("timed out" in r.reason for r in result.rulings)
    # And the claims survive the failure, because annotate() works from them.
    # A verification that forgot what it was checking marks nothing inline,
    # and the draft then reads as though it had passed.
    assert len(result.claims) == 2
    marked = annotate(DRAFT, result)
    assert marked.count("[UNSUPPORTED:") == 2
    assert len(verify.open_items(result)) == 2


def test_no_retrieved_provisions_means_nothing_can_be_supported() -> None:
    """Deterministic, and it spends nothing: the answer is known without a call."""

    def must_not_be_called(prompt: str) -> tuple[str, JudgeCall]:
        raise AssertionError("the judge was called with no provisions to judge against")

    result = verify_draft(DRAFT, provisions=(), judge=must_not_be_called)
    assert {r.verdict for r in result.rulings} == {"UNSUPPORTED"}
    assert result.call is None


def test_a_verdict_survives_the_whole_path_when_the_judge_answers_properly() -> None:
    judge = judge_saying(
        {
            "c1": {
                "verdict": "SUPPORTED",
                "provision_ids": [PROVISION_IDS[0]],
                "reason": "the provision says exactly this",
            },
            "c2": {
                "verdict": "PARTIAL",
                "provision_ids": [PROVISION_IDS[1]],
                "reason": "the provision states four weeks, not six",
            },
        }
    )
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=judge)
    assert result.counts == {"SUPPORTED": 1, "PARTIAL": 1, "UNSUPPORTED": 0}
    assert [r.claim_id for r in result.flagged] == ["c2"]


def test_the_prompt_carries_the_provisions_and_nothing_else_identifying() -> None:
    claims, _ = split_claims(DRAFT)
    prompt = build_prompt(claims, FAIR_WORK_PROVISIONS)
    for provision_row in FAIR_WORK_PROVISIONS:
        assert str(provision_row.provision_id) in prompt
        assert provision_row.text in prompt
    for claim in claims:
        assert claim.text in prompt


def test_a_provision_longer_than_the_cap_is_truncated_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncation can only lose support, never manufacture it - but it must be visible."""
    from michael import config

    monkeypatch.setenv("VERIFY_PROVISION_MAX_CHARS", "40")
    config.settings.cache_clear()
    long = provision(section_number="62", heading="Maximum weekly hours")
    prompt = build_prompt(one_claim(), (long,))
    assert "[TRUNCATED" in prompt


# --- output: marking, listing and reporting -------------------------------


def test_a_flagged_claim_is_marked_inline_without_losing_a_word() -> None:
    judge = judge_saying(
        {
            "c1": {
                "verdict": "SUPPORTED",
                "provision_ids": [PROVISION_IDS[0]],
                "reason": "yes",
            },
            "c2": {
                "verdict": "UNSUPPORTED",
                "provision_ids": [],
                "reason": "the provisions state no such period",
            },
        }
    )
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=judge)
    marked = annotate(DRAFT, result)

    assert "[UNSUPPORTED: c2]" in marked
    assert "[SUPPORTED: c1]" not in marked, "a supported claim is not marked"
    # Nothing is removed: every character of the draft survives, in order.
    assert DRAFT.replace("\n", "") in marked.replace("[UNSUPPORTED: c2]", "").replace("\n", "")


def test_several_flagged_claims_are_all_marked_in_the_right_places() -> None:
    """Insertion happens back to front; done forwards, later offsets drift."""
    judge = judge_saying(
        {
            "c1": {"verdict": "UNSUPPORTED", "provision_ids": [], "reason": "no"},
            "c2": {"verdict": "PARTIAL", "provision_ids": [PROVISION_IDS[0]], "reason": "part"},
        }
    )
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=judge)
    marked = annotate(DRAFT, result)
    assert "termination day. [UNSUPPORTED: c1]" in marked
    assert "weeks of notice. [PARTIAL: c2]" in marked


def test_every_flagged_claim_reaches_open_items_with_its_reason() -> None:
    judge = judge_saying(
        {
            "c1": {"verdict": "SUPPORTED", "provision_ids": [PROVISION_IDS[0]], "reason": "yes"},
            "c2": {
                "verdict": "UNSUPPORTED",
                "provision_ids": [],
                "reason": "the provisions state no such notice period",
            },
        }
    )
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=judge)
    items = verify.open_items(result)
    assert len(items) == 1
    assert "c2" in items[0]
    assert "the provisions state no such notice period" in items[0]


def test_the_verification_section_reports_the_counts_the_judge_and_the_caveat() -> None:
    judge = judge_saying(
        {
            "c1": {"verdict": "SUPPORTED", "provision_ids": [PROVISION_IDS[0]], "reason": "yes"},
            "c2": {"verdict": "UNSUPPORTED", "provision_ids": [], "reason": "no"},
        }
    )
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=judge)
    text = verify.section(result)
    assert "- SUPPORTED: 1" in text
    assert "- UNSUPPORTED: 1" in text
    assert "- PARTIAL: 0" in text
    assert "- Judge model: test/judge" in text
    assert "does not replace review by an admitted Australian legal practitioner" in text


def test_annotating_a_draft_leaves_the_missing_list_alone() -> None:
    """open_items is the [MISSING] list. Verification adds beside it, never into it."""
    original = drafting.Draft(
        body=DRAFT,
        template="templates/employment/x.md",
        open_items=("employer abn",),
        verify_before_use=("something",),
        citations=("Fair Work Act 2009 (Cth) s 117 (snapshot 2026-07-01)",),
    )
    judge = judge_saying(
        {
            "c1": {"verdict": "SUPPORTED", "provision_ids": [PROVISION_IDS[0]], "reason": "yes"},
            "c2": {"verdict": "UNSUPPORTED", "provision_ids": [], "reason": "no source for it"},
        }
    )
    result = verify_draft(DRAFT, provisions=FAIR_WORK_PROVISIONS, judge=judge)
    annotated = annotate_draft(original, result)

    assert annotated.open_items == ("employer abn",)
    rendered = annotated.render()
    assert "1. [MISSING: employer abn]" in rendered
    assert "2. [UNSUPPORTED: c2]" in rendered
    assert "## VERIFICATION" in rendered
    assert rendered.rstrip().endswith(drafting.CLOSING_NOTICE)
    # The mandatory blocks keep their order.
    assert (
        rendered.index("## VERIFICATION")
        < rendered.index("## OPEN ITEMS")
        < rendered.index("## VERIFY BEFORE USE")
        < rendered.index(drafting.CLOSING_NOTICE)
    )


# --- draft_document always verifies ---------------------------------------


def test_draft_document_verifies_every_draft_it_returns(
    monkeypatch: pytest.MonkeyPatch, real_templates: None
) -> None:
    """Requirement 1, asserted where it is enforced rather than where it is promised."""
    from michael import retrieve, tools
    from tests.fixtures import covered_result

    monkeypatch.setattr(retrieve, "search", lambda q, **kw: covered_result(q))
    seen: list[str] = []

    def judge(prompt: str) -> tuple[str, JudgeCall]:
        seen.append(prompt)
        return json.dumps({"verdicts": []}), JudgeCall(model="test/judge", seconds=0.01)

    result = tools.draft_document("casual employment contract", judge=judge)
    assert seen, "draft_document returned a draft without calling the judge"
    assert result["verification"]["judge_model"] == "test/judge"
    assert "## VERIFICATION" in result["document"]
    # An empty verdict list is a judge that answered nothing, so every claim
    # comes back flagged rather than quietly passing.
    assert result["verification"]["counts"]["SUPPORTED"] == 0
    assert result["verification"]["flagged"]


def test_the_no_template_outline_has_nothing_to_verify_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """An outline is scaffolding and placeholders. There is no prose to check.

    Asserted because the alternative is worse in both directions: judging the
    scaffolding fills OPEN ITEMS with complaints about lines draft.py wrote,
    and reporting "0 claims checked" with no reason reads as a verifier that
    fell over.
    """
    from michael import retrieve, tools
    from tests.fixtures import covered_result

    monkeypatch.setenv("MICHAEL_TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(retrieve, "search", lambda q, **kw: covered_result(q))

    def must_not_be_called(prompt: str) -> tuple[str, JudgeCall]:
        raise AssertionError("the judge was called on an outline with no claims")

    result = tools.draft_document("a deed of release", judge=must_not_be_called)
    assert result["no_template"] is True
    assert result["verification"]["claims_checked"] == 0
    assert result["verification"]["flagged"] == []
    assert "no verifiable claim" in result["document"]


def test_the_agent_cannot_reach_draft_document_without_verification() -> None:
    """``judge`` is a test seam, not a tool argument: dispatch cannot pass it."""
    from michael import tools

    schema = next(t for t in tools.ANSWERING_TOOLS if t["name"] == "draft_document")
    assert "judge" not in schema["input_schema"]["properties"]


def test_verify_draft_is_not_an_agent_tool() -> None:
    """Phase 3 adds a check, not a fourth thing the model may decide to skip."""
    from michael import tools

    names = {t["name"] for t in tools.ANSWERING_TOOLS + tools.INGESTION_TOOLS}
    assert "verify_draft" not in names
    assert "verify_draft" not in tools._HANDLERS


# --- the labelled calibration set -----------------------------------------


def test_the_labelled_set_still_matches_the_splitter() -> None:
    """A labelled set that has drifted from split_claims measures nothing."""
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "calibration" / "verify_labelled.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: Sequence[dict[str, object]] = payload["cases"]
    assert len(cases) >= 20

    injected_kinds: set[str] = set()
    for case in cases:
        produced, skipped = split_claims(str(case["draft"]))
        labelled = case["claims"]
        assert isinstance(labelled, list)
        assert [c.claim_id for c in produced] == [c["claim_id"] for c in labelled], case["case_id"]
        assert [c.text for c in produced] == [c["text"] for c in labelled], case["case_id"]
        assert skipped == 0, case["case_id"]
        injected_kinds |= {c["label"] for c in labelled if c["injected"]}

    assert injected_kinds == {
        "wrong_number",
        "wrong_party",
        "invented_exception",
        "overstated_scope",
        "no_source",
    }


# --- the real judge -------------------------------------------------------
#
# Marked ``integration`` so the default run deselects it, like everything else
# here that leaves the process. Unlike the rest of that marker's population it
# needs no container - only OPENROUTER_API_KEY and a route to the judge::
#
#     uv run pytest tests/test_verify.py -m integration
#
# It asserts the one thing a mock cannot: that a real judge, given a real
# provision and a claim contradicting its plainest number, does not answer
# SUPPORTED. The rate at which it does that across the whole labelled set is
# measured by calibration/score_verify.py, not here.


@pytest.mark.integration
@pytest.mark.network
def test_the_real_judge_does_not_support_a_number_the_provision_contradicts() -> None:
    import pathlib

    from michael import config
    from michael.cli import load_dotenv

    root = pathlib.Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    config.settings.cache_clear()
    if not config.settings().openrouter_api_key:
        pytest.fail("OPENROUTER_API_KEY is not set, so the real judge cannot be reached")

    payload = json.loads((root / "calibration" / "verify_labelled.json").read_text("utf-8"))
    case = next(c for c in payload["cases"] if c["case_id"] == "fw-notice-periods")
    wrong = next(c for c in case["claims"] if c["label"] == "wrong_number")

    sys.path.insert(0, str(root))
    from calibration.score_verify import provision_of

    result = verify_draft(case["draft"], provisions=[provision_of(p) for p in case["provisions"]])
    ruling = next(r for r in result.rulings if r.claim_id == wrong["claim_id"])
    assert ruling.verdict != "SUPPORTED", (
        f"the real judge called an injected wrong number SUPPORTED: {ruling.reason}"
    )
    assert result.judge_model == config.settings().verify_judge_model
