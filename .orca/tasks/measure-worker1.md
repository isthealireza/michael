MEASURE THE PARSER DAMAGE — against the real corpus, read-only.
You are WORKER-1, Ingestion & Corpus Engineer. Orca restarted; your terminal
is fresh and you hold no memory of earlier work.

Read first, in full:
  D:\Projects\michael\.orca\WORKER.md      (rules binding every worker)
  D:\Projects\michael\.orca\WORKER-1.md    (your role, your files, your traps)
  D:\Projects\michael\MICHAEL.md           (the safety rules)
  D:\Projects\michael\.orca\RAILWAY.md     (the operator path - new)

## Background, all owner-verified, none of it to re-litigate

`_is_contents_entry` used to decide from the trailing number alone, so it
silently dropped every section heading ending in a cited Act's year — e.g.
`26WD ... My Health Records Act 2012`, `80P ... Freedom of Information Act
1982`, `7B Acts and practices of organisations 1988`. The fix is committed and
pushed as `bac667e`: the predicate now decides from NEIGHBOURING lines, because
a table of provisions paginates every row so its entries cluster, whereas a
cited year stands alone in a body that is not paginated inline. 150 tests
pass, mypy clean. That fix is accepted and is NOT reopened by this task.

The Privacy Act 1988 (Cth) has since been ingested in both environments.
Local and production are identical: **205 documents, 9111 provisions.**

## The question this task answers

The corpus predates the fix, so it is still missing every heading of that
class. **Nobody knows how many.** That number decides whether the existing
documents get re-ingested — a decision for the ORCHESTRATOR to put to the
owner, and the owner's alone to take. Not yours, not mine.

## MANDATORY: measure the real corpus, not a dataset

Run against the **stored document text in the corpus database** — the 205
documents actually ingested.

**Do NOT measure against the public `isaacus/open-australian-legal-corpus`
dataset.** A previous attempt did exactly that and produced 14,128 and 1,923;
both were unusable, because they measured a superset population and counted
case law that is not even in this corpus. Do not repeat it. If you cannot
reach the corpus, say so and stop rather than substituting a proxy population.

Local Postgres is up (127.0.0.1:5433) and Railway is reachable per RAILWAY.md.
Either corpus is fine — they are identical — but say which you used.

## Deliverable 1 — the classification test, stated and demonstrated

Before any count, state your classification rule EXPLICITLY: the test that
decides whether a recovered heading is a genuine section carrying operative
text, or is a contents row, a footnote row, or a table row.

Then show **worked examples of each class, taken from the real corpus**, with
the document named and the real text quoted:
  - a genuine section the new predicate now keeps;
  - a contents row the new predicate still correctly drops;
  - a footnote row;
  - a table row.

A number whose classification rule is not visible is not a number that can be
ruled on. This deliverable is not optional and the count means nothing without
it.

## Deliverable 2 — the count, BOTH ways

Report two figures and keep them clearly distinct:

  (a) **UPPER BOUND** — how many headings the OLD predicate dropped that the
      NEW one keeps, across the real corpus.
  (b) **ACTUAL DAMAGE** — of those, how many carry OPERATIVE TEXT rather than
      being contents, footnote or table rows.

(b) is the number the decision turns on. (a) is the ceiling. Label them.

Also report:
  - the per-document breakdown, sorted, written to a file whose path you give;
  - the recovered headings themselves, with real excerpts;
  - **the regression check, explicitly**: does the new predicate drop anything
    the old one kept? Check it; do not assume zero;
  - the Privacy Act (document 480) reported SEPARATELY and excluded from the
    legacy figure — it was ingested with the fixed parser already, so counting
    it would dilute the damage number for the 204 older documents.

## Deliverable 3 — one sentence

State plainly what a re-ingest of the existing documents would pull in TODAY,
given the two splitter defects that are still unfixed (endnote/amendment
tables, and the isolated-lookalike rows the neighbour rule cannot catch).
That sentence is what the owner most needs.

## Constraints — these bind you
- **READ-ONLY. No writes of any kind.** No ingest, no re-ingest, no UPDATE,
  no DELETE, no TRUNCATE, no schema change, no embedding call.
- **Do not delete data.** Anything that deletes escalates to me and then to
  the owner. Never decide it yourself.
- Do not fix either splitter defect. Scoping the endnote junk is your NEXT
  task; do not start it here.
- Do not touch retrieval scoring, thresholds, `calibration/` or `bench/` —
  WORKER-2's ground, and a held stage.
- MICHAEL.md is read-only to you. Propose wording to me instead.
- No git push — the owner's alone. No commit without my approval.
- **Leave the repo root clean.** Work in a temp directory. Scratch files left
  in this repo have bitten the project before.
- Secrets stay in `.env`/Railway variables. Never print one.

## Observable acceptance
All three deliverables, every figure traceable to real corpus data with the
query or script you ran shown. Where you could not source something, say so
plainly rather than filling the gap. Report what you ran and what you saw.
Never claim a result you did not verify.
