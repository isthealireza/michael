# Contract Comparison and Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Michael the ability to compare two contract drafts clause by clause and to report which standard elements a document lacks, without ever judging either.

**Architecture:** One new module of pure functions (`contracts.py`), one new data file (`elements.yaml`) with a loader mirroring `domains.py`, and two new read-only MCP tools. No schema change, no persistence, no new dependency. The no-template outline becomes a consumer of the element list.

**Tech Stack:** Python 3.13, stdlib `difflib` and `re`, PyYAML (already a dependency, used by `domains.py`), pytest, mypy.

**Spec:** `docs/superpowers/specs/2026-09-17-contract-comparison-design.md`

## Global Constraints

Copied verbatim from the spec and `MICHAEL.md`. Every task's requirements include these.

- **No verdicts.** No dataclass may carry `risk`, `severity`, `score` or `recommendation`. `MICHAEL.md`: "Never assert that a clause is compliant, and never assert that it is not."
- **Pure functions.** No database read, no database write, no file written by `compare()` or `audit()`.
- **Nothing is dropped.** Text before the first clause is emitted with id `(preamble)`.
- **No sequence assumptions.** The splitter must not assume clause numbering rises.
- **Never report UNCHANGED for a clause that changed.** Normalisation collapses whitespace only; it does not lowercase or strip punctuation.
- **Element `basis` is worded "related provision", never "required by".**
- `uv run pytest` and `uv run mypy .` must be green before any commit.
- No push. Push is the owner's.
- Run tests with the project venv: `.venv/Scripts/python.exe -m pytest` on Windows.

## Existing interfaces this plan consumes

Do not re-derive these; they exist and are stable.

```python
# src/michael/docx_text.py
def looks_like_docx(body: bytes, content_type: str = "") -> bool
def docx_to_text(body: bytes) -> str

# src/michael/html_text.py
def looks_like_html(body: str, content_type: str = "") -> bool
def html_to_text(html: str) -> str

# src/michael/domains.py - the loader pattern elements.py mirrors
@lru_cache(maxsize=4)
def load_domains(path: Path | None = None) -> tuple[Domain, ...]

# src/michael/config.py - how a path setting is declared
def _path(name: str, default: str) -> Path
# settings() exposes: sources_dir, templates_dir, ingestion_log, domains_file,
# system_prompt_file
```

`ingest.extract_text()` exists but **must not be imported** — the guard test in Task 8 forbids any answering-path handler importing `michael.ingest`. Task 1 writes its own dispatcher.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/michael/contract_text.py` | Turn bytes into contract text. DOCX, HTML, Markdown, plain text. Nothing else. |
| `src/michael/contracts.py` | `Clause`, `split_clauses()`, `align()`, `diff()`, `compare()`. No I/O. |
| `src/michael/elements.py` | `Element`, `load_elements()`, `audit()`. Reads `elements.yaml`. |
| `elements.yaml` | The reference list. Data, not code. |
| `src/michael/tools.py` | Two new entries in `ANSWERING_TOOLS`, two new handlers. Modified. |
| `src/michael/draft.py` | `outline_without_template()` consumes the element list. Modified. |
| `src/michael/config.py` | One new path setting, `elements_file`. Modified. |
| `tests/test_contract_text.py` | Extraction and sniffing. |
| `tests/test_contracts.py` | Splitter, alignment, diff, the normalisation invariant. |
| `tests/test_elements.py` | Loader validation and the audit's three states. |
| `tests/test_runtime_config.py` | Guard test restructure. Modified. |

Extraction is split from `contracts.py` on purpose: the splitter is the part with real difficulty and must stay small enough to hold in context, and the extractor is the part that will grow when PDF arrives.

---

### Task 1: Contract text extraction

**Files:**
- Create: `src/michael/contract_text.py`
- Create: `tests/test_contract_text.py`

**Interfaces:**
- Consumes: `docx_text.looks_like_docx`, `docx_text.docx_to_text`, `html_text.looks_like_html`, `html_text.html_to_text`
- Produces: `class ContractTextError(RuntimeError)`, `def contract_text(body: bytes, *, content_type: str = "", origin: str = "") -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_contract_text.py
import pytest

from michael.contract_text import ContractTextError, contract_text


def test_plain_utf8_passes_through() -> None:
    assert contract_text(b"1. PARTIES\nThe parties agree.") == "1. PARTIES\nThe parties agree."


def test_markdown_headings_are_stripped_to_their_text() -> None:
    body = b"## 1. PARTIES\n\n**Employer:** Acme Pty Ltd\n"
    out = contract_text(body)
    assert "1. PARTIES" in out
    assert "#" not in out
    assert "**" not in out
    assert "Employer: Acme Pty Ltd" in out


def test_html_is_converted() -> None:
    body = b"<html><body><h2>1. PARTIES</h2><p>The parties agree.</p></body></html>"
    out = contract_text(body)
    assert "1. PARTIES" in out
    assert "<h2>" not in out


def test_empty_input_is_an_error_naming_its_origin() -> None:
    with pytest.raises(ContractTextError, match="draft.docx"):
        contract_text(b"   \n  ", origin="draft.docx")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contract_text.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'michael.contract_text'`

- [ ] **Step 3: Write the implementation**

```python
# src/michael/contract_text.py
"""Turn a contract file's bytes into text the clause splitter can read.

Deliberately separate from `michael.ingest.extract_text`, which does the same
job for legislation. The answering path must not import from `ingest` - the
guard test enforces it - and the two will diverge: PDF belongs here when it
arrives, and never there.
"""

from __future__ import annotations

import re

from michael.docx_text import docx_to_text, looks_like_docx
from michael.html_text import html_to_text, looks_like_html

#: A markdown heading marker at the start of a line, and inline bold markers.
#: Stripped so "## 1. PARTIES" is seen by the splitter as "1. PARTIES".
MARKDOWN_HEADING = re.compile(r"^#{1,6}[ \t]*", re.MULTILINE)
MARKDOWN_BOLD = re.compile(r"\*\*(?P<text>[^*]*)\*\*")


class ContractTextError(RuntimeError):
    """The file carried no usable text. Never silently returns an empty string."""


def _strip_markdown(text: str) -> str:
    """Remove the markdown markers that would hide a clause heading."""
    text = MARKDOWN_HEADING.sub("", text)
    return MARKDOWN_BOLD.sub(lambda m: m.group("text"), text)


def contract_text(body: bytes, *, content_type: str = "", origin: str = "") -> str:
    """Return the contract's text, or raise rather than return nothing.

    An empty result is always an error here. A comparison of two empty
    documents would report no changes, which is indistinguishable from a
    comparison of two identical documents and is the wrong answer to give.
    """
    where = origin or "input"

    if looks_like_docx(body, content_type):
        text = docx_to_text(body)
    else:
        decoded = body.decode("utf-8", errors="replace")
        text = html_to_text(decoded) if looks_like_html(decoded, content_type) else decoded

    text = _strip_markdown(text)
    if not text.strip():
        raise ContractTextError(f"{where}: no text survived extraction")
    return text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contract_text.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full suite and mypy**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy .`
Expected: all pass, `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/michael/contract_text.py tests/test_contract_text.py
git commit -m "feat(contracts): extract contract text from docx, html, markdown and plain text"
```

---

### Task 2: The clause splitter

**Files:**
- Create: `src/michael/contracts.py`
- Create: `tests/test_contracts.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `contracts.py` takes text, not bytes.
- Produces:
```python
PREAMBLE_ID = "(preamble)"

@dataclass(frozen=True, slots=True)
class Clause:
    clause_id: str
    heading: str
    text: str
    char_start: int
    char_end: int

def split_clauses(text: str) -> tuple[Clause, ...]
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_contracts.py
from michael.contracts import PREAMBLE_ID, Clause, split_clauses


DECIMAL_NUMBERED = """SERVICES AGREEMENT
This agreement is made between the parties named below.

1. DEFINITIONS
1.1 In this agreement, "Services" means the services described in Schedule 1.
1.2 "Fees" means the amounts set out in clause 4.

2. TERM
2.1 This agreement commences on the Commencement Date.
"""

ALL_CAPS_UNNUMBERED = """CONSULTANCY AGREEMENT
Recitals appear here before any clause.

PAYMENT
The Client must pay within 30 days of invoice.

TERMINATION
Either party may terminate on 14 days notice.
"""

SCHEDULE_RESTARTS_NUMBERING = """1. PARTIES
The parties are named in Schedule 1.

2. TERM
The term is three years.

SCHEDULE 1
1. The Client is Acme Pty Ltd of 1 Example Street.
2. The Consultant is Beta Pty Ltd of 2 Sample Road.
"""


def test_text_before_the_first_clause_is_kept_as_preamble() -> None:
    clauses = split_clauses(DECIMAL_NUMBERED)
    assert clauses[0].clause_id == PREAMBLE_ID
    assert "made between the parties" in clauses[0].text


def test_the_deepest_numbered_unit_is_the_clause() -> None:
    ids = [c.clause_id for c in split_clauses(DECIMAL_NUMBERED)]
    assert "1.1" in ids and "1.2" in ids and "2.1" in ids


def test_a_clause_carries_its_heading_and_its_own_text() -> None:
    by_id = {c.clause_id: c for c in split_clauses(DECIMAL_NUMBERED)}
    assert "Services" in by_id["1.1"].text
    assert "Fees" in by_id["1.2"].text
    assert "Fees" not in by_id["1.1"].text


def test_unnumbered_all_caps_headings_split() -> None:
    ids = [c.clause_id for c in split_clauses(ALL_CAPS_UNNUMBERED)]
    assert "PAYMENT" in ids
    assert "TERMINATION" in ids


def test_numbering_that_restarts_in_a_schedule_is_not_dropped() -> None:
    """The monotonic-sequence assumption cost 1,199 provisions once. Never again."""
    clauses = split_clauses(SCHEDULE_RESTARTS_NUMBERING)
    text = " ".join(c.text for c in clauses)
    assert "Acme Pty Ltd" in text
    assert "Beta Pty Ltd" in text


def test_nothing_in_the_document_is_lost() -> None:
    """Every character of every document must survive into some clause."""
    for document in (DECIMAL_NUMBERED, ALL_CAPS_UNNUMBERED, SCHEDULE_RESTARTS_NUMBERING):
        clauses = split_clauses(document)
        joined = "".join(document[c.char_start:c.char_end] for c in clauses)
        assert joined.strip() == document.strip()


def test_a_document_with_no_clause_headings_returns_one_clause() -> None:
    clauses = split_clauses("Just some prose with no headings at all in it.")
    assert len(clauses) == 1
    assert clauses[0].clause_id == PREAMBLE_ID
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contracts.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'michael.contracts'`

- [ ] **Step 3: Write the implementation**

```python
# src/michael/contracts.py
"""Clause-level comparison of two contracts. Pure functions, no I/O.

Deliberately separate from the legislation splitter in `michael.ingest`.
Contracts number nothing like Acts - "1.1", "3.2(a)", "SCHEDULE A", bare
capitalised headings - and a splitter tuned for one document shape meeting
another is how this project lost 1,199 provisions in a single afternoon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The clause id given to text that precedes the first heading.
PREAMBLE_ID = "(preamble)"

#: A numbered clause heading: "1.", "1.1", "1.1.1", "3.2(a)", optionally
#: followed by a heading on the same line. Anchored at the line start.
NUMBERED_CLAUSE = re.compile(
    r"^[ \t]*(?P<number>\d{1,3}(?:\.\d{1,3})*(?:\([a-z]{1,2}\))?)[.)]?[ \t]+(?P<heading>\S[^\n]*)?$",
    re.MULTILINE,
)

#: An unnumbered heading in capitals on a line of its own - "PAYMENT",
#: "SCHEDULE A". Requires at least three characters so an initial or a stray
#: "A" cannot split a document.
CAPS_CLAUSE = re.compile(r"^[ \t]*(?P<number>[A-Z][A-Z0-9 &'()-]{2,60})[ \t]*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Clause:
    """One clause of a contract, with its offsets into the source text."""

    clause_id: str
    heading: str
    text: str
    char_start: int
    char_end: int


def _headings(text: str) -> list[tuple[int, str, str]]:
    """Every clause heading as (offset, clause_id, heading), in document order.

    Both patterns are applied and the results merged by offset. A line matched
    by both - a numbered heading in capitals - is taken once, numbered form
    first, because the number is the more stable identifier.
    """
    found: dict[int, tuple[str, str]] = {}
    for match in CAPS_CLAUSE.finditer(text):
        found[match.start()] = (match.group("number").strip(), "")
    for match in NUMBERED_CLAUSE.finditer(text):
        found[match.start()] = (
            match.group("number").strip(),
            (match.group("heading") or "").strip(),
        )
    return [(offset, *found[offset]) for offset in sorted(found)]


def split_clauses(text: str) -> tuple[Clause, ...]:
    """Split a contract into clauses. Never drops text.

    Everything before the first heading becomes the ``(preamble)`` clause, and
    a document with no headings at all becomes a single ``(preamble)`` clause,
    so every character of the input survives into exactly one clause.

    No assumption is made that numbering rises. A schedule restarting at 1 is
    ordinary drafting, not evidence that the text stopped being a contract.
    """
    headings = _headings(text)
    if not headings:
        return (
            Clause(
                clause_id=PREAMBLE_ID,
                heading="",
                text=text.strip(),
                char_start=0,
                char_end=len(text),
            ),
        )

    clauses: list[Clause] = []
    first_offset = headings[0][0]
    if text[:first_offset].strip():
        clauses.append(
            Clause(
                clause_id=PREAMBLE_ID,
                heading="",
                text=text[:first_offset].strip(),
                char_start=0,
                char_end=first_offset,
            )
        )

    for index, (offset, clause_id, heading) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        clauses.append(
            Clause(
                clause_id=clause_id,
                heading=heading,
                text=text[offset:end].strip(),
                char_start=offset,
                char_end=end,
            )
        )
    return tuple(clauses)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contracts.py -v`

Expected: 6 of 7 pass. `test_text_before_the_first_clause_is_kept_as_preamble`
FAILS, with `assert 'SERVICES AGREEMENT' == '(preamble)'`.

That failure is real and the implementation above is wrong, not the test:
`CAPS_CLAUSE` matches the fixture's own title line, because nothing in the
pattern distinguishes a document title from any other line in capitals.

- [ ] **Step 4a: Fix it in `_headings()`**

A caps-style match at offset 0 has no preceding text to be a boundary between,
so it is the document's title rather than a clause heading. Skip it, and let
the title fold into the preamble:

```python
    for match in CAPS_CLAUSE.finditer(text):
        if match.start() == 0:
            # A caps-style line with nothing before it has no boundary to mark:
            # it is the document's title, not a clause heading, and folds into
            # the preamble rather than manufacturing a spurious clause. A
            # numbered "1." at offset 0 is unambiguous and is not touched.
            continue
        found[match.start()] = (match.group("number").strip(), "")
```

Do not fix this by changing the test to expect a `SERVICES AGREEMENT` clause.
That would enshrine the bug as intended behaviour.

Re-run: all 7 pass.

- [ ] **Step 5: Run the full suite and mypy**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy .`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/michael/contracts.py tests/test_contracts.py
git commit -m "feat(contracts): split a contract into clauses without dropping text"
```

---

### Task 3: Real-document proof of the splitter

**Files:**
- Create: `docs/superpowers/plans/evidence/2026-09-17-splitter-evidence.md`
- Test: none. This task's deliverable is evidence, not code.

**Interfaces:**
- Consumes: `contracts.split_clauses`, `contract_text.contract_text`
- Produces: a committed evidence file. No code.

**Why this task exists.** On 2026-09-17 a splitter change passed five green
tests and cut 13% of the corpus. A splitter is not proven by fixtures. It is
proven by running it over real documents and reading the output.

- [ ] **Step 1: Assemble three real contracts**

Use `templates/employment/casual_employment_contract.md`, one contract from
`sources/` if present, and one synthetic-but-realistic third-party contract
written by hand in a different numbering style. Put them under
`tests/fixtures/contracts/` (create the directory).

Do not use the repository's own sample contracts alone: they share one
drafting style, and a splitter that works on one style proves nothing.

- [ ] **Step 2: Run the splitter over each and record the output**

```bash
.venv/Scripts/python.exe - <<'PY'
import pathlib
from michael.contract_text import contract_text
from michael.contracts import split_clauses

for path in sorted(pathlib.Path("tests/fixtures/contracts").iterdir()):
    text = contract_text(path.read_bytes(), origin=path.name)
    clauses = split_clauses(text)
    print(f"=== {path.name}: {len(text):,} chars -> {len(clauses)} clauses ===")
    for c in clauses:
        print(f"  {c.clause_id:<14} {len(c.text):>6} chars  {c.heading[:52]}")
PY
```

- [ ] **Step 3: Read the output and judge it by eye**

Check, for each document:
- the preamble is present and holds the recitals, not clause 1
- no clause is implausibly large (a sign a heading was missed)
- no clause is a fragment of a sentence (a sign a heading was invented)
- character coverage is total: sum of clause spans equals the document

- [ ] **Step 4: Write the evidence file**

Record, per document: name, character count, clause count, and any clause the
splitter got wrong with the reason. State plainly whether the splitter is fit
to proceed. If it is not, stop and fix Task 2 before continuing.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/contracts docs/superpowers/plans/evidence
git commit -m "test(contracts): real-document evidence for the clause splitter"
```

---

### Task 4: Normalisation and the UNCHANGED invariant

**Files:**
- Modify: `src/michael/contracts.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: `Clause` from Task 2
- Produces: `def normalise(text: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_contracts.py
import pytest

from michael.contracts import normalise


@pytest.mark.parametrize(
    "before, after",
    [
        ("The Employer must pay.", "The Employer may pay."),
        ("A fee of $5,000 applies.", "A fee of $5000 applies."),
        ("Notice of 30 days.", "Notice of 3 days."),
        ("Governed by WA law.", "Governed by NSW law."),
    ],
)
def test_a_material_edit_never_normalises_to_the_same_text(before: str, after: str) -> None:
    """The invariant: never report UNCHANGED for a clause that changed.

    A false CHANGED is noise a reader discards. A false UNCHANGED is a
    negotiation term that slipped through silently.
    """
    assert normalise(before) != normalise(after)


@pytest.mark.parametrize(
    "before, after",
    [
        ("The  Employer   must pay.", "The Employer must pay."),
        ("The Employer must pay.\n", "The Employer must pay."),
        ("The Employer\tmust pay.", "The Employer must pay."),
    ],
)
def test_whitespace_only_differences_normalise_away(before: str, after: str) -> None:
    assert normalise(before) == normalise(after)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contracts.py -k normalise -v`
Expected: FAIL, `ImportError: cannot import name 'normalise'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/michael/contracts.py

def normalise(text: str) -> str:
    """Collapse whitespace, and nothing else.

    Deliberately timid. Lowercasing would make "Employer" and "employer" the
    same word, and stripping punctuation would make "$5,000" and "$5000" the
    same amount - both are edits a reader is negotiating over. The cost of
    timidity is a few reported changes that are cosmetic; the cost of
    aggression is a changed term reported as unchanged.
    """
    return " ".join(text.split())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contracts.py -k normalise -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/michael/contracts.py tests/test_contracts.py
git commit -m "feat(contracts): normalise whitespace only, so no edit reads as unchanged"
```

---

### Task 5: Alignment and the comparison report

**Files:**
- Modify: `src/michael/contracts.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: `Clause`, `split_clauses`, `normalise`
- Produces:

```python
SIMILARITY_THRESHOLD = 0.6            # a guess; Task 9 measures it
MAX_UNPAIRED_FOR_SIMILARITY = 400

@dataclass(frozen=True, slots=True)
class ClauseChange:
    clause_id_before: str | None
    clause_id_after: str | None
    status: Literal["CHANGED", "ADDED", "REMOVED", "MOVED"]
    heading: str
    diff: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class ComparisonReport:
    unchanged: int
    changes: tuple[ClauseChange, ...]

def compare(text_a: str, text_b: str) -> ComparisonReport
```

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_contracts.py
from michael.contracts import ClauseChange, ComparisonReport, compare

V1 = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. PAYMENT
The Client must pay within 30 days of invoice.

3. TERMINATION
Either party may terminate on 14 days notice.
"""

V2_EDITED = V1.replace("within 30 days", "within 60 days")

V2_ADDED = V1 + "\n4. CONFIDENTIALITY\nEach party must keep information confidential.\n"

V2_RENUMBERED = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. TERMINATION
Either party may terminate on 14 days notice.

3. PAYMENT
The Client must pay within 30 days of invoice.
"""


def test_an_edited_clause_is_reported_changed_with_its_id() -> None:
    report = compare(V1, V2_EDITED)
    changed = [c for c in report.changes if c.status == "CHANGED"]
    assert len(changed) == 1
    assert changed[0].clause_id_before == "2"
    assert changed[0].clause_id_after == "2"


def test_an_edited_clause_shows_what_changed() -> None:
    report = compare(V1, V2_EDITED)
    rendered = " ".join(report.changes[0].diff)
    assert "30" in rendered and "60" in rendered


def test_an_added_clause_has_no_before_id() -> None:
    added = [c for c in compare(V1, V2_ADDED).changes if c.status == "ADDED"]
    assert len(added) == 1
    assert added[0].clause_id_before is None
    assert added[0].clause_id_after == "4"


def test_a_removed_clause_has_no_after_id() -> None:
    removed = [c for c in compare(V2_ADDED, V1).changes if c.status == "REMOVED"]
    assert len(removed) == 1
    assert removed[0].clause_id_after is None


def test_a_renumbered_but_identical_clause_is_moved_not_changed() -> None:
    statuses = {c.status for c in compare(V1, V2_RENUMBERED).changes}
    assert "MOVED" in statuses
    assert "CHANGED" not in statuses


def test_identical_documents_report_no_changes() -> None:
    report = compare(V1, V1)
    assert report.changes == ()
    assert report.unchanged > 0


def test_the_report_has_nowhere_to_put_a_verdict() -> None:
    """MICHAEL.md: Michael does not certify a clause in either direction."""
    fields = set(ClauseChange.__dataclass_fields__) | set(ComparisonReport.__dataclass_fields__)
    assert not fields & {"risk", "severity", "score", "recommendation", "verdict"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contracts.py -k compare -v`
Expected: FAIL, `ImportError: cannot import name 'compare'`

- [ ] **Step 3a: Add the dataclasses and the word diff**

```python
# append to src/michael/contracts.py
import difflib
from typing import Literal

#: Above this ratio, two clauses with different ids are taken to be the same
#: clause, edited. A GUESS. Task 9 measures it against a labelled set of real
#: before/after pairs, the way RETRIEVAL_MIN_SCORE was measured. Until then it
#: is not described as calibrated.
SIMILARITY_THRESHOLD = 0.6

#: Above this many unpaired clauses on either side, stage 3 is skipped and the
#: remainder reported as ADDED/REMOVED. Bounds an O(u^2) scan.
MAX_UNPAIRED_FOR_SIMILARITY = 400


@dataclass(frozen=True, slots=True)
class ClauseChange:
    """One difference between two drafts. Carries no judgement of it."""

    clause_id_before: str | None
    clause_id_after: str | None
    status: Literal["CHANGED", "ADDED", "REMOVED", "MOVED"]
    heading: str
    diff: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """What differs between two drafts, and how much did not."""

    unchanged: int
    changes: tuple[ClauseChange, ...]


def _word_diff(before: str, after: str) -> tuple[str, ...]:
    """A compact word-level rendering of what changed. Facts, not opinions."""
    old = normalise(before).split()
    new = normalise(after).split()
    lines: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new).get_opcodes():
        if tag == "equal":
            continue
        if old[i1:i2]:
            lines.append("- " + " ".join(old[i1:i2]))
        if new[j1:j2]:
            lines.append("+ " + " ".join(new[j1:j2]))
    return tuple(lines)
```

- [ ] **Step 3b: Add stages 1 and 2**

```python
# append to src/michael/contracts.py
def compare(text_a: str, text_b: str) -> ComparisonReport:
    """Compare two drafts clause by clause.

    Three stages, cheapest first: exact id match, then exact text match among
    what is left, then bounded similarity. A clause is reported UNCHANGED only
    when its normalised text is identical - see :func:`normalise`.
    """
    before = {c.clause_id: c for c in split_clauses(text_a)}
    after = {c.clause_id: c for c in split_clauses(text_b)}

    changes: list[ClauseChange] = []
    unchanged = 0

    # Stage 1 - same id.
    for clause_id in before.keys() & after.keys():
        a, b = before[clause_id], after[clause_id]
        if normalise(a.text) == normalise(b.text):
            unchanged += 1
            continue
        changes.append(
            ClauseChange(
                clause_id_before=clause_id,
                clause_id_after=clause_id,
                status="CHANGED",
                heading=b.heading or a.heading,
                diff=_word_diff(a.text, b.text),
            )
        )

    shared = before.keys() & after.keys()
    left = {k: v for k, v in before.items() if k not in shared}
    right = {k: v for k, v in after.items() if k not in shared}

    # Stage 2 - identical text, different id.
    by_text = {normalise(v.text): k for k, v in right.items()}
    for clause_id, clause in list(left.items()):
        match_id = by_text.get(normalise(clause.text))
        if match_id is None or match_id not in right:
            continue
        changes.append(
            ClauseChange(
                clause_id_before=clause_id,
                clause_id_after=match_id,
                status="MOVED",
                heading=clause.heading,
                diff=(),
            )
        )
        del left[clause_id]
        del right[match_id]
```

- [ ] **Step 3c: Add stage 3 and the remainder**

```python
# continue inside compare(), same indentation level as stage 2
    # Stage 3 - similarity, bounded.
    if len(left) <= MAX_UNPAIRED_FOR_SIMILARITY and len(right) <= MAX_UNPAIRED_FOR_SIMILARITY:
        scored = sorted(
            (
                difflib.SequenceMatcher(None, normalise(a.text), normalise(b.text)).ratio(),
                a_id,
                b_id,
            )
            for a_id, a in left.items()
            for b_id, b in right.items()
        )
        for ratio, a_id, b_id in reversed(scored):
            if ratio < SIMILARITY_THRESHOLD:
                break
            if a_id not in left or b_id not in right:
                continue
            a, b = left[a_id], right[b_id]
            changes.append(
                ClauseChange(
                    clause_id_before=a_id,
                    clause_id_after=b_id,
                    status="CHANGED",
                    heading=b.heading or a.heading,
                    diff=_word_diff(a.text, b.text),
                )
            )
            del left[a_id]
            del right[b_id]

    changes += [
        ClauseChange(clause_id_before=k, clause_id_after=None, status="REMOVED",
                     heading=v.heading, diff=())
        for k, v in left.items()
    ]
    changes += [
        ClauseChange(clause_id_before=None, clause_id_after=k, status="ADDED",
                     heading=v.heading, diff=())
        for k, v in right.items()
    ]
    return ComparisonReport(unchanged=unchanged, changes=tuple(changes))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contracts.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite and mypy**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy .`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/michael/contracts.py tests/test_contracts.py
git commit -m "feat(contracts): align two drafts clause by clause and report what changed"
```

---

### Task 6: The element list and its loader

**Files:**
- Create: `elements.yaml`
- Create: `src/michael/elements.py`
- Create: `tests/test_elements.py`
- Modify: `src/michael/config.py` (add `elements_file`)

**Interfaces:**
- Consumes: `config.settings()`, `config._path`
- Produces:

```python
class ElementConfigError(RuntimeError)

@dataclass(frozen=True, slots=True)
class Element:
    id: str
    label: str
    placeholder: str
    synonyms: tuple[str, ...]
    basis: str                  # "drafting convention", or "Citation s N"

@lru_cache(maxsize=4)
def load_elements(path: Path | None = None) -> tuple[Element, ...]
def elements_for(domain: str | None = None, *, path: Path | None = None) -> tuple[Element, ...]
```

- [ ] **Step 1: Write `elements.yaml`**

```yaml
# Reference list of contract elements. Data, not law.
#
# `basis` is the honesty field. An element either points at a provision in the
# corpus or is marked `drafting convention` with no legal claim attached. The
# report words it "related provision", never "required by": MICHAEL.md permits
# stating what a clause is based on and forbids certifying it in either
# direction, and a checklist saying an Act "requires" a clause has become advice.
#
# Per-domain lists exist only where the corpus can ground them.
universal:
  - id: parties
    label: Parties
    placeholder: PARTY_A_NAME
    synonyms: [parties, between, party a, party b]
    basis: drafting convention
  - id: term
    label: Term and commencement
    placeholder: COMMENCEMENT_DATE
    synonyms: [term, commencement, duration, expiry, expiration]
    basis: drafting convention
  - id: payment
    label: Payment terms
    placeholder: PAYMENT_TERMS
    synonyms: [payment, fees, invoice, remuneration, consideration]
    basis: drafting convention
  - id: termination
    label: Termination
    placeholder: TERMINATION_NOTICE_PERIOD
    synonyms: [termination, terminate, notice period, end of agreement]
    basis: drafting convention
  - id: renewal
    label: Renewal
    placeholder: RENEWAL_TERMS
    synonyms: [renewal, auto-renewal, extension, rollover]
    basis: drafting convention
  - id: governing_law
    label: Governing law
    placeholder: GOVERNING_LAW_JURISDICTION
    synonyms: [governing law, applicable law, choice of law, proper law]
    basis: drafting convention
  - id: dispute_resolution
    label: Dispute resolution
    placeholder: DISPUTE_RESOLUTION_FORUM
    synonyms: [dispute resolution, arbitration, mediation, jurisdiction of courts]
    basis: drafting convention
  - id: liability
    label: Limitation of liability
    placeholder: LIABILITY_CAP_AMOUNT
    synonyms: [limitation of liability, liability cap, indemnity, exclusion of liability]
    basis: drafting convention
  - id: intellectual_property
    label: Intellectual property
    placeholder: IP_OWNERSHIP
    synonyms: [intellectual property, ip ownership, moral rights, copyright]
    basis: drafting convention
  - id: confidentiality
    label: Confidentiality
    placeholder: CONFIDENTIALITY_DURATION
    synonyms: [confidentiality, confidential information, non-disclosure]
    basis: drafting convention
  - id: notices
    label: Notices
    placeholder: NOTICE_ADDRESS
    synonyms: [notices, service of notice, address for service]
    basis: drafting convention
  - id: variation
    label: Variation
    placeholder: VARIATION_TERMS
    synonyms: [variation, amendment, written agreement to vary]
    basis: drafting convention

employment:
  - id: modern_award
    label: Modern Award name
    placeholder: MODERN_AWARD_NAME
    synonyms: [modern award, award, classification level]
    basis: Fair Work Act 2009 (Cth) s 47
  - id: superannuation
    label: Superannuation fund
    placeholder: SUPERANNUATION_FUND
    synonyms: [superannuation, super fund, contributions]
    basis: drafting convention
  - id: ordinary_hours
    label: Ordinary hours of work
    placeholder: ORDINARY_HOURS
    synonyms: [ordinary hours, hours of work, roster, span of hours]
    basis: Fair Work Act 2009 (Cth) s 62

contracts: []
consumer: []
property: []
corporate: []
work_health_safety: []
privacy: []
```

- [ ] **Step 2: Add the config setting**

In `src/michael/config.py`, add `elements_file: Path` to the settings dataclass
beside `domains_file`, and in the constructor beside the existing `_path` calls:

```python
elements_file=_path("MICHAEL_ELEMENTS_FILE", "elements.yaml"),
```

Add the same key to `.env.example` with the value `elements.yaml`.

- [ ] **Step 3: Write the failing loader tests**

```python
# tests/test_elements.py
from pathlib import Path

import pytest

from michael.elements import Element, ElementConfigError, elements_for, load_elements


def test_the_shipped_file_loads() -> None:
    elements = load_elements()
    assert elements
    assert all(isinstance(e, Element) for e in elements)


def test_every_element_id_is_unique() -> None:
    ids = [e.id for e in load_elements()]
    assert len(ids) == len(set(ids))


def test_every_element_has_synonyms() -> None:
    assert all(e.synonyms for e in load_elements())


def test_employment_adds_to_the_universal_list_rather_than_replacing_it() -> None:
    universal = {e.id for e in elements_for()}
    employment = {e.id for e in elements_for("employment")}
    assert universal < employment
    assert "modern_award" in employment
    assert "governing_law" in employment


def test_an_unknown_domain_falls_back_to_universal() -> None:
    assert {e.id for e in elements_for("nonsense")} == {e.id for e in elements_for()}


def test_a_duplicate_id_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "elements.yaml"
    bad.write_text(
        "universal:\n"
        "  - {id: x, label: X, placeholder: X, synonyms: [x], basis: drafting convention}\n"
        "  - {id: x, label: Y, placeholder: Y, synonyms: [y], basis: drafting convention}\n",
        encoding="utf-8",
    )
    with pytest.raises(ElementConfigError, match="duplicate"):
        load_elements(bad)


def test_an_element_with_no_synonyms_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "elements.yaml"
    bad.write_text(
        "universal:\n"
        "  - {id: x, label: X, placeholder: X, synonyms: [], basis: drafting convention}\n",
        encoding="utf-8",
    )
    with pytest.raises(ElementConfigError, match="synonyms"):
        load_elements(bad)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_elements.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'michael.elements'`

- [ ] **Step 5: Write the loader**

```python
# src/michael/elements.py
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_elements.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add elements.yaml src/michael/elements.py tests/test_elements.py src/michael/config.py .env.example
git commit -m "feat(elements): a validated reference list of contract elements"
```

---

### Task 7: The completeness audit

**Files:**
- Modify: `src/michael/elements.py`
- Modify: `tests/test_elements.py`

**Interfaces:**
- Consumes: `Element`, `elements_for`, `contracts.Clause`, `contracts.split_clauses`
- Produces:

```python
@dataclass(frozen=True, slots=True)
class ElementFinding:
    element_id: str
    label: str
    state: Literal["PRESENT", "ABSENT", "UNCERTAIN"]
    clause_id: str | None
    basis: str

@dataclass(frozen=True, slots=True)
class CompletenessReport:
    domain: str | None
    findings: tuple[ElementFinding, ...]

def audit(text: str, *, domain: str | None = None, path: Path | None = None) -> CompletenessReport
```

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_elements.py
from michael.elements import CompletenessReport, ElementFinding, audit

WITH_GOVERNING_LAW = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. GOVERNING LAW
This agreement is governed by the laws of Western Australia.
"""

WITHOUT_GOVERNING_LAW = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd.

2. PAYMENT
The Client must pay within 30 days.
"""

MENTIONED_ONLY_IN_BODY = """1. PARTIES
The parties are Acme Pty Ltd and Beta Pty Ltd, and the governing law of any
dispute is a matter the parties will address elsewhere.
"""


def _state(report: CompletenessReport, element_id: str) -> str:
    return next(f.state for f in report.findings if f.element_id == element_id)


def test_an_element_with_its_own_clause_heading_is_present() -> None:
    report = audit(WITH_GOVERNING_LAW)
    assert _state(report, "governing_law") == "PRESENT"


def test_an_element_that_appears_nowhere_is_absent() -> None:
    report = audit(WITHOUT_GOVERNING_LAW)
    assert _state(report, "governing_law") == "ABSENT"


def test_an_element_mentioned_only_in_body_text_is_uncertain() -> None:
    """Three states, not two: a false ABSENT costs the report its credibility."""
    report = audit(MENTIONED_ONLY_IN_BODY)
    assert _state(report, "governing_law") == "UNCERTAIN"


def test_a_present_finding_names_the_clause_it_was_found_in() -> None:
    finding = next(f for f in audit(WITH_GOVERNING_LAW).findings if f.element_id == "governing_law")
    assert finding.clause_id == "2"


def test_the_domain_adds_its_own_elements_to_the_report() -> None:
    ids = {f.element_id for f in audit(WITH_GOVERNING_LAW, domain="employment").findings}
    assert "modern_award" in ids


def test_a_finding_has_nowhere_to_put_a_verdict() -> None:
    fields = set(ElementFinding.__dataclass_fields__) | set(CompletenessReport.__dataclass_fields__)
    assert not fields & {"risk", "severity", "score", "recommendation", "verdict"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_elements.py -k audit -v`
Expected: FAIL, `ImportError: cannot import name 'audit'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/michael/elements.py
from typing import Literal

from michael.contracts import split_clauses


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
            if any(s in f"{c.clause_id} {c.heading}".lower() for s in element.synonyms)
        ]
        body_hits = [
            c for c in clauses
            if any(s in c.text.lower() for s in element.synonyms)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_elements.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite and mypy**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy .`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/michael/elements.py tests/test_elements.py
git commit -m "feat(elements): audit a document for the elements it does not mention"
```

---

### Task 8: The outline consumes the element list

**Files:**
- Modify: `src/michael/draft.py` (`outline_without_template`)
- Modify: `tests/test_draft.py`

**Interfaces:**
- Consumes: `elements.elements_for`
- Produces: no new public name. The outline's text changes.

**Why.** `outline_without_template()` emits a hardcoded PARTIES block and one
heading per retrieved provision. It emits nothing for governing law, dispute
resolution or limitation of liability. Those are not invented - the `[MISSING]`
rule holds - they are absent and unmentioned, which leaves the reader nothing
to check. Emitting each element's placeholder makes `fill()` mark them
`[MISSING: ...]` automatically, through machinery that already exists.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_draft.py
def test_the_outline_asks_for_every_universal_element() -> None:
    from michael.elements import elements_for

    draft = outline_without_template(
        request="a services agreement for a consultant",
        provisions=(),
        domain="contracts",
        write_template=False,
    )
    for element in elements_for("contracts"):
        assert element.label.upper() in draft.body.upper(), element.label
        assert f"[MISSING: {element.placeholder.replace('_', ' ').lower()}]" in draft.body


def test_the_outline_lists_those_missing_items_as_open_items() -> None:
    draft = outline_without_template(
        request="a services agreement for a consultant",
        provisions=(),
        domain="contracts",
        write_template=False,
    )
    assert "OPEN ITEMS" in draft.body
    assert "governing law jurisdiction" in draft.body.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_draft.py -k outline -v`
Expected: FAIL, the governing-law assertion

- [ ] **Step 3: Replace the hardcoded PARTIES block**

In `src/michael/draft.py`, in `outline_without_template`, replace these lines:

```python
        "## PARTIES",
        "",
        "- Party A: {{PARTY_A_NAME}}, ABN {{PARTY_A_ABN}}, of {{PARTY_A_ADDRESS}}",
        "- Party B: {{PARTY_B_NAME}}, of {{PARTY_B_ADDRESS}}",
        "- Commencement: {{COMMENCEMENT_DATE}}",
        "",
        "## CLAUSES",
        "",
```

with:

```python
        "## ELEMENTS TO SETTLE",
        "",
        "Each element below is one a contract of this kind commonly addresses.",
        "An element listed here is not a statement that the law requires it.",
        "",
    ]

    for element in elements_for(domain):
        heading_lines += [
            f"### {element.label}",
            "",
            "- " + "{{" + element.placeholder + "}}",
            f"- Related provision: {element.basis}",
            "",
        ]

    heading_lines += [
        "## CLAUSES",
        "",
```

and add the import at the top of `draft.py`:

```python
from michael.elements import elements_for
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_draft.py -v`
Expected: all pass. Other outline tests may now fail on exact text; update
their expected strings, do not weaken their assertions.

- [ ] **Step 5: Run the full suite and mypy**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy .`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/michael/draft.py tests/test_draft.py
git commit -m "feat(draft): the no-template outline asks for the elements it used to omit"
```

---

### Task 9: MCP registration and the guard test

**Files:**
- Modify: `src/michael/tools.py`
- Modify: `hermes/config.template.yaml`
- Modify: `tests/test_runtime_config.py`
- Modify: `tests/test_tools.py`

**Interfaces:**
- Consumes: `contracts.compare`, `elements.audit`, `contract_text.contract_text`
- Produces: handlers `compare_documents`, `check_completeness`

- [ ] **Step 1: Write the failing guard test**

Replace `test_the_agent_profile_grants_no_write_tool` in
`tests/test_runtime_config.py` with:

```python
#: Every tool the answering agent may hold. Exact, not a floor: the point of
#: this set is that adding a name to it is a deliberate act with a reviewer.
READ_ONLY_TOOLS = frozenset({
    "classify_request",
    "search_provisions",
    "draft_document",
    "compare_documents",
    "check_completeness",
})

#: Every tool that writes. None may reach the agent.
WRITE_TOOLS = frozenset({
    "apply_schema",
    "ingest_source_url",
    "ingest_local_file",
    "seed_corpus",
})


def test_the_agent_profile_grants_no_write_tool() -> None:
    """The incident this guards against.

    A deployed agent called ingest_source_url mid-answer, pulled a
    headings-only page into the corpus, and cited it in the same turn. The
    read-only database role could not stop it: ingestion runs under the
    read/write URL by design. So the write tools must not reach the agent at
    all, and --allow-writes must not be passed on the answering path.
    """
    import yaml

    out = yaml.safe_load(mod.render(body(), FULL_ENV))
    michael = out["mcp_servers"]["michael"]

    assert "--allow-writes" not in michael["args"]

    granted = set(michael["tools"]["include"])
    assert granted == set(READ_ONLY_TOOLS)
    assert not (granted & WRITE_TOOLS)
    assert WRITE_TOOLS <= set(michael["tools"]["exclude"])
    assert READ_ONLY_TOOLS.isdisjoint(WRITE_TOOLS)


def test_no_answering_handler_can_reach_the_ingestion_module() -> None:
    """The invariant the incident was actually about, not today's tool names.

    A tool name can be added to the profile by anyone. What must stay true is
    that nothing reachable from the answering path imports the module that
    writes to the corpus.
    """
    import inspect

    from michael import tools

    for schema in tools.ANSWERING_TOOLS:
        handler = tools._HANDLERS[schema["name"]]
        source = inspect.getsource(inspect.getmodule(handler))
        assert "from michael.ingest import" not in source, schema["name"]
        assert "import michael.ingest" not in source, schema["name"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_runtime_config.py -v`
Expected: FAIL, granted set is the old three

- [ ] **Step 3: Add the two handlers to `tools.py`**

```python
# add near the other handlers in src/michael/tools.py
from pathlib import Path

from michael.contract_text import contract_text
from michael.contracts import compare
from michael.elements import audit


def compare_documents(path_a: str, path_b: str) -> dict[str, Any]:
    """Compare two contract drafts clause by clause. Reads files, writes nothing."""
    text_a = contract_text(Path(path_a).read_bytes(), origin=path_a)
    text_b = contract_text(Path(path_b).read_bytes(), origin=path_b)
    report = compare(text_a, text_b)
    return {
        "unchanged": report.unchanged,
        "changes": [
            {
                "clause_id_before": c.clause_id_before,
                "clause_id_after": c.clause_id_after,
                "status": c.status,
                "heading": c.heading,
                "diff": list(c.diff),
            }
            for c in report.changes
        ],
    }


def check_completeness(path: str, domain: str | None = None) -> dict[str, Any]:
    """Report which standard elements a document does not mention. Read-only."""
    report = audit(contract_text(Path(path).read_bytes(), origin=path), domain=domain)
    return {
        "domain": report.domain,
        "findings": [
            {
                "element_id": f.element_id,
                "label": f.label,
                "state": f.state,
                "clause_id": f.clause_id,
                "related_provision": f.basis,
            }
            for f in report.findings
        ],
    }
```

The JSON key is `related_provision`, never `required_by`. `MICHAEL.md` permits
stating what a clause is based on and forbids certifying it in either direction.

- [ ] **Step 4: Register both in `ANSWERING_TOOLS` and `_HANDLERS`**

```python
# append to the ANSWERING_TOOLS tuple in src/michael/tools.py
    {
        "name": "compare_documents",
        "description": (
            "Compare two contract drafts clause by clause and report which clauses "
            "changed, were added, removed or moved. Reports what differs, never "
            "whether a difference is good or bad. Reads two files; writes nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "the earlier draft"},
                "path_b": {"type": "string", "description": "the later draft"},
            },
            "required": ["path_a", "path_b"],
        },
    },
    {
        "name": "check_completeness",
        "description": (
            "Report which standard contract elements a document addresses and which "
            "it does not mention. Each element is PRESENT, ABSENT or UNCERTAIN. "
            "Naming an element is not a statement that the law requires it. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "domain": {
                    "type": "string",
                    "description": "Override the routed domain. Omit for universal elements.",
                },
            },
            "required": ["path"],
        },
    },
```

```python
# add to _HANDLERS in src/michael/tools.py
    "compare_documents": compare_documents,
    "check_completeness": check_completeness,
```

- [ ] **Step 5: Add both names to the Hermes profile**

In `hermes/config.template.yaml`, add `compare_documents` and
`check_completeness` to `tools.include` for the `michael` MCP server. Leave
`exclude` exactly as it is.

- [ ] **Step 6: Run the tests and mypy**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy .`
Expected: all pass

- [ ] **Step 7: Verify the live config after deploying, not the template**

```bash
railway ssh --service michael-hermes --environment production sh -c "python3 -c \"import yaml; c=yaml.safe_load(open('/opt/data/config.yaml')); m=c['mcp_servers']['michael']; print(sorted(m['tools']['include'])); print(sorted(m['tools']['exclude']))\""
```

The repository is not what is running. This check has caught a silently
restored toolset before.

- [ ] **Step 8: Commit**

```bash
git add src/michael/tools.py hermes/config.template.yaml tests/test_runtime_config.py
git commit -m "feat(tools): expose compare_documents and check_completeness, read-only"
```

---

### Task 10: Calibrate the similarity threshold

**Files:**
- Create: `calibration/labelled_clause_pairs.json`
- Create: `calibration/calibrate_similarity.py`
- Modify: `src/michael/contracts.py`, the constant, only if the measurement moves it

**Interfaces:**
- Consumes: `contracts.normalise`, `contracts.SIMILARITY_THRESHOLD`
- Produces: a measured threshold and a committed labelled set. No new public name.

**Why this is its own task.** 0.6 is a guess. `RETRIEVAL_MIN_SCORE` was
measured twice against a labelled set, and the README once described 0.65 as
calibrated long after that stopped being true. A guess described as tuned is
the same defect wearing a different number.

- [ ] **Step 1: Build the labelled set**

At least 15 pairs that ARE one clause before and after an edit, and 10 pairs
that are DIFFERENT clauses on a similar topic - two indemnity clauses from
different contracts, two termination clauses with different triggers. The
second group is the one that matters: it is where a threshold set too low
produces a false CHANGED, pairing two clauses that merely share a subject.

```json
{
  "note": "Real clause text. same_clause is one clause before and after an edit. different_clause is two genuinely different clauses that a topic-similarity measure would wrongly pair.",
  "same_clause": [
    {
      "before": "The Client must pay each invoice within 30 days of receipt.",
      "after": "The Client must pay each invoice within 60 days of receipt."
    }
  ],
  "different_clause": [
    {
      "a": "Either party may terminate this agreement on 14 days written notice.",
      "b": "Either party may terminate this agreement immediately for material breach."
    }
  ]
}
```

- [ ] **Step 2: Write the sweep**

```python
# calibration/calibrate_similarity.py
"""Measure the clause-similarity threshold. Mirrors calibration/calibrate.py."""

import difflib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from michael.contracts import normalise

data = json.loads(
    (pathlib.Path(__file__).parent / "labelled_clause_pairs.json").read_text(encoding="utf-8")
)
same = [
    difflib.SequenceMatcher(None, normalise(p["before"]), normalise(p["after"])).ratio()
    for p in data["same_clause"]
]
different = [
    difflib.SequenceMatcher(None, normalise(p["a"]), normalise(p["b"])).ratio()
    for p in data["different_clause"]
]

print(f"{'thresh':>7} {'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4} {'precision':>10} {'recall':>8}")
best = None
for step in range(30, 96, 5):
    threshold = step / 100
    tp = sum(1 for r in same if r >= threshold)
    fn = len(same) - tp
    fp = sum(1 for r in different if r >= threshold)
    tn = len(different) - fp
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / len(same) if same else 0.0
    print(f"{threshold:>7.2f} {tp:>4} {fn:>4} {fp:>4} {tn:>4} {precision:>10.3f} {recall:>8.3f}")
    if fp == 0 and best is None:
        best = threshold

print()
print(f"lowest threshold with zero false positives: {best}")
print(f"highest different-clause ratio: {max(different) if different else 0:.4f}")
```

- [ ] **Step 3: Run it and read the sweep**

Run: `.venv/Scripts/python.exe calibration/calibrate_similarity.py`

Take the lowest threshold with zero false positives, exactly as
`calibration/calibrate.py` does for retrieval.

- [ ] **Step 4: Update the constant only if the measurement moves it**

If the measured value differs from 0.6, change `SIMILARITY_THRESHOLD` and
replace the "A GUESS" comment with the measured value, the date, and the set
size. If it does not move, say so explicitly in the comment rather than leaving
a reader unable to tell a confirmed number from an unexamined one.

- [ ] **Step 5: Commit**

```bash
git add calibration/labelled_clause_pairs.json calibration/calibrate_similarity.py src/michael/contracts.py
git commit -m "test(contracts): measure the clause-similarity threshold against a labelled set"
```

---

## Task assignment across the Orca team

| Tasks | Worker | Why |
|---|---|---|
| 1, 2, 3, 4, 5 | WORKER-3 | Drafting and compliance is its ground; the splitter and comparison are one body of work |
| 6, 7, 8 | WORKER-3 | The element list and its two consumers; same ground, and Task 8 edits `draft.py` |
| 9 | WORKER-4 | The tool surface and the guard test are its ground |
| 10 | WORKER-2 | Retrieval and calibration; it owns every measured threshold in this project |

Tasks 1-8 are sequential. Task 9 depends on 5 and 7. Task 10 depends on 4 and
may run any time after it.

## Definition of done

- `uv run pytest` green, `uv run mypy .` clean
- Task 3's evidence file committed and read by a person, not merely produced
- The live Railway config shows five tools included and four excluded
- No dataclass carries a risk, severity, score, recommendation or verdict field
- `SIMILARITY_THRESHOLD` is either measured, or its comment says plainly that
  it is not
- Nothing pushed. Push is the owner's.
