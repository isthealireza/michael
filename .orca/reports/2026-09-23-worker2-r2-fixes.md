# WORKER-2 — Round 2 retrieval fixes (W2-S1, W2-S2)

Both defects shared one root cause and were fixed with one change.

## Root cause

`_section_lookup` (`src/michael/retrieve.py`) matched a citation's identifier
by exact string equality on `provisions.section_number`. `ingest.py` numbers a
Schedule clause from its own Schedule, not the Act (`"Sch 1 cl 47A"`, not
`"47A"`), specifically so that a Schedule clause and a plain section sharing
a bare number are two distinct, real provisions - but that means the exact
string a user types for one of them ("47A") and the string the *other* one
is stored under ("Sch 1 cl 47A") are unrelated as far as `= upper(...)` is
concerned. That produced two failures from the same gap:

- **W2-S2 (blocking):** a bare-number lookup ("s 1 of the Fair Work Act")
  never considered the Schedule-clause sibling, so `total_matches` and
  `ambiguous_pinpoint` reported a false, confident single match while a real
  second (or, for Fair Work Act's "1", fifth) meaning of the same citation
  existed and was silently never checked.
- **W2-S1 (serious):** when the Act name narrows the search to an Act whose
  "s 47A" is *only* filed as a Schedule clause and has no plain section 47A
  at all, the exact-match query found nothing, `_section_lookup` returned
  `None`, and the query fell through to the weaker, unreliable hybrid path.

## Fix

`src/michael/retrieve.py`:

- `SECTION_LOOKUP_SQL` now matches a provision whose `section_number` either
  equals the extracted identifier exactly, **or** matches the pattern
  `^Sch \S+ cl <identifier>$` (case-insensitive) - the sibling form a
  Schedule clause of the same number would be stored under.
- `_section_lookup` computes that sibling pattern only when the extracted
  identifier is a bare number (not already a `"Sch N cl M"` query, which is
  already the specific, unambiguous string and has no bare-number sibling to
  gain from this).
- `total_matches` / `ambiguous_pinpoint` are unchanged in shape (still "how
  many, in full", still never truncated) - what changed is that the
  underlying SQL now actually finds every real candidate, so the count it
  reports is true. No change was needed to make the *reporting* honest once
  the *lookup* stopped missing rows; the false certainty was never in
  `tools.py`, it was in what `retrieve.py` handed it.

No change to `RETRIEVAL_MIN_SCORE`, the fusion weights, or the tsvector -
this is the identifier-lookup path, which does not touch the calibrated
hybrid threshold.

## Verification

**Against real production data**, via `.orca/ro.sh python` with a
self-contained script (not importing `michael.retrieve`, since the deployed
container still runs the old code — this proves the new SQL against the
real corpus without needing a push):

```
--- section='47A' act_phrase='Fair Work Act' -> 1 rows ---
  Fair Work Act 2009 (Cth) | Sch 1 cl 47A | Casual employees of small business employers
--- section='1' act_phrase='Fair Work Act' -> 6 rows ---
  Fair Work Act 2009 (Cth) | Sch 4 cl 1 | Definition
  Fair Work Act 2009 (Cth) | Sch 5 cl 1 | Definition
  Fair Work Act 2009 (Cth) | Sch 1 cl 1 | Definitions
  Fair Work Act 2009 (Cth) | Sch 2 cl 1 | Definitions
  Fair Work Act 2009 (Cth) | Sch 3 cl 1 | Definitions
  Fair Work Act 2009 (Cth) | 1 | Short title
--- section='Sch 1 cl 47A' act_phrase='Fair Work Act' -> 1 rows ---     (control, unaffected)
  Fair Work Act 2009 (Cth) | Sch 1 cl 47A | Casual employees of small business employers
--- section='117' act_phrase=None -> 7 rows ---                         (was 6; a 7th real sibling now found)
  Civil Judgments Enforcement Act 2004 (WA) | 117 | Sheriff and bailiffs to carry out orders
  Fair Work Act 2009 (Cth) | Sch 1 cl 117 | References to employees etc. in fair work instruments made before commencement
  Fair Work Act 2009 (Cth) | 117 | Requirement for notice of termination or payment in lieu
  Food Act 2008 (WA) | 117 | CEO may delegate
  Heritage Act 2018 (WA) | 117 | Term used: investigation purposes
  Offshore Minerals Act 2003 (WA) | 117 | General
  Residential Parks (Long-stay Tenants) Act 2006 (WA) | 117 | Validation of r. 13A and 22
--- section='Sch 1 cl 47A' act_phrase=None -> 1 rows ->                 (control, unaffected)
```

- "s 1 of the Fair Work Act" now reports the same six real provisions the
  auditor named by hand for W2-S2 (was 1).
- "s 47A of the Fair Work Act" now resolves (was: fell through to hybrid,
  best_score 0.6085, W2-S1).
- A Schedule-clause query given by itself, qualified or not, still resolves
  to exactly one row - the new arm does not manufacture ambiguity where the
  citation was already specific.

**New regression tests** (`tests/test_integration.py`, run against the local
`michael_test` database, `uv run pytest -m integration`): a fixture Act with
a Schedule clause colliding on a bare number (section 200 / Sch 1 cl 200) and
one that exists only as a Schedule clause (Sch 1 cl 82, no plain 82):

- `test_a_bare_number_lookup_also_finds_its_schedule_clause_sibling` - W2-S2:
  asserts `total_matches == 2`, `ambiguous_pinpoint is True`, both
  `section_number`s returned.
- `test_an_act_qualified_bare_number_resolves_to_its_schedule_clause` -
  W2-S1: asserts the Act-qualified bare-number query resolves to the
  Schedule clause, `total_matches == 1`.

**Full suite, local:**

```
uv run pytest -q            -> 440 passed, 27 deselected (integration)
uv run pytest -m integration -q -> 27 passed
uv run mypy src tests        -> Success: no issues found in 65 source files
```

**Calibration, re-run as required, against the local seeded corpus**
(`uv run python calibration/calibrate.py`):

```
 thresh   TP   FN   FP   TN  precision   recall
   0.60   21    0    0   10      1.000    1.000   <- unchanged, chosen
CHOSEN: RETRIEVAL_MIN_SCORE = 0.60
  precision 1.000, recall 1.000 (21/21 known-good retrieved)
  highest known-absent score was 0.579
```

**Threshold does not move.** 0.60, precision 1.000, recall 1.000, same as
both prior calibrations. Expected: this fix is entirely inside the direct
identifier-lookup path, which is tried before the hybrid arms and does not
touch `RETRIEVAL_MIN_SCORE`, the fusion weights, or the tsvector; none of the
labelled queries contain an explicit section marker, so none of them ever
reach `_section_lookup`.

## Files changed

- `src/michael/retrieve.py` - `SECTION_LOOKUP_SQL`, `_section_lookup`.
- `tests/test_integration.py` - two new regression tests plus their fixture.

## Scope note

The auditor's sweep also named a **minor**, unfiled issue in the same area:
a query naming two identifiers ("s 47 and s 26WK") silently keeps only the
first (`extract_section_number` uses `.search()`, not a scan for all
markers) and drops the second with no signal. Not touched here - it was not
part of this round's two named defects, and multi-identifier detection is a
different, larger change (it would need to decide how to report *several*
identifier lookups in one response, not just find the second string).
Naming it, not fixing it, per the task boundary.

Committed locally only, not pushed, per standing instructions.
