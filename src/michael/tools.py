"""The tool surface Hermes calls.

Hermes is the brain and the only orchestrator: it receives the scenario, plans,
calls these tools, and writes the answer. Nothing in this module plans, loops,
or calls a model. There is no second agent framework here.

Two groups, deliberately separated:

* answering tools - ``classify_request``, ``search_provisions``,
  ``draft_document``, ``load_system_prompt``, ``list_domains``. These read the
  database through the read-only role and never write to it.
* ingestion tools - ``ingest_source_url``, ``seed_corpus``, ``apply_schema``.
  These are the only writers.

Every tool returns plain JSON-serialisable data so the transport (direct calls,
MCP, or anything else) stays interchangeable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from michael import draft as drafting
from michael import ingest as ingestion
from michael import retrieve
from michael.config import settings
from michael.domains import Routing, load_domains, route
from michael.output_check import citation_fidelity
from michael.schema import apply_schema as _apply_schema
from michael.sources import ALLOWED_HOSTS

#: A drafting verb, or an explicit ask for a document. Bare document nouns are
#: deliberately not enough: "what notice must an employer give" is a question
#: about notice, not a request to draft one.
DRAFT_VERB = re.compile(
    r"\b(draft|drafts|drafting|prepare|prepares|preparing|write|writes|writing|"
    r"produce|produces|producing|generate|generates|generating|create|creates|creating)\b",
    re.IGNORECASE,
)
DOCUMENT_NOUN = (
    r"(contract|agreement|deed|letter|clause|template|policy|notice|memorandum|"
    r"mou|nda|statement|terms)"
)
ASK_FOR_DOCUMENT = re.compile(
    rf"\b(need|needs|want|wants|require|requires|give me|send me)\b[^.?!]{{0,60}}?"
    rf"\b{DOCUMENT_NOUN}\b",
    re.IGNORECASE,
)
RESEARCH_INTENT = re.compile(
    r"\b(what|which|when|who|how|why|does|do|can|is|are|must|should|explain|research|"
    r"requirement|requirements|entitlement|entitlements|obligation|obligations|rule|rules|"
    r"apply|applies|allowed|lawful|minimum)\b",
    re.IGNORECASE,
)


def classify_request(request: str) -> dict[str, Any]:
    """Classify a request as RESEARCH, DRAFT or BOTH, and route it to a domain.

    A drafting request in a recognised legal domain is BOTH: the draft has to be
    grounded in provisions, which is research.
    """
    routing = route(request)
    wants_draft = bool(DRAFT_VERB.search(request) or ASK_FOR_DOCUMENT.search(request))
    wants_research = bool(RESEARCH_INTENT.search(request)) or "?" in request

    if wants_draft and (wants_research or routing.recognised):
        classification = "BOTH"
    elif wants_draft:
        classification = "DRAFT"
    else:
        classification = "RESEARCH"

    return {
        "classification": classification,
        "domain": routing.name,
        "domain_recognised": routing.recognised,
        "matched_keywords": list(routing.matched_keywords),
        "jurisdiction_filter": list(routing.jurisdictions),
        "doc_type_filter": list(routing.doc_types),
        "note": (
            ""
            if routing.recognised
            else "Domain unrecognised - retrieval will run unfiltered and must say so."
        ),
    }


def list_domains() -> dict[str, Any]:
    """The routing table, as configured in domains.yaml."""
    return {
        "domains": [
            {
                "name": d.name,
                "jurisdictions": list(d.jurisdictions),
                "doc_types": list(d.doc_types),
                "templates": d.templates,
                "keywords": list(d.keywords),
            }
            for d in load_domains()
        ]
    }


def load_system_prompt() -> str:
    """MICHAEL.md, to be loaded on every request."""
    return settings().system_prompt_file.read_text(encoding="utf-8")


def validate_output(
    output: str,
    *,
    provisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate quoted text in a proposed answer against retrieved sources."""
    findings = citation_fidelity(output, provisions or [])
    return {
        "clean": not findings,
        "findings": [{"rule": f.rule, "detail": f.detail} for f in findings],
        "note": (
            "Quoted legal text must be copied from retrieved provisions. "
            "Paraphrases still require the pinpoint citation and human review."
        ),
    }


def allowed_hosts() -> dict[str, Any]:
    """The fetch allowlist. Fixed; no configuration widens it."""
    return {"allowed_hosts": sorted(ALLOWED_HOSTS)}


def _provision_json(p: retrieve.RetrievedProvision) -> dict[str, Any]:
    return {
        "provision_id": p.provision_id,
        "jurisdiction": p.jurisdiction,
        "title": p.title,
        "citation": p.citation,
        "section_number": p.section_number,
        "heading": p.heading,
        "text": p.text,
        "pinpoint": p.pinpoint(),
        "snapshot_date": p.snapshot_date.isoformat(),
        "source_url": p.source_url,
        "sha256": p.sha256,
        "doc_type": p.doc_type,
        "char_range": [p.char_start, p.char_end],
        "scores": {
            "lexical": round(p.lexical_score, 4),
            "vector": round(p.vector_score, 4),
            "fused": round(p.score, 4),
        },
    }


def _routing_for(query: str, domain: str | None) -> Routing:
    """Use the named domain if it exists, otherwise classify the query."""
    if domain is None:
        return route(query)
    for candidate in load_domains():
        if candidate.name == domain:
            return Routing(domain=candidate, matched_keywords=(), recognised=True)
    return Routing(domain=None, matched_keywords=(), recognised=False)


def search_provisions(
    query: str,
    *,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Hybrid BM25 + vector retrieval. Read-only.

    An empty result is returned as ``covered: false`` with a reason and the
    exact NOT COVERED line to use. It is never returned as silence, and the
    nearest guess is never substituted for it.
    """
    routing = _routing_for(query, domain)
    result = retrieve.search(query, routing=routing, top_k=top_k)
    payload: dict[str, Any] = {
        "query": query,
        "domain": result.routing_domain,
        "domain_recognised": result.domain_recognised,
        "filters": {k: list(v) for k, v in result.filters.items()},
        "covered": result.covered,
        "threshold": round(result.threshold, 4),
        "best_score": round(result.best_score, 4),
        "provisions": [_provision_json(p) for p in result.provisions],
    }
    if not result.covered:
        payload["reason"] = result.reason
        payload["not_covered"] = result.not_covered_message(query.strip() or "this topic")
    if result.identifier_lookup:
        # A direct identifier lookup never truncates (retrieve._section_lookup
        # returns every matching row), so this is always "how many, in full" -
        # never "how many of some hidden larger number". total_matches makes
        # that explicit rather than leaving the reader to infer it from the
        # length of "provisions".
        payload["identifier_lookup"] = True
        payload["total_matches"] = result.total_matches
        if result.total_matches > 1:
            payload["ambiguous_pinpoint"] = True
            payload["note"] = (
                f"This pinpoint matches {result.total_matches} distinct provisions in the "
                "corpus, not one. This is a known duplicate-citation defect in the source "
                "data, not a ranking choice - all matching provisions are returned below, "
                "in citation order, and none of them is authoritative over the others."
            )
    if result.jurisdiction_mismatch:
        payload["jurisdiction_mismatch"] = result.jurisdiction_mismatch
        payload["jurisdiction_mismatch_note"] = (
            f"This query names {result.jurisdiction_mismatch}, which the corpus does not "
            "hold - only Western Australia and Commonwealth legislation is ingested. Any "
            "provisions returned below are WA or Commonwealth law, not law of the named "
            "jurisdiction."
        )
    return payload


def retrieval_query(request: str, routing: Routing, template: Path | None = None) -> str:
    """Turn a drafting request into a query worth searching with.

    A drafting request is a poor search query: "I need a casual employment
    contract between Company X and Mr Y, with conditions A and B" is mostly
    party names and filler, and measured against the corpus it scores 0.592
    where the same intent as "casual employment contract" scores 0.655.

    Two better signals are already to hand. The matched domain keywords are the
    request's subject matter. And when a template matches, its name *is* what
    is being drafted - ``casual_employment_contract.md`` says so plainly. Both
    are used, template first, because the template is the more specific of the
    two.

    Falls back to the raw request when neither is available, which is the
    unrecognised-domain case: there is nothing better to search with.
    """
    terms: list[str] = []
    if template is not None:
        terms += [w for w in re.split(r"[^a-z0-9]+", template.stem.lower()) if len(w) > 2]
    # Longest keyword first: the most specific phrase leads.
    terms += sorted(routing.matched_keywords, key=len, reverse=True)

    seen: list[str] = []
    for term in terms:
        if term not in seen:
            seen.append(term)
    return " ".join(seen) if seen else request


def draft_document(
    request: str,
    *,
    facts: dict[str, str] | None = None,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Draft from a template, or produce a grounded outline if none matches.

    Reads the database through the read-only role. The only thing it writes is a
    new draft template under templates/drafts/, and only on the no-template path.
    """
    routing = _routing_for(request, domain)
    template_path = drafting.find_template(request, domain=routing.name)
    query = retrieval_query(request, routing, template_path)
    retrieved = retrieve.search(query, routing=routing, top_k=top_k)

    extra_verify: tuple[str, ...] = ()
    if not retrieved.covered:
        extra_verify = (
            f"Retrieval returned nothing for this request ({retrieved.reason}). "
            f"{retrieved.not_covered_message(request.strip())}",
        )

    if template_path is not None:
        result = drafting.draft_from_template(
            template_path=template_path,
            facts=facts,
            provisions=retrieved.provisions,
            domain=routing.name,
            extra_verify=extra_verify,
        )
    else:
        result = drafting.outline_without_template(
            request=request,
            provisions=retrieved.provisions,
            domain=routing.name,
        )

    return {
        "domain": routing.name,
        "domain_recognised": routing.recognised,
        "template": result.template,
        "no_template": result.no_template,
        "retrieval_covered": retrieved.covered,
        "citations": list(result.citations),
        "open_items": list(result.open_items),
        "verify_before_use": list(result.verify_before_use),
        "written_template": str(result.written_to) if result.written_to else None,
        "document": result.render(),
    }


# --- ingestion tools: the only writers -------------------------------------


def apply_schema() -> dict[str, Any]:
    """Create the schema if absent. Never drops or alters existing objects."""
    _apply_schema()
    return {"ok": True, "embedding_dim": settings().embedding_dim}


def ingest_source_url(
    *,
    url: str,
    jurisdiction: str,
    title: str,
    citation: str,
    doc_type: str,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """Gap-filling fetch from an allowlisted host, then ingest.

    Any other host is refused and logged. There is no override.
    """
    parsed = date.fromisoformat(snapshot_date) if snapshot_date else None
    result = ingestion.ingest_url(
        url=url,
        jurisdiction=jurisdiction,
        title=title,
        citation=citation,
        doc_type=doc_type,
        snapshot_date=parsed,
    )
    return {
        "document_id": result.document_id,
        "citation": result.citation,
        "sha256": result.sha256,
        "provisions": result.provisions,
        "created": result.created,
    }


def ingest_local_file(
    *,
    path: str,
    source_url: str,
    jurisdiction: str,
    title: str,
    citation: str,
    doc_type: str,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """Ingest a DOCX, HTML or text file already on disk.

    For sources that cannot be fetched programmatically. ``source_url`` records
    provenance and is still checked against the host allowlist, so a local file
    cannot launder an off-allowlist source.
    """
    from pathlib import Path as _Path

    parsed = date.fromisoformat(snapshot_date) if snapshot_date else None
    result = ingestion.ingest_file(
        path=_Path(path),
        source_url=source_url,
        jurisdiction=jurisdiction,
        title=title,
        citation=citation,
        doc_type=doc_type,
        snapshot_date=parsed,
    )
    return {
        "document_id": result.document_id,
        "citation": result.citation,
        "sha256": result.sha256,
        "provisions": result.provisions,
        "created": result.created,
    }


def seed_corpus(*, limit: int | None = None, doc_types: list[str] | None = None) -> dict[str, Any]:
    """Seed from the Open Australian Legal Corpus, WA and Commonwealth only.

    ``doc_types`` narrows what is stored, e.g. ["act", "regulation"] for
    legislation only.
    """
    results = ingestion.seed_from_corpus(limit=limit, doc_types=doc_types)
    return {
        "documents": len(results),
        "created": sum(1 for r in results if r.created),
        "provisions": sum(r.provisions for r in results),
    }


# --- tool definitions for the orchestrator ---------------------------------

#: JSON-schema tool definitions. Hermes (or any runtime) registers these and
#: calls :func:`dispatch`. The answering tools are safe to expose to the model;
#: the ingestion tools write, so expose them only to an explicit ingestion run.
ANSWERING_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "classify_request",
        "description": (
            "Classify a request as RESEARCH, DRAFT or BOTH and route it to one domain "
            "from domains.yaml. Call this first, on every request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
    },
    {
        "name": "search_provisions",
        "description": (
            "Hybrid BM25 + vector retrieval over ingested provisions. Returns provisions "
            "with full metadata for citation. If nothing clears the relevance threshold "
            "it returns covered=false with a NOT COVERED line; the nearest guess is never "
            "returned. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "domain": {
                    "type": "string",
                    "description": "Override the routed domain. Omit to route automatically.",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "draft_document",
        "description": (
            "Produce a draft from a matching template in templates/, or a clause-level "
            "outline labelled 'DRAFT - NO TEMPLATE' when none matches. Only facts passed "
            "in are used; everything else is marked [MISSING: item]. Output always ends "
            "with OPEN ITEMS, VERIFY BEFORE USE and the closing notice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "request": {"type": "string"},
                "facts": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Known values keyed by template placeholder, e.g. "
                        "{'PARTY_EMPLOYER_NAME': 'Palm Vision Pty Ltd'}. Supply only what "
                        "the user actually gave you. Never fill one in to be helpful."
                    ),
                },
                "domain": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["request"],
        },
    },
    {
        "name": "validate_output",
        "description": (
            "Validate a proposed answer before returning it. Pass the exact output text "
            "and the provisions returned by search_provisions. Any quoted legal text "
            "not present in those provisions must be removed or corrected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "output": {"type": "string"},
                "provisions": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["output", "provisions"],
        },
    },
    {
        "name": "list_domains",
        "description": "The domain routing table from domains.yaml.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "allowed_hosts",
        "description": "The fixed allowlist of hosts ingestion may fetch from.",
        "input_schema": {"type": "object", "properties": {}},
    },
)

INGESTION_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "apply_schema",
        "description": "Create the database schema if absent. Never drops anything.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ingest_source_url",
        "description": (
            "Fetch one document from an allowlisted host and ingest it. Any other host "
            "is refused and logged. Writes to the database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "jurisdiction": {"type": "string", "enum": ["wa", "commonwealth"]},
                "title": {"type": "string"},
                "citation": {"type": "string"},
                "doc_type": {
                    "type": "string",
                    "enum": ["act", "regulation", "award", "case"],
                },
                "snapshot_date": {"type": "string", "description": "ISO date. Defaults to today."},
            },
            "required": ["url", "jurisdiction", "title", "citation", "doc_type"],
        },
    },
    {
        "name": "ingest_local_file",
        "description": (
            "Ingest a DOCX, HTML or text file already on disk, recording source_url as "
            "provenance. The URL is still checked against the host allowlist. Writes to "
            "the database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "source_url": {"type": "string"},
                "jurisdiction": {"type": "string", "enum": ["wa", "commonwealth"]},
                "title": {"type": "string"},
                "citation": {"type": "string"},
                "doc_type": {"type": "string", "enum": ["act", "regulation", "award", "case"]},
                "snapshot_date": {"type": "string"},
            },
            "required": ["path", "source_url", "jurisdiction", "title", "citation", "doc_type"],
        },
    },
    {
        "name": "seed_corpus",
        "description": (
            "Seed from the Open Australian Legal Corpus, filtered to WA and Commonwealth. "
            "Writes to the database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1},
                "doc_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["act", "regulation", "award", "case"],
                    },
                    "description": "Narrow what is stored. Omit to take everything.",
                },
            },
        },
    },
)

TOOL_SCHEMAS: tuple[dict[str, Any], ...] = ANSWERING_TOOLS + INGESTION_TOOLS

_HANDLERS: dict[str, Callable[..., Any]] = {
    "classify_request": classify_request,
    "search_provisions": search_provisions,
    "draft_document": draft_document,
    "validate_output": validate_output,
    "list_domains": list_domains,
    "allowed_hosts": allowed_hosts,
    "apply_schema": apply_schema,
    "ingest_source_url": ingest_source_url,
    "seed_corpus": seed_corpus,
    "ingest_local_file": ingest_local_file,
}

#: Tools that may write. Kept as data so a caller can expose the answering set
#: alone without having to know which names are safe.
WRITING_TOOLS = frozenset({"apply_schema", "ingest_source_url", "seed_corpus", "ingest_local_file"})


def dispatch(
    name: str, arguments: dict[str, Any] | None = None, *, allow_writes: bool = False
) -> Any:
    """Call a tool by name.

    ``allow_writes`` must be set explicitly for the ingestion tools, so the
    answering path cannot reach a writer by passing a different tool name.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown tool: {name}")
    if name in WRITING_TOOLS and not allow_writes:
        raise PermissionError(
            f"{name} writes to the database and is not available on the answering path"
        )
    return handler(**(arguments or {}))
