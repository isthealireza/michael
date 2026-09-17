"""Ingestion. Writes to the database; the answering path never does.

Two entry points:

* :func:`seed_from_corpus` - bulk seed from the Open Australian Legal Corpus,
  filtered to WA and Commonwealth.
* :func:`ingest_url` - gap-filling fetch from an allowlisted host.

Both go through :func:`ingest_document`, so provenance (source URL, fetch
timestamp, sha256 over the original bytes) is recorded the same way whatever
the origin.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from psycopg import Connection, Cursor
from psycopg.rows import DictRow

from michael.config import settings
from michael.db import vector_literal, writable
from michael.docx_text import docx_to_text, looks_like_docx
from michael.embeddings import embed
from michael.html_text import html_to_text, looks_like_html
from michael.schema import DOC_TYPES, JURISDICTIONS, refresh_corpus_stats
from michael.sources import SourceRefused, check_url, fetch, host_of, log_attempt

#: A section heading in Australian legislation: a number that may carry letter
#: suffixes ("15", "15A", "23AB"), followed by a heading on the same line.
#: Deliberately conservative - a line that does not look like this stays inside
#: the preceding section rather than starting a spurious one.
SECTION_RE = re.compile(
    r"^[ \t]*(?P<number>\d{1,4}[A-Z]{0,3})[.)]?[ \t–—-]+(?P<heading>[A-Z][^\n]{0,150})$",
    re.MULTILINE,
)

MIN_PROVISION_CHARS = 40

#: Lines that carry no operative text: structural headings, page numbers, blanks.
STRUCTURAL_PREFIXES = (
    "part ",
    "division ",
    "subdivision ",
    "chapter ",
    "schedule ",
    "endnote",
    "notes to ",
    "table of ",
    "contents",
    # A compilation's own front-matter lists these as further TOC-style rows,
    # unnumbered, right after the last real section in the table of
    # provisions. Without recognising them as structural too, the prose
    # lookahead in find_body_start mistakes them for the start of the body -
    # they are not section-shaped, so _is_structural's other checks pass them
    # through as if they were operative text.
    "notes",
    "compilation table",
    "defined terms",
)

#: How many section-like lines must precede the body before we believe we have
#: found a table of provisions rather than simply the start of the document.
#: Below this, nothing is cut - keeping a little junk is safer than dropping law.
CONTENTS_MIN_ENTRIES = 10

#: How far past a section heading to look for prose before concluding there is
#: none. Legislation puts the first operative line directly under the heading.
PROSE_LOOKAHEAD = 3


def _ends_in_bare_number(line: str) -> bool:
    """True when a line's last whitespace-separated token is a run of digits.

    On its own this says nothing about *why* the number is there - a page
    reference and a cited Act's year both end a line this way. It is a building
    block for :func:`_is_contents_entry`, not a verdict.
    """
    stripped = line.strip()
    if not stripped:
        return False
    tail = stripped.rsplit(maxsplit=1)
    return len(tail) == 2 and tail[1].isdigit()


def _is_contents_entry(line: str, *, previous: str = "", following: str = "") -> bool:
    """True when ``line`` is a row of a table of provisions, not an operative heading.

    A trailing bare number is not, by itself, evidence of anything: a page
    reference (``15A Meaning of casual employee 68``) and a cited Act's year
    (``26WD Exception-notification under the My Health Records Act 2012``) are
    both a heading-shaped line followed by a run of digits, and no rule about
    the string alone - a digit count, a plausible year range - tells them apart,
    because a plausible year and a plausible page number overlap completely.

    What differs is what is *around* the number. A table of provisions paginates
    every row - Part, Division and section headings alike - so its rows cluster:
    each one ends in a bare number, and so does its neighbour. A cited year is
    part of the heading's own text; it appears whether or not the row before or
    after it does the same, because a compiled Act's body is not paginated
    inline. So the number is only a page reference when a neighbouring row - the
    line directly above or below ``line`` in the source document - carries one
    too. ``previous`` and ``following`` are exactly that: real, adjacent lines,
    not a guess about the number itself.
    """
    stripped = line.strip()
    if not SECTION_RE.match(stripped):
        return False
    if not _ends_in_bare_number(stripped):
        return False
    return _ends_in_bare_number(previous) or _ends_in_bare_number(following)


def _adjacent_lines(text: str, position: int) -> tuple[str, str]:
    """The raw lines immediately before and after the line starting at ``position``.

    ``position`` must be the start of a line - true for any :data:`SECTION_RE`
    match, since the pattern is anchored at ``^`` under ``re.MULTILINE``.
    """
    line_end = text.find("\n", position)
    if line_end == -1:
        line_end = len(text)

    if position == 0:
        previous = ""
    else:
        previous_end = position - 1
        previous_start = text.rfind("\n", 0, previous_end) + 1
        previous = text[previous_start:previous_end]

    next_start = line_end + 1
    if next_start > len(text):
        following = ""
    else:
        next_end = text.find("\n", next_start)
        if next_end == -1:
            next_end = len(text)
        following = text[next_start:next_end]

    return previous, following


#: How many lines either side of a candidate heading to scan for a table's
#: pagination signal. A contents row's neighbour is the very next line; a
#: commencement table's rows wrap a cell of explanatory prose between two
#: dated rows, so the same signal - another row ending in a bare number -
#: can sit one line further away without the row in between being anything
#: but that wrapped cell text.
_TABLE_ROW_LOOKAROUND = 2


def _line_index(text: str) -> tuple[list[str], list[int]]:
    """The document's lines and the character offset each one starts at.

    Built once per document and passed down, not recomputed per candidate
    heading: doing it per match made :func:`split_sections` quadratic in
    document length, measured at 0.006s for 100 sections against 0.331s for
    800 - fine on a small Act, an hour of needless work across a corpus.
    """
    lines = text.splitlines()
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line) + 1
    return lines, offsets


def _is_table_row(
    text: str,
    position: int,
    lines: list[str],
    offsets: list[int],
) -> bool:
    """True when a heading-shaped line at ``position`` sits inside a table.

    This is not :func:`_is_contents_entry` again - that check is accepted and
    unchanged, and only ever looks one line either side. This is the same
    underlying claim - a trailing bare number is a pagination or a table-cell
    marker, not part of the heading's own text, exactly when a *nearby* row
    carries one too - applied to the wider, more irregular tables a
    compilation embeds inside a real section's own body: a commencement
    table's "26 May 2009" or "3. Sections 41 to 572" is not a cited year
    like Part IIIC's; it is one cell of a table whose neighbouring cells
    happen to wrap across more than one line of extracted text.

    ``lines`` and ``offsets`` come from :func:`_line_index` over the same
    ``text``, so the caller pays for them once per document.
    """
    line_end = text.find("\n", position)
    stripped_line = text[position : line_end if line_end != -1 else len(text)]
    if not _ends_in_bare_number(stripped_line):
        return False

    index = bisect.bisect_right(offsets, position) - 1
    for delta in range(1, _TABLE_ROW_LOOKAROUND + 1):
        for neighbour_index in (index - delta, index + delta):
            if 0 <= neighbour_index < len(lines) and _ends_in_bare_number(lines[neighbour_index]):
                return True
    return False


# A monotonic section-sequence floor was tried here and removed. The premise -
# that an Act numbers its operative body once, in a run whose base number never
# goes backwards, so a lower number afterwards must be an endnote - is false.
# A Schedule numbers its own clauses from 1, and a compilation volume carries
# Schedules alongside sections, so the sequence legitimately restarts inside one
# document. Measured against the corpus the rule cost 1,199 provisions to
# recover 44: Fair Work Act volume 04 fell from 198 to 141, the Privacy Act from
# 355 to 313. Dropping real law to remove duplicate pinpoints is the wrong
# trade in a system whose whole purpose is that a citation can be trusted.
#
# The endnote problem is real and still open. Whatever solves it must key on
# what the text *is* - an amendment-history table, a schedule, an endnote
# section - and not on where its number sits in a sequence.


def _is_structural(line: str) -> bool:
    """True when a line is a heading, a page number or blank - never operative text."""
    stripped = line.strip()
    if not stripped:
        return True
    if SECTION_RE.match(stripped):
        return True
    if stripped.lower().startswith(STRUCTURAL_PREFIXES):
        return True
    # A bare page number, as tables of provisions carry.
    if stripped.replace(".", "").replace("-", "").isdigit():
        return True
    # A compilation's own front-matter and tail labels paginate themselves
    # the same way a numbered contents row does - "Compilation table 13",
    # "Uncommenced provisions table 1", "Other notes 1" - whatever the label
    # text says, a line that ends in a bare page number here is never
    # operative prose, so it cannot be what tells find_body_start it has
    # reached the real body.
    return _ends_in_bare_number(stripped)


#: The heading above a compilation's endnotes, on a line of its own.
#: Commonwealth compilations say "Endnotes"; Western Australian ones say
#: "Notes". Everything after it is apparatus *about* the Act - the compilation
#: table, the amendment history, the defined-terms index - and none of it is
#: operative text, however section-shaped its rows look.
ENDNOTES_HEADING = re.compile(r"^(?:end)?notes\s*$", re.IGNORECASE | re.MULTILINE)

#: What must follow that heading for it to be the endnote block rather than a
#: stray line of prose. "Notes" alone is too common a word to cut a document on.
ENDNOTES_FOLLOWERS = (
    "compilation table",
    "defined terms",
    "other notes",
    "about the endnotes",
    "legislation history",
    "uncommenced provisions table",
    "uncommenced amendments",
    "abbreviation key",
    "endnote 1",
)

#: A Schedule's own heading. Schedules are law, but they number their clauses
#: locally from 1, so their numbers collide with the body's section numbers.
#: A Schedule's heading stands alone on its line, or is followed by a dash and
#: the Schedule's title - "Schedule 2", "Schedule 1-Application, saving and
#: transitional provisions". The separator is what makes it a heading rather
#: than a sentence: "Schedule 2 commencement day means the day on which
#: Schedule 2 to the amending Act commences" is a *definition* inside a
#: Schedule, and matching it labelled the Fair Work Act's s 47A as
#: "Sch 2 cl 47A" - a citation to a provision that does not exist.
SCHEDULE_HEADING = re.compile(
    r"^schedule\s+(\d+[A-Z]*|[IVXLC]+)[ \t]*(?:[—–-]|$)",
    re.IGNORECASE | re.MULTILINE,
)


def find_body_end(text: str, body_start: int) -> int | None:
    """Character offset where the endnotes begin, or None if there are none.

    The endnotes carry an amendment history whose rows are numbered like
    sections - "1 The provisions in this Act amending ..." - and restart from
    1, so stored as provisions they produce a second, competing pinpoint for a
    section number the body has already used. They are not law and must not be
    split.

    Two guards, because cutting a document short is worse than keeping junk.
    The marker is only trusted **after** ``body_start``: a table of provisions
    lists "Notes" and "Endnotes" among its own rows near the top of the file,
    and truncating there would discard the entire Act. And it must be followed,
    within a few lines, by one of :data:`ENDNOTES_FOLLOWERS` - the apparatus
    headings that only ever appear in an endnote block - because "Notes" on its
    own line is otherwise ordinary enough to appear inside real text.

    In a multi-volume compilation the endnotes live in the last volume alone,
    so the earlier volumes have no marker after their body and keep everything.
    """
    for match in reversed(list(ENDNOTES_HEADING.finditer(text))):
        if match.start() <= body_start:
            continue
        following = text[match.end() : match.end() + 400].lower()
        if any(follower in following for follower in ENDNOTES_FOLLOWERS):
            return match.start()
    return None


#: Markers that open a numbered block *inside* a section's own body. Both open
#: apparatus that belongs to the section, and both number their items "1.",
#: "2.", "3." - indistinguishable from section headings once the source's
#: layout is flattened into lines of text.
#:
#: "Column 1" heads a formal table, the commencement table in section 2 above
#: all. "Notes for this section:" heads an explanatory note list.
APPARATUS_OPENERS = re.compile(
    r"^(?:column\s+1\b|notes?\b[^\n]*for this section\s*:)",
    re.IGNORECASE | re.MULTILINE,
)


def apparatus_rows(text: str, matches: list[re.Match[str]]) -> set[int]:
    """Start offsets of headings that are really rows of in-body apparatus.

    Apparatus like this is part of a real section and stays in that section's
    text; what must not happen is its *items* being split off as provisions of
    their own. "1. Sections 1 and 2 and anything in this Act not elsewhere
    covered by this table" is a cell of section 2's commencement table, and
    "1. Subsection (1) incorporates into this Act ..." is a note on section 14
    - stored as provisions, each becomes a second, competing section 1.

    Suppressing a character *range* after each opener was tried and rejected.
    A range cannot know where its table ends: bounded by a blank line it
    swallowed 357,073 characters of a Fair Work volume, because DOCX extraction
    produces almost none; bounded by a character cap instead it overran the end
    of its own section and ate the next section's heading, dropping five real
    Privacy Act sections - 16B, 21J, 38, 38A and 38B - because 16A's permitted-
    situations table runs right up to 16B.

    So follow the table's own numbering instead. Its rows are numbered 1., 2.,
    3. - consecutively, from 1 - and that sequence is what ends the table: the
    first heading that does not continue it is the section's own text resuming.
    A real section heading after a table never continues from 1, so it cannot
    be absorbed, whatever its distance from the opener.
    """
    if not matches:
        return set()

    starts = [m.start() for m in matches]
    suppressed: set[int] = set()

    for opener in APPARATUS_OPENERS.finditer(text):
        index = bisect.bisect_left(starts, opener.end())
        expected = 1
        while index < len(matches):
            number = matches[index].group("number").strip()
            if not number.isdigit() or int(number) != expected:
                break
            suppressed.add(starts[index])
            expected += 1
            index += 1

    return suppressed


def schedule_spans(text: str) -> list[tuple[int, str]]:
    """Where each Schedule starts, and its number, in document order.

    A Schedule is operative law - it is not dropped - but its clauses are
    numbered locally, from 1, so a Schedule 1 clause 11 and the Act's own
    section 11 are different provisions that would otherwise share a pinpoint.
    Knowing where each Schedule begins lets the splitter label them apart.
    """
    return [(m.start(), m.group(1).upper()) for m in SCHEDULE_HEADING.finditer(text)]


def find_body_start(text: str) -> int | None:
    """Character offset where the operative text begins, or None if not found.

    A table of provisions is a run of section headings with nothing between
    them. The body is the first section heading followed, within a line or two,
    by something that is not another heading - that is, by prose.

    Returns None when no such point exists, in which case the caller must not
    cut anything.
    """
    lines = text.splitlines()
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line) + 1

    for index, line in enumerate(lines):
        if not SECTION_RE.match(line.strip()):
            continue
        previous_line = lines[index - 1] if index > 0 else ""
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        # A wrapped contents line can look like prose, so an entry carrying a
        # page number is never treated as the start of the body.
        if _is_contents_entry(line, previous=previous_line, following=next_line):
            continue
        window = lines[index + 1 : index + 1 + PROSE_LOOKAHEAD]
        if any(not _is_structural(candidate) for candidate in window):
            return offsets[index]
    return None


class IngestionError(RuntimeError):
    """Ingestion could not complete. Nothing is left half-written."""


@dataclass(frozen=True, slots=True)
class Provision:
    """One section of a document, with its offsets into the document text."""

    section_number: str
    heading: str
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one document's ingestion produced."""

    document_id: int
    citation: str
    sha256: str
    provisions: int
    created: bool


def split_sections(text: str) -> list[Provision]:
    """Split a document into provisions by section heading, not by token count.

    Text before the first section (cover page, long title, table of provisions)
    is not emitted as a provision: it is not operative text and citing it would
    be misleading.

    If no section headings are found the whole document is returned as a single
    provision numbered ``(whole document)``, so nothing is silently dropped.
    """
    # Drop the table of provisions, if there is one. Its entries are section
    # numbers and headings with no operative text; stored as provisions they
    # produce duplicate citations and, being short, outrank the real sections
    # under BM25 length normalisation.
    offset = 0
    body_start = find_body_start(text)
    if body_start is not None:
        preceding = sum(1 for _ in SECTION_RE.finditer(text[:body_start]))
        if preceding >= CONTENTS_MIN_ENTRIES:
            offset = body_start
            text = text[body_start:]

    # Drop the endnotes, if the document has them. Their amendment history is
    # numbered like sections and restarts from 1, so left in it produces a
    # second provision competing for a section number the body already used.
    body_end = find_body_end(text, 0) if body_start is not None else None
    if body_end is not None:
        text = text[:body_end]

    matches = list(SECTION_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        if not stripped:
            return []
        return [
            Provision(
                section_number="(whole document)",
                heading="",
                text=stripped,
                char_start=0,
                char_end=len(text),
            )
        ]

    provisions: list[Provision] = []
    lines, offsets = _line_index(text)
    schedules = schedule_spans(text)
    apparatus = apparatus_rows(text, matches)
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end]
        if len(body.strip()) < MIN_PROVISION_CHARS:
            continue
        # Belt and braces: a contents entry anywhere - a second contents table,
        # a per-Part list - is never stored as a provision.
        previous_line, next_line = _adjacent_lines(text, start)
        if _is_contents_entry(match.group(0), previous=previous_line, following=next_line):
            continue
        # A commencement table, embedded in section 2's own body, wraps a
        # cell of prose between two dated rows - the same pagination signal
        # as a contents entry, just one line further away.
        if _is_table_row(text, start, lines, offsets):
            continue
        # An item of an in-body table or note block is part of its section,
        # not a provision of its own.
        if start in apparatus:
            continue
        number = match.group("number").strip()
        # A clause inside a Schedule is cited as a clause of that Schedule, not
        # as a section of the Act. Labelling it here keeps the law and removes
        # the collision at the same time: "Sch 1 cl 11" and "11" are two
        # pinpoints, which is what they are.
        enclosing = [n for position, n in schedules if position < start]
        if enclosing:
            number = f"Sch {enclosing[-1]} cl {number}"
        provisions.append(
            Provision(
                section_number=number,
                heading=match.group("heading").strip(),
                text=body.strip(),
                char_start=offset + start,
                char_end=offset + end,
            )
        )
    return provisions


def _validate(jurisdiction: str, doc_type: str, sha256: str) -> None:
    if jurisdiction not in JURISDICTIONS:
        raise IngestionError(f"jurisdiction must be one of {JURISDICTIONS}, got {jurisdiction!r}")
    if doc_type not in DOC_TYPES:
        raise IngestionError(f"doc_type must be one of {DOC_TYPES}, got {doc_type!r}")
    if len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256):
        raise IngestionError("sha256 must be 64 lowercase hex characters")


def ingest_document(
    *,
    jurisdiction: str,
    title: str,
    citation: str,
    source_url: str,
    snapshot_date: date,
    sha256: str,
    doc_type: str,
    text: str,
    fetched_at: datetime | None = None,
    conn: Connection[DictRow] | None = None,
) -> IngestResult:
    """Insert one document and its provisions in a single transaction.

    A provision is never stored without its parent document row: both are
    written inside one transaction, and the foreign key is NOT NULL.
    """
    _validate(jurisdiction, doc_type, sha256)

    provisions = split_sections(text)
    if not provisions:
        raise IngestionError(f"{citation}: no text to ingest")

    def write(target: Connection[DictRow]) -> IngestResult:
        return _write(
            target,
            jurisdiction=jurisdiction,
            title=title,
            citation=citation,
            source_url=source_url,
            snapshot_date=snapshot_date,
            sha256=sha256,
            doc_type=doc_type,
            fetched_at=fetched_at or datetime.now(UTC),
            provisions=provisions,
        )

    if conn is not None:
        return write(conn)
    with writable() as owned:
        result = write(owned)
        owned.commit()
        return result


def _write(
    conn: Connection[DictRow],
    *,
    jurisdiction: str,
    title: str,
    citation: str,
    source_url: str,
    snapshot_date: date,
    sha256: str,
    doc_type: str,
    fetched_at: datetime,
    provisions: list[Provision],
) -> IngestResult:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents
                (jurisdiction, title, citation, source_url, snapshot_date,
                 sha256, doc_type, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (citation, sha256) DO NOTHING
            RETURNING id
            """,
            (
                jurisdiction,
                title,
                citation,
                source_url,
                snapshot_date,
                sha256,
                doc_type,
                fetched_at,
            ),
        )
        row = cur.fetchone()
        if row is None:
            return _already_stored(cur, citation=citation, sha256=sha256)

        document_id = int(row["id"])
        vectors = embed([f"{p.heading}\n\n{p.text}".strip() for p in provisions])
        for provision, vector in zip(provisions, vectors, strict=True):
            cur.execute(
                """
                INSERT INTO provisions
                    (document_id, section_number, heading, text, embedding,
                     char_start, char_end, token_count)
                VALUES (
                    %s, %s, %s, %s, %s::vector, %s, %s,
                    (SELECT coalesce(sum(cardinality(positions)), 0)
                       FROM unnest(to_tsvector('english', %s)))
                )
                ON CONFLICT (document_id, section_number, char_start) DO NOTHING
                """,
                (
                    document_id,
                    provision.section_number,
                    provision.heading,
                    provision.text,
                    vector_literal(vector),
                    provision.char_start,
                    provision.char_end,
                    f"{provision.heading} {provision.text}",
                ),
            )

        reason = f"ingested {len(provisions)} provisions"
        cur.execute(
            """
            INSERT INTO ingestion_log (url, host, outcome, reason, sha256, document_id)
            VALUES (%s, %s, 'allowed', %s, %s, %s)
            """,
            (
                source_url,
                host_of(source_url) or "(corpus)",
                reason,
                sha256,
                document_id,
            ),
        )

    # The database row lives inside this transaction and is gone if a purge
    # cascades `documents` away. The file log is the one record meant to
    # survive that - so it must be written for every path that reaches here
    # (ingest_url, ingest_file, seed_from_corpus alike), not just the fetch
    # path that happened to have a logging call already.
    log_attempt(
        url=source_url,
        host=host_of(source_url) or "(corpus)",
        outcome="allowed",
        reason=reason,
        sha256=sha256,
    )

    return IngestResult(
        document_id=document_id,
        citation=citation,
        sha256=sha256,
        provisions=len(provisions),
        created=True,
    )


def _already_stored(cur: Cursor[DictRow], *, citation: str, sha256: str) -> IngestResult:
    """Same citation, same bytes: this exact snapshot is already stored."""
    cur.execute("SELECT id FROM documents WHERE citation = %s AND sha256 = %s", (citation, sha256))
    existing = cur.fetchone()
    if existing is None:  # pragma: no cover - only on a concurrent delete
        raise IngestionError(f"{citation}: document vanished during ingestion")
    document_id = int(existing["id"])
    cur.execute("SELECT count(*) AS n FROM provisions WHERE document_id = %s", (document_id,))
    counted = cur.fetchone()
    return IngestResult(
        document_id=document_id,
        citation=citation,
        sha256=sha256,
        provisions=int(counted["n"]) if counted else 0,
        created=False,
    )


def ingest_url(
    *,
    url: str,
    jurisdiction: str,
    title: str,
    citation: str,
    doc_type: str,
    snapshot_date: date | None = None,
) -> IngestResult:
    """Gap-filling fetch and ingest. Refuses any host outside the allowlist."""
    try:
        source = fetch(url)
    except SourceRefused as exc:
        _log_refusal(url=url, reason=str(exc))
        raise

    text = extract_text(source.body, content_type=source.content_type, origin=url)

    result = ingest_document(
        jurisdiction=jurisdiction,
        title=title,
        citation=citation,
        source_url=source.url,
        snapshot_date=snapshot_date or source.fetched_at.date(),
        sha256=source.sha256,
        doc_type=doc_type,
        text=text,
        fetched_at=source.fetched_at,
    )
    # BM25 reads N and avgdl from corpus_stats. Leaving it stale makes every
    # subsequent score wrong, and silently invalidates the calibrated
    # retrieval threshold.
    refresh_corpus_stats()
    return result


def extract_text(body: bytes, *, content_type: str = "", origin: str = "") -> str:
    """Turn downloaded bytes into the text that will become provisions.

    DOCX and HTML are converted; anything else must already be UTF-8 text. The
    sha256 recorded for a document is always taken over the *original* bytes, so
    deriving text here never weakens provenance: the original stays on disk and
    the hash still identifies exactly what was downloaded.
    """
    where = origin or "input"

    if looks_like_docx(body, content_type):
        text = docx_to_text(body)
        if not text.strip():
            raise IngestionError(f"{where}: no text survived DOCX extraction")
        return text

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestionError(
            f"{where}: body is neither DOCX nor UTF-8 text. Convert it before ingesting."
        ) from exc

    if looks_like_html(text, content_type):
        text = html_to_text(text)
        if not text.strip():
            raise IngestionError(f"{where}: no text survived HTML extraction")
    return text


def ingest_file(
    *,
    path: Path,
    source_url: str,
    jurisdiction: str,
    title: str,
    citation: str,
    doc_type: str,
    snapshot_date: date | None = None,
) -> IngestResult:
    """Ingest a document already on disk, recording where it came from.

    For sources that cannot be fetched programmatically - the Federal Register
    publishes authorised Acts as Word documents behind a client-rendered page -
    the file is downloaded by hand and ingested here. ``source_url`` is recorded
    as provenance and is still checked against the host allowlist, so a local
    file cannot be used to launder an off-allowlist source.
    """
    try:
        check_url(source_url)
    except SourceRefused as exc:
        _log_refusal(url=source_url, reason=str(exc))
        log_attempt(url=source_url, host=host_of(source_url), outcome="refused", reason=str(exc))
        raise

    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    text = extract_text(body, origin=str(path))

    result = ingest_document(
        jurisdiction=jurisdiction,
        title=title,
        citation=citation,
        source_url=source_url,
        snapshot_date=snapshot_date or datetime.now(UTC).date(),
        sha256=digest,
        doc_type=doc_type,
        text=text,
    )
    refresh_corpus_stats()
    return result


def _log_refusal(*, url: str, reason: str) -> None:
    """Mirror a refusal into the database. The file log is the durable record."""
    try:
        with writable(connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingestion_log (url, host, outcome, reason) VALUES (%s, %s, %s, %s)",
                (url, host_of(url) or "(none)", "refused", reason),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - never let logging mask the refusal itself
        pass


CORPUS_TYPE_MAP = {
    "primary_legislation": "act",
    "secondary_legislation": "regulation",
    "decision": "case",
    "bill": "act",
}

#: The corpus spells jurisdictions out in full; Michael's schema uses short
#: codes. Anything absent from this map is a jurisdiction Michael does not
#: hold, and is skipped. Getting this wrong is silent: an unmapped label looks
#: exactly like an out-of-scope one, which is how every WA document was
#: discarded until this map existed.
CORPUS_JURISDICTION_MAP = {
    "commonwealth": "commonwealth",
    "western_australia": "wa",
    "wa": "wa",
}


def seed_from_corpus(
    *,
    limit: int | None = None,
    jurisdictions: Iterable[str] = ("wa", "commonwealth"),
    doc_types: Iterable[str] | None = None,
    dataset_id: str = "isaacus/open-australian-legal-corpus",
) -> list[IngestResult]:
    """Seed from the Open Australian Legal Corpus, filtered by jurisdiction.

    Streams the dataset so the whole corpus is never held in memory. The sha256
    is computed over the corpus record's text bytes, which are the original
    bytes Michael received for that document.

    ``doc_types`` narrows what is stored, e.g. ("act", "regulation") to take
    legislation and leave case law out. None takes everything mappable.
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - optional extra
        raise IngestionError(
            "The corpus seed needs the 'corpus' extra: uv sync --extra corpus"
        ) from exc

    wanted = {j.lower() for j in jurisdictions}
    unknown = wanted - set(JURISDICTIONS)
    if unknown:
        raise IngestionError(f"unsupported jurisdictions: {sorted(unknown)}")

    wanted_types = {d.lower() for d in doc_types} if doc_types is not None else None
    if wanted_types is not None:
        unknown_types = wanted_types - set(DOC_TYPES)
        if unknown_types:
            raise IngestionError(f"unsupported doc_types: {sorted(unknown_types)}")

    stream = load_dataset(dataset_id, split="corpus", streaming=True)
    results: list[IngestResult] = []
    with writable() as conn:
        for record in normalise_corpus_records(stream, wanted, wanted_types):
            body = str(record["text"]).encode("utf-8")
            results.append(
                ingest_document(
                    jurisdiction=str(record["jurisdiction"]),
                    title=str(record["title"]),
                    citation=str(record["citation"]),
                    source_url=str(record["source_url"]),
                    snapshot_date=record["snapshot_date"],  # type: ignore[arg-type]
                    sha256=hashlib.sha256(body).hexdigest(),
                    doc_type=str(record["doc_type"]),
                    text=str(record["text"]),
                    conn=conn,
                )
            )
            if limit is not None and len(results) >= limit:
                break
        conn.commit()

    refresh_corpus_stats()
    return results


def normalise_corpus_records(
    stream: Iterable[dict[str, object]],
    wanted: set[str],
    wanted_types: set[str] | None = None,
) -> Iterator[dict[str, object]]:
    """Map corpus records onto Michael's schema, skipping what cannot be mapped.

    A record with no citation, no text, or a type outside CORPUS_TYPE_MAP is
    dropped rather than stored under a guessed value. ``wanted_types`` narrows
    it further to the given schema doc_types; None accepts all of them.
    """
    for raw in stream:
        jurisdiction = CORPUS_JURISDICTION_MAP.get(str(raw.get("jurisdiction", "")).lower())
        if jurisdiction is None or jurisdiction not in wanted:
            continue
        doc_type = CORPUS_TYPE_MAP.get(str(raw.get("type", "")).lower())
        text = str(raw.get("text", "") or "")
        citation = str(raw.get("citation", "") or "").strip()
        if not doc_type or not text.strip() or not citation:
            continue
        if wanted_types is not None and doc_type not in wanted_types:
            continue
        yield {
            "jurisdiction": jurisdiction,
            "title": citation,
            "citation": citation,
            "source_url": str(raw.get("url", "") or "").strip() or "(corpus record)",
            "snapshot_date": snapshot_date_of(raw.get("date")),
            "doc_type": doc_type,
            "text": text,
        }


def snapshot_date_of(value: object) -> date:
    """Parse a corpus date, falling back to today when it is absent or unusable.

    A citation is pinned to its snapshot date, so one is always stored; an
    unparseable date becomes the ingestion date rather than a guess at the
    document's own date.
    """
    if isinstance(value, str) and value.strip():
        text = value.strip()
        for fmt, width in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10), ("%Y", 4)):
            try:
                return datetime.strptime(text[:width], fmt).date()
            except ValueError:
                continue
    return datetime.now(UTC).date()


def ingestion_settings_summary() -> dict[str, object]:
    """What the ingestion tool will use. Handy as a pre-flight check."""
    config = settings()
    return {
        "database": config.database_url.rsplit("@", 1)[-1],
        "sources_dir": str(config.sources_dir),
        "ingestion_log": str(config.ingestion_log),
        "embedding_model": config.embedding_model,
        "embedding_dim": config.embedding_dim,
    }
