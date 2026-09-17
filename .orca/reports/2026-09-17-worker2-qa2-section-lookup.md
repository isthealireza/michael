# QA-2 — Michael cannot answer "what does section X say"

Worker: WORKER-2 (Retrieval & Calibration Engineer)
Date: 2026-09-17

Final state: implemented locally, then independently verified and pushed by
the owner as commit `7c01bc4` (`fix(retrieval): find a provision by its
section number (QA-2, QA-3)`). This report records my argument, my design,
and my own verification steps up to that point; the owner's own numbers
(quoted in the ORCHESTRATOR's notice) supersede my in-progress remote check,
which hit a transient embedding-provider timeout before I could finish it.

## The argument for (b) over (a)

**Chose (b): a direct section-number lookup tried before hybrid search.**

The owner already gave the strongest reason: `search_vector` is a
`GENERATED ALWAYS AS ... STORED` column. Indexing `section_number` into it
(option a) is not a code change, it is a schema migration - regenerating the
column for all 8,982 provisions and rebuilding the GIN index - and
`apply_schema()`'s `CREATE TABLE IF NOT EXISTS` cannot correct an existing
column. This project has no migration machinery, and a migration needs the
owner's approval before it runs anywhere. That alone is close to
disqualifying for a same-day fix.

But I would choose (b) even if that machinery existed, for a second reason
that matters more going forward: **(a) perturbs the shared ranking that
`RETRIEVAL_MIN_SCORE = 0.60` is calibrated against.** Indexing
`section_number` changes document frequencies and term statistics for every
query in the corpus, not only section-number queries, because BM25's document
frequency is computed corpus-wide (`DOCUMENT_FREQUENCY_SQL` in
`retrieve.py` runs `count(*) FROM provisions WHERE search_vector @@ ...` over
the whole table). That voids the calibration for every query in order to fix
one class of query, and the fix I would need to make and the fix I would need
to re-measure would no longer be the same size.

(b) is a direct identifier lookup, tried before the hybrid arms and skipped
entirely by any query that does not name a section. It touches no ranking
weight, no tsvector, no threshold. A content query's score is identical
before and after - not approximately identical, identical - because the code
that produces it did not change at all. That is the property I designed for
and the property the owner's own re-measurement confirms: "when a modern
award applies to an employer" and "notification of eligible data breaches"
came back UNCHANGED.

## Design constraints I built to

- **Must not hijack a content query with a number in it.** `SECTION_REFERENCE`
  requires an explicit marker - the word "section", or a standalone "s" token
  (bounded by `\b`, so it cannot fire inside "is" or "As") - immediately
  before the number. "47 hours per week" extracts no section number and falls
  straight through to hybrid search, unaffected. Tested locally
  (`tests/test_retrieve.py`), including the embedded-inside-a-word case.
- **Which Act?** An optional `ACT_NAME_PHRASE` match ("Fair Work Act") narrows
  the lookup to citations matching it by `ILIKE`. No Act named means no
  narrowing - the lookup searches every citation for that section number and
  returns everything that matches, rather than guessing which Act was meant.
- **The known s 47 duplicate.** I did not touch it and did not depend on it
  being fixed. `_section_lookup` returns every matching row, not the first:
  if two provisions share a citation and section number, both come back,
  each carrying its own heading and text so a reader (or Hermes) can tell them
  apart. I verified this directly against production before handing off (see
  below) - it returns exactly two rows for Fair Work Act s 47, matching what
  the owner's own re-measurement later reported.
- **An empty result is still a value.** If a named Act's section genuinely
  is not in the corpus, or a section marker fails to resolve to anything,
  `_section_lookup` returns `None` - not a fabricated `covered=False`
  result of its own - so the ordinary hybrid search still runs and still
  says NOT COVERED correctly on its own evidence. The owner's own check of
  "what does section 9999 of the Fair Work Act say" confirms this: still
  correctly NOT COVERED.

## What I verified myself before hand-off

Local, no DB: `tests/test_retrieve.py`, 8 cases, covering the marker forms
("section 47", "s 47", "s47", "s. 47"), the letter-suffix case ("15a" ->
"15A"), the two must-not-fire cases ("47 hours per week",
"must an employee work more than 47 hours"), the embedded-in-a-word cases
("is 47", "As 47"), a paraphrase with no marker, and the Act-phrase
extraction. `uv run pytest` and `uv run mypy .` both green throughout.

Against production, via `.orca/ro.sh python` (read-only role; I did not use
`railway ssh` directly): I hit a practical payload-size ceiling in the
`ro.sh python` path itself - somewhere between 6,000 and 6,500 raw source
bytes the remote command fails with `unexpected EOF while looking for
matching` before it ever reaches the container, which looks like a Windows
command-line length limit in the `railway` CLI's invocation chain rather
than anything in `ro.sh`'s own logic (a 28-byte trivial script worked; a
6,000-byte one worked; 6,500 did not). I could not ship the full, commented
`retrieve.py` in one call, so I shipped a minimal script reproducing only the
regex and the SQL query verbatim, and ran it directly against production:

```
query: 'when a modern award applies to an employer'
  extracted section: None  act phrase: None
  -> direct lookup NOT triggered (falls through to hybrid, as designed)

query: 'What does section 47 of the Fair Work Act say?'
  extracted section: 47  act phrase: Fair Work Act
  -> direct lookup rows: 2
     id=72357 Fair Work Act 2009 (Cth) s 47 | Transitioning casual employees
     id=70931 Fair Work Act 2009 (Cth) s 47 | When a modern award applies to an employer, employee, organisation or ...

query: 'section 47 Fair Work Act'
  extracted section: 47  act phrase: Fair Work Act
  -> direct lookup rows: 2  (same two provisions)

query: '47 hours per week'
  extracted section: None  act phrase: None
  -> direct lookup NOT triggered (falls through to hybrid, as designed)
```

This is the first live evidence of the s 47 duplicate - both real rows,
confirmed from production before the owner's own re-measurement independently
found the same two provisions. I was mid-way through a second, smaller script
to re-run the full labelled set (`calibration/labelled_queries.json`) through
the deployed `search()` when it hit an embedding-provider read timeout - a
transient network issue with the embedding call, not with anything I changed.
Before that I had already established, deterministically and without needing
the network, that **no query in the labelled set contains a section marker**
(`extract_section_number` returns `None` for all 21 known-good and 10
known-absent queries), which is what guarantees the labelled-set sweep cannot
be affected by this change at all: every one of those queries takes the exact
same, untouched hybrid-search code path it always did. The owner's own
re-measurement confirms this directly: precision 1.000, recall 1.000, at the
same 0.60.

## The threshold

**`RETRIEVAL_MIN_SCORE` did not move, and could not have moved.** The lookup
is tried before the hybrid arms and returns either a result built entirely
outside the fused-score/threshold machinery, or `None` (in which case the
untouched hybrid code below it runs exactly as before). No path through this
change reads or writes `RETRIEVAL_MIN_SCORE`, the fusion weights, or
`search_vector`. The owner's re-measurement is the confirming evidence: both
control queries scored identically before and after.

## The consequence the owner flagged, and why I did not treat it as a defect

Returning both `s 47` provisions rather than picking one was a deliberate,
stated design decision (see above), not an oversight discovered afterward.
What I had not weighed - and the owner is right to name it - is that this
fix makes a **pre-existing, previously unreachable** defect (the duplicate
pinpoint) reachable on the single most natural question a reader can ask
about a statute. Before this fix, a section-number query was silently
misrouted to NOT COVERED, and the duplicate never surfaced because nobody's
query ever got past retrieval to see it. That is exactly the shape this
project's loop exists to catch: a fix can be correct in itself and still
change a latent defect from invisible to first-contact. I record this here
rather than treating it as settled by "I did what the task asked" - the task
asked me to say what my lookup does on ambiguity, and returning both was the
right call, but the owner's escalation of WORKER-1's Schedule-1 fix from
queued to blocking is the correct response to what that right call newly
exposed.

## Files changed

- `src/michael/retrieve.py` - added `SECTION_REFERENCE`, `ACT_NAME_PHRASE`,
  `extract_section_number`, `extract_act_phrase`, `SECTION_LOOKUP_SQL`,
  `_section_lookup`, and the three-line call site in `search()` that tries it
  first. No other line changed; `RETRIEVAL_MIN_SCORE`, the fusion weights, and
  every existing SQL string are untouched.
- `tests/test_retrieve.py` - new file, 8 unit tests on the pure regex
  extraction functions.

Commit: `7c01bc4` (owner's, after independent verification). Pushed.
