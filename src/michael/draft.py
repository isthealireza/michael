"""Drafting from templates.

The [MISSING] rule is enforced here, in code, rather than left to the model:
:func:`fill` only ever substitutes a value that was supplied. Anything absent
becomes ``[MISSING: <item>]``. There is no code path that invents a party name,
ABN, address, date, pay rate, award name, classification level or
superannuation fund, because there is no code path that produces a value from
anything other than the caller's facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from michael.config import settings
from michael.retrieve import RetrievedProvision

#: Placeholders in templates look like {{PARTY_EMPLOYER_NAME}}.
PLACEHOLDER_RE = re.compile(r"\{\{\s*(?P<name>[A-Z0-9_]+)\s*\}\}")
MISSING_RE = re.compile(r"\[MISSING:\s*(?P<item>[^\]]+)\]")

CLOSING_NOTICE = (
    "Internal research only. Not legal advice. Requires review by an "
    "admitted Australian legal practitioner."
)

NO_TEMPLATE_LABEL = "DRAFT - NO TEMPLATE"

#: Items that must never be inferred, only supplied. Kept explicit so the list
#: in MICHAEL.md and the behaviour of the code cannot drift apart.
NEVER_INFERRED = (
    "party name",
    "ABN",
    "address",
    "date",
    "pay rate",
    "award name",
    "classification level",
    "superannuation fund",
)


class DraftingError(RuntimeError):
    """The draft could not be produced."""


@dataclass(frozen=True, slots=True)
class Draft:
    """A rendered draft, with everything needed to review it."""

    body: str
    template: str
    open_items: tuple[str, ...]
    verify_before_use: tuple[str, ...]
    citations: tuple[str, ...]
    no_template: bool = False
    written_to: Path | None = None

    def render(self) -> str:
        """The full output, including the three mandatory closing blocks."""
        return "\n".join(
            [
                self.body.rstrip(),
                "",
                closing_blocks(
                    open_items=self.open_items,
                    verify_before_use=self.verify_before_use,
                ),
            ]
        )


def humanise(placeholder: str) -> str:
    """Turn PARTY_EMPLOYER_NAME into 'party employer name' for the OPEN ITEMS list."""
    return placeholder.replace("_", " ").lower()


def fill(template_text: str, facts: dict[str, str] | None = None) -> tuple[str, tuple[str, ...]]:
    """Substitute supplied facts; mark everything else [MISSING].

    Returns the filled text and the ordered, de-duplicated list of missing
    items. A fact whose value is blank counts as missing: an empty string in a
    contract reads as an answer, and it is not one.
    """
    supplied = {k.upper(): v for k, v in (facts or {}).items() if str(v).strip()}
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        name = match.group("name")
        value = supplied.get(name)
        if value is not None:
            return str(value)
        item = humanise(name)
        if item not in missing:
            missing.append(item)
        return f"[MISSING: {item}]"

    return PLACEHOLDER_RE.sub(substitute, template_text), tuple(missing)


def unresolved(text: str) -> tuple[str, ...]:
    """Every [MISSING: ...] item present in a body of text, in order."""
    seen: list[str] = []
    for match in MISSING_RE.finditer(text):
        item = match.group("item").strip()
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def closing_blocks(*, open_items: tuple[str, ...], verify_before_use: tuple[str, ...]) -> str:
    """The three blocks every output ends with. Never optional."""
    lines = ["## OPEN ITEMS"]
    if open_items:
        lines += [f"{n}. [MISSING: {item}]" for n, item in enumerate(open_items, start=1)]
    else:
        lines.append("None.")

    lines += ["", "## VERIFY BEFORE USE"]
    if verify_before_use:
        lines += [f"- {item}" for item in verify_before_use]
    else:
        lines.append(
            "- Nothing in this output was confirmed against a source beyond the "
            "provisions cited above."
        )

    lines += ["", CLOSING_NOTICE]
    return "\n".join(lines)


def citations_of(provisions: tuple[RetrievedProvision, ...]) -> tuple[str, ...]:
    """Pinpoint citations, de-duplicated, in retrieval order."""
    seen: list[str] = []
    for provision in provisions:
        pinpoint = provision.pinpoint()
        if pinpoint not in seen:
            seen.append(pinpoint)
    return tuple(seen)


def wrong_jurisdiction_warnings(
    *, domain: str, provisions: tuple[RetrievedProvision, ...]
) -> tuple[str, ...]:
    """Flag WA provisions retrieved for national-system employment.

    Employment for national-system employees is governed by the Fair Work Act
    2009 (Cth) and the applicable Modern Award. A WA provision surfacing here is
    reported for review rather than quietly cited.
    """
    if domain != "employment":
        return ()
    offenders = sorted({p.pinpoint() for p in provisions if p.jurisdiction == "wa"})
    if not offenders:
        return ()
    return (
        "WA legislation was retrieved for an employment question. National-system "
        "employment is governed by the Fair Work Act 2009 (Cth) and the applicable "
        "Modern Award, so these were not relied on: " + "; ".join(offenders),
    )


def _slug(text: str, *, limit: int = 60) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (cleaned[:limit].rstrip("_")) or "untitled"


def find_template(request: str, *, domain: str) -> Path | None:
    """Pick the best template in the domain's directory, or None.

    Matching is on filename words appearing in the request, so it is
    inspectable: a template is chosen because its name is in the ask.
    """
    directory = settings().templates_dir / domain
    if not directory.is_dir():
        return None

    text = request.lower()
    best: tuple[int, str] | None = None
    chosen: Path | None = None
    for candidate in sorted(directory.glob("*.md")):
        words = [w for w in re.split(r"[^a-z0-9]+", candidate.stem.lower()) if len(w) > 2]
        if not words:
            continue
        hits = sum(1 for w in words if w in text)
        if hits == 0:
            continue
        # Prefer the template with the most matched words, then the most
        # specific name, then alphabetical order.
        key = (-hits, candidate.name)
        if best is None or key < best:
            best = key
            chosen = candidate
    return chosen


def draft_from_template(
    *,
    template_path: Path,
    facts: dict[str, str] | None,
    provisions: tuple[RetrievedProvision, ...],
    domain: str,
    extra_verify: tuple[str, ...] = (),
) -> Draft:
    """Render a template, marking every unsupplied item [MISSING]."""
    try:
        template_text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DraftingError(f"cannot read template {template_path}: {exc}") from exc

    body, missing = fill(template_text, facts)
    citations = citations_of(provisions)

    verify = list(extra_verify)
    verify += list(wrong_jurisdiction_warnings(domain=domain, provisions=provisions))
    if not citations:
        verify.append(
            "No provisions were retrieved for this draft, so no clause here is tied to a source."
        )
    verify.append(
        "Whether this draft is appropriate for these parties and these facts. "
        "Its clauses are based on the provisions cited; that is not a statement "
        "that any clause is compliant."
    )

    if citations:
        body = body.rstrip() + "\n\n## BASED ON\n" + "\n".join(f"- {c}" for c in citations)

    return Draft(
        body=body,
        template=str(template_path.relative_to(settings().templates_dir.parent)),
        open_items=missing,
        verify_before_use=tuple(dict.fromkeys(verify)),
        citations=citations,
    )


def outline_without_template(
    *,
    request: str,
    provisions: tuple[RetrievedProvision, ...],
    domain: str,
    write_template: bool = True,
) -> Draft:
    """Produce a clause-level outline when no template matches.

    Never a refusal. The outline is grounded in the retrieved provisions, is
    labelled DRAFT - NO TEMPLATE, and applies the [MISSING] rule as normal.
    Afterwards the outline is written to templates/drafts/ for review.
    """
    heading_lines = [
        f"# {NO_TEMPLATE_LABEL}",
        "",
        f"Request: {request.strip()}",
        f"Domain: {domain}",
        "",
        "No template in templates/ matched this request. The outline below is a "
        "clause-level skeleton grounded in the provisions retrieved for it. It is "
        "not a drafted instrument.",
        "",
        "## PARTIES",
        "",
        "- Party A: {{PARTY_A_NAME}}, ABN {{PARTY_A_ABN}}, of {{PARTY_A_ADDRESS}}",
        "- Party B: {{PARTY_B_NAME}}, of {{PARTY_B_ADDRESS}}",
        "- Commencement: {{COMMENCEMENT_DATE}}",
        "",
        "## CLAUSES",
        "",
    ]

    clause_lines: list[str] = []
    if provisions:
        for index, provision in enumerate(provisions, start=1):
            label = provision.heading.strip() or f"Section {provision.section_number}"
            quote = " ".join(provision.text.split())[:300]
            clause_lines += [
                f"### {index}. {label}",
                "",
                f"- Nearest retrieved provision (relevance not confirmed): {provision.pinpoint()}",
                f'- Operative words: "{quote}"',
                f"- Source: {provision.source_url}",
                "- Clause to be drafted from the above. Terms not supplied: "
                "{{CLAUSE_" + str(index) + "_TERMS}}",
                "",
            ]
    else:
        clause_lines += [
            "No provisions were retrieved for this request, so there is nothing to "
            "ground an outline in. Run ingestion for this topic before drafting.",
            "",
        ]

    template_text = "\n".join(heading_lines + clause_lines)
    body, missing = fill(template_text, None)
    citations = citations_of(provisions)

    verify = [
        "Every clause heading above is a placeholder for drafting, not drafted text.",
        "Whether an instrument of this kind is the right vehicle at all.",
    ]
    if provisions:
        verify.append(
            "Each clause's 'Nearest retrieved provision' is the closest match "
            "retrieval found, not a confirmed authority for that clause. "
            "Retrieval can surface a real, correctly quoted provision that is "
            "topically unrelated to the clause it sits under - confirm each "
            "one actually supports its clause before relying on it."
        )
    verify += list(wrong_jurisdiction_warnings(domain=domain, provisions=provisions))
    if not provisions:
        verify.append("Nothing in this outline is grounded in a retrieved provision.")

    written_to = (
        save_draft_template(request=request, text=template_text) if write_template else None
    )

    return Draft(
        body=body,
        template="(none)",
        open_items=missing,
        verify_before_use=tuple(dict.fromkeys(verify)),
        citations=citations,
        no_template=True,
        written_to=written_to,
    )


def save_draft_template(*, request: str, text: str) -> Path:
    """Write a no-template outline to templates/drafts/ for review.

    Written with its placeholders intact, so it is a template rather than a
    filled document. Never overwrites: a timestamp keeps each attempt.
    """
    directory = settings().templates_dir / "drafts"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{_slug(request)}__{stamp}.md"
    header = (
        f"<!-- Generated by Michael on {stamp} as a DRAFT template for review.\n"
        f"     Not reviewed. Not approved for use. -->\n\n"
    )
    path.write_text(header + text, encoding="utf-8")
    return path
