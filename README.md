# Michael

An internal legal research and drafting assistant for a single user. Not
published, not multi-tenant. Optimised for correctness and traceability, not
for scale.

**Michael is not a lawyer and does not give legal advice.** Every output ends
with OPEN ITEMS, VERIFY BEFORE USE, and a notice that the work requires review
by an admitted Australian legal practitioner.

## Architecture

Hermes is the brain and the only orchestrator. It receives the scenario, plans,
calls tools, and writes the answer. Everything in `src/michael/` is a tool
Hermes calls. There is no second agent framework here.

```
Hermes ──▶ classify_request ──▶ search_provisions ──▶ draft_document
             (domains.yaml)      (BM25 + vector,       (templates/,
                                  read-only)            [MISSING] rule)
                                                             │
                                                     in code, every draft
                                                             ▼
                                                        verify_draft
                                                   (judge in another model
                                                    family, fails closed)
```

`verify_draft` is not a tool. It is called by `draft_document` in code, on every
draft, against the provisions that same call retrieved. The agent's MCP profile
still grants exactly `classify_request`, `search_provisions`, `draft_document`
and `validate_output`, so there is no way for the agent to ask for an unverified
draft: the argument that injects a judge exists for tests and is absent from the
tool schema.

Ingestion is a separate tool group with its own database role. The answering
path connects as `michael_ro`, which holds SELECT only and is created with
`default_transaction_read_only = on`, so it cannot write even by mistake.

## Setup

```bash
cp .env.example .env    # then fill it in; .env is git-ignored
docker compose up -d
uv sync --all-groups
uv run michael schema
uv run michael migrate     # apply_schema() cannot alter an existing table
```

Seed the corpus (needs the `corpus` extra):

```bash
uv sync --extra corpus
uv run michael seed --limit 500
```

Fill a gap from an allowlisted host:

```bash
uv run michael ingest https://www.legislation.gov.au/... \
  --jurisdiction commonwealth --doc-type act \
  --title "Fair Work Act 2009" --citation "Fair Work Act 2009 (Cth)"
```

Only `legislation.wa.gov.au`, `legislation.gov.au`, `fairwork.gov.au`,
`austlii.edu.au` and `immigration.homeaffairs.gov.au` (and their subdomains)
are fetchable. Every other host is refused and logged. There is no
configuration key that widens this.

`immigration.homeaffairs.gov.au` publishes departmental guidance, not law, and
is bound to `--doc-type guidance` in both directions: a Home Affairs page
ingested as `act` or `regulation` is refused, and so is `guidance` claimed for
any other host. The parent `homeaffairs.gov.au` is not on the list. See
"Departmental guidance is never cited as legislation" below.

## Migrations

`apply_schema()` is `CREATE TABLE IF NOT EXISTS` throughout: it builds a
database that does not exist yet and by design never alters one that does. It
therefore cannot add a column to a table already holding the corpus.
`db/migrations/` and `src/michael/migrations.py` are the other half.

```bash
uv run michael migrate --status        # what is on disk and what has run
uv run michael migrate                 # apply everything not yet recorded
uv run michael migrate --rollback 0001 # reverse one, by id
```

A migration is a pair of files, `<0000>_<name>.up.sql` and
`<0000>_<name>.down.sql`. **A migration with no `.down.sql` does not load**,
so an irreversible one cannot be added by forgetting to write the reverse.
Each runs in one transaction, and `schema_migrations` records the id, name and
the sha256 of the forward SQL that was applied — a file edited after it ran is
reported as *drifted* rather than silently re-run or silently ignored.

Applying is guarded twice: by the ledger, and by the SQL itself being written
`IF NOT EXISTS` / `IF EXISTS`, so a database whose ledger was lost converges
instead of failing. `tests/test_migrations.py` tests that it applies, that it
rolls back, that applying twice is safe, and that applying twice is *still*
safe with the ledger row deleted.

Rollback is by id, never "the last one": reversing whatever happens to be
newest is how the wrong migration gets undone on a machine nobody checked
first. `0001`'s rollback drops the column, which discards which rows were
paragraphs, orders or whole documents; that is re-derivable only by
re-ingesting, and the `.down.sql` says so rather than pretending otherwise.

`0002_guidance_doc_type` widens the `documents.doc_type` CHECK to admit
`guidance`. Its rollback does **not** delete guidance documents to make the
narrower constraint fit: with any guidance row present the rollback fails and
its transaction leaves the database as it was. Removing guidance is a decision
about the corpus, taken by deleting those documents first.

An existing database needs the migrations; `uv run michael schema` alone is no
longer enough. The integration suite applies them in its own fixtures.

## Using it

```bash
uv run michael classify "I need a casual employment contract for a new hire"
uv run michael search "casual employee entitlements"
uv run michael draft "casual employment contract" --fact EMPLOYER_NAME="Palm Vision Pty Ltd"
```

To attach it to an agent runtime over MCP:

```bash
uv sync --extra mcp
uv run python -m michael.mcp_server
```

## Retrieval

Hybrid: BM25 over the Postgres `tsvector` (one tokeniser and one stemmer end to
end) fused with pgvector cosine similarity, each squashed to `[0, 1)` and
weighted 0.5/0.5. The threshold is therefore absolute and meaningful.

If the best fused score is below `RETRIEVAL_MIN_SCORE` the result is **empty**.
The nearest guess is never returned. An empty result travels as a value —
`covered: false` plus a `NOT COVERED — run ingestion for <topic>` line — so it
is reported, not silently dropped.

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

#### Adding case law: the delta, measured LOCALLY

Measured 2026-09-24 against the **local** corpus (205 legislation documents
and 6 judgments), not production. The labelled set gained 8 known-good and 6
known-absent case-law queries, so `calibration/labelled_queries.json` now
holds 56 known-good and 39 known-absent. Case-law entries carry `kind: case`
and, on the known-good side, the `unit` the target is stored under.
`calibration/calibrate.py` takes `--kind {all,legislation,case}` and
`--corpus {all,legislation}`; `--corpus legislation` reproduces the corpus as
it was before case law was ingested, without removing anything from it.

| run | zero-FP threshold | recall there | highest known-absent | top-10 ceiling |
|---|---|---|---|---|
| A — legislation queries, case law excluded | 0.70 | 0.250 (12/48) | 0.667 | 0.771 (37/48) |
| B — legislation queries, case law present | 0.70 | 0.250 (12/48) | 0.667 | 0.771 (37/48) |
| C — all queries, case law present | 0.80 | 0.036 (2/56) | 0.761 | 0.804 (45/56) |
| D — case-law queries only | 0.80 | 0.125 (1/8) | 0.761 | 1.000 (8/8) |

**A and B are identical at every threshold, row for row.** Adding 6 judgments
and 258 units to the corpus changes legislation retrieval not at all. Case law
does not crowd legislation out.

**B to C makes the overlap worse and the ceiling better.** The ceiling rises
from 0.771 to 0.804 because all 8 case-law targets rank in the top 10, 7 of
them at rank 1. But the highest known-absent score rises from 0.667 to 0.761,
so the lowest zero-false-positive threshold moves from 0.70 to 0.80 and recall
there collapses from 0.250 to 0.036.

**The new worst false positive is not case law.** The 0.761 is
`Fair Work Act 2009 (Cth) s 536H` returned for "the implied freedom of
political communication as stated in Lange v Australian Broadcasting
Corporation". The second worst, 0.669, is `Settlement Agents Code of Conduct
2016 (WA) s 13` returned for a question about Amadio. Both are *legislation*
rows returned for questions about judgments the corpus does not hold. Adding
case-law questions did not introduce a case-law failure; it exposed the same
pre-existing defect the production table already records — the fused score
does not order "covered" above "not covered" — on a class of question nobody
had asked before.

**Caveat on run D.** Case-law recall of 1.000 down to threshold 0.55 is not
evidence that case-law retrieval scales. The local corpus holds six judgments;
a case-law query has almost nothing to be confused by. Re-measure before
reading anything into it.

No threshold change is proposed. `RETRIEVAL_MIN_SCORE` stays at 0.60 and the
production table above remains the reference measurement.

#### Adding migration law: the delta, measured LOCALLY

Measured 2026-09-25 against the **local** corpus after the Migration
Regulations 1994 and the Migration Act 1958 were ingested (216 documents,
12,789 provisions, `corpus_stats` refreshed by the ingest). **Not deployed to
Railway; production does not have any of this.** The labelled set gained 5
known-good and 3 known-absent migration queries (`"domain": "migration"`) and
now holds 61 known-good and 42 known-absent.

| threshold | TP | FN | FP | TN | precision | recall |
|---|---|---|---|---|---|---|
| 0.55 | 45 | 16 | 29 | 13 | 0.608 | 0.738 |
| 0.60 | 40 | 21 | 19 | 23 | 0.678 | 0.656 |
| 0.65 | 35 | 26 | 6 | 36 | 0.854 | 0.574 |
| 0.70 | 19 | 42 | 1 | 41 | 0.950 | 0.311 |
| 0.75 | 12 | 49 | 1 | 41 | 0.923 | 0.197 |
| 0.80 | 1 | 60 | 0 | 42 | 1.000 | 0.016 |

**The lowest zero-false-positive threshold stays at 0.80**, where recall is
0.016 (1 of 61). The highest known-absent score is 0.764 and is not a
migration query: it is the pre-existing Lange question returning Fair Work Act
s 536H, 0.761 in run C above. The lowest known-good target retained at 0.80
scores 0.8262, so the **margin** - that score minus the highest known-absent,
as the 2026-09-24 threshold report defines it - is **0.0627**.
`calibrate.py` now prints that figure. The top-10 ceiling rose from 45/56 to
50/61: all five new targets rank, and no existing target was crowded out.

Recall at 0.80 fell from run C's 2/56 to 1/61 because `AGV20 ... at [5]` moved
from just above 0.80 to 0.799. Adding 3,309 provisions changes BM25's N and
avgdl. That is a boundary tie, not a regression, and it is the same kind of
movement the 2026-09-24 statistics refresh produced.

The migration queries alone do not separate either:

| query | kind | score | top result |
|---|---|---|---|
| standard business sponsor approval | good | 0.733 (rank 1) | Regs 2.59 |
| sponsor pays a worker's travel home | good | 0.538 (rank 2) | Regs 2.80 |
| visa refused or cancelled on character | good | 0.688 (rank 5) | Act s 501 |
| detention without a valid visa | good | 0.651 (rank 7) | Act s 189 |
| visas still open after an onshore refusal | good | 0.661 (rank 1) | Act s 48 |
| NZ resident visa under the Immigration Act 2009 | absent | 0.679 | Act s 294 |
| residence before citizenship by conferral | absent | 0.668 | Regs Sch 2 cl 155.211 |
| partner visa processing times | absent | 0.600 | Regs Sch 1 cl 1214C |

At the configured 0.60 the migration set keeps 4 of 5 targets and answers 2
of the 3 questions it cannot answer. The third, processing times, escapes by
0.00002 (0.59998): a tie-break, not a pass. The NZ question is now the second-worst false
positive in the whole set. It is the same defect the production table
records, on a new domain: a large body of visa law makes any visa-shaped
question look covered. The known-absent re-check retired nothing. Two
pre-existing known-absent queries now have a Migration provision as their top
hit (Briginshaw 0.645, Act s 486X; Northern Territory sentencing 0.581, Regs
Sch 8 cl 8528), and neither crosses 0.70.

No threshold change is proposed. `RETRIEVAL_MIN_SCORE` stays at 0.60.

#### Score separation: rerankers and per-domain thresholds, measured LOCALLY

Measured 2026-09-25 on the same local corpus and the same 61 known-good / 42
known-absent set, to answer whether anything orders covered above not-covered
where the fused score does not. `calibration/separation.py` retrieves every
labelled query once and caches its top-20 fused shortlist
(`calibration/separation_probes.json`), so every option is scored on exactly
the same candidates. Reranker scores are in `calibration/separation_scores/`.

Each option is summarised at its **exact** lowest zero-false-positive
threshold, not the 0.05 grid `calibrate.py` sweeps. That is why the baseline
reads 0.7635 with recall 10/61 here and 0.80 with 1/61 there: the same
measurement, read at finer resolution. **Leave-one-out** (LOO) refits the
rule without each query and scores that query. It is the honest number,
because production only ever sees questions the threshold was not fitted to.
Routed retrieval (domain filter from `classify_request`) and unfiltered
retrieval gave identical results for every option.

| option | zero-FP threshold | recall there | margin | LOO FP / 42 | LOO recall | top-10 ceiling | latency / query (median, CPU) |
|---|---|---|---|---|---|---|---|
| fused score (today) | 0.7635 | 0.164 (10/61) | 0.0029 | 1 | 0.164 | 50/61 | - |
| per-domain thresholds | fitted per domain | 0.443 (27/61) in-sample | 0.0009 (worst domain) | **8** | 0.443 | 50/61 | - |
| **MiniLM-L-6-v2 cross-encoder** | 0.9972 (logit 5.874) | **0.361 (22/61)** | 0.0000 (0.005 logits) | 1 | 0.361 | **56/61** | **1.6 s** |
| bge-reranker-base | 0.9894 (logit 4.534) | 0.311 (19/61) | 0.0009 (0.086 logits) | 1 | 0.311 | 55/61 | 10.4 s |
| Laya (English), refit T | migration subset only; see below | | | | | | ~54 s |
| Jev | not measured: not served on OpenRouter | | | | | | |

Rerankers score the top-20 fused shortlist. Load times, paid once: MiniLM
32 s, bge 133 s, Laya 42 s. No option has an API cost; all three run on local
CPU (6 threads). A single global threshold, whatever the score, always shows
exactly 1 LOO false positive: the held-out worst known-absent query.

**Per-domain thresholds do not generalise.** In-sample they look best: zero
false positives at recall 0.443. Under leave-one-out, 8 of the 42 known-absent
queries become false positives, one in each of 8 of the 9 routing outcomes
(every one but `contracts`). Each
domain's threshold is set by exactly one query, and with 1 to 4 known-absent
queries per domain the next unseen question lands above it. Adding a 0.01
safety offset in-sample already drops recall to 0.361. Routing also misfiles
questions: "does an employer who sponsored a worker have to pay for their
flight home" goes to `employment`, and the citizenship question matches no
domain.

**MiniLM beats bge.** It has higher recall, a higher ceiling and one sixth of
the latency. Reranking lifts the top-10 ceiling from 50 to 56 of 61. That is
a real ranking improvement, independent of any threshold.

**Laya was measured on the 8 migration queries only.** The full 103-query
run was stopped at 50: the queries run in labelled-set order, so the first
half held no known-absent queries and could not inform a zero-FP threshold.
At about 54 s per query it is ruled out on latency alone. On the subset:

| migration subset (5 good / 3 absent) | zero-FP recall | margin (prob / logit) | top-10 ceiling |
|---|---|---|---|
| fused score | 0.40 | 0.0087 / - | 4/5 |
| Laya, T refit 0.9225 | 0.40 | 0.0485 / 0.718 | 4/5 |
| Laya, shipped T 1.9834 | 0.40 | 0.0597 / 0.334 | 4/5 |
| MiniLM | 0.60 | 0.0001 / 0.048 | 5/5 |
| bge | 0.20 | 0.0102 / 3.194 | 5/5 |

The temperature was refit by maximum likelihood on 5 positive and 60 negative
labelled pairs, to 0.92 against the shipped 1.98. A single temperature is
monotonic: it moved the margin and the numeric threshold, and could not move
recall. Laya's worst known-absent is the processing-times question (0.90),
which both cross-encoders reject (0.38, 0.43).

**None of them fixes the migration false positives by separation.** The
citizenship-by-conferral and New Zealand visa questions score 0.997 and 0.985
under MiniLM and 0.989 and 0.982 under bge, as high as real answers.
MiniLM's zero-FP threshold is set by the citizenship question itself, with a
margin of 0.005 logits. A reranker reads "about visas" as relevant. It cannot
see that the answer lives in an Act the corpus does not hold (the Australian
Citizenship Act 2007) or in another country's law.

**Recommendation, not adopted.** MiniLM over the top-20 fused shortlist is
the better of the two remedies measured. It more than doubles zero-FP recall,
from 0.164 to 0.361, holds up under leave-one-out, and costs 1.6 s a query.
The runner-up, bge, is worse on every measure. It should not yet set
`RETRIEVAL_MIN_SCORE` or any reranker threshold:

- the zero-FP point rests on one query, with no margin;
- 42 known-absent queries are too few to fix a threshold at 0.997;
- AGENTS.md makes TypeSafe the default for AI features, and MiniLM is not
  TypeSafe. Building it in is a decision against that default, or it waits
  for a Jev measurement with a TypeSafe key.

`RETRIEVAL_MIN_SCORE` is unchanged at 0.60.

Reproduce:

```bash
uv run python calibration/separation.py probe
uv run python calibration/separation.py domain
uv run python calibration/separation.py pairs pairs.json
<python-with-torch> calibration/rerank_local.py pairs.json calibration/separation_scores/minilm.json --model minilm
uv run python calibration/separation.py rerank calibration/separation_scores/minilm.json calibration/separation_scores/bge.json
```

### Migration law: what was ingested

Latest compilations as at 2026-09-25, from the Federal Register's Word
originals, fetched per volume from the dated API document URL. The
`/text/original/word` page URL the Privacy Act used returns 404 for a
multi-volume compilation, and `asatspecification='Latest'` resolves to
different bytes over time. The dated key pins the snapshot:

```bash
uv run michael ingest "https://api.prod.legislation.gov.au/v1/documents(titleid='F1996B03551',start=2026-07-01T00:00:00,retrospectivestart=2026-07-01T00:00:00,rectificationversionnumber=0,type='Primary',uniqueTypeNumber=0,volumeNumber=1,format='Word')" \
  --jurisdiction commonwealth --doc-type regulation --snapshot-date 2026-07-01 \
  --title "Migration Regulations 1994" --citation "Migration Regulations 1994 (Cth)"
```

| instrument | compilation | volumes ingested | provisions |
|---|---|---|---|
| Migration Regulations 1994 (Cth) | No. 288, 1 July 2026 (F2026C00667) | 1, 2, 3 | **2,353** (479 + 1,321 + 553) |
| Migration Act 1958 (Cth) | No. 171, 4 June 2026 (C2026C00232) | 1, 2 | **956** (588 + 368) |

Regulations volume 4 is the endnotes alone, which hold no law, so it was not
ingested. Split, it produces one 2,476-character cover-page row. No
duplicate pinpoints in either instrument, and every row is embedded.

**Part 2A of the Regulations has operative text:** 82 regulations, 2.57 to
2.99, 237,223 characters. 64 of them carry numbered subregulations, and none
is heading-only. For example, 2.59 opens with the criterion itself ("For
subsection 140E(1) of the Act, the criterion that must be satisfied...").

**Neither instrument could be ingested by the splitter as it was.** Measured
on these exact files before the changes below:

* **Regulations.** `SECTION_RE` has no form for a Part-numbered regulation
  ("2.57 Interpretation"). Its number stops at the dot and then demands
  whitespace. Volume 1 split into 74 rows, none of them from Parts 1 to 5, so
  **Part 2A was absent from the corpus altogether**. Volumes 2 and 4 each came
  out as a single `(whole document)` row of about 780,000 characters.
  `detect_headings_only` passed all of it. `DOTTED_SECTION_RE` now accepts
  the Part number for doc_type `regulation` only. An award numbers its
  subclauses "10.1", so awards, Acts, cases and guidance keep `SECTION_RE`.
* **Schedule 2's shape.** A clause number stands alone on its line
  ("010.211"), and the Division and Subdivision headings above it
  ("010.21—Criteria to be satisfied...") carry the heading text. Taken naively,
  the headings became clauses: "Sch 2 cl 010.21" does not exist, and it held
  the real clause 010.211 inside it. A bare `NNN.NNN` line is now a clause
  with an empty heading. An `NNN.N` or `NNN.NN` heading is not a provision and
  stays in the text of the clause before it, as a Part heading of an Act does.
* **Short clauses were discarded.** "417.611 / Conditions 8547 and 8548." is
  33 characters, under `MIN_PROVISION_CHARS`, and its text was stored in no
  provision at all (8 clauses in volume 2). The floor no longer applies to a
  heading-less clause that has text.
* **Quoted clauses.** Sch 13 cl 9903 directs that Division 132.3 be read "as if
  ... replaced with the following" and sets out clauses 132.311 to 132.314.
  They were being cited as "Sch 13 cl 132.311". A Schedule 2 clause number
  outside Schedule 2 is now left inside the clause that quotes it.
* **Act: every section was labelled a Schedule clause.** The cover page of a
  multi-volume compilation lists what each volume holds ("Volume 2: /
  sections 262-507 / Schedule / Endnotes"). `_opening_schedule` took that
  bare "Schedule" line for the real heading, so s 1 to s 507 of the
  Migration Act came out as "Sch 1 cl 1" to "Sch 1 cl 507". It did the same
  to the Regulations' own regs 1.01 to 5.45. `VOLUME_LISTING` bounds the
  listing by the compilation's fixed wording at both ends, and neither
  `_opening_schedule` nor `schedule_spans` reads a Schedule heading inside
  it.

**No stored document is affected.** All 50 originals under `sources/` were
re-split with the old and the new code. Under the Act pattern, none changes.
Under the regulation pattern, two WA originals would split differently if
re-ingested as regulations. Neither is in the database, and the larger change
turns a retail-lease Schedule's "1.1 Terms used in this clause" into its own
clause.

**A Part-numbered pinpoint is looked up whole.** `SECTION_REFERENCE` and
`SCHEDULE_REFERENCE` stopped at the dot, so pasting back the pinpoint Michael
renders, "Migration Regulations 1994 (Cth) s 2.59", looked up "2": every
section 2 in the corpus, returned as an exact identifier match. Both now
accept "2.59", "2.57A" and "Sch 2 cl 010.211".

**The `migration` domain** (`domains.yaml`) filters to `commonwealth` and to
`[act, regulation, case, guidance]`. Its keywords avoid bare "nomination" and
"sponsor" so that it does not take corporate or employment requests.
`tests/test_domains.py` pins both, and pins that no other domain reaches
guidance.

### Departmental guidance is never cited as legislation

`guidance` is a document type for how a department says it applies the law.
Pages from `immigration.homeaffairs.gov.au` are guidance and nothing else, and
nothing else is guidance (`sources.check_doc_type`, enforced in `ingest_url`,
`ingest_file` and again in `ingest_document`, which every path passes
through). Four controls stop a guidance page from being taken for law, and
none of them depends on the model:

| where | what happens to guidance |
|---|---|
| splitting | stored as one `(whole document)` row. Its numbered steps ("1. Check you are eligible") are never taken for sections |
| citation | `pinpoint()` renders `(departmental guidance, not legislation)` in place of any section, paragraph or whole-document locator, decided by `doc_type` before `unit_type`, so it reaches BASED ON, outlines, the tool payload and the judge's prompt with no separate step |
| drafting | VERIFY BEFORE USE names every guidance page relied on, and says it is not the law. An outline introduces it as "guidance", not "authority" |
| verification | judged as `kind="guidance"`, never as legislation or as a judgment. The judge's rule for it is appended only when guidance is in the evidence, so every prompt without guidance is byte-for-byte unchanged and the measured judge figures still apply. In code, a claim that **only** guidance carries is never SUPPORTED: it is marked PARTIAL, with the reason "supported only by departmental guidance, which is not legislation" |

No guidance has been ingested yet. Phase 1 adds the host and the type only.

### Case law is split and cited as case law

Judgments do not go through `split_sections()`. `ingest_document()` dispatches
on `doc_type`: `case` is split by `split_judgment()`, everything else by
`split_sections()`. A judgment is cut into the units a court is actually cited
by, and `provisions.unit_type` records which kind each row is:

| `unit_type` | what it is | pinpoint |
|---|---|---|
| `section` | a section or Schedule clause of an Act, Regulation or Award | `s 15A`, `Sch 1 cl 3` |
| `paragraph` | a numbered paragraph of a court's reasons | `at [12]` (AGLC) |
| `order` | the orders and declarations the court made, as one block | `(orders)` |
| `document` | a document with no internal numbering | `(whole document)` |

So `Muir v Open Brethren [1956] HCA 14 s 2` is now
`Muir v Open Brethren [1956] HCA 14 (whole document)`, and paragraph 6 of
Cromwell Corporation v ARA Real Estate is
`... [2020] FCA 1492 at [6] (snapshot 2020-10-16)`.

Case law can now be seeded:

```bash
uv run michael seed --limit 200 --doc-type case
```

**Three things the splitter refuses to guess.**

*A judgment with no paragraph numbers is reported, never numbered.* Older
reports — Muir v Open Brethren [1956] HCA 14, United Firefighters' Union v
Metropolitan Fire Brigades Board [1998] FCA 1119 — are continuous prose whose
certificate counts *pages*, not paragraphs. They are stored as one
`(whole document)` row and `seed` reports it:

```json
{"documents": 6, "created": 6, "provisions": 258,
 "notes": [{"citation": "Muir v Open Brethren [1956] HCA 14",
            "note": "no numbered paragraphs found; stored as one (whole document) provision and cited without a pinpoint"}]}
```

Measured on the first 60 case records of the corpus: 13 of 60 carry no
paragraph numbering at all.

*Quoted matter is not a unit of the quoting judgment.* A judgment indents
what it quotes, and only lines at the same indent as the judgment's own
paragraph 1 are paragraph starts; whatever survives that must still count
upwards. Both filters can only decline to start a new provision, so neither
can lose text: the quotation stays inside the paragraph that quotes it.

*Orders are not given paragraph numbers.* See "Restarted numbering" below.

**Accuracy.** A Federal Court report states its own paragraph count
("Number of paragraphs: 145", or the associate's certificate). Over the first
60 case records of the Open Australian Legal Corpus in scope: 45 agree exactly
with the declared count, 13 carry no numbering and are reported as such, 1 has
no declared count, and 1 "disagrees" — Lu v Minister for Immigration &
Multicultural Affairs [2000] FCA 178, where the splitter finds 19 and the
first certificate says 18, because that report certifies each judge's reasons
separately and 19 = Kiefel J's paragraph 1 + the other members' 18. The
splitter is right and the check is coarse.

### Restarted numbering: a judgment's orders are not paragraph 1

A judgment numbers its orders from 1 and its reasons then restart from 1, so
"paragraph 1" names two different pieces of text in one document. Both
multi-paragraph judgments in the local seed do this: Cromwell Corporation v
ARA Real Estate [2020] FCA 1492 runs orders 1-2 then reasons 1-145, and AGV20
v Minister [2023] FCA 1430 runs orders 1-4 then reasons 1-8. Under the section
splitter each produced two rows for "1" in the same document, rendering the
same pinpoint.

The orders are stored as **one** provision numbered `(orders)`. They are not
renumbered, and they are not dropped — the operative disposition stays in the
corpus, with its own internal numbering inside its own text.

The runner-up was to number them separately, `order 1` / `order 2`. It was
rejected on measurement: a report can carry more than one such block, and ACCC
v George Weston Foods [2003] FCA 601 opens "THE COURT DECLARES THAT: 1." and
then "THE COURT ORDERS THAT: 1.", so `order 1` collides with itself. AGLC has
no pinpoint form for an order, and inventing one to render a number the court
did not use for citation is the error this phase exists to remove.

Measured after re-ingesting the 6 seeded judgments: **258 case-law rows, 258
distinct pinpoints, 0 duplicate groups.** (The 181 duplicate groups in the
local corpus as a whole are all legislation, and are the pre-existing item
recorded in `.orca/PRODUCTION-READY.md` E1.)

The two rows this section used to print as evidence of the defect are now
regression tests. `section_number = 2 / heading = "(1842) 5 Beav., at p. 303
[49 E.R., at p. 594]."` is a numbered *footnote* of Muir; a footnote is not a
unit of a judgment and is never given a pinpoint
(`tests/test_ingest.py::test_a_numbered_footnote_never_becomes_a_citable_unit`).
`section_number = 6 / heading = "The Tang respondents are:"` is paragraph 6 of
Cromwell, and now renders `... at [6]`
(`tests/test_retrieve.py::test_the_readme_row_6_renders_as_an_aglc_paragraph_pinpoint`).

## Verification

`draft.citations_of()` guarantees the citation list: every pinpoint under
BASED ON came from a row retrieval returned. Nothing guaranteed the prose. A
clause could state a four-week notice period, cite s 117 correctly, and say
something s 117 does not say. `src/michael/verify.py` closes that in three
steps, two of which are code.

1. **Split** (code). The draft is cut into atomic claims. Headings, rules,
   label lines, the execution block, the generated outline scaffolding, the
   BASED ON list and the closing blocks are not claims. Neither is a sentence
   whose operative value is `[MISSING: ...]` — it is already in front of the
   practitioner under OPEN ITEMS, and the VERIFICATION section reports how
   many were set aside for that reason.
2. **Judge** (one model call). The claims and the verbatim text of the
   retrieved provisions go to a judge, and nothing else does — no web, no
   second retrieval, no model memory. The judge must be from a different
   model family from the drafting model; `check_judge_model` refuses one that
   is not, because a model does not reliably audit its own output.
3. **Validate** (code). A claim the judge did not answer, answered with a word
   that is not one of the three verdicts, or supported by a provision id
   retrieval never returned, is UNSUPPORTED. So is every claim in a draft
   whose judge timed out, was unreachable, or returned something that is not
   JSON. There is no path that turns a failure into a SUPPORTED claim.

Flagged claims are marked inline — `[UNSUPPORTED: c7]`, `[PARTIAL: c12]` —
and listed under OPEN ITEMS with the judge's reason. Nothing is deleted and
nothing is rewritten: annotation is insertion at character offsets recorded
during the split. A VERIFICATION section reports the counts, the judge model,
and that verification is automated and does not replace practitioner review.

Configure with `VERIFY_JUDGE_MODEL`, `VERIFY_JUDGE_TIMEOUT_SECONDS` and
`VERIFY_PROVISION_MAX_CHARS`; see `.env.example`.

`calibration/verify_labelled.json` holds 26 drafts built from real corpus
provisions, 93 labelled claims, 40 of them injected errors across five kinds
(wrong number, wrong party obligation, invented exception, overstated scope,
claim with no source). The provisions are re-extracted from the files
ingestion downloaded, through the same `extract_text` and `split_sections`
the corpus was built with, so the text a judge sees is the text retrieval
returns.

    uv run python calibration/build_verify_labelled.py   # rebuild the set
    uv run python calibration/score_verify.py            # measure a judge

The builder refuses to write a labelled claim that `split_claims` does not
produce, and `tests/test_verify.py` asserts the same thing, so the set cannot
drift away from the splitter unnoticed.

## Drafting

Templates are markdown in `templates/` with `{{PLACEHOLDER}}` markers. The
[MISSING] rule is enforced in code, not left to the model: `fill()` substitutes
only values that were passed in, and everything else becomes
`[MISSING: <item>]`. No code path produces a party name, ABN, address, date,
pay rate, award name, classification level or superannuation fund from
anything other than the caller's facts.

If no template matches, Michael does not refuse. It produces a clause-level
outline grounded in the retrieved provisions, labels it `DRAFT - NO TEMPLATE`,
and writes the outline to `templates/drafts/` for review.

## Known limitations

**Work health and safety and privacy questions cannot return case law.**
`domains.yaml` filters both domains to `doc_types: [act, regulation]`, so no
WHS or privacy question reaches a judgment however well it matches, while
`employment`, `contracts`, `consumer`, `property`, `corporate` and `migration`
do. Both
exclusions are arguably wrong for the reason `employment`'s was — WHS
prosecutions and privacy determinations are reasoned in decided cases — but
widening a domain changes what retrieval searches and therefore moves the
calibrated numbers, so they are left as they are deliberately rather than by
inheritance. `tests/test_domains.py` pins both directions, so the state is a
decision rather than an accident.

**Material after the associate's certificate is dropped.** A judgment
sometimes annexes a document after the certificate — ACCC v George Weston
Foods [2003] FCA 601 reproduces a 32,728-character s 155 notice as Schedule 1.
It is not the court's reasons and has no pinpoint, so it is not stored and
cannot be quoted. The cover sheet (catchwords, counsel, "Number of
paragraphs") is dropped for the same reason.

**A quotation numbered exactly one above the running count is taken as the
next paragraph.** The indent filter catches every collision the corpus
actually contains, and the next-in-run rule exists because extraction
occasionally loses a paragraph's indent (measured: Flashback Holdings v
Showtime DVD (No 6) [2010] FCA 694, paragraph 10 of 47). A quotation whose
number happens to be `highest + 1` at the same moment would be taken as a
paragraph. Not observed in the 60 judgments measured; stated because it is
reachable.

**`_paragraph_spans` mis-anchors on a body with blanked scaffolding.** Not
introduced here, but found while testing it: for a `DRAFT - NO TEMPLATE`
outline, `verify._verifiable_body()` blanks generated lines to runs of spaces
and `_fix_offsets` then re-finds blocks at the wrong offsets, so the outline
yields no claims at all. The outcome is what `verify_draft` already documents
("0 claims, this draft holds no verifiable prose"), so nothing is currently
wrong with an output — but it is right for the wrong reason, and a test
asserting "no claim mentions X" over an outline cannot fail.

**About 100 transitional clauses of the Migration Regulations carry the
previous clause's pinpoint.** Schedule 13's clauses are headed "Operation of
Schedule 4" and the like. Each sits next to a Part heading ending in a year
("... Regulation 2013"), or next to another such heading. `_is_table_row` and
`_is_contents_entry` read two neighbouring lines that end in bare numbers as
pagination, and reject the heading. Measured in volume 3: 99 rejected as
table rows and 3 as contents rows. None of the text is lost; each clause's
text is stored inside the clause before it, so a hit on it is cited to the
wrong transitional clause. The heuristic is shared by every document type,
and changing it moves every future split, so it was not changed here.
Schedule 13 holds application and transitional provisions, not visa criteria.

**Regulations are cited "s 2.59", not "reg 2.59".** `locator()` renders any
numbered section-type unit as `s N`. AGLC cites a regulation as `reg` (and a
Schedule clause as `sch 2 cl`, which this already does). The Migration
Regulations inherit a form every WA regulation in the corpus already has.
Changing it re-renders every existing regulation citation, so it is recorded
here, not made silently.

**A guidance page is embedded from its opening only.** It is stored as one
row, so a page longer than `EMBEDDING_MAX_CHARS` has a vector built from its
first part. Its full text is still stored and indexed by BM25. A heading-based
guidance splitter is the fix, and no guidance is ingested yet.

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

**Tables of provisions were ingested as provisions — fixed, with a residue.**
Each Act repeats its section numbers and headings in a contents table at the
front. `_is_contents_entry()` now separates those rows from operative headings
by looking at the *neighbouring* lines rather than at the trailing number: a
table of provisions paginates every row, so its entries cluster, whereas a
cited Act's year stands alone in a body that is not paginated inline.

The first version of that rule tested the trailing number alone, which made it
drop every heading ending in a cited year — `26WD Exception—notification under
the My Health Records Act 2012`, `80P … Freedom of Information Act 1982`,
`7B … organisations 1988` — in every document, silently.

**The residue:** the corpus predates the fix, so it is still missing every such
heading. The extent has not been measured, and whether to re-ingest the
existing documents is an open decision. A re-ingest changes the corpus and
therefore voids the calibrated threshold.

**The Federal Register's authorised text needs the right URL, not a browser.**
`/latest/text` is a client-rendered page whose HTML carries only the table of
provisions, and the OData API exposes metadata but not file content — so a
naive fetch of an Act returns headings and looks like a successful ingest. That
mistake put a headings-only Privacy Act page into the corpus once.

The dated Word original *is* fetchable, and `michael ingest` handles it end to
end — fetch, sha256 over the original bytes, DOCX to text, section split,
ingestion log:

```bash
uv run michael ingest   "https://www.legislation.gov.au/C2004A03712/2026-06-04/2026-06-04/text/original/word"   --jurisdiction commonwealth --doc-type act --snapshot-date 2026-06-04   --title "Privacy Act 1988" --citation "Privacy Act 1988 (Cth)"
```

That path produced 355 provisions, 29 of them in Part IIIC, with `26WD`
carrying operative subsections rather than a heading alone.

AustLII still returns 403 to non-browser clients. For anything that genuinely
cannot be fetched, download the volumes by hand and ingest them with provenance
intact:

```bash
uv run michael ingest-file "Fair Work Act 2009 Vol 1.docx"   --source-url https://www.legislation.gov.au/C2009A00028/latest/downloads   --jurisdiction commonwealth --doc-type act   --title "Fair Work Act 2009" --citation "Fair Work Act 2009 (Cth)"
```

`ingest-file` still checks `--source-url` against the host allowlist, so a local
file cannot be used to launder an off-allowlist source. DOCX and HTML are
converted to text by `docx_text.py` and `html_text.py`; the sha256 is taken over
the original bytes either way, so deriving text never weakens provenance.

**Purging documents wiped the database audit log, and the file log did not
catch it.** `ingestion_log` carries a foreign key to `documents`, so
`TRUNCATE documents CASCADE` truncates it too. The intended safety net — the
append-only file at `sources/ingestion.log.jsonl` — was written only from the
URL-fetch step inside `ingest_url`; `ingest_file` and `seed_from_corpus`
reached the database row alone. A full-corpus purge exercised exactly that
gap: 205 database rows gone, 3 lines left in the file — the record meant to
survive the purge was the empty one. `_write()` now writes the file-log entry
itself, for every path that inserts a `documents` row, so an ingestion is
durable across a purge regardless of which command produced it. Refusals are
unaffected and are logged the same way they always were, from
`sources.fetch()` and from `ingest_file`'s own host check.

**The corpus cannot rebuild itself from the database alone.** `documents`
stores metadata and the sha256 of the original bytes, not the extracted text;
`provisions.char_start`/`char_end` are offsets into that text, which is kept
nowhere once ingestion finishes — not even the file log records it. A heading
a splitter bug dropped was never stored, so it cannot be recovered by
re-running the splitter over what is in the database. Fixing a splitter bug
means re-fetching the sources and re-ingesting, not re-splitting in place.

**Very long sections are embedded from their opening only.** Embedding
providers cap input (8192 tokens for `text-embedding-3-small`), so
`EMBEDDING_MAX_CHARS` (default 24,000) caps what is *sent*. The provision
stored in the database keeps its full verbatim text — that is what gets quoted
and cited — and the BM25 arm indexes every word of it. Only the vector for an
oversized section is computed from its opening portion.

**Seeding is one transaction.** `seed_from_corpus` commits once at the end, so a
failure part-way rolls back cleanly and leaves nothing half-written, but a large
`--limit` means a long-running transaction and no partial progress. Seed in
batches if that matters.

**The schema pins the embedding dimension at creation.** `apply_schema()` reads
`EMBEDDING_DIM` when it first creates `provisions`, and `CREATE TABLE IF NOT
EXISTS` will not correct a later mismatch. Changing the embedding model to one
with a different dimension needs a migration, which does not exist yet.

## Hermes

Hermes Agent v0.21.0 drives Michael as its orchestrator. Michael's stdio MCP
server is installed *into* the Hermes image: Hermes runs stdio servers as
subprocesses inside its own container, so a host path would not resolve, and a
Windows `.venv` holds binaries a Linux container cannot execute.

```bash
uv run python hermes/render_config.py      # fills secrets from .env
docker compose -f hermes/docker-compose.yml up -d --build
docker exec michael-hermes hermes -z "your question"
```

**Model choice is benchmarked, not assumed.** `deepseek/deepseek-v4-pro`,
chosen by 42 runs (7 models x 2 prompts x 3 runs) over the acceptance prompt and
a deliberately uncovered question. Harness in `bench/`. Disqualification was any
single rule breach. Re-measured against the corrected MICHAEL.md; an earlier run
against the old "and stop" wording is void.

| model | mean $/run | verdict |
|---|---|---|
| anthropic/claude-opus-5 | 0.21727 | pass |
| anthropic/claude-sonnet-5 | 0.06036 | pass |
| **deepseek/deepseek-v4-pro** | **0.01171** | **pass — selected** |
| anthropic/claude-haiku-4.5 | 0.01117 | disqualified — asked a question instead of drafting |
| deepseek/deepseek-v4-flash | 0.00403 | disqualified — drafted with no pinpoint citation (2/3) |
| openai/gpt-oss-120b | 0.00185 | disqualified — bare "can't provide that" refusal |
| qwen/qwen3.7-flash | 0.00115 | disqualified — no notice, no [MISSING], no citation |

Cost is actual OpenRouter spend, from diffing the key's cumulative usage around
each run; the `--usage-file` figure is self-declared "estimated". Total spend for
the 42 runs was $1.85.

The prompt fix worked: **no model dropped the closing notice on the uncovered
path**, against four of seven before. opus-5 and haiku-4.5 requalified on that
rule; haiku still fails elsewhere. The cheapest model, `v4-flash`, is 2.9x
cheaper again but drafted twice with no pinpoint citation, which is the one
failure this project cannot tolerate.

**The scorer produced two false failures before it was trusted.** A loose
act-name pattern absorbed a "BASED ON" heading and a leading "and" into the
citation, which then would not resolve. `bench/score_final.py` now anchors the
act name to Title Case and **self-tests on known citations before scoring**.
Every remaining disqualification was confirmed by reading the transcript.

**Pin the provider when overriding the model.** `hermes -m anthropic/claude-sonnet-5`
routes to provider `gmi` and fails with no credentials; `--provider openrouter`
is required, and is also what keeps spend on the measured key.

**One container, not two.** The dashboard's chat talks to the gateway over
localhost, so they must share a network namespace. Upstream's Linux compose
splits them and relies on `network_mode: host`, which Docker Desktop for Windows
does not support; on a bridge network the dashboard reports "Gateway Status:
Stopped" and never opens a websocket, because its localhost is not the
gateway's. `HERMES_DASHBOARD=1` has s6 supervise both in one container.

**Docker Desktop can crash at startup and look like a config problem.** A hard
kill of Docker Desktop leaves orphaned `AF_UNIX` socket reparse points behind in
`AppData\Local\Docker\run` and `AppData\Local\docker-secrets-engine`; Windows
reports "The file cannot be accessed by the system" for each and refuses to
delete them individually, so the engine crashes on the next launch. The
symptom points at the wrong thing: `com.docker.service` sits `Stopped`,
`docker info` and `docker compose ps` fail to reach the engine pipe, and
nothing listens on `127.0.0.1:5433` — none of it is a compose or configuration
fault. Rotate (rename) both directories and Docker recreates them clean;
starting `com.docker.service` again needs elevation. This recurs after any hard
kill of Docker Desktop.

**The dashboard requires auth.** A non-loopback bind is refused outright without
an auth provider, and `--insecure` has been a no-op since the June 2026
hardening — the process exits 0 and `restart: unless-stopped` loops it, which
presents to the browser as ERR_EMPTY_RESPONSE. Inside a container the bind must
be `0.0.0.0` for Docker's port proxy to reach it, so credentials are mandatory:
`HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD` in `hermes.env`. The
published port is `127.0.0.1` only.

**`agent.disabled_toolsets` lives in `config.yaml`,** the same file
`render_config.py` writes — so it is declared in the template. It is not
per-platform: omitting it once restored the full toolset to the web chat while
the CLI stayed restricted.

The image tag is `nousresearch/hermes-agent:v2026.8.31` — there is no `v0.21.0`
tag; the registry uses date tags, and `hermes --version` in that image reports
`v0.21.0 (2026.8.31)`.

Its own container (`michael-hermes`), volume (`michael_hermes_data`) and port
(`127.0.0.1:9120`). It joins the `michael_default` network and reaches Postgres
as `michael-postgres:5432`, so the database is never exposed to the host for it.

**Persona.** `/opt/data/SOUL.md` is a byte-identical copy of `MICHAEL.md`,
verified by sha256 at deploy time. `MICHAEL.md` is the source of truth and is
never edited.

**Environment.** Hermes filters the environment of stdio MCP subprocesses to a
small allowlist, so Michael's settings are declared in the `env:` block of the
server entry in `config.yaml`. Inheriting them from the container does not work
— the first deployment failed exactly this way.

**Michael writes in Simplified Technical English.** MICHAEL.md requires
ASD-STE100 for Michael's own prose: short sentences, one idea each, active
voice, present tense, plain words, no filler. The rule is carved out twice so it
cannot erode the guarantees — it does not touch quoted statutory text, which
stays verbatim including long or passive wording, and it removes nothing from
OPEN ITEMS, VERIFY BEFORE USE or the closing notice.

**Web search is a locator, not a source.** The `web` toolset stays enabled so
the agent can find a document worth ingesting, but MICHAEL.md forbids citing a
web page, search result, snippet or summary: only a provision retrieved from the
corpus may be cited, and a web hit never converts NOT COVERED into covered.

That rule is a prompt-level control, and prompt-level controls are not
guarantees. The structural guarantee sits underneath it: the `BASED ON`
citations in a draft are generated by `draft.citations_of()` from the provisions
`retrieve.search()` returned, so a web page cannot become a citation there
whatever the model writes in prose. Anything outside that block is the model's
own text and is checked by reading it.

**Tool posture.** Everything that can write a file or execute code is disabled:

```bash
hermes tools disable terminal file code_execution computer_use                      skills cronjob delegation browser image_gen tts
```

`skills`, `cronjob` and `delegation` are included because each is an indirect
route back to execution — installing a package, scheduling a job, or spawning a
subagent with its own tools. `--safe-mode` is **not** the mechanism for this: it
is a troubleshooting flag that disables all customisation *including MCP
servers*, which would switch Michael off.

## Operating on Railway

Michael is deployed on Railway and is reachable at
`michael-hermes-production.up.railway.app`. Operations run there rather than
against local Docker.

```
railway ssh --service michael-hermes --environment production <command>
```

That reaches the running container over Railway's private network. It does not
open the public Postgres proxy, and nothing here should: the database is
private by design. Michael's operator CLI lives at
`/opt/michael/.venv/bin/michael` inside the container and carries every
subcommand the local CLI does.

The CLI uses the read/write database URL. That is a different surface from the
agent's MCP profile, which holds `classify_request`, `search_provisions` and
`draft_document` and no write tool. Operating by hand does not return write
access to the agent, and must not.

**What runs where.** Code, the test suite and `mypy` stay local, because tests
touch the schema and production data is not a fixture. Corpus inspection,
schema work and production ingests run on Railway.

**The first ingest of any new source runs locally first.** That gate caught
`_is_contents_entry` dropping every section heading that ended in a cited Act's
year; a production-first ingest would have landed 355 provisions with a section
missing and no symptom beyond an occasional wrong `NOT COVERED`. It caught
more on the migration ingest (2026-09-25): every section of the Migration Act
cited as "Sch 1 cl N", and Part 2A of the Migration Regulations missing
entirely. Both are in "Migration law: what was ingested".

**Migration law is local only.** Production has neither migration 0002 nor
the Migration corpus. When it is taken there: deploy the image, run
`michael migrate` (0002 must precede any guidance ingest, since the old CHECK
refuses the type), ingest the five volumes by the dated URLs above, then
re-run calibration against production.

**Deploy before ingesting on Railway.** The container runs the code in its
image, not the working tree, so a parser change that has not been pushed and
built means the production ingest runs the old parser.

Two shell traps. `railway ssh` arguments are parsed by the local shell first,
so pass a Python payload base64-encoded rather than as a quoted multi-line
string. And Windows PowerShell 5.1 swallows a native command's stderr, so a
remote failure appears as empty output — run `railway ssh` from bash when a
command fails for no visible reason.

## Layout

```
docker-compose.yml       pgvector/pg16, loopback-bound, own volume
.env.example             every key; .env is never committed
MICHAEL.md               the system prompt, loaded on every request
domains.yaml             domain routing: filters and template dirs only
db/init/                 creates the read-only role on first start
src/michael/
  config.py  db.py  schema.py  sources.py  embeddings.py
  bm25.py  ingest.py  retrieve.py  domains.py  draft.py
  tools.py  cli.py  mcp_server.py
templates/               markdown templates with explicit placeholders
sources/                 downloaded originals, content-addressed, immutable
tests/
```

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy . && uv run pytest
```

`pytest -m integration` additionally needs the container running; those tests
are excluded from the default run.
