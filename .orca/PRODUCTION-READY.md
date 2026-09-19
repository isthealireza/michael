# Production readiness — the exit criteria

Date opened: 2026-09-20
Owner of this file: ORCHESTRATOR
Status: OPEN

"Production ready" is not a feeling. Without a written, falsifiable list, four
workers each report done against a private standard and nobody can tell whether
the system is finished or merely unexamined. This file is that list.

**A criterion is met only when someone re-ran the measurement and recorded the
output.** A worker's claim closes nothing. Every line below is either PASS with
evidence, FAIL with evidence, or BLOCKED with the reason and who it is blocked
on. There is no fourth state, and "probably fine" is not a state.

---

## A. The gate — nothing ships past a red gate

| # | Criterion | Command |
|---|---|---|
| A1 | Test suite green | `uv run pytest -q` |
| A2 | Type check clean | `uv run mypy src tests` |
| A3 | **Lint clean** | `uv run ruff check` |
| A4 | **Format clean** | `uv run ruff format --check .` |
| A5 | Integration tests run at least once and their result recorded | `uv run pytest -m integration` |

A3 and A4 are on this list because they were missing from it. Every acceptance
this week was gated on A1 and A2 only, while the README's own *Checks* section
lists ruff **first**. Seventy-three lint errors reached `main` through a bar
that had a rail missing. That was the orchestrator's omission, not a worker's.

## B. Safety invariants — these are why the project exists

| # | Criterion | How it is proved |
|---|---|---|
| B1 | The answering agent holds exactly three tools, and no write tool | read the RENDERED config in the container, not the repo |
| B2 | `--allow-writes` absent from the live argv | container process, not the template |
| B3 | The answering path cannot write to the database | `michael_ro` refuses a write, demonstrated |
| B4 | Host allowlist refuses every non-allowlisted host, on every redirect hop | a refusal demonstrated and logged |
| B5 | Empty retrieval returns `covered: false` and NOT COVERED, never a guess | measured on a genuinely absent query |
| B6 | Drafting never invents a fact; unsupplied values become `[MISSING:]` | enforced in `fill()`, proved in code not prose |
| B7 | Never certifies a clause in either direction | measured against the deployed agent |
| B8 | Every output carries the three closing blocks — answer, refusal, NOT COVERED alike | measured on all three shapes |
| B9 | No secret in the repo, in the image, or in page source | searched, not assumed |

## C. Correctness of the corpus

| # | Criterion |
|---|---|
| C1 | Every provision has a parent `documents` row; zero orphans |
| C2 | No provision is a contents-table row, an endnote row, or a `Note:` block |
| C3 | A pinpoint resolves to exactly one provision, or the ambiguity is visible to the reader |
| C4 | `corpus_stats` matches the live counts |
| C5 | Every ingestion is recorded in BOTH the database log and the file log |

## D. The deployed artefact

| # | Criterion |
|---|---|
| D1 | `MICHAEL.md`, `/opt/data/SOUL.md` and `load_system_prompt()` agree by sha256 |
| D2 | Served page assets match the repo byte-for-byte, LF-normalised |
| D3 | The deployment id is recorded, and the artefact — not the green light — is the proof |

## E. Known defects — each closed, or accepted in writing with a reason

An open defect does not prevent production. **An unrecorded one does.** Each
must end as FIXED, or ACCEPTED with the owner's reason and its blast radius.

| # | Defect | State |
|---|---|---|
| E1 | 151 duplicate pinpoint groups in production | open — needs re-ingest of 44 documents, an OPERATOR action, owner-only |
| E2 | 189 excess pinpoints from unnumbered and ordinal Schedule headings | open |
| E3 | `docx_text.py` loses Word `<w:numPr>` auto-numbering | open — on the ingestion path, needs a full rebuild to prove |
| E4 | QA-4 — output stutter, three occurrences, diagnosis unfinished | open — stream inspection before any prompt change |
| E5 | Case law cited as legislation (`s 2` for a judgment paragraph) | open — recorded as a README limitation |
| E6 | Schedule-1 labelling fix measured only at splitter level | blocked — local Postgres down |

## F. Operability

| # | Criterion |
|---|---|
| F1 | A fresh clone reaches a working state from the README alone |
| F2 | `.env.example` carries every key the code reads |
| F3 | A rollback path exists and is written down |
| F4 | Every known trap that has cost this project a session is in a brief or the README |

---

## The rule that governs this campaign

**Workers TEST and REPORT. They do not fix during the test phase.** Fixes are
planned from the complete list, so the programme is ordered by severity rather
than by discovery order. This project has already shipped a fix that was
correct on its own and made a latent defect reachable; fixing by discovery
order is how that happens twice.

**`MICHAEL.md` is owner-only.** A needed prompt change is a drafted diff,
unapplied.

**Production is read-only** through `.orca/ro.sh`. Any mutation is an operator
action and escalates.
