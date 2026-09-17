"""The reference list of contract elements, and who may read it.

Mirrors `michael.domains`: a YAML file, validated on load, cached. The list is
data about drafting practice, not a statement of law - see the `basis` field
and the note at the top of elements.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from michael.config import settings
from michael.contracts import Clause, split_clauses

#: The value of `basis` when an element rests on drafting practice and no
#: provision is claimed for it.
CONVENTION = "drafting convention"


class ElementConfigError(RuntimeError):
    """elements.yaml is malformed. Never loaded half-valid."""


@dataclass(frozen=True, slots=True)
class Element:
    """One thing a contract is commonly expected to address."""

    id: str
    label: str
    placeholder: str
    synonyms: tuple[str, ...]
    basis: str


def _element(raw: Any, where: str) -> Element:
    if not isinstance(raw, dict):
        raise ElementConfigError(f"{where}: each element must be a mapping")
    missing = {"id", "label", "placeholder", "synonyms", "basis"} - set(raw)
    if missing:
        raise ElementConfigError(f"{where}: element missing keys {sorted(missing)}")
    synonyms = tuple(str(s).strip().lower() for s in raw["synonyms"] if str(s).strip())
    if not synonyms:
        raise ElementConfigError(f"{where}: element {raw['id']!r} has no synonyms")
    return Element(
        id=str(raw["id"]),
        label=str(raw["label"]),
        placeholder=str(raw["placeholder"]).upper(),
        synonyms=synonyms,
        basis=str(raw["basis"]),
    )


@lru_cache(maxsize=4)
def load_elements(path: Path | None = None) -> tuple[Element, ...]:
    """Every element in the file, universal first, validated.

    Ids are unique across the whole file, not per section: an id appearing in
    both `universal` and a domain would make `elements_for` ambiguous.
    """
    source = path or settings().elements_file
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ElementConfigError(f"cannot read {source}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ElementConfigError(f"{source}: top level must be a mapping of sections")

    elements: list[Element] = []
    seen: set[str] = set()
    for section, entries in raw.items():
        for entry in entries or ():
            element = _element(entry, f"{source}:{section}")
            if element.id in seen:
                raise ElementConfigError(f"{source}: duplicate element id {element.id!r}")
            seen.add(element.id)
            elements.append(element)
    return tuple(elements)


def _sections(path: Path | None) -> dict[str, list[str]]:
    source = path or settings().elements_file
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return {
        section: [str(e["id"]) for e in (entries or ())]
        for section, entries in raw.items()
    }


def elements_for(domain: str | None = None, *, path: Path | None = None) -> tuple[Element, ...]:
    """The universal elements, plus the named domain's own.

    A domain adds to the universal list; it never replaces it. An unrecognised
    domain returns the universal list rather than raising: a completeness
    report on an unrouted document is still worth having.
    """
    sections = _sections(path)
    wanted = list(sections.get("universal", []))
    if domain:
        wanted += sections.get(domain, [])
    by_id = {e.id: e for e in load_elements(path)}
    return tuple(by_id[element_id] for element_id in wanted if element_id in by_id)


@dataclass(frozen=True, slots=True)
class ElementFinding:
    """Whether one element was found, and where. Never whether that is good."""

    element_id: str
    label: str
    state: Literal["PRESENT", "ABSENT", "UNCERTAIN"]
    clause_id: str | None
    basis: str


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    """What a document addresses and what it does not mention."""

    domain: str | None
    findings: tuple[ElementFinding, ...]


#: A heading longer than this reads as body prose that happened to share its
#: line with the clause number, not a deliberately short title. A one-line
#: numbered clause such as "11.1 This document records the whole agreement
#: between the parties about the employment..." puts that entire sentence
#: into `Clause.heading` (Task 2's design: the heading capture is whatever
#: follows the number on its line). Matching synonyms against a sentence
#: rather than a title produced a false second "heading hit" for the
#: `parties` element on the real casual-employment fixture - PRESENT via
#: clause "1" (PARTIES) became UNCERTAIN because clause "11.1"'s sentence
#: happened to contain the word "parties" too. 60 matches CAPS_CLAUSE's own
#: cap on what counts as a plausible heading.
MAX_HEADING_LEN = 60


def _heading_text(clause: Clause) -> str:
    """The clause's id and heading, lowercased, for matching.

    The id is always included - it IS the heading on an unnumbered caps-style
    document, per Task 3. The heading is included only when it plausibly
    reads as a short title rather than a sentence: not implausibly long (see
    :data:`MAX_HEADING_LEN`), and not ending in sentence-terminating
    punctuation. Length alone was not enough - "The employment is covered by
    {{MODERN_AWARD_NAME}}." is 52 characters, under the cap, and produced a
    spurious extra heading hit for the `modern_award` element alongside the
    genuine "AWARD AND CLASSIFICATION" heading, turning a clean PRESENT into
    UNCERTAIN. A real heading is a label; it does not end in a full stop.
    """
    heading = clause.heading
    if len(heading) > MAX_HEADING_LEN or heading.endswith((".", "!", "?")):
        heading = ""
    return f"{clause.clause_id} {heading}".lower()


def _hits(text: str, phrase: str) -> bool:
    """Whole-phrase match, bounded by non-word characters on each side.

    Mirrors `michael.domains._hits`. Without a boundary check, the `term`
    element's synonym "term" matches inside "termination" - a different
    element entirely - and every document with a TERMINATION heading would
    report `term` as at least UNCERTAIN whether or not the document says
    anything about its own term.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def audit(
    text: str,
    *,
    domain: str | None = None,
    path: Path | None = None,
) -> CompletenessReport:
    """Report which elements the document addresses.

    Three states, not two. A synonym in a clause heading is PRESENT. A synonym
    in body text with no heading of its own is UNCERTAIN, and so is a synonym
    matching more than one clause heading. Nothing else is ABSENT.

    The third state is the point. A false ABSENT sends the reader hunting for
    something sitting in clause 14 and costs the whole report its credibility;
    a false PRESENT hides a clause that is genuinely missing. Rather than
    choosing which way to be wrong, the ambiguity is carried to the surface -
    the same move as empty retrieval returning covered=false rather than the
    nearest guess.
    """
    clauses = split_clauses(text)
    findings: list[ElementFinding] = []

    for element in elements_for(domain, path=path):
        heading_hits = [
            c for c in clauses
            if any(_hits(_heading_text(c), s) for s in element.synonyms)
        ]
        body_hits = [
            c for c in clauses
            if any(_hits(c.text.lower(), s) for s in element.synonyms)
        ]

        if len(heading_hits) == 1:
            state: Literal["PRESENT", "ABSENT", "UNCERTAIN"] = "PRESENT"
            clause_id: str | None = heading_hits[0].clause_id
        elif len(heading_hits) > 1:
            state, clause_id = "UNCERTAIN", heading_hits[0].clause_id
        elif body_hits:
            state, clause_id = "UNCERTAIN", body_hits[0].clause_id
        else:
            state, clause_id = "ABSENT", None

        findings.append(
            ElementFinding(
                element_id=element.id,
                label=element.label,
                state=state,
                clause_id=clause_id,
                basis=element.basis,
            )
        )

    return CompletenessReport(domain=domain, findings=tuple(findings))
