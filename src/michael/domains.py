"""Domain routing. Configuration, not sub-agents.

A domain only decides which provisions are searched and which template
directory is offered. It never changes the answering procedure, and no safety
rule is duplicated here: those live in MICHAEL.md alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from michael.config import settings


class DomainConfigError(RuntimeError):
    """domains.yaml is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Domain:
    """One routing entry from domains.yaml."""

    name: str
    keywords: tuple[str, ...]
    jurisdictions: tuple[str, ...]
    doc_types: tuple[str, ...]
    templates: str


@dataclass(frozen=True, slots=True)
class Routing:
    """The outcome of routing one request.

    ``domain`` is None when nothing matched. The caller then searches
    unfiltered and says the domain was unrecognised - it never refuses.
    """

    domain: Domain | None
    matched_keywords: tuple[str, ...]
    recognised: bool

    @property
    def name(self) -> str:
        return self.domain.name if self.domain else "unrecognised"

    @property
    def jurisdictions(self) -> tuple[str, ...]:
        return self.domain.jurisdictions if self.domain else ()

    @property
    def doc_types(self) -> tuple[str, ...]:
        return self.domain.doc_types if self.domain else ()


@lru_cache(maxsize=4)
def load_domains(path: Path | None = None) -> tuple[Domain, ...]:
    """Read and validate domains.yaml."""
    target = path or settings().domains_file
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DomainConfigError(f"domains.yaml not found at {target}") from exc
    except yaml.YAMLError as exc:
        raise DomainConfigError(f"domains.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("domains"), list):
        raise DomainConfigError("domains.yaml must contain a top-level 'domains' list")

    domains: list[Domain] = []
    seen: set[str] = set()
    for entry in raw["domains"]:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise DomainConfigError(f"domain entry without a name: {entry!r}")
        name = str(entry["name"])
        if name in seen:
            raise DomainConfigError(f"duplicate domain name: {name}")
        seen.add(name)
        domains.append(
            Domain(
                name=name,
                keywords=tuple(sorted({str(k).lower().strip() for k in entry.get("keywords", [])})),
                jurisdictions=tuple(str(j).lower() for j in entry.get("jurisdictions") or ()),
                doc_types=tuple(str(t).lower() for t in entry.get("doc_types") or ()),
                templates=str(entry.get("templates") or name),
            )
        )

    if not domains:
        raise DomainConfigError("domains.yaml lists no domains")
    return tuple(domains)


def _hits(request: str, keyword: str) -> bool:
    """Whole-word keyword match, so 'award' does not fire on 'awarded'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", request) is not None


def route(request: str, *, path: Path | None = None) -> Routing:
    """Classify a request into exactly one domain.

    Scored by the total length of the matched keywords, so a specific phrase
    ("australian consumer law") outweighs several generic words. Ties break on
    domain name, so routing is deterministic for a given request.
    """
    text = request.lower()
    best: tuple[int, str] | None = None
    best_domain: Domain | None = None
    best_matches: tuple[str, ...] = ()

    for domain in load_domains(path):
        matched = tuple(k for k in domain.keywords if _hits(text, k))
        if not matched:
            continue
        score = sum(len(k) for k in matched)
        key = (-score, domain.name)
        if best is None or key < best:
            best = key
            best_domain = domain
            best_matches = matched

    if best_domain is None:
        return Routing(domain=None, matched_keywords=(), recognised=False)
    return Routing(domain=best_domain, matched_keywords=best_matches, recognised=True)


def template_dir(routing: Routing) -> Path | None:
    """Where this domain's templates live, if the directory exists."""
    if routing.domain is None:
        return None
    candidate = settings().templates_dir / routing.domain.templates
    return candidate if candidate.is_dir() else None
