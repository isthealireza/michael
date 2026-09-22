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

Every defect below has an explicit disposition. `ACCEPTED` means the owner has
recorded the residual risk and a mitigation; it does not claim that the defect
is repaired. Production remains read-only for this review.

| # | Disposition | Owner | Impact / blast radius | Mitigation | Evidence |
|---|---|---|---|---|---|
| E1 | **ACCEPTED** | ORCHESTRATOR / OPERATOR | 151 duplicate pinpoint groups remain in the production corpus; a lookup can expose multiple provisions with the same citation and a caller may select the wrong first result. | Keep ambiguous results visible; do not silently choose one. Owner-only re-ingest of the 44 affected documents remains the remediation, with post-ingest duplicate enumeration required. | `.orca/ORCHESTRATOR.md` records production 205 documents / 8,982 provisions and duplicate groups 618 -> 151; `.orca/reports/2026-09-17-triage-round1.md` records the two `s 47` matches and the required owner action. |
| E2 | **ACCEPTED** | WORKER-1 / ORCHESTRATOR | 189 excess pinpoints from unnumbered or ordinal Schedule headings can create misleading or competing citations. | Treat these as known corpus risk; keep citation ambiguity visible and require a corpus re-ingest/recount before any parser rule is promoted. | `.orca/tasks/qa-campaign.md` records the measured 189-item open ground; `tests/test_ingest.py` covers Schedule heading and local-numbering behavior. Full production re-ingest was not run in this review. |
| E3 | **ACCEPTED** | WORKER-1 / ORCHESTRATOR | DOCX Word auto-numbering (`<w:numPr>`) is lost on extraction, so numbering-only changes and some clause identity can be invisible in contract comparison. | State the limitation; do not claim numbering-only change coverage. Any parser change requires a full corpus rebuild and before/after comparison. | `.orca/ORCHESTRATOR.md` records owner verification of 148 `w:numPr` items with none extracted; `tests/test_docx_text.py` is green in the 276-test run. No production parser change was made. |
| E4 | **ACCEPTED** | ORCHESTRATOR / OWNER | Three observed deployed output stutters can repeat a refusal or closing content, degrading answer readability and trust. | Keep stream diagnosis ahead of any prompt change; inspect `message.delta` index, replace flag, and message id, then apply an owner-reviewed fix only if the stream identifies the cause. | `.orca/reports/2026-09-17-triage-round1.md` records three occurrences and the required stream inspection; `tests/test_tools.py` preserves the regression scenario. Diagnosis remains unverified. |
| E5 | **ACCEPTED** | ORCHESTRATOR / OWNER | Case-law paragraph citations may be rendered as legislation-style `s N`, which can misstate the authority type. | Retain the documented README limitation; require human review of case citations and do not treat the pinpoint as paragraph-level authority. | `README.md:140-142` explicitly documents the limitation using `Muir v Open Brethren [1956] HCA 14 s 2`; `tests/test_draft.py` and the full suite pass. No production corpus rewrite was made. |
| E6 | **ACCEPTED** | ORCHESTRATOR / OPERATOR | Schedule-1 labelling was measured at splitter level only; production/database parity was not demonstrated, so duplicate or mislabelled production citations may remain. | Keep the Schedule-1 change unapplied to production; operator must run the local/production-safe re-ingest and query-level count before closing this item. | `.orca/reports/2026-09-17-triage-round1.md` records splitter results (128 -> 0, 141 -> 0, no provisions dropped) and the Postgres blocker; `uv run pytest -m integration -q` produced 5 skipped because the database was unavailable. |

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
