"""Check one Michael output against the rules MICHAEL.md states.

This is detection, not enforcement. It cannot make a model write a rule it
omitted, and it never rewrites an output to supply one: a supplied block
would fake a guarantee nobody made. It turns a silent omission into a
visible one, which is the choice web/michael.js already makes for the
closing notice.

The rules that have a code-level guarantee elsewhere are not repeated here.
draft.fill() writes the [MISSING] tokens over the template, and the
answering path is read-only because the database refuses writes. Those hold
whatever the model does. Everything checked below is prose the model has to
write, so a check is the only thing that can see it.

One definition, shared by the renderer, the CLI and the benchmark scorer, so
that "a well-formed output" cannot come to mean three different things.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLASSIFICATION = re.compile(
    r"^[ \t]*#{0,6}[ \t]*\*{0,2}CLASSIFICATION\*{0,2}[ \t]*:",
    re.IGNORECASE | re.MULTILINE,
)
"""The declaration line. A heading marker or bold wrapping is allowed,
because real output uses both. What is not allowed is the word appearing
inside a sentence: "Internal research only" in the closing notice and
"I'll draft the contract" in prose each carry a classification word, and a
substring test scores 41 of 42 baseline outputs as passing when 23 pass."""


def _block(key: str) -> re.Pattern[str]:
    """A block heading is its own line, never a mention inside prose.

    Michael's own refusal names the blocks in a sentence - "asked to omit the
    OPEN ITEMS, VERIFY BEFORE USE, or closing notice" - so a substring test
    counts a refusal to write a block as the block itself.
    """
    return re.compile(
        r"^[ \t]*#{0,6}[ \t]*\*{0,2}" + re.escape(key) + r"\*{0,2}(?=[ \t]*[—:-]|[ \t]*$)",
        re.MULTILINE,
    )


OPEN_ITEMS = _block("OPEN ITEMS")
VERIFY = _block("VERIFY BEFORE USE")
NOTICE = re.compile(
    r"Internal research only\.[\s\S]{0,220}?practitioner\.",
    re.IGNORECASE,
)
"""Matched across line breaks, because the notice is hard-wrapped in real
output. Two opus runs read as a dropped notice until this was normalised."""

CERTIFIES = re.compile(
    r"\b(?:this|the|that)\s+clause\b[^.\n]{0,60}?"
    r"(?:\b(?:is|is not|isn't)\b[^.\n]{0,30}?\bcomplian(?:t|ce)\b"
    r"|\b(?:complies|does not comply|doesn't comply)\b)",
    re.IGNORECASE,
)
"""Michael does not certify a clause in either direction. Scoped to an
assertion about a clause, so "an employer complies with s 117 by ..." - a
statement about the law, which is allowed - does not trip it."""

DISCLAIMS = re.compile(
    r"\b(?:not|never|no|nor|nothing)\b[^.\n]{0,60}?"
    r"\b(?:a|an)?\s*(?:statement|assertion|certification|claim|finding)\b"
    r"[^.\n]{0,30}?\bthat\b"
    r"|\b(?:does not|doesn't|do not|never)\s+(?:assert|certify|claim|say|state)\b"
    r"[^.\n]{0,30}?\bthat\b",
    re.IGNORECASE,
)
"""MICHAEL.md requires exactly this kind of sentence: "that is not a
statement that the clause is compliant." Read in isolation, CERTIFIES
matches "the clause is compliant" inside it - the regex has no way to see
the negated "statement that" in front of it. This pattern finds that
negating frame so check() can tell "the clause is compliant" the assertion
from "the clause is compliant" the thing someone declined to assert."""


@dataclass(frozen=True)
class Finding:
    """One rule the output does not satisfy."""

    rule: str
    detail: str


def _first_nonblank(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line
    return ""


def check(text: str) -> list[Finding]:
    """Report every rule this output breaks. An empty list is a clean output."""
    out = text or ""
    findings: list[Finding] = []

    if not CLASSIFICATION.search(out):
        findings.append(Finding("classification", "no CLASSIFICATION line"))
    elif not CLASSIFICATION.match(_first_nonblank(out)):
        findings.append(
            Finding(
                "classification_position",
                "the CLASSIFICATION line is not the first line",
            )
        )

    if not OPEN_ITEMS.search(out):
        findings.append(Finding("open_items", "no OPEN ITEMS block"))
    if not VERIFY.search(out):
        findings.append(Finding("verify", "no VERIFY BEFORE USE block"))
    if not NOTICE.search(out):
        findings.append(Finding("notice", "no closing notice"))

    for certified in CERTIFIES.finditer(out):
        preceding = out[max(0, certified.start() - 100) : certified.start()]
        if DISCLAIMS.search(preceding):
            continue
        findings.append(Finding("certification", f"certifies a clause: {certified.group(0)!r}"))
        break

    return findings
