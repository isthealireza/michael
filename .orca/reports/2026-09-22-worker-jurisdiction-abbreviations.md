# Jurisdiction abbreviation detection (NSW, Vic, Qld, SA, Tas, NT, ACT)

Worker: dispatched worker (task_97cd5ab1418e)
Date: 2026-09-22

## What changed

`src/michael/retrieve.py` — extended the existing jurisdiction-mismatch
signal (`named_jurisdiction_mismatch`, added in W2-5) to also recognise
Australian jurisdiction **abbreviations**, not only full names:

- Added `AUSTRALIAN_JURISDICTION_ABBREVIATIONS` (`NSW`, `Vic`/`VIC`,
  `Qld`/`QLD`, `SA`, `Tas`/`TAS`, `NT`, `ACT`) mapped to the same short codes
  as the existing `AUSTRALIAN_JURISDICTION_NAMES` table.
- Added `JURISDICTION_ABBREVIATION_PATTERN`, matched **case-sensitively**
  (no `re.IGNORECASE`) and on word boundaries. Case sensitivity is the
  mechanism that keeps "SA"/"NT" from firing on ordinary lowercase tokens,
  and — the constraint the task called out specifically — keeps "ACT" (the
  territory, written in caps) from firing on "Act"/"act" (the ordinary word
  for a statute, which is also the tail of every Act name the corpus holds).
- `named_jurisdiction_mismatch` now tries the full-name pattern first, then
  falls back to the abbreviation pattern. Word-boundary matching on
  `\bNSW\b` etc. already covers the citation-suffix form ("... Act 2010
  (NSW)") for free, since `(` and `)` are non-word characters and so already
  sit on a `\b` boundary either side of the abbreviation — no separate regex
  was needed for that form.
- No changes to `CORPUS_JURISDICTIONS`, the SQL, the fused-score pipeline,
  `domains.yaml`, or any jurisdiction filter. This is purely an additional
  way to populate the same `jurisdiction_mismatch` field that `tools.py`
  already surfaces; `tools.py` itself needed no change.
- `domains.yaml` was checked and needs no change: no domain's keyword list
  contains a jurisdiction name or abbreviation, so routing/filtering is
  unaffected.

## Tests added (`tests/test_retrieve.py`)

- Every abbreviation recognised in running text (`"... in NSW ..."` style),
  for all 10 accepted spellings.
- Every abbreviation recognised as a citation suffix (`"... Act 2010 (NSW)"`
  style).
- `WA` abbreviation is *not* flagged (held jurisdiction — mirrors the
  existing full-name "Western Australia" test).
- Negative: lowercase forms do not match (`"usa"`, `"isn't"`, `"nt sure"`).
- Negative: the ordinary word "Act" never triggers a mismatch — sentence-
  initial, mid-sentence, and inside a real Act name including one held in
  the corpus (`"Cat Act 2011"`).
- Positive: a genuine `(ACT)` citation suffix is still recognised even in a
  query that also contains the ordinary word "Act" elsewhere.
- Negative: abbreviations embedded inside longer words do not match
  ("vicinity", "contact", "compact", "exact"), and a full name occurring
  before its own abbreviation in the same query resolves to the full name.

## Before / after

Before this change, `named_jurisdiction_mismatch` recognised only full
jurisdiction names. Every one of the new test queries above using an
abbreviation or citation-suffix form was a **false negative**: the corpus
would return WA/Commonwealth provisions for a NSW/Vic/Qld/SA/Tas/NT/ACT
query with no mismatch flag at all. That covers 10 running-text cases + 7
citation-suffix cases = 17 previously-undetected cases now correctly
flagged, with the `WA` abbreviation and every "ordinary word Act" case
correctly still unflagged (0 false positives before and after).

## Full pipeline checks

```
uv run pytest -q                                  # 276 passed, 5 deselected
uv run pytest tests/test_retrieve.py -q            # 24 passed (8 new)
uv run ruff check .                                # All checks passed
uv run ruff format --check src/michael/retrieve.py tests/test_retrieve.py
                                                    # 2 files already formatted
uv run mypy src tests                              # Success: no issues found in 41 source files
```

(`ruff format --check .` over the whole repo flags 3 pre-existing markdown
files under `docs/superpowers/` unrelated to this change — not touched here.)

## The 31-query labelled calibration set

`calibration/labelled_queries.json` holds 21 `known_good` + 10
`known_absent` = 31 queries — this is the set the task means. It calibrates
`RETRIEVAL_MIN_SCORE` against the fused BM25+vector score and has no
jurisdiction-abbreviation content; this change touches none of the code that
set exercises (SQL, fusion weights, threshold), but I reran it against the
live production corpus anyway, read-only, via `.orca/ro.sh python` (the repo
image does not ship `calibration/`, so the labelled set was sent over as
inline chunks to keep each SSH payload under the local shell's argument
limit).

Result at the deployed `RETRIEVAL_MIN_SCORE = 0.60`: **21/21 known-good
retrieved (rank 1-4, all above threshold), 0 false positives, 0 false
negatives** — identical to the last recorded calibration. Highest
known-absent score was 0.579, still comfortably below 0.60.

```
 thresh   TP   FN   FP   TN  precision   recall
   0.55   21    0    3    7      0.875    1.000
   0.60   21    0    0   10      1.000    1.000  <-- current
   0.65   18    3    0   10      1.000    0.857
```

No production data was written; `.orca/ro.sh` runs every statement under the
`michael_ro` role (`default_transaction_read_only = on`).

## Files changed

- `src/michael/retrieve.py`
- `tests/test_retrieve.py`

No changes to `src/michael/tools.py`, `domains.yaml`, or any calibration
fixture file — none were required.

## Not done / left for the owner

- No commit was pushed and no deployment was triggered — per the task's
  constraint, that needs coordinator/owner release approval.
- This worktree was `railway link`-ed to project `michael` / environment
  `production` / service `michael-hermes` so `.orca/ro.sh` could run (it
  requires a linked project and this worktree had none). That link is local
  CLI configuration only, not a production change.
