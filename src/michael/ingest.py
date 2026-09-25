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
from michael.sources import (
    GUIDANCE_DOC_TYPE,
    SourceRefused,
    check_doc_type,
    check_url,
    fetch,
    host_of,
    log_attempt,
)

#: A section heading in Australian legislation: a number that may carry letter
#: suffixes ("15", "15A", "23AB"), followed by a heading on the same line.
#: Deliberately conservative - a line that does not look like this stays inside
#: the preceding section rather than starting a spurious one.
#: The heading capture has no upper bound on length. It used to be capped at
#: 150 characters after the first ("[^\n]{0,150}"), but the cap did not make
#: long headings safer to reject - the "$" anchor under re.MULTILINE already
#: requires the WHOLE line to match, so a heading-shaped line is bounded by
#: its own newline whatever its length. All the cap did was make the pattern
#: fail to match a genuine heading whenever the heading text itself ran past
#: 150 characters (a long title is common in WA regulations and Fair Work
#: Act divisions), and a heading the pattern does not match is not "kept as
#: a heading" - it is silently absorbed into the body of the PREVIOUS
#: section, with no warning. (W1-S2.)
SECTION_RE = re.compile(
    r"^[ \t]*(?P<number>\d{1,4}[A-Z]{0,3})[.)]?[ \t–—-]+(?P<heading>[A-Z][^\n]*)$",
    re.MULTILINE,
)

#: A regulation heading may also be numbered by Part: "2.57 Interpretation",
#: "2.57A Meaning of earnings", and in a Schedule "189.211 Criteria". The
#: Migration Regulations 1994 (Cth) number every regulation this way, and
#: SECTION_RE cannot match one: its number stops at the dot and then demands
#: whitespace. Measured on compilation 288 before this existed: volume 1 split
#: into 74 provisions, none of them in Parts 1-5, so Part 2A was simply absent,
#: and volumes 2 and 4 each came out as ONE "(whole document)" row of about
#: 780,000 characters - embedded from its opening only, cited without a
#: pinpoint, and passed by detect_headings_only because one row has nothing to
#: compare against.
#:
#: Used for doc_type "regulation" only. An award numbers its SUBclauses the
#: same way - "10. Types of employment" then "10.1 A full-time employee is" -
#: and under this pattern every subclause would become a provision of its own.
#: A plain number still matches exactly as it does under SECTION_RE, so a
#: regulation numbered "3. Terms used" splits as it always did.
#:
#: Two refinements, both from the shape of Schedule 2 of the Migration
#: Regulations, where each visa subclass is laid out as
#:
#:     010.2—Primary criteria                               (Division)
#:     010.21—Criteria to be satisfied at the time of ...   (Subdivision)
#:     010.211                                              (clause)
#:     (1) The applicant meets the requirements of ...
#:
#: * A clause number stands ALONE on its line, with no heading - "010.211" -
#:   so a bare three-digit.three-digit number is accepted as a heading with an
#:   empty title. Nothing shorter is: a bare "2.57" or "177" on its own line
#:   is a page number or a cross-reference far more often than a provision.
#: * A Division or Subdivision heading - three digits, a dot, one or two
#:   digits, "010.21—..." - is NOT a provision. Taken as one it was cited as
#:   "Sch 2 cl 010.21", a clause that does not exist, carrying the real
#:   clause 010.211 inside it. It stays in the text of the clause before it,
#:   as a Part or Division heading of an Act does. A regulation of Parts 1-5
#:   ("2.57") has a one-digit Part number and is unaffected.
DOTTED_SECTION_RE = re.compile(
    r"^[ \t]*(?!\d{3}\.\d{1,2}(?![\d]))"
    r"(?P<number>\d{1,4}[A-Z]{0,3}(?:\.\d{1,4}[A-Z]{0,3})?)"
    r"(?:[.)]?[ \t–—-]+(?P<heading>[A-Z][^\n]*)"
    r"|(?:(?<=\d{3}\.\d{3})|(?<=\d{3}\.\d{3}[A-Z]))[ \t]*)$",
    re.MULTILINE,
)


#: The shape of a Schedule 2 clause number - subclass, dot, three digits.
SCHEDULE_2_CLAUSE = re.compile(r"\d{3}\.\d{3}[A-Z]{0,3}")


def section_pattern(doc_type: str) -> re.Pattern[str]:
    """The heading pattern ``doc_type`` is split with; see DOTTED_SECTION_RE."""
    return DOTTED_SECTION_RE if doc_type == "regulation" else SECTION_RE


MIN_PROVISION_CHARS = 40

#: A subsection marker - "(1)", "(2)", "(23)" - the clearest sign that a
#: provision carries real operative structure rather than a bare summary.
SUBSECTION_MARKER_RE = re.compile(r"\(\d{1,3}\)")

#: How many provisions a document needs before a document-wide proportion is
#: meaningful. Below this, one or two naturally marker-free sections (a short
#: title, a commencement clause) would otherwise look like "all of them".
HEADINGS_ONLY_MIN_PROVISIONS = 3

#: A genuine Act's provisions vary a lot in length - a one-line short title
#: next to a page-long substantive section. A generated or extracted stub set
#: does not: every heading gets roughly the same boilerplate sentence. This is
#: the ratio between the longest and shortest provision body in the document;
#: below it, the lengths are suspiciously uniform. Calibrated against real
#: text on both sides:
#:   - the W1-2 fixture (three "guide"-style stubs) sits at ratio 1.11;
#:   - this project's own short-Act test fixture (SAMPLE in test_ingest.py -
#:     a short title plus two real, differently-worded definitions) sits at
#:     1.91, and must NOT be flagged;
#:   - every zero-marker document with 3+ provisions in the full 205-document
#:     production corpus sits at 2.72 or above (Corporations (Taxing) Act
#:     1990 (WA) is the closest real case).
#: 1.5 sits with margin below both real cases and above the fixture.
HEADINGS_ONLY_MAX_LENGTH_RATIO = 1.5

#: A provision needs at least this many subsection markers, not just one, to
#: count as showing real operative structure. A lone "(1)" - a footnote
#: marker, a list label, any decorative digit in parentheses - satisfies
#: SUBSECTION_MARKER_RE on its own and, before this fix, that single match
#: anywhere in the document was enough to disable detect_headings_only for
#: the whole thing. Real subsections run in sequence within one provision -
#: "(1)" is followed by "(2)" - so requiring two catches that shape without
#: punishing genuine structure, which is never a single isolated marker.
MIN_MARKERS_PER_PROVISION = 2

#: How many provisions must clear HEADINGS_ONLY_MAX_LENGTH_RATIO (measured
#: against the document's shortest provision) before the length spread counts
#: as genuine variation. One is not enough: a stub set generated from a
#: template is uniform except for whichever single provision was padded out -
#: by an attacker or by accident - and before this fix that one outlier alone
#: pushed max/min past the ratio and passed the whole document. Requiring a
#: second long provision is what makes the check resistant to a single
#: outlier at either end.
HEADINGS_ONLY_MIN_LONG_PROVISIONS = 2

#: The tightened, "more than one long provision" length check only applies
#: from this many provisions up. Below it, the plain max/min ratio - the
#: original, calibrated check - applies instead. Measured against the full
#: 205-document production corpus: applying HEADINGS_ONLY_MIN_LONG_PROVISIONS
#: at the floor of HEADINGS_ONLY_MIN_PROVISIONS (3) produced two real false
#: positives - Corporations (Taxing) Act 1990 (WA) and a Town Planning
#: by-law - because a genuine three-provision Act is overwhelmingly "short
#: title, commencement, one substantive section": exactly one long provision,
#: by its own shape, indistinguishable at that size from the attack this
#: tightening targets. 5 is the smallest size at which "several short stubs
#: plus one padded one" (the attack) and "several genuinely-varied real
#: sections" stop looking the same on this measure; re-running the same
#: 205-document measurement at 5 found no false positives.
HEADINGS_ONLY_PROPORTIONAL_MIN_PROVISIONS = 5

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
    # A compilation reprint repeats "Western Australia" as a running header -
    # once at the top of the document, again just past the table of
    # provisions. It is boilerplate, never the first word of an operative
    # sentence, but nothing else here recognises it: found only when
    # extending the prose lookahead in find_body_start to Schedule headings
    # too (_opening_schedule), where an untitled reprint's contents block
    # ends "Schedule 1 - <title>", "Notes", "Compilation table N", "Western
    # Australia" - and that last line, unrecognised, looked like the real
    # prose that tells the lookahead a heading is genuine.
    "western australia",
    # The same reprint boilerplate, a different sentence: "Reprinted under
    # the Reprints Act 1984 as at <date>" appears, sometimes indented, in
    # the same spot "Western Australia" does - between the contents block's
    # tail and the repeated document title.
    "reprinted under the reprints act",
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


def _is_paginated_row(stripped: str, *, previous: str = "", following: str = "") -> bool:
    """True when a trailing bare number on ``stripped`` is a page reference.

    A trailing bare number is not, by itself, evidence of anything: a page
    reference (``15A Meaning of casual employee 68``) and a cited Act's year
    (``26WD Exception-notification under the My Health Records Act 2012``) are
    both a heading-shaped line followed by a run of digits, and no rule about
    the string alone - a digit count, a plausible year range - tells them apart,
    because a plausible year and a plausible page number overlap completely.

    What differs is what is *around* the number. A table of provisions paginates
    every row - Part, Division, section and Schedule headings alike - so its
    rows cluster: each one ends in a bare number, and so does its neighbour. A
    cited year is part of the heading's own text; it appears whether or not the
    row before or after it does the same, because a compiled Act's body is not
    paginated inline. So the number is only a page reference when a
    neighbouring row - the line directly above or below it in the source
    document - carries one too. ``previous`` and ``following`` are exactly
    that: real, adjacent lines, not a guess about the number itself.

    This is the shared reasoning behind :func:`_is_contents_entry` (section
    headings) and the Schedule-heading check in :func:`_opening_schedule`
    below - one claim about pagination, applied to two shapes of heading.
    """
    if not _ends_in_bare_number(stripped):
        return False
    return _ends_in_bare_number(previous) or _ends_in_bare_number(following)


def _is_contents_entry(
    line: str,
    *,
    previous: str = "",
    following: str = "",
    pattern: re.Pattern[str] = SECTION_RE,
) -> bool:
    """True when ``line`` is a row of a table of provisions, not an operative heading."""
    stripped = line.strip()
    if not pattern.match(stripped):
        return False
    return _is_paginated_row(stripped, previous=previous, following=following)


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


def _is_structural(line: str, *, pattern: re.Pattern[str] = SECTION_RE) -> bool:
    """True when a line is a heading, a page number or blank - never operative text."""
    stripped = line.strip()
    if not stripped:
        return True
    if pattern.match(stripped):
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
#: An ordinal Schedule heading writes its number as a word. Old-style WA
#: Agreement Acts use this form throughout - "First Schedule - Iron Ore
#: Agreement". The word IS the number, so it maps to one.
_SCHEDULE_ORDINALS = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
}

SCHEDULE_HEADING = re.compile(
    r"^(?:"
    r"schedule[ \t]+(?P<number>\d+[A-Z]*|[IVXLC]+)"
    r"|(?P<ordinal>" + "|".join(_SCHEDULE_ORDINALS) + r")[ \t]+schedule"
    r"|(?:the[ \t]+)?(?P<bare>schedule)"
    r")[ \t]*(?:[—–-]|$)",
    re.IGNORECASE | re.MULTILINE,
)


def _schedule_number(match: re.Match[str]) -> str | None:
    """The Schedule's number as written, or None when the heading has none."""
    numbered = match.group("number")
    if numbered:
        return numbered.upper()
    ordinal = match.group("ordinal")
    if ordinal:
        return _SCHEDULE_ORDINALS[ordinal.lower()]
    return None


def _numbers_its_schedules(text: str) -> bool:
    """Does this document give any Schedule of its own a number?

    If it does, a bare "Schedule" line is a cross-reference or a contents row,
    not the sole Schedule - and labelling the clauses after it "Sch 1" would
    assert a number the document does not use.
    """
    return any(_schedule_number(m) is not None for m in SCHEDULE_HEADING.finditer(text))


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
    # A "Note:" line opens a note block just as the longer "Notes for this
    # section:" does. Offshore Minerals Act 2003 (WA) carries 31 of them, each
    # followed by items numbered from 1, so each became another section 1
    # competing with the real Short title provision - 32 provisions under one
    # pinpoint. The colon is required and is the whole difference between a
    # note block and a front-matter heading: Chattel Securities Regulations
    # 1988 (WA) lists a bare "Notes" line in its contents immediately before
    # the body, and matching that suppressed the entire document, all eight
    # provisions of it.
    # The noun is not always "section". Offshore Minerals Act 2003 (WA) heads
    # most of its note blocks "Notes for this subsection:" and Supreme Court
    # (Fees) Regulations 2002 (WA) heads each fee row's "Notes for this item:".
    # Hardcoding "section" left both unrecognised - 79 excess pinpoints
    # between them, the two largest remaining groups in the corpus - so the
    # noun is now any single word. The colon is what still separates a note
    # opener from a contents row, and it carries more weight than ever now
    # that the noun no longer narrows the match: a bare "Notes" line in
    # Chattel Securities Regulations 1988 (WA) sits in its contents block
    # immediately before the body, and matching that suppressed the entire
    # document, all eight provisions of it.
    r"^(?:column\s+1\b|notes?\b[^\n]*for this \w+[ \t]*:|notes?[ \t]*:[ \t]*$)",
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


#: The cover page of a multi-volume Commonwealth compilation lists what each
#: volume holds, one item per line:
#:
#:     This compilation is in 2 volumes
#:     Volume 1:
#:     sections 1-261K
#:     Volume 2:
#:     sections 262-507
#:     Schedule
#:     Endnotes
#:     Each volume has its own contents
#:
#: Those "Schedule" / "Schedule 1" lines are a listing, not headings, and the
#: line after each one is another listing line - which _has_prose_ahead reads
#: as prose, because "Volume 2:" is not heading-shaped. Before this was
#: excluded, the listing's "Schedule" made _opening_schedule report that the
#: Migration Act 1958 (Cth) body opens inside Schedule 1, and every section of
#: both volumes - s 1 to s 507 - was stored as "Sch 1 cl N". The Migration
#: Regulations 1994 cover lists "Schedule 1" and did the same to regs 1.01 to
#: 5.45. The block is bounded by the compilation's own fixed wording at both
#: ends, so nothing outside it is skipped.
VOLUME_LISTING = re.compile(
    r"^This compilation is in \d+ volumes[ \t]*$.*?^Each volume has its own contents[ \t]*$",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def schedule_spans(text: str) -> list[tuple[int, str]]:
    """Where each Schedule starts, and its number, in document order.

    A Schedule is operative law - it is not dropped - but its clauses are
    numbered locally, from 1, so a Schedule 1 clause 11 and the Act's own
    section 11 are different provisions that would otherwise share a pinpoint.
    Knowing where each Schedule begins lets the splitter label them apart.
    """
    numbered = _numbers_its_schedules(text)
    spans: list[tuple[int, str]] = []
    # The cover's volume listing, when the text still has it - a document
    # whose table of provisions is too short to be cut keeps its cover page,
    # and the listing's "Schedule" line would otherwise open a Schedule that
    # swallows every section after it. See VOLUME_LISTING.
    cover = VOLUME_LISTING.search(text)
    for match in SCHEDULE_HEADING.finditer(text):
        if cover is not None and cover.start() <= match.start() < cover.end():
            continue
        number = _schedule_number(match)
        if number is None:
            # An unnumbered "Schedule - Title". A document with exactly one
            # Schedule does not number it, and its clauses restart at 1, so
            # before this they fell through with plain numeric ids and
            # collided with the Act's own sections - 87 of production's 151
            # duplicate pinpoint groups, every one of them silent.
            if numbered:
                continue
            number = "1"
        spans.append((match.start(), number))
    return spans


def _opening_schedule(
    text: str, body_start: int, *, pattern: re.Pattern[str] = SECTION_RE
) -> str | None:
    """The Schedule a document's operative body starts *inside*, if any.

    A compilation volume is not always the whole Act. Fair Work Act volume 04
    begins mid-Schedule 1: its own table of provisions runs long enough that
    ``find_body_start`` correctly cuts everything before the real body - but
    the real "Schedule 1" heading sits inside that cut, at char 20,902,
    900 characters before ``body_start`` at 21,122. ``schedule_spans`` only
    ever sees the text *after* the cut, so it finds Schedules 2 to 5 and never
    1: every Schedule 1 clause falls through with a plain numeric id,
    colliding with the Act's own section of the same number - Fair Work
    s 47 and Sch 1 cl 47 both stored as "47".

    So: look for a Schedule heading in the ORIGINAL, uncut text, at or before
    ``body_start``, and use the last one found - the Schedule the cut text
    opens inside. Two things matter for this to be safe, and both were needed
    - a version checked only against Fair Work volume 04 passed while the
    first still misfired on a second, unrelated real Act:

    - **It must be a real heading, not a contents row - checked two ways, not
      one.** The table of provisions being cut away lists "Schedule 1-...
      relating to amendments of this Act 1" with a trailing page number; the
      real heading, further on, has none. :data:`SCHEDULE_HEADING` cannot
      tell them apart on its own - both carry a dash after the number - so
      this first tries :func:`_is_paginated_row`, the pagination-clustering
      check :func:`_is_contents_entry` already makes for section headings.
      That alone is not enough: the real Bank of Western Australia Act 1995
      (WA)'s own contents block has a Schedule row with NO trailing page
      number at all - a rendering gap in that document, not evidence of a
      real heading - and pagination-only wrongly seeded "Sch 2" from the very
      start of that Act's body, mislabelling its own real section 1. So this
      also asks the same question :func:`find_body_start` asks of a
      candidate: is it followed, within a line or two, by something that is
      not itself another heading - real prose? A contents-row Schedule
      heading is followed by more contents rows; a real one is followed by
      the Schedule's own text. Both checks must agree.
    - **A bare "Schedule N", with no title on the same line, has its title
      on the next one** - "Schedule I\nSingle sideband installations",
      "Schedule 3\nAcoustic output descriptors and labels" - and that title
      is not operative prose either, however little it resembles a heading
      itself. Not skipping it made the lookahead see real text one line too
      early and wrongly call several WA regulations' own contents rows
      real headings - and it recurs: three Schedules listed back to back,
      each bare with its title wrapped ("Schedule 1\nEquipment\nSchedule
      2\nImplementation dates\nSchedule 3\n..."), needs the same skip
      applied to every one of them met while looking ahead, not only the
      one being tested - so the lookahead walks forward line by line
      rather than taking a fixed slice, treating each further bare
      "Schedule N" it meets, and the wrapped title after it, as structural
      too.
    - **Finding nothing means nothing.** A single-volume Act with no Schedule
      heading before its body starts must keep plain section ids - the same
      shape of error as the monotonic floor, wrong in the direction that
      corrupts citations, so this returns ``None`` rather than guessing.
    """
    lines, offsets = _line_index(text)
    numbered = _numbers_its_schedules(text)
    last_real: str | None = None
    cover = VOLUME_LISTING.search(text, 0, body_start)
    for match in SCHEDULE_HEADING.finditer(text):
        if match.start() > body_start:
            break
        if cover is not None and cover.start() <= match.start() < cover.end():
            continue
        line_end = text.find("\n", match.start())
        line = text[match.start() : line_end if line_end != -1 else len(text)]
        previous, following = _adjacent_lines(text, match.start())
        if _is_paginated_row(line.strip(), previous=previous, following=following):
            continue
        index = bisect.bisect_right(offsets, match.start()) - 1
        lookahead_start = index + 1
        if re.fullmatch(r"schedule\s+\S+", line.strip(), re.IGNORECASE):
            lookahead_start += 1  # this heading's own title wraps onto the next line
        if _has_prose_ahead(lines, lookahead_start, pattern=pattern):
            number = _schedule_number(match)
            if number is None:
                if numbered:
                    continue
                number = "1"
            last_real = number
    return last_real


def _has_prose_ahead(
    lines: list[str], start: int, *, pattern: re.Pattern[str] = SECTION_RE
) -> bool:
    """Does real prose appear within :data:`PROSE_LOOKAHEAD` lines of ``start``?

    Walked rather than sliced: a bare "Schedule N" met while looking ahead is
    itself structural, and so - not operative prose, however little it
    resembles a heading - is the line right after it, that Schedule's own
    title wrapped onto its own line. Skipping both lets a run of several such
    headings, back to back, be walked through without any of their wrapped
    titles being mistaken for the real prose this is looking for.
    """
    found = 0
    index = start
    while index < len(lines) and found < PROSE_LOOKAHEAD:
        line = lines[index]
        if re.fullmatch(r"schedule\s+\S+", line.strip(), re.IGNORECASE):
            index += 2  # the heading, and its wrapped title
            continue
        if not _is_structural(line, pattern=pattern):
            return True
        found += 1
        index += 1
    return False


def find_body_start(text: str, *, pattern: re.Pattern[str] = SECTION_RE) -> int | None:
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
        if not pattern.match(line.strip()):
            continue
        previous_line = lines[index - 1] if index > 0 else ""
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        # A wrapped contents line can look like prose, so an entry carrying a
        # page number is never treated as the start of the body.
        if _is_contents_entry(line, previous=previous_line, following=next_line, pattern=pattern):
            continue
        window = lines[index + 1 : index + 1 + PROSE_LOOKAHEAD]
        if any(not _is_structural(candidate, pattern=pattern) for candidate in window):
            return offsets[index]
    return None


class IngestionError(RuntimeError):
    """Ingestion could not complete. Nothing is left half-written."""


@dataclass(frozen=True, slots=True)
class Provision:
    """One citable unit of a document, with its offsets into the document text.

    ``section_number`` is the identifier as the document itself writes it -
    ``15A``, ``Sch 1 cl 3``, ``12``, or the sentinel ``(whole document)``.
    ``unit_type`` says what KIND of identifier that is, and therefore how it
    must be cited; see :data:`michael.schema.UNIT_TYPES`. The field is here
    rather than derived from the document's type because one judgment
    produces more than one kind.
    """

    section_number: str
    heading: str
    text: str
    char_start: int
    char_end: int
    unit_type: str = "section"


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one document's ingestion produced."""

    document_id: int
    citation: str
    sha256: str
    provisions: int
    created: bool
    #: Anything about the split a reader must not have to infer from a count.
    #: A judgment whose report carries no paragraph numbers says so here, so
    #: "1 provision" is never read as a splitter failure - or, worse, as
    #: paragraph numbers that were there.
    note: str = ""


def split_sections(text: str, *, pattern: re.Pattern[str] = SECTION_RE) -> list[Provision]:
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
    opening_schedule: str | None = None
    body_start = find_body_start(text, pattern=pattern)
    if body_start is not None:
        preceding = sum(1 for _ in pattern.finditer(text[:body_start]))
        if preceding >= CONTENTS_MIN_ENTRIES:
            # A compilation volume can begin mid-Schedule: find the real
            # Schedule heading, if any, that the cut text opens inside -
            # before the cut, so it still sees what the cut removes.
            opening_schedule = _opening_schedule(text, body_start, pattern=pattern)
            offset = body_start
            text = text[body_start:]

    # Drop the endnotes, if the document has them. Their amendment history is
    # numbered like sections and restarts from 1, so left in it produces a
    # second provision competing for a section number the body already used.
    body_end = find_body_end(text, 0) if body_start is not None else None
    if body_end is not None:
        text = text[:body_end]

    matches = list(pattern.finditer(text))
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
                unit_type="document",
            )
        ]

    provisions: list[Provision] = []
    lines, offsets = _line_index(text)
    schedules = schedule_spans(text)
    if opening_schedule is not None:
        # The body (post-cut) opens inside this Schedule, before any Schedule
        # heading schedule_spans can see within it - so it applies from the
        # very first character until a real in-body heading takes over.
        # Position -1, not 0: a clause can itself start at char 0 of the cut
        # text, and "enclosing" below is a strict "<" on position.
        schedules = [(-1, opening_schedule), *schedules]
    apparatus = apparatus_rows(text, matches)

    # Which heading candidates survive, decided BEFORE any boundary is drawn.
    # A row rejected here is not a provision, and a provision must therefore
    # run THROUGH it to the next surviving heading. Ending a provision at the
    # next candidate instead - which is what this did - left every rejected
    # row's text in no provision at all: apparatus_rows' own docstring says
    # such a row "is part of a real section and stays in that section's text",
    # and it did not. Measured on Supreme Court (Fees) Regulations 2002 (WA),
    # whose Schedule 1 is one long run of fee items and their note blocks:
    # 32,362 characters - the whole fee schedule - sat past the last provision
    # in no provision at all, unreachable by any search.
    kept: list[int] = []
    for index, match in enumerate(matches):
        start = match.start()
        # Belt and braces: a contents entry anywhere - a second contents table,
        # a per-Part list - is never stored as a provision.
        previous_line, next_line = _adjacent_lines(text, start)
        if _is_contents_entry(
            match.group(0), previous=previous_line, following=next_line, pattern=pattern
        ):
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
        # A Schedule 2 clause number is a clause of Schedule 2 and of nothing
        # else. Met inside another Schedule it is quoted: Migration
        # Regulations Sch 13 cl 9903 directs that Division 132.3 be read "as
        # if ... replaced with the following", then sets out clauses 132.311
        # to 132.314 in full. Split off, they were cited as "Sch 13 cl
        # 132.311", which does not exist; kept, they stay in 9903, which is
        # where the law put them.
        if pattern is DOTTED_SECTION_RE and SCHEDULE_2_CLAUSE.fullmatch(match.group("number")):
            enclosing_now = [n for position, n in schedules if position < start]
            if not enclosing_now or enclosing_now[-1] != "2":
                continue
        kept.append(index)

    for position, index in enumerate(kept):
        match = matches[index]
        start = match.start()
        end = matches[kept[position + 1]].start() if position + 1 < len(kept) else len(text)
        body = text[start:end]
        # The floor exists to drop a heading with nothing under it. A bare
        # Schedule 2 clause number has no heading to be left alone with, and
        # its whole text can be shorter than the floor - "417.611 / Conditions
        # 8547 and 8548." is 33 characters and is the visa's conditions.
        # Dropped, that text was in no provision at all.
        heading_less = match.group("heading") is None
        too_short = len(body.strip()) < MIN_PROVISION_CHARS
        if too_short and not (heading_less and body.strip() != match.group(0).strip()):
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
                # None for a bare Schedule 2 clause number; see DOTTED_SECTION_RE.
                heading=(match.group("heading") or "").strip(),
                text=body.strip(),
                char_start=offset + start,
                char_end=offset + end,
            )
        )
    return provisions


# --- judgments ------------------------------------------------------------
#
# A judgment is not legislation and must not be split as if it were.
# split_sections() looks for a section heading - a number followed by a
# capitalised title on its own line - and a judgment has none. What it finds
# instead is the first line of each numbered paragraph, which it then stores
# in `section_number` with the paragraph's opening words as a `heading`, and
# pinpoint() renders as "s 2". Measured on the three judgments seeded into the
# local corpus, it also picked up, as if they were paragraphs of the judgment
# itself: paragraphs 10-14 of St Barbara Mines quoted inside Cromwell's
# paragraph 40, paragraphs 11-18 of Drillsearch quoted inside paragraph 42,
# paragraphs 52, 53, 143 and 145 of an affidavit, and the text of ss 659AA and
# 659B of the Corporations Act. Every one of those was stored under a pinpoint
# asserting it was a numbered unit of Cromwell. That is not a formatting
# defect; it is a citation that names the wrong court, in the wrong case, on
# the wrong point.

#: Where the reasons begin. Australian superior-court reports put this on its
#: own line, in capitals, immediately before the first numbered paragraph.
#:
#: The line may be qualified - "EX TEMPORE REASONS FOR DECISION", "FURTHER
#: REASONS FOR JUDGMENT", "REVISED REASONS FOR JUDGMENT" - so the heading is
#: not required to begin the line. What IS required is that the whole line is
#: in capitals: that is what separates a heading from a sentence of ordinary
#: prose mentioning "the reasons for judgment below", which would otherwise
#: move the start of the reasons into the middle of a paragraph. Measured:
#: requiring the line to *start* with "REASONS" missed Adlam v Bauer [1999]
#: FCA 634 entirely, and the splitter then anchored its paragraph numbering
#: on the date line "10 MAY 1999" and emitted one provision instead of 14.
REASONS_HEADING = re.compile(
    r"^[ \t]*(?:[A-Z][^a-z\n]*[ \t])?"
    r"REASONS?\s+FOR\s+(?:JUDGMENT|DECISION|RULING|SENTENCE|ORDER)S?\b[^\n]*$",
    re.MULTILINE,
)

#: Where the orders begin: "THE COURT ORDERS THAT:", "THE COURT DECLARES
#: THAT:", "THE COURT DIRECTS THAT:". Matched on the shape rather than on a
#: list of verbs, so an unusual one is still found.
ORDERS_OPENER = re.compile(
    r"^[ \t]*(?:THE\s+COURT|IT\s+IS)\b[^\n:]{0,60}\bTHAT\s*:[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

#: The associate's certificate that closes the reasons. It is not part of any
#: paragraph and, left in, it is absorbed into the last one - where it reads
#: as the court's own words.
CERTIFICATE = re.compile(r"^[ \t]*I\s+certify\s+that\b", re.MULTILINE)

#: The certificate says what the report is divided into: "the preceding
#: eight (8) numbered paragraphs", or "this and the preceding three (3)
#: pages". That is the report stating its own unit of citation, which is
#: better evidence than any shape rule, and it is the one signal that
#: distinguishes a genuinely unnumbered judgment from one this code failed to
#: parse.
CERTIFIED_UNIT = re.compile(
    r"I\s+certify\s+that[\s\S]{0,200}?\b(?P<kind>numbered\s+paragraphs?|pages?)\b",
    re.IGNORECASE,
)


def certified_unit(text: str) -> str | None:
    """What the report's own certificate says it is divided into.

    ``"paragraph"``, ``"page"``, or ``None`` when there is no certificate (the
    High Court does not write one). A report certified in *pages* has no
    paragraph numbering, whatever numbered lines it may contain: Commissioner
    of Taxation v Northumberland Development Co Pty Ltd [1995] FCA 588 is
    certified in pages and carries a numbered list of three propositions
    inside the third judgment, which without this check is stored as
    "paragraphs 1, 2 and 3" of a judgment that has none.
    """
    kinds = {m.group("kind").lower() for m in CERTIFIED_UNIT.finditer(text)}
    if any("paragraph" in kind for kind in kinds):
        return "paragraph"
    if kinds:
        return "page"
    return None


#: The first line of a numbered paragraph: the number, an optional "." or ")",
#: then the text. Both spacings occur in the corpus - the Federal Court writes
#: reasons as "12 The applicant..." and orders as "1. The application..." - so
#: neither is required.
#:
#: The text must begin with a capital, an opening quote or an opening bracket.
#: Without that, "2020 was the first year..." and a wrapped line beginning
#: with a bare figure both match, and a spurious paragraph boundary invents a
#: pinpoint that cites part of one paragraph as another.
PARAGRAPH_RE = re.compile(
    "^(?P<indent>[ \t]*)(?P<number>\\d{1,4})[.)]?[ \t]+(?=[\"'‘’“”(\\[]?[A-Z])",
    re.MULTILINE,
)

#: What a judgment with no paragraph numbering is stored as. Not a paragraph
#: and not a section: a whole document, cited as one.
WHOLE_DOCUMENT = "(whole document)"

#: What the block of orders and declarations is stored as. Orders are NOT
#: given paragraph numbers - see :func:`split_judgment`.
ORDERS_NUMBER = "(orders)"

#: How much of a paragraph's opening sentence is kept as its heading. A
#: judgment paragraph has no heading of its own, and `heading` is weighted
#: above `text` in the search vector, so this is a locator for a human reading
#: a result list - not a title, and never presented as one.
PARAGRAPH_HEADING_CHARS = 120


def _reasons_start(text: str) -> int | None:
    """Where the reasons for judgment begin, or None if the report says nowhere."""
    match = REASONS_HEADING.search(text)
    return match.end() if match is not None else None


def _paragraph_heading(body: str) -> str:
    """The paragraph's opening words, for a result list to show."""
    first = " ".join(PARAGRAPH_RE.sub("", body, count=1).split())
    if len(first) <= PARAGRAPH_HEADING_CHARS:
        return first
    return first[:PARAGRAPH_HEADING_CHARS].rsplit(" ", 1)[0]


def numbered_paragraphs(text: str) -> tuple[list[tuple[int, int]], int]:
    """Pick the judgment's own paragraph markers out of ``text``.

    Returns ``(markers, rejected)`` where ``markers`` is ``(position, number)``
    in document order with strictly increasing numbers, and ``rejected`` counts
    the numbered lines that were not treated as paragraph starts.

    Two filters, in this order.

    **Indentation.** A judgment indents what it quotes. A numbered line that
    is not at the same indent as the judgment's own paragraph 1 belongs to a
    quotation - of another judgment, of an affidavit, of a statute - and is
    not a paragraph of this judgment. The anchor is taken from the document
    rather than fixed at column 0 because both occur: the Federal Court's
    modern reports start paragraphs at column 0 and indent quotations by six
    spaces, while its 2003-era reports indent paragraphs by four and
    quotations by nine or more.

    **Strictly increasing.** Whatever survives the indent filter must still
    count upwards. A number that does not is not made into a boundary, so the
    text it introduces stays inside the paragraph that is quoting it.

    Both filters can only ever *refuse* to start a new provision, so neither
    can lose text and neither can invent a number: a rejected marker means no
    new provision begins there, and the enclosing paragraph runs through it to
    the next real one.

    Numbering begins at paragraph 1 and nothing before it is considered. A
    numbered line that precedes the first "1." cannot be part of a run that
    starts at 1, and treating it as one poisons the strictly-increasing filter
    for the whole document: in Adlam v Bauer [1999] FCA 634 the date line "10
    MAY 1999" reads as paragraph 10, after which every real paragraph from 1
    to 9 is below the running maximum and is rejected.
    """
    candidates = [
        (m.start(), len(m.group("indent").expandtabs(4)), int(m.group("number")))
        for m in PARAGRAPH_RE.finditer(text)
    ]
    first = next((i for i, (_, _, number) in enumerate(candidates) if number == 1), None)
    if first is None:
        # No paragraph 1 anywhere. Either the report is unnumbered and these
        # are quotations, or the numbering is one this code does not
        # understand. Either way it is not safe to number anything from it.
        return [], len(candidates)
    anchor = candidates[first][1]
    markers: list[tuple[int, int]] = []
    highest = 0
    for position, indent, number in candidates[first:]:
        # The next number in the run is taken whatever its indent. Extraction
        # loses a paragraph's indent occasionally - measured on Flashback
        # Holdings v Showtime DVD (No 6) [2010] FCA 694, where paragraph 10
        # alone of 47 sits at column 0 while the rest sit at column 4 - and
        # without this its text is absorbed into paragraph 9 and would be
        # quoted under [9]. This cannot let quoted matter back in except in
        # the one case where a quotation's number is exactly the number this
        # judgment's next paragraph would have had: every wider collision the
        # corpus actually contains (a quoted run restarting at 1, an
        # affidavit's paragraphs 52 and 143, another judgment's 10-14) is
        # still rejected, because none of them is highest + 1.
        if number != highest + 1 and (indent != anchor or number <= highest):
            continue
        markers.append((position, number))
        highest = number
    return markers, len(candidates) - len(markers)


def split_judgment(text: str) -> list[Provision]:
    """Split a judgment into the units a court is actually cited by.

    Three kinds of unit come out of this, and which kinds appear depends on
    the report:

    ``paragraph``
        A numbered paragraph of the reasons. Cited, per the Australian Guide
        to Legal Citation, as ``at [12]``.

    ``order``
        The orders and declarations the court made, as ONE provision numbered
        ``(orders)``. They are not given paragraph numbers, and this is the
        deliberate answer to the collision the corpus actually contains: a
        judgment's orders are numbered from 1 and its reasons then restart
        from 1, so "paragraph 1" names two different pieces of text in one
        document. Numbering the orders separately - "order 1" - does not fix
        it either: a report can carry more than one such block (ACCC v George
        Weston Foods [2003] FCA 601 opens "THE COURT DECLARES THAT: 1." and
        then "THE COURT ORDERS THAT: 1."), so "order 1" collides with itself.
        AGLC has no pinpoint form for an order, and inventing one to render a
        number the court did not use for citation is the error this phase
        exists to remove. So the orders keep their text, keep their own
        internal numbering inside that text, and are cited as the block they
        are.

    ``document``
        The whole report, when it carries no paragraph numbering at all.
        Older reports - Muir v Open Brethren [1956] HCA 14, United
        Firefighters' Union v Metropolitan Fire Brigades Board [1998] FCA 1119
        - are continuous prose whose certificate counts *pages*, not
        paragraphs. There is nothing to number and nothing is invented: one
        provision, cited as the case.

    The cover sheet - catchwords, counsel, "Number of paragraphs: 145" - is
    dropped, and only when the report's own structure says where it ends (an
    orders block, or a reasons heading). It is apparatus, not the court's
    words, and there is no pinpoint under which it could honestly be cited.
    The associate's certificate at the foot is dropped for the same reason.

    :data:`MIN_PROVISION_CHARS` is deliberately NOT applied here. "42 I
    agree." is a complete paragraph of a judgment and a citable one; dropping
    it would lose the disposition and shift its text into paragraph 41, which
    would then be quoted under the wrong pinpoint.
    """
    reasons_at = _reasons_start(text)
    search_to = reasons_at if reasons_at is not None else len(text)
    orders_match = ORDERS_OPENER.search(text, 0, search_to)

    provisions: list[Provision] = []
    if orders_match is not None:
        orders_end = reasons_at if reasons_at is not None else len(text)
        block = text[orders_match.start() : orders_end]
        if block.strip():
            provisions.append(
                Provision(
                    section_number=ORDERS_NUMBER,
                    heading="Orders",
                    text=block.strip(),
                    char_start=orders_match.start(),
                    char_end=orders_end,
                    unit_type="order",
                )
            )

    reasons_from = reasons_at if reasons_at is not None else 0
    tail = text[reasons_from:]

    # The paragraphs are found BEFORE the certificate is cut, and the cut is
    # then made at the first certificate that follows the last paragraph. A
    # judgment with separate reasons per judge carries one certificate per
    # set: Lu v Minister for Immigration & Multicultural Affairs [2000] FCA
    # 178 certifies Kiefel J's paragraph 1 before the other members' reasons
    # begin at paragraph 2. Cutting at the first certificate found would end
    # the judgment there and store 1 paragraph of 18.
    markers, _rejected = ([], 0) if certified_unit(text) == "page" else numbered_paragraphs(tail)
    after = markers[-1][0] if markers else 0
    certificate = CERTIFICATE.search(tail, after)
    reasons_to = reasons_from + (certificate.start() if certificate else len(tail))
    reasons = text[reasons_from:reasons_to]
    markers = [(position, number) for position, number in markers if position < len(reasons)]

    if not markers:
        stripped = reasons.strip()
        if not stripped:
            return provisions
        provisions.append(
            Provision(
                section_number=WHOLE_DOCUMENT,
                heading="",
                text=stripped,
                char_start=reasons_from,
                char_end=reasons_to,
                unit_type="document",
            )
        )
        return provisions

    for index, (position, number) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(reasons)
        # A certificate inside a paragraph's span ends that paragraph. A
        # judgment with separate reasons per judge certifies each set, so the
        # certificate closing one judge's reasons sits between that judge's
        # last paragraph and the next judge's first - and absorbed into the
        # former it reads as the court's own words under that paragraph's
        # pinpoint. The certificate is left out of both, so the span of a
        # paragraph is not always adjacent to the next one's.
        inner = CERTIFICATE.search(reasons, position, end)
        if inner is not None:
            end = inner.start()
        body = reasons[position:end].strip()
        if not body:
            continue
        provisions.append(
            Provision(
                section_number=str(number),
                heading=_paragraph_heading(body),
                text=body,
                char_start=reasons_from + position,
                char_end=reasons_from + end,
                unit_type="paragraph",
            )
        )
    return provisions


def split_guidance(text: str) -> list[Provision]:
    """A guidance page as ONE provision, cited without a pinpoint.

    Departmental guidance has no section numbering of its own. What it does
    have is numbered steps and lists - "1. Check you are eligible" - which
    split_sections would take for section headings and render as "s 1",
    a pinpoint asserting the page is a statute. So nothing is split: the page
    is stored whole, under the same sentinel a judgment with no paragraph
    numbers gets, and pinpoint() renders it as guidance.
    """
    stripped = text.strip()
    if not stripped:
        return []
    return [
        Provision(
            section_number=WHOLE_DOCUMENT,
            heading="",
            text=stripped,
            char_start=0,
            char_end=len(text),
            unit_type="document",
        )
    ]


def unnumbered_note(provisions: list[Provision]) -> str:
    """What to report about a judgment that carried no paragraph numbers.

    Empty when there is nothing to report, and never silently empty for the
    case it exists for: a judgment without paragraph numbers has to be
    reported rather than given invented ones, and a count of provisions alone
    does not say which of the two happened.
    """
    if any(p.unit_type == "paragraph" for p in provisions):
        return ""
    if any(p.unit_type == "document" for p in provisions):
        return (
            "no numbered paragraphs found; stored as one (whole document) provision "
            "and cited without a pinpoint"
        )
    return ""


def detect_headings_only(provisions: list[Provision]) -> str | None:
    """Detect a document whose provisions carry headings but not operative text.

    The production incident this guards against: a "guide" or summary page
    (for example a legislation.gov.au ``/latest`` HTML view, as happened with
    Privacy Act 1988 (Cth) Part IIIC) extracts as real-looking sections - real
    headings, bodies that clear :data:`MIN_PROVISION_CHARS` - while containing
    no operative law at all, just short restatements of the heading.

    Not a character-count threshold: ``MIN_PROVISION_CHARS`` is exactly what
    that page defeats, and raising it would both miss a longer stub and reject
    genuinely short real sections (a one-sentence "Short title" clause is
    common and legitimate). Two structural signals instead, both required:

    1. **No provision with genuine subsection structure, anywhere in the
       document.** Not "few" - many short, genuine WA Acts have entire
       sections written as a single flowing paragraph with no
       ``(1)``/``(2)`` markers at all (pre-1900 Imperial Acts adopted into WA
       law, one-clause validation Acts). "Genuine" means at least
       :data:`MIN_MARKERS_PER_PROVISION` markers in the SAME provision, not
       just one anywhere in the document. A single ``(1)`` proves nothing on
       its own - it is exactly what a footnote marker or a list label looks
       like - but real subsections come in a run: ``(1)`` is followed by
       ``(2)``, and so on, inside one provision's own text. Before this
       fix, any single marker anywhere disabled the check for the WHOLE
       document, which made the defect reachable by accident: one
       decorative "(1)" anywhere in a headings-only page was enough to wave
       it through. (W1-S5.)
    2. **More than one disproportionately long provision - once there are
       enough provisions for that to be a meaningful question.** A stub set
       generated from a template is uniform except for whatever one
       provision an attacker (or a copy-paste accident) padded out, so
       requiring at least two long provisions, not merely a high max/min
       ratio, is what makes this resistant to that single outlier. Before
       this fix, one disproportionately long stub alone pushed the ratio
       past :data:`HEADINGS_ONLY_MAX_LENGTH_RATIO` and passed the whole
       document. (W1-S4.) This tightened check applies only from
       :data:`HEADINGS_ONLY_PROPORTIONAL_MIN_PROVISIONS` provisions up:
       measured against the full production corpus, applying it at the
       floor of 3 flagged two real documents outright - Corporations
       (Taxing) Act 1990 (WA) (ratio 2.72, exactly the case
       :data:`HEADINGS_ONLY_MAX_LENGTH_RATIO` was calibrated against) and a
       Town Planning by-law - because a genuine three-provision Act is
       overwhelmingly "short title, commencement, ONE substantive section",
       which has only one long provision by its very shape and is not
       distinguishable from the attack at that size. Below the floor, the
       plain max/min ratio - the original, calibrated check - still applies.

    Both signals must hold, so a document is never flagged on either alone:
    a real short Act (signal 1 true, signal 2 false because it still varies)
    passes, and a document that merely happens to have one long provision
    covering a lot of the wordcount (signal 2 true, signal 1 false because it
    still has ordinary subsection numbering somewhere) also passes.

    Returns a human-readable reason if the document looks headings-only, or
    ``None`` if it reads as real law.
    """
    if len(provisions) < HEADINGS_ONLY_MIN_PROVISIONS:
        return None
    with_marker = sum(
        1
        for p in provisions
        if len(SUBSECTION_MARKER_RE.findall(p.text)) >= MIN_MARKERS_PER_PROVISION
    )
    if with_marker > 0:
        return None
    lengths = [len(p.text) for p in provisions]
    minimum = min(lengths)
    if len(lengths) >= HEADINGS_ONLY_PROPORTIONAL_MIN_PROVISIONS:
        long_provisions = sum(
            1 for length in lengths if length / minimum >= HEADINGS_ONLY_MAX_LENGTH_RATIO
        )
        if long_provisions >= HEADINGS_ONLY_MIN_LONG_PROVISIONS:
            return None
        variation_detail = (
            f"and only {long_provisions} provision(s) reaching the "
            f"{HEADINGS_ONLY_MAX_LENGTH_RATIO} floor for genuine variation, below "
            f"the {HEADINGS_ONLY_MIN_LONG_PROVISIONS} needed"
        )
    else:
        if max(lengths) / minimum >= HEADINGS_ONLY_MAX_LENGTH_RATIO:
            return None
        variation_detail = f"below the {HEADINGS_ONLY_MAX_LENGTH_RATIO} floor for genuine variation"
    spread = max(lengths) / minimum
    return (
        f"{len(provisions)} provisions, none with {MIN_MARKERS_PER_PROVISION}+ "
        "subsection markers in one provision, with body lengths ranging only "
        f"{minimum}-{max(lengths)} chars (ratio {spread:.2f}, {variation_detail}) - "
        "reads as headings with summary text, not operative provisions."
    )


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
    # Every path that writes a document passes here, so the host/doc_type
    # binding is enforced here too - not only at the fetch. ingest_url and
    # ingest_file check it first so the refusal is logged before any work.
    try:
        check_doc_type(source_url, doc_type)
    except SourceRefused as exc:
        raise IngestionError(str(exc)) from exc

    # A judgment is split by the unit a court is cited by, which is not the
    # unit legislation is cited by. See split_judgment.
    is_judgment = doc_type == "case"
    is_guidance = doc_type == GUIDANCE_DOC_TYPE
    if is_judgment:
        provisions = split_judgment(text)
    elif is_guidance:
        provisions = split_guidance(text)
    else:
        provisions = split_sections(text, pattern=section_pattern(doc_type))
    if not provisions:
        raise IngestionError(f"{citation}: no text to ingest")
    if not is_judgment and not is_guidance:
        # detect_headings_only is calibrated on legislation: it asks whether
        # provisions carry subsection markers and vary in length. A judgment's
        # paragraphs carry neither property by nature, so running it here
        # would refuse real judgments for being judgments.
        headings_only_reason = detect_headings_only(provisions)
        if headings_only_reason is not None:
            raise IngestionError(f"{citation}: refused as headings-only - {headings_only_reason}")
    note = unnumbered_note(provisions) if is_judgment else ""

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
            note=note,
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
    note: str = "",
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
                    (document_id, section_number, unit_type, heading, text, embedding,
                     char_start, char_end, token_count)
                VALUES (
                    %s, %s, %s, %s, %s, %s::vector, %s, %s,
                    (SELECT coalesce(sum(cardinality(positions)), 0)
                       FROM unnest(to_tsvector('english', %s)))
                )
                ON CONFLICT (document_id, section_number, char_start) DO NOTHING
                """,
                (
                    document_id,
                    provision.section_number,
                    provision.unit_type,
                    provision.heading,
                    provision.text,
                    vector_literal(vector),
                    provision.char_start,
                    provision.char_end,
                    f"{provision.heading} {provision.text}",
                ),
            )

        reason = f"ingested {len(provisions)} provisions"
        if note:
            reason = f"{reason} - {note}"
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
        note=note,
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
        check_doc_type(url, doc_type)
    except SourceRefused as exc:
        _log_refusal(url=url, reason=str(exc))
        log_attempt(url=url, host=host_of(url), outcome="refused", reason=str(exc))
        raise
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
        # utf-8-sig, not utf-8: a Windows-authored source can carry a UTF-8
        # BOM (EF BB BF) at byte 0. Decoded as plain utf-8 that BOM survives
        # into the text as U+FEFF, sitting in front of the document's own
        # first character - which is usually the first section heading.
        # SECTION_RE anchors "^" at the true start of the line, so a BOM
        # there means the first heading never matches at all: find_body_start
        # then finds the SECOND heading as if it were the first, and every
        # line up to it - including the real section 1, heading and all -
        # is cut away as if it were front matter. utf-8-sig strips a leading
        # BOM if present and decodes identically to utf-8 if it is not, so
        # this is a strict improvement with no other document affected.
        # (W1-S1.)
        text = body.decode("utf-8-sig")
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
        check_doc_type(source_url, doc_type)
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
            citation = str(record["citation"])
            try:
                # One savepoint per document. Every refusal reachable today is
                # raised before this document's first write - _validate, the
                # empty split, the headings-only check, and the one raise
                # inside the write path, which is reached only when the
                # document INSERT hit ON CONFLICT DO NOTHING and therefore
                # wrote nothing either. So `continue` below is safe as the code
                # stands, and the savepoint changes nothing today.
                #
                # It is here because that safety is a property of the current
                # control flow rather than of this loop, and nothing pins it:
                # add one IngestionError after the document row is written -
                # a dimension check on embed() is the obvious future one - and
                # `continue` would step over a half-written document that the
                # enclosing `with writable()` then COMMITS. A row with some of
                # its provisions, no error, no log. The savepoint makes the
                # rollback structural, so this loop no longer depends on an
                # invariant held somewhere else.
                with conn.transaction():
                    result = ingest_document(
                        jurisdiction=str(record["jurisdiction"]),
                        title=str(record["title"]),
                        citation=citation,
                        source_url=str(record["source_url"]),
                        snapshot_date=record["snapshot_date"],  # type: ignore[arg-type]
                        sha256=hashlib.sha256(body).hexdigest(),
                        doc_type=str(record["doc_type"]),
                        text=str(record["text"]),
                        conn=conn,
                    )
            except IngestionError as exc:
                # Refusing one document must not lose the rest. Before this
                # fix, letting the exception propagate out of the loop meant a
                # single headings-only page found anywhere in a multi-hundred
                # document batch aborted the "with writable()" block without a
                # commit, discarding every other document already ingested in
                # that same run. The savepoint above has already undone
                # whatever this document wrote, so the batch continues on a
                # clean transaction. (W1-S3.)
                _log_refusal(url=str(record["source_url"]), reason=f"{citation}: {exc}")
                continue
            results.append(result)
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
