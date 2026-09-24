# Proposed README changes — Phase 1 (threshold recalibration)

Coordinator merges these centrally. Two edits, both in `README.md`.

The headline: **`RETRIEVAL_MIN_SCORE = 0.60` is not supported by measurement
against the current corpus, and no value in the sweep is.** The README
currently presents 0.60 as calibrated with precision and recall 1.000. Against
the 2026-09-24 corpus and an 81-query labelled set that is false, and the table
it prints is from a corpus that no longer exists. Leaving it as written is the
worst option: it reads as a verified number.

Measured twice against production, read-only: once while `corpus_stats` was
stale (8,982 provisions against an actual 8,915) and again after the coordinator
refreshed it (8,915 / avg_tokens 103.7493). **The refresh changed nothing
material** — max movement on any fused score was 0.00025. The figures below are
the post-refresh run.

---

## Edit 1 — replace the whole of "### Calibrating the threshold" (README line 81)

Replace from `### Calibrating the threshold` down to (and including) the
paragraph ending `...two Fair Work queries were retired from it once the Act was
ingested, because they stopped being absent.` with:

````markdown
### Calibrating the threshold

`RETRIEVAL_MIN_SCORE = 0.60` is **not currently supported by measurement.** It
was calibrated against a 200-document WA-only corpus with 15 known-good and 10
known-absent queries. Re-measured on 2026-09-24 against the production corpus
(205 documents, 8,915 provisions, including the Privacy Act 1988 (Cth), with
`corpus_stats` freshly refreshed) using an expanded labelled set of 48
known-good and 33 known-absent queries, it admits **11 false positives out of
33** and retrieves only 30 of 48 known-good targets.

The labelled set is `calibration/labelled_queries.json`. Queries are paraphrases
rather than verbatim provision text, so the test is not trivially lexical.
Reproduce with:

```bash
uv run python calibration/calibrate.py
```

Scoring runs **unfiltered**, with no domain filter. A jurisdiction filter would
reject most known-absent queries before scoring and flatter the result;
unfiltered, the threshold alone has to do the work.

| threshold | TP | FN | FP | TN | precision | recall |
|---|---|---|---|---|---|---|
| 0.30 | 38 | 10 | 33 | 0 | 0.535 | 0.792 |
| 0.35 | 38 | 10 | 32 | 1 | 0.543 | 0.792 |
| 0.40 | 38 | 10 | 32 | 1 | 0.543 | 0.792 |
| 0.45 | 37 | 11 | 31 | 2 | 0.544 | 0.771 |
| 0.50 | 37 | 11 | 26 | 7 | 0.587 | 0.771 |
| 0.55 | 33 | 15 | 20 | 13 | 0.623 | 0.688 |
| 0.60 | 30 | 18 | 11 | 22 | 0.732 | 0.625 |
| 0.65 | 23 | 25 | 2 | 31 | 0.920 | 0.479 |
| 0.70 | 13 | 35 | 0 | 33 | 1.000 | 0.271 |
| 0.75 | 9 | 39 | 0 | 33 | 1.000 | 0.188 |
| 0.80 | 1 | 47 | 0 | 33 | 1.000 | 0.021 |

**No threshold separates the two sets.** The lowest threshold with zero false
positives is 0.70, and there recall is 0.271 — 13 of 48 known-good queries
survive. The highest known-absent score is 0.6669 ("what privacy obligations
apply to Queensland government agencies handling personal information", which
returns APP 9). 29 of 48 known-good targets — 60% — score at or below that,
including 10 that never enter the top 10 at all.

Recall of 0.9 is unreachable at **any** threshold, including no threshold: with
the cutoff disabled entirely, only 38 of 48 targets rank in the top 10, a
ceiling of 0.792. That is a retrieval-quality limit, not a threshold choice, so
no value of `RETRIEVAL_MIN_SCORE` fixes it.

Accordingly **no new value has been adopted**, and `.env` is unchanged at 0.60.
Changing the number cannot help: at 0.60 the search answers 11 questions the
corpus cannot answer, and lifting it to 0.70 to stop that discards three
quarters of the questions it can. Both failures are the same defect — the fused
score does not order "covered" above "not covered" on this corpus. See the open
decisions below.

Re-derive after any change to the corpus, the embedding model, the splitter or
the fusion weights; none of those preserve this scale. The known-absent set also
has to be re-checked whenever the corpus grows: two Fair Work queries were
retired from it once that Act was ingested, because they stopped being absent.
On the 2026-09-24 re-check **no query was retired** — all ten pre-existing
known-absent queries remain genuinely uncovered.
````

---

## Edit 2 — replace the "**The calibrated threshold trails the corpus.**" paragraph (README line ~160)

That paragraph says the threshold "has not been re-derived since the Privacy Act
1988 (Cth) was ingested". It has been now, and the answer was negative. Replace
the whole block (from `**The calibrated threshold trails the corpus.**` through
`An earlier value of 0.65, derived against a WA-only corpus, is superseded.`)
with:

````markdown
**The threshold does not separate covered from uncovered questions.** Measured
2026-09-24 against the production corpus with 48 known-good and 33 known-absent
queries: the lowest threshold with zero false positives is 0.70, at which recall
is 0.271. The configured 0.60 admits 11 false positives out of 33. See
"Calibrating the threshold" above for the full table.

This is the retrieval quality limit, not a tuning problem, and it is the
blocking issue for the NOT COVERED guarantee: MICHAEL.md promises that an empty
result means "the corpus cannot answer this", and on the current corpus a
0.60 cutoff breaks that promise on a third of questions built to be uncoverable.
An earlier value of 0.65, derived against a WA-only corpus, is superseded and is
no better — it still admits 2 false positives at recall 0.479.

The leading suspect is **the Privacy Act's internal near-duplication.** Part
IIIA (credit reporting, ss 20A–22F) restates access, correction, quality,
security and notification for credit information, in language close to the
Australian Privacy Principles in Schedule 1. Plain-English APP questions return
the credit-reporting provisions instead: "can an individual demand a copy of
what a company holds about them" returns ss 20T, 21V and 20B, and never APP 12.
Eight of the ten known-good targets that never rank are Privacy Act APP or Part
IIIC provisions.

**Ruled out:** stale corpus statistics. `corpus_stats` had drifted to 8,982
provisions against an actual 8,915 after a note-merge deleted 67 rows without
refreshing, and BM25 reads N and avgdl from that row. It was refreshed on
2026-09-24 and the whole labelled set re-scored. Maximum movement on any fused
score was 0.00025; every headline figure was identical. Worth knowing, because
"the statistics were stale" is the obvious explanation and it is not the answer.
````

---

## Not changed

`RETRIEVAL_MIN_SCORE` in `.env` is left at 0.60. Adopting 0.70 would have been a
measured value with zero false positives, but the brief forbids picking a value
when recall at that threshold is below 0.9, and 0.271 is not close. Changing the
number without fixing the ordering would trade one failure for a worse one.
