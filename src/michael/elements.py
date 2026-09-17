"""The reference list of contract elements, and who may read it.

Mirrors `michael.domains`: a YAML file, validated on load, cached. The list is
data about drafting practice, not a statement of law - see the `basis` field
and the note at the top of elements.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from michael.config import settings

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
