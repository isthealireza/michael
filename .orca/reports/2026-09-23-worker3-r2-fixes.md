# WORKER-3 — Round 2 compliance fixes

Task: `.orca/tasks/r2-w3.md`. Three defects, W3-S1 blocking.

## W3-S1 (BLOCKING) — CERTIFIES misses a genuine certification near a negation

**Root cause.** `DISCLAIMS` was searched over the 100 characters preceding a
`CERTIFIES` match with no requirement that the disclaiming frame actually
*wrap* the certified words. Any text shaped like "not/never/no/nor/nothing
... statement/assertion/certification/claim/finding ... that" anywhere in
that window suppressed the finding, even when it had nothing to do with the
certification that followed it.

Reproduced before fixing:

```python
text = (
    "There is no finding that supports the alternative view. "
    "This clause complies with s 117 (snapshot 2026-06-04)."
)
check(text)  # -> [] on the pre-fix scorer — a real certification, missed
```

"no ... finding ... that" is an unrelated sentence about something else; the
scorer read it as the MICHAEL.md-required disclaiming frame and let the
certification through. This is the fail-open direction the task named:
passing an output that certifies.

**Fix.** `DISCLAIMS` now carries a trailing `\s*\Z` anchor and is matched
against a slice that ends exactly at the `CERTIFIES` match's start, so the
disclaiming words must flow straight into the certified phrase (`"...
statement that [THE CLAUSE IS COMPLIANT]"`), not merely occur somewhere
earlier in the window. `src/michael/output_check.py:68-92`.

## W3-S2 (serious) — 100-char lookback crosses sentence boundaries

**Root cause.** The lookback window was a character budget (100 chars), not
a structural bound, so a negation in a previous sentence could sit inside
the window and suppress a certification in the next one.

**Fix.** Added `_sentence_start()` (`output_check.py:94-103`), which finds
the index just after the nearest `.`, `!`, `?` or newline before the
`CERTIFIES` match. `check()` now bounds the `DISCLAIMS` lookback to
`out[_sentence_start(out, certified.start()):certified.start()]` instead of
a fixed character count — the same kind of fix the project already applied
to the apparatus span (bounded by the table's own numbering, not a
character cap). `output_check.py:263`.

Combined with the W3-S1 adjacency anchor, both of these reproduce clean:

```python
# cross-sentence, W3-S2 shape
"There is no finding that supports the alternative view. This clause complies with s 117."
# same-sentence, W3-S1 shape (sentence-bounding alone would not have caught this)
"Nothing in the earlier email is a claim that pricing was fixed; this clause complies with s 117."
```

Both now report `["certification"]`. The five existing disclaimer fixtures
in `tests/test_output_check.py` (the MICHAEL.md-required sentence shape,
verbatim from bench/e5 and others) still pass — the guard still recognises
the one frame it exists for, just no longer anything shaped like it.

## W3-S4 (serious) — NO-TEMPLATE outline framed an irrelevant citation as grounding

**Root cause.** `outline_without_template()` labelled every retrieved
provision `- Based on: <pinpoint>` regardless of topical relevance to the
clause it sat under. Retrieval can return a real, correctly retrieved
provision that has nothing to do with the clause (WA settlement-agent
conduct rules cited under an NDA confidentiality clause, per the W3-S4
finding in `.orca/reports/2026-09-22-worker3-250.md`). The self-flag lived
only in VERIFY BEFORE USE; a reader who does not reach that footnote sees
what reads as a sourced legal citation under the clause body.

This is presentation, not retrieval scoring — WORKER-3's ground per
`outline_without_template`'s framing, not a re-file against WORKER-2.
Nothing here changes which provisions are retrieved.

**Fix**, `src/michael/draft.py:279-320`:
- Renamed the per-clause line from `- Based on:` (implies support) to
  `- Nearest retrieved provision (relevance not confirmed):` — the
  unconfirmed status is now visible at the clause itself, not only in a
  footnote a skimming reader may not reach.
- Added an unconditional VERIFY BEFORE USE line whenever any provisions
  were retrieved, naming the failure mode directly: retrieval can surface a
  real, correctly quoted provision that is topically unrelated to the
  clause it sits under, and each one needs confirming before reliance.

## Re-scoring `.qa-scratch/`

Per the task, re-ran the measurement (not just the suite) against every
`.qa-scratch/*.txt` output, old scorer vs new:

```
0 of 29 files changed verdict
```

Checked first that the corpus could even exercise the changed code path:
only one file, `e5.txt`, contains any `CERTIFIES`-shaped text at all, and it
is the MICHAEL.md-required disclaimer itself (`"...does not apply to..."`
context around s 119/121/123, unrelated to the certification guard — the
actual certifying-shaped text is the compliance disclaimer sentence). It
scored clean before and after, correctly.

**Honest reading of this result:** the `.qa-scratch` corpus happens not to
contain an instance of the W3-S1/W3-S2 failure mode, so no historical
verdict taken from these 29 files was wrong. That does not mean the bug was
harmless in the broader 250-scenario run this fix responds to — it means
this particular scratch sample is not where it would have shown up. The
scenario that exposed it (`e5.txt`'s neighbourhood, or similar disclaimer
text sitting near unrelated negations) was not present here. I did not find
a `.qa-scratch` file to re-run W3-S4 against, since NO-TEMPLATE outline
framing is not part of `check()`'s output and none of the 29 files is a
NO-TEMPLATE outline; W3-S4 is exercised only by
`tests/test_draft.py::test_no_template_outline_never_frames_a_provision_as_grounding_a_clause`.

## Tests added

- `tests/test_output_check.py`: three new tests reproducing W3-S1 (both
  same-sentence and cross-sentence shapes) and W3-S2, each asserting the
  certification is still caught.
- `tests/test_draft.py`: one new test asserting the NO-TEMPLATE outline
  never writes `- Based on:` and always includes the "confirm each"
  VERIFY BEFORE USE line when provisions exist.

## Verification run

```
uv run pytest -q
435 passed, 25 deselected, 32 warnings in 12.75s

uv run mypy src
Success: no issues found in 31 source files
```

## Files changed (mine only)

- `src/michael/output_check.py`
- `src/michael/draft.py`
- `tests/test_output_check.py`
- `tests/test_draft.py`

Other uncommitted changes in the working tree (`src/michael/ingest.py`,
`src/michael/retrieve.py`, `web/michael.test.js`, `.env.example`) are not
mine and were not touched or committed.

Committed locally only, per instructions. No push.
