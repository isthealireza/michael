SCOPING PROPOSAL — the two remaining splitter defects, written up together.
You are WORKER-1. **No code. No ingest. No database write.** A document is
the entire deliverable.

Read .orca/WORKER.md and .orca/WORKER-1.md.

## Standing first: clean up now, not at commit time

You left scratch files in the repo root: `.scratch_step2_measure.py`,
`.scratch_step2_out.log`, `.scratch_step2_err.log`,
`.scratch_step2_bounded_err.log`, `.scratch_step2_bounded_err2.log`,
`new_kept_or_lost.jsonl`. Remove them from the repo working tree now.

If any of it is evidence you want to keep, move it outside the repo first —
your report directory in temp is the right home. Untracked junk in this repo
has bitten the project before, so it does not wait for commit time.
Do NOT touch `.orca/` — those are mine. Do NOT touch your two real modified
files, `src/michael/ingest.py` and `tests/test_ingest.py`; they are accepted
and staying. Confirm with `git status --short` afterwards.

## Context: your Step 1 fix is ACCEPTED

The owner verified it himself against the real function, independently of
both of us. All three cited-year headings return False in real body context;
26WC stays False; a real contents row with paginated neighbours stays True;
and an isolated row with no neighbour context returns False, which he called
the correct conservative default — unproven means keep the law. 150 passed,
mypy clean. Accepted. This task does not revisit it.

He also endorsed withholding the 14,128 / 1,923 figures from his decision,
because they measured a superset population including case law and footnote
rows, not the 204 documents the decision is about.

## Why this task exists now

Local Postgres is down — the Docker Linux engine is dead, the Windows service
com.docker.service is Stopped and needs elevation the workers do not have.
The owner is dealing with it. Phase B is held until it is back; do not start
it and do not ask for it.

Meanwhile the re-ingest decision is blocked on something that is NOT blocked
on Docker: neither remaining splitter defect is scoped. Scope them now so the
decision is ready when the database returns.

## The two defects, both yours, both already evidenced by you

**Defect A — the contents-table blind spot.** Introduced by the accepted fix,
and known. The new rule is: a trailing bare number is a page reference only
when a neighbouring line carries one too. So a genuine contents row whose
neighbours are blank returns False and is kept as a provision. I probed this
directly:
    _is_contents_entry('15A Meaning of casual employee 68',
                       previous='', following='')  ->  False
This is the mechanism behind the junk gains you observed: case-law footnote
lists (`1. Gartside v Sheffield...`) and an Act's internal definitions
cross-reference table (`1. Adoption Act 1994`) do not cluster the way a
paginated table does, so the rule now keeps them. It did not bite the Privacy
Act, where contents rows were still correctly excluded.

**Defect B — endnote and amendment-history tables ingested as provisions.**
Your Phase A finding: the compilation endnotes split into junk rows with
section numbers like `1`, `24`, `21` and headings like
`Jan 1989 (s 2 and gaz 1988, No S399)`. Corpus-wide, not Privacy-Act-specific.

## Deliverable — ONE written proposal covering both

Write it to a file and give me the path. For EACH defect, state:

1. **The defect** — what is wrong, with real evidence from documents you have
   already inspected. Quote real lines. No invented examples.
2. **The rule it needs** — stated in prose, the way your accepted fix stated
   its rule. A rule about what the text *means* structurally, not a heuristic
   on a string. If the honest answer is that the two defects need one shared
   rule rather than two, say so and argue it.
3. **The test fixture it would carry** — the specific cases, with expected
   verdicts, each sourced from a real document you name. Include the cases
   that must NOT regress, not only the ones that must flip. Your accepted fix
   is the standard here: the fixture is what proves the rule.
4. **Blast radius** — does the rule change the corpus? Does it void the
   calibrated RETRIEVAL_MIN_SCORE? Does it interact with the other defect or
   with the accepted fix? Say plainly what re-ingestion it would imply.
5. **Sequencing** — your recommendation on whether A and B are one change or
   two, and what order, and whether either must land before a re-ingest of
   the 204 documents. Recommend; do not decide.

Also state, once, across both: **what a re-ingest would pull in today** if it
happened before either is fixed. That is the sentence the owner most needs.

## Constraints — these bind you
- **NO CODE.** Do not edit ingest.py, tests, or anything else. If you find
  yourself writing an implementation, stop — that is the next task, not this
  one. Read-only analysis and a document.
- No ingest, no DB write, no embedding call, no re-ingest, no deletion.
- Do not touch retrieval scoring, thresholds, calibration/ or bench/.
- Do not attempt to start Docker or reach Postgres. Both are known down.
- Do not use the Railway MCP tools; their view of this account is a decoy.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- No git push. No commit without my approval.

## Observable acceptance
- `git status --short` after cleanup, showing the scratch files gone and the
  two real modified files still present.
- The proposal file path, and the document itself covering all five points
  for both defects plus the re-ingest sentence.
- Every example traceable to a real document you name. Say explicitly where
  you could not source something, as you correctly did for 80P and 7B.
