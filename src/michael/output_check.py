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
from collections.abc import Mapping, Sequence
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
    r"(?:\b(?:not|never|no|nor|nothing)\b[^.\n]{0,60}?"
    r"\b(?:a|an)?\s*(?:statement|assertion|certification|claim|finding)\b"
    r"[^.\n]{0,30}?\bthat\b"
    r"|\b(?:does not|doesn't|do not|never)\s+(?:assert|certify|claim|say|state)\b"
    r"[^.\n]{0,30}?\bthat\b)"
    r"\s*\Z",
    re.IGNORECASE,
)
r"""MICHAEL.md requires exactly this kind of sentence: "that is not a
statement that the clause is compliant." Read in isolation, CERTIFIES
matches "the clause is compliant" inside it - the regex has no way to see
the negated "statement that" in front of it. This pattern finds that
negating frame so check() can tell "the clause is compliant" the assertion
from "the clause is compliant" the thing someone declined to assert.

The trailing `\s*\Z` is load-bearing: it is anchored against a slice that
ends exactly where the CERTIFIES match begins, so the disclaiming frame
must flow straight into the certified words ("... statement that [THE
CLAUSE IS COMPLIANT]"), not merely occur somewhere earlier in the window.
Without it, an unrelated negation - "There is no finding that supports the
alternative view. This clause complies with s 117." - matched the pattern
on its own "no ... finding ... that" and swallowed a genuine certification
that had nothing to do with it. The scorer failed open on exactly the
outputs MICHAEL.md forbids."""

_SENTENCE_BREAK = re.compile(r"[.!?]|\n")
"""Where one sentence ends and the next begins, for bounding the disclaiming
lookback. A negation in a PREVIOUS sentence must not suppress a
certification in this one: the guard exists to recognise one specific
sentence shape, not to scan an arbitrary character budget that happens to
cross into unrelated prose."""


def _sentence_start(text: str, pos: int) -> int:
    """Index just after the closest sentence break before `pos`, or 0."""
    start = 0
    for match in _SENTENCE_BREAK.finditer(text, 0, pos):
        start = match.end()
    return start


@dataclass(frozen=True)
class Finding:
    """One rule the output does not satisfy."""

    rule: str
    detail: str


_PUNCTUATION = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u2013": "-",
        "\u2014": "-",
        "\u2012": "-",
        "\u2212": "-",
        "\u00a0": " ",
    }
)
"""Models and renderers swap these for their ASCII equivalents freely. A
quote differing from its source only by an em dash is a faithful quote, and
flagging it teaches the model to delete quotes that were never wrong."""


def _normalise_quote(text: str) -> str:
    """Make source/output whitespace and punctuation comparable.

    Words are never changed. Only the characters a model can substitute
    without altering what the provision says are folded together.
    """
    return " ".join(text.translate(_PUNCTUATION).split())


_VALUE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b(?:\d[\d,]*(?:\.\d+)?"
    r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s*(?:'s|s')?\s*"
    r"(?:weeks?|days?|months?|years?|hours?|penalty units?|per ?cent|%)\b",
    re.IGNORECASE,
)
"""A quantity with its unit - the load-bearing span of a tiered statutory
table. "3 weeks" is seven characters, so the length rule below, which exists
to let pinpoints like "s 117" through, skipped the one figure in the answer
a reader cannot check for themselves. That is the span that diverged in the
s 117(3) instance."""

_SEGMENT_GAP = r".{0,12}?"
"""How far apart two quoted fragments may sit in the source and still count
as quoted from one place. A statutory table separates a tier from its value
by a cell boundary, which normalises to a single space; the model renders
that boundary as a dash it invented. Twelve characters admits the cell
boundary without reaching into the next row."""

_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?(.*)$", re.MULTILINE)
_MODEL_SEPARATOR = re.compile(r"\s+-+\s+|\s*\|\s*")
"""What a model puts between the cells of a row it is rendering. None of it
is in the source, so a blockquoted row can only be checked cell by cell.
Matched after normalisation, never before: a model renders a cell boundary
as an em dash far more often than as a hyphen, and a pattern applied to the
raw line splits nothing and then condemns a faithfully quoted row."""

_QUOTED = re.compile(r'["\u201c]([^"\u201d\n]+)["\u201d]')


def _worth_checking(segment: str) -> bool:
    """Is this fragment a claim about what a source says, or just a label?

    A pinpoint like "s 117", or a defined term in quotation marks, asserts
    nothing about the wording of a provision. A quantity does, and so does
    any fragment long enough to be operative text.
    """
    return len(segment) >= 20 or bool(_VALUE.search(segment))


def _groups(text: str) -> list[list[str]]:
    """The quoted fragments of the output, grouped by where they came from.

    Fragments the model wrote next to each other - `"<tier>" - "<value>"`, or
    the cells of one blockquoted row - form one group, because together they
    are a single claim about a single place in a single provision. Checking
    each fragment on its own is what let a real figure from the wrong row of
    the right table pass.
    """
    groups: list[list[str]] = []

    for line in _BLOCKQUOTE.findall(text):
        row = [
            normalised
            for segment in _MODEL_SEPARATOR.split(_normalise_quote(line))
            if _worth_checking(normalised := _normalise_quote(segment))
        ]
        if row:
            groups.append(row)

    current: list[str] = []
    end_of_previous = -1
    for match in _QUOTED.finditer(text):
        glue = text[end_of_previous : match.start()] if end_of_previous >= 0 else None
        adjacent = glue is not None and len(glue) <= 12 and not any(c.isalpha() for c in glue)
        if not adjacent and current:
            groups.append(current)
            current = []
        if _worth_checking(normalised := _normalise_quote(match.group(1))):
            current.append(normalised)
        end_of_previous = match.end()
    if current:
        groups.append(current)

    return groups


def citation_fidelity(text: str, provisions: Sequence[Mapping[str, object]]) -> list[Finding]:
    """Find quoted legal text that is not present in retrieved provisions.

    This deliberately does not judge paraphrases. It enforces only Michael's
    stronger promise that text inside quotation marks is copied from the
    retrieved corpus - and that fragments quoted together were adjacent in
    the source they are attributed to.
    """
    source_text = tuple(
        _normalise_quote(str(provision.get("text", "")))
        for provision in provisions
        if str(provision.get("text", "")).strip()
    )
    findings: list[Finding] = []
    for group in _groups(text or ""):
        pattern = re.compile(_SEGMENT_GAP.join(re.escape(segment) for segment in group))
        if any(pattern.search(source) for source in source_text):
            continue
        findings.append(
            Finding(
                "citation_fidelity",
                f"quoted text is not present in retrieved provisions: {' ... '.join(group)!r}",
            )
        )
    return findings


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
        preceding = out[_sentence_start(out, certified.start()) : certified.start()]
        if DISCLAIMS.search(preceding):
            continue
        findings.append(Finding("certification", f"certifies a clause: {certified.group(0)!r}"))
        break

    return findings
