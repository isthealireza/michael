# Contract comparison and completeness — design

Date: 2026-09-17
Status: approved, not implemented

## Why

Michael drafts contracts but cannot read one back. Two gaps follow from that.

**A returned draft cannot be compared.** When a counterparty sends a contract
back, nothing tells the reader which clauses moved. Word's compare gives a
character diff; it does not say "clause 7.2 Payment Terms changed".

**The no-template outline is thinner than it looks.** `outline_without_template()`
emits a hardcoded PARTIES block and one heading per retrieved provision. It
emits nothing for governing law, dispute resolution, limitation of liability,
termination, IP or confidentiality. Those are not invented — the `[MISSING]`
rule holds — they are *absent and unmentioned*, which in a drafting tool is the
worse failure, because absence leaves no trace for the reader to check.

The two gaps share one component: a canonical list of contract elements, read
once when generating an outline and once when auditing a document.

## Scope

In scope: clause-level comparison of two contracts; a completeness audit against
a reference element list; extending the outline to ask for those elements.

Out of scope, deliberately: PDF input; risk scoring; any verdict, severity or
recommendation; persistence of compared documents; automated folder watching.

The last three are not omissions of effort. A verdict engine contradicts
`MICHAEL.md`, persistence would require write access on the answering path, and
folder watching would make ingestion automatic again — the property that caused
the production incident.

## Prior art, and what was rejected

The request came from reading `github.com/Lethe044/hermes-legal` (MIT). Two
ideas were taken: clause-level version comparison, and the extracted-field list.

The rest was rejected on inspection. A grep of that repository for
`retriev|corpus|citation|statute|legislation|vector|embed|rag|database` returns
one hit, and it is a keyword list — the analysis is the model's own judgement,
ungrounded. It emits `SIGN / NEGOTIATE / REJECT` verdicts, which `MICHAEL.md`
forbids in both directions. Its risk thresholds ("non-compete > 1 year",
"contracts > $10,000") have no legal source; in Australia a restraint's
enforceability is a common-law reasonableness question, not a duration. No code
is copied.

## Architecture

Three new files. No schema change, no migration, no new dependency.

```
src/michael/contracts.py   split_clauses() · align() · diff() · audit()
elements.yaml              the reference list of contract elements
src/michael/elements.py    loader and validation, mirroring domains.yaml
```

Reading a document reuses `docx_text.py` and `html_text.py`. Markdown and plain
text need no parser; markdown headings have `#` and `**` stripped before
matching. One dispatch function sniffs content, as `ingest.extract_text()`
already does, and that is the only thing `contracts.py` takes from the
ingestion side.

**Everything is a pure function.** `compare(a, b)` and `audit(doc)` take text
and return a dataclass. No database read, no database write, no file written.
This is deliberate: the answering path stays read-only at the database level,
neither tool can become a route back into the corpus, and every test is a
fixture and an assertion with no container and no fixtures database.

```
compare:  file_a ─┐
                  ├─ extract_text ─ split_clauses ─ align ─ diff ─ ComparisonReport
          file_b ─┘

audit:    file ───── extract_text ─ split_clauses ─┐
          elements.yaml ──────────────────────────┴─ audit ─ CompletenessReport
```

## The clause splitter

A clause is the deepest numbered unit that carries text — `1.1`, not `1` —
because that is where negotiation happens. The clause id is the dotted path
itself (`1.1`, `3.2`, `Sch A cl 2`): stable, human-meaningful, and what a reader
would say aloud.

Shapes it must handle: `1. PARTIES`, `1.1 The Employer must…`, `1.1.1`,
`3.2(a)`, `Clause 5 — Termination`, `SCHEDULE A`, all-caps unnumbered headings,
and the markdown forms of each.

Four rules, each carried from a defect this codebase has already paid for:

1. **Nothing is dropped.** Text before the first clause — recitals, preamble,
   the parties block — is emitted with id `(preamble)`, the way
   `split_sections()` emits `(whole document)` when nothing matches. A splitter
   that discards is worse than one that over-includes.
2. **No sequence assumptions.** The splitter must not assume numbering rises.
   That assumption, added to the legislation splitter on 2026-09-17, cost 1,199
   provisions to recover 44 before it was reverted. Contracts renumber, restart
   inside schedules, and skip numbers routinely.
3. **Conservative matching.** A line that is not clearly a clause heading stays
   inside the preceding clause.
4. **Linear.** The line index is built once per document. Rebuilding it per
   candidate made the legislation splitter quadratic: 0.331s for 800 sections
   against 0.019s after the fix.

## Alignment

Three stages, cheapest first.

**Stage 1 — exact id match.** Same clause id both sides. Covers the ordinary
case, a clause edited in place, with no ambiguity.

**Stage 2 — exact text match among the unpaired.** Identical normalised text,
different id: renumbered or moved. Cheap and certain.

**Stage 3 — similarity among what remains.** `difflib.SequenceMatcher.ratio()`
over normalised text, paired greedily best-first.

`difflib`, not embeddings, and the reason is not cost. An embedding answers
"are these two clauses about the same topic", which is precisely the false
positive to avoid: two different indemnity clauses are about the same topic and
are not the same clause. An edited clause shares most of its literal words, so
lexical similarity is the correct signal here, not a weaker substitute. It is
also stdlib, deterministic, and offline, which keeps the function pure.

Stage 3 is O(u²) over unpaired clauses only. Above 400 unpaired on either side
it stops and reports the remainder as ADDED/REMOVED rather than running 160,000
comparisons — the same failing-towards-safety as the apparatus-span cap.

### Outcomes

| Condition | Reported |
|---|---|
| same id, identical text | `UNCHANGED` — counted, not listed |
| same id, different text | `CHANGED` with a word-level diff |
| different id, identical text | `MOVED` |
| different id, similar text | `CHANGED` and `MOVED` |
| present in v1 only | `REMOVED` |
| present in v2 only | `ADDED` |

### The invariant

**A comparison must never report UNCHANGED for a clause that changed.**

A false CHANGED is noise a reader discards in a second. A false UNCHANGED is a
negotiation term that slipped through silently — the same class of failure as
ingesting a headings-only page and citing it.

Normalisation is therefore deliberately timid: collapse whitespace, yes;
lowercase or strip punctuation, no. `must` against `may`, `$5,000` against
`$5000`, `30 days` against `3 days` must each read as a change, and each gets a
test asserting so.

### Known gap: the similarity threshold

The stage-3 threshold starts at 0.6 and **that number is a guess**. This
project measures its thresholds: `RETRIEVAL_MIN_SCORE` is calibrated against
`calibration/labelled_queries.json`, currently 21 known-good and 10
known-absent queries, scored unfiltered, taking the lowest threshold with zero
false positives. The comparison threshold ships labelled as an assumption, and
calibrating it against a labelled set of real before/after clause pairs is a
separate task with its own acceptance. Claiming it is tuned when it is not
would repeat the README defect where 0.65 was described as calibrated long
after it had stopped being true.

## The element list

`elements.yaml`, shaped like `domains.yaml`, which already establishes the
pattern in this repository.

```yaml
universal:
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
  - id: liability_cap
    label: Limitation of liability
    placeholder: LIABILITY_CAP_AMOUNT
    synonyms: [limitation of liability, liability cap, indemnity, exclusion of liability]
    basis: drafting convention

employment:
  - id: modern_award
    label: Modern Award name
    placeholder: MODERN_AWARD_NAME
    synonyms: [modern award, award, classification]
    basis: { citation: "Fair Work Act 2009 (Cth)", section: "47" }
```

`basis` is the honesty field. An element either points at a provision in the
corpus or is marked `drafting convention` with no legal claim attached. The
report prints that distinction and words it **related provision**, never
**required by**. `MICHAEL.md` permits "State what the clause is based on and
which provision is engaged" and forbids certifying a clause in either
direction; a checklist saying "the Fair Work Act requires this clause" has
quietly become advice.

Per-domain lists exist only where the corpus can ground them: `employment` now,
the other six left empty until there is law to cite rather than filled with
plausible assertions.

A loader validates on import, as `domains.yaml` does: unique ids, non-empty
synonyms, a `basis` that is either the literal `drafting convention` or a
citation and section that resolve against the corpus.

## The completeness audit

Detection matches synonyms against clause headings first, then clause text.

The interesting decision is what to do when matching is ambiguous, and the
answer follows the grain of the rest of the system: **three states, not two.**

`PRESENT` / `ABSENT` / `UNCERTAIN`

A false ABSENT sends the reader hunting for something sitting in clause 14 and
costs the report its credibility. A false PRESENT hides a genuinely missing
clause. Rather than choosing which way to be wrong, the third state carries the
ambiguity to the surface — the same move as empty retrieval returning
`covered: false` instead of the nearest guess, and `[MISSING]` instead of a
plausible ABN. Uncertainty is a value here, not a hole in the design.

`UNCERTAIN` means a synonym matched body text but no clause heading, or two
clauses matched the same element. The report names what it found and leaves the
judgement to the reader.

## The outline consumer

`outline_without_template()` will emit a section per universal element carrying
that element's `{{PLACEHOLDER}}`, so `fill()` marks each one
`[MISSING: governing law jurisdiction]` automatically. The existing machinery
does the work; it needs a longer list of things to ask for.

Two consequences, recorded so they are not discovered mid-build: the outline
gets materially longer, and the existing outline tests will need updating.

**`MICHAEL.md` does not change for this.** Its `[MISSING]` list is prose
describing a rule about inventing facts; enforcement is structural, in `fill()`,
over whatever placeholders a template declares. Extending the outline's
placeholders makes new `[MISSING]` items appear with no prompt edit. That
matters because `MICHAEL.md` is owner-only, and this design needs nothing from
it.

## Tool surface

Two tools, not one:

```
compare_documents(path_a, path_b)  -> ComparisonReport
check_completeness(path, domain?)  -> CompletenessReport
```

Separate because they answer different questions. Combining them would produce
a tool whose output shape depends on its arguments, which reads acceptably in a
schema and confuses a model in use.

Both join `ANSWERING_TOOLS`. Both are read-only and take no database
connection.

### Output shapes

```python
@dataclass(frozen=True, slots=True)
class ClauseChange:
    clause_id_before: str | None    # None when ADDED
    clause_id_after: str | None     # None when REMOVED
    status: Literal["CHANGED", "ADDED", "REMOVED", "MOVED"]
    heading: str
    diff: tuple[str, ...]           # word-level, before and after

@dataclass(frozen=True, slots=True)
class ElementFinding:
    element_id: str
    label: str
    state: Literal["PRESENT", "ABSENT", "UNCERTAIN"]
    clause_id: str | None           # where it was found, when it was
    basis: str                      # "drafting convention", or a pinpoint
```

Neither carries `risk`, `severity`, `score` or `recommendation`. A model reading
these cannot report a verdict because the structure has nowhere to put one.
This is the `[MISSING]` technique: a guarantee expressed in code holds under
paraphrase, and a guarantee expressed in a prompt does not.

### The guard test

`tests/test_runtime_config.py::test_the_agent_profile_grants_no_write_tool`
currently asserts an exact set of three tool names. That exactness is what makes
it strong, and the lazy change — appending two names — converts a whitelist into
a list nobody guards. It is restructured instead:

```python
READ_ONLY_TOOLS = frozenset({
    "classify_request", "search_provisions", "draft_document",
    "compare_documents", "check_completeness",
})
WRITE_TOOLS = frozenset({
    "apply_schema", "ingest_source_url", "ingest_local_file", "seed_corpus",
})

assert granted == READ_ONLY_TOOLS
assert not (granted & WRITE_TOOLS)
assert WRITE_TOOLS <= set(excluded)
assert READ_ONLY_TOOLS.isdisjoint(WRITE_TOOLS)
assert "--allow-writes" not in michael["args"]
```

One further assertion is added, because it states the invariant the incident was
actually about rather than today's spelling of it: **no handler reachable from
`ANSWERING_TOOLS` may import from `michael.ingest`.**

## Testing

Unit tests are fixtures and assertions; nothing needs a database.

Required fixtures, each from a real contract, not invented:

- three numbering styles for the splitter, including one that restarts inside a
  schedule and one with unnumbered all-caps headings
- a renumbered clause, for stage 2
- an edited clause, for stage 3
- the four normalisation cases: `must`/`may`, `$5,000`/`$5000`,
  `30 days`/`3 days`, and a whitespace-only change that must read UNCHANGED
- a document missing governing law, for ABSENT
- a document where governing law appears only in body text, for UNCERTAIN

**The acceptance criterion for the splitter is not `pytest`.** A splitter change
is proven by running it over real documents and diffing the output, read by a
person. Five green fixtures hid a 13% corpus loss on 2026-09-17. Stage 1 passes
on a before-and-after clause count across real contracts, not on a green suite.

## Build order

WORKER-3 owns the work; drafting and compliance is its ground.

| Stage | Worker | Deliverable |
|---|---|---|
| 1 | WORKER-3 | `contracts.py` splitter plus real-document fixtures |
| 2 | WORKER-3 | alignment and diff, with the normalisation tests |
| 3 | WORKER-3 | `elements.yaml`, loader, outline consumes it |
| 4 | WORKER-4 | MCP registration and the guard-test restructure |
| 5 | WORKER-2 | similarity threshold: labelled pair set, measured, reported |

Stage 5 is separate on purpose. The threshold is a guess until measured, and
this project does not describe guesses as calibrated.

## Risks

**The splitter meets a contract shape no fixture covers.** Most likely risk.
Mitigated by rule 1 — nothing is dropped — so the failure mode is a clause too
large rather than a clause missing.

**The audit reports ABSENT for something present.** Mitigated by the third
state, and by wording that names where it looked.

**A future change adds a scoring field.** Mitigated by the dataclasses being
frozen and by review; not mitigated by anything structural. Worth watching.

**Workers reach production.** Orca workers run on the operator's machine and
share its authenticated Railway session, so their read-only discipline is a
brief rather than a mechanism. `michael_ro` refuses writes and is verified to do
so, but a worker that ignores the protocol can still reach the read/write role.
The only true boundary is a separate environment. Tracked separately from this
design.
